"""
Data Pipeline — MEF Subnational Efficiency
Procesa el CSV de ejecución presupuestal 2025 (10+ GB) directamente con DuckDB en disco.
NUNCA carga el dataset completo en memoria ni en el contexto del LLM.
Guarda micro-archivos Parquet/JSON en data/processed/.

Uso:
    python data_pipeline.py --periodo 2025-09
    python data_pipeline.py --periodo 2025-Q3
    python data_pipeline.py --periodo 2025        # año completo
"""
import argparse
import json
import sys
import time
import warnings
from pathlib import Path

import duckdb

warnings.filterwarnings("ignore")

DATA_DIR    = Path(__file__).parent.parent / "data"
PROCESSED_DIR = DATA_DIR / "processed"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
RAW_CSV     = DATA_DIR / "raw_pdfs" / "2025-Gasto-Mensual.csv"

PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

MIN_PIM_SOLES = 10_000_000
PIPELINE_VERSION = "1.1.0"


def _periodo_to_filter(periodo: str) -> tuple[str, list[int]]:
    """
    Devuelve (cláusula WHERE, lista de meses incluidos).
    El filtro es ACUMULADO: si el período es 2025-09 incluye meses 1..9 (enero–sept).
    Si es trimestral 2025-Q3 incluye meses 1..9 también.
    """
    if "-Q" in periodo:
        q = int(periodo.split("-Q")[1])
        m_end = q * 3
        meses = list(range(1, m_end + 1))
        return f"CAST(MES_EJE AS INTEGER) <= {m_end}", meses
    if "-" in periodo:
        mes = int(periodo.split("-")[1])
        meses = list(range(1, mes + 1))
        return f"CAST(MES_EJE AS INTEGER) <= {mes}", meses
    return "1=1", list(range(1, 13))  # año completo


def run_pipeline(periodo: str) -> dict:
    t_start = time.time()

    if not RAW_CSV.exists():
        return {"error": f"CSV no encontrado: {RAW_CSV}. Descárgalo de datosabiertos.mef.gob.pe"}

    where, meses = _periodo_to_filter(periodo)
    csv_path = str(RAW_CSV).replace("\\", "/")

    # DuckDB procesa el CSV directamente en disco — cero pandas en memoria
    con = duckdb.connect()

    # ── PASO 1: Snapshot del esquema (solo las primeras 10 filas) ─────────────
    snap_df = con.execute(f"""
        SELECT * FROM read_csv_auto('{csv_path}', ignore_errors=true)
        LIMIT 10
    """).fetchdf()

    snapshot = {
        "periodo": periodo,
        "csv_path": str(RAW_CSV),
        "columnas": list(snap_df.columns),
        "tipos": {c: str(snap_df[c].dtype) for c in snap_df.columns},
        "muestra": snap_df.head(5).to_dict(orient="records"),
    }
    (SNAPSHOTS_DIR / f"snapshot_mef_2025.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(f"Esquema: {len(snap_df.columns)} columnas detectadas", file=sys.stderr)

    # ── PASO 2: Agregación por región ─────────────────────────────────────────
    print(f"Agregando por región para período {periodo}...", file=sys.stderr)
    df_region = con.execute(f"""
        SELECT
            DEPARTAMENTO_EJECUTORA_NOMBRE                                       AS region,
            SUM(TRY_CAST(MONTO_COMPROMETIDO_ANUAL AS DOUBLE))                  AS pim_total,
            SUM(TRY_CAST(MONTO_DEVENGADO AS DOUBLE))                            AS devengado_total,
            SUM(TRY_CAST(MONTO_COMPROMETIDO AS DOUBLE))                         AS comprometido_total,
            SUM(TRY_CAST(MONTO_GIRADO AS DOUBLE))                               AS girado_total,
            COUNT(*)                                                             AS n_registros
        FROM read_csv_auto('{csv_path}', ignore_errors=true)
        WHERE {where}
          AND DEPARTAMENTO_EJECUTORA_NOMBRE IS NOT NULL
        GROUP BY DEPARTAMENTO_EJECUTORA_NOMBRE
        ORDER BY pim_total DESC NULLS LAST
    """).fetchdf()

    # Limpiar y calcular métricas
    for col in ["pim_total", "devengado_total", "comprometido_total", "girado_total"]:
        df_region[col] = df_region[col].fillna(0)

    df_region["avance_pct"] = (
        df_region["devengado_total"] / df_region["pim_total"].replace(0, float("nan")) * 100
    ).round(2).fillna(0)
    df_region["saldo_no_devengado"] = (df_region["pim_total"] - df_region["devengado_total"]).round(0)

    # ── PASO 3: Hall of Shame (ejecutoras con PIM > 10M y bajo avance) ────────
    print("Calculando Hall of Shame...", file=sys.stderr)
    df_shame = con.execute(f"""
        SELECT
            DEPARTAMENTO_EJECUTORA_NOMBRE   AS region,
            EJECUTORA_NOMBRE                AS entidad,
            FUNCION_NOMBRE                  AS funcion,
            SUM(TRY_CAST(MONTO_COMPROMETIDO_ANUAL AS DOUBLE)) AS pim,
            SUM(TRY_CAST(MONTO_DEVENGADO AS DOUBLE))          AS devengado,
            COUNT(*)                                           AS n_registros
        FROM read_csv_auto('{csv_path}', ignore_errors=true)
        WHERE {where}
          AND DEPARTAMENTO_EJECUTORA_NOMBRE IS NOT NULL
        GROUP BY DEPARTAMENTO_EJECUTORA_NOMBRE, EJECUTORA_NOMBRE, FUNCION_NOMBRE
        HAVING SUM(TRY_CAST(MONTO_COMPROMETIDO_ANUAL AS DOUBLE)) >= {MIN_PIM_SOLES}
        ORDER BY pim DESC NULLS LAST
        LIMIT 300
    """).fetchdf()

    for col in ["pim", "devengado"]:
        df_shame[col] = df_shame[col].fillna(0)

    df_shame["avance_pct"] = (
        df_shame["devengado"] / df_shame["pim"].replace(0, float("nan")) * 100
    ).round(2).fillna(0)
    df_shame["saldo_no_devengado"] = (df_shame["pim"] - df_shame["devengado"]).round(0)
    df_shame = df_shame.sort_values("avance_pct")

    # ── PASO 4: KPIs nacionales ───────────────────────────────────────────────
    pim_nac = float(df_region["pim_total"].sum())
    dev_nac = float(df_region["devengado_total"].sum())
    avance_nac = round(dev_nac / pim_nac * 100, 2) if pim_nac else 0

    con.close()

    # ── PASO 5: Guardar micro-archivos en data/processed/ ─────────────────────
    key = periodo.replace("-", "_")
    out_region = PROCESSED_DIR / f"region_{key}.parquet"
    out_shame  = PROCESSED_DIR / f"shame_{key}.parquet"
    out_kpis   = PROCESSED_DIR / f"kpis_{key}.json"

    df_region.to_parquet(out_region, index=False)
    df_shame.to_parquet(out_shame, index=False)
    t_total = round(time.time() - t_start, 1)
    print(f"Pipeline completado en {t_total}s", file=sys.stderr)

    out_kpis.write_text(
        json.dumps({
            "periodo": periodo,
            "meses_acumulados": meses,
            "pim_nacional": pim_nac,
            "devengado_nacional": dev_nac,
            "avance_nacional_pct": avance_nac,
            "saldo_no_devengado_nacional": round(pim_nac - dev_nac, 0),
            "n_regiones": len(df_region),
            "n_ejecutoras_hall_of_shame": len(df_shame),
            "tiempo_ejecucion_seg": t_total,
            "nota": "Acumulado enero–mes indicado. PIM = MONTO_COMPROMETIDO_ANUAL del CSV MEF.",
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary = {
        "periodo": periodo,
        "pim_nacional": pim_nac,
        "devengado_nacional": dev_nac,
        "avance_nacional_pct": avance_nac,
        "tiempo_ejecucion_seg": t_total,
        "n_registros_procesados": int(df_region["n_registros"].sum()),
        "archivos_generados": {
            "regiones": str(out_region),
            "hall_of_shame": str(out_shame),
            "kpis": str(out_kpis),
        },
        "top5_regiones": df_region.head(5)[["region", "pim_total", "avance_pct"]].to_dict(orient="records"),
        "peores5_ejecutores": df_shame.head(5)[["region", "entidad", "pim", "avance_pct"]].to_dict(orient="records"),
    }
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MEF Data Pipeline — procesa CSV local con DuckDB")
    parser.add_argument("--periodo", required=True, help="Período: 2025-09, 2025-Q3, 2025")
    parser.add_argument("--url", default=None)          # ignorado, compatibilidad
    parser.add_argument("--col-monto", default=None)
    parser.add_argument("--col-devengado", default=None)
    parser.add_argument("--col-region", default=None)
    args = parser.parse_args()

    result = run_pipeline(periodo=args.periodo)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
