"""
Data Pipeline — MEF Subnational Efficiency
Descarga, filtra y agrega datos de ejecución presupuestal 2025.
Escribe archivos pequeños en data/processed/ — NUNCA carga CSVs completos en memoria del LLM.

Uso:
    python data_pipeline.py --url <csv_url> --periodo 2025-12 \
        --col-monto PIM --col-devengado DEVENGADO --col-region DEPARTAMENTO
"""
import argparse
import json
import sys
from pathlib import Path

import duckdb
import httpx
import pandas as pd

DATA_DIR = Path(__file__).parent.parent / "data"
PROCESSED_DIR = DATA_DIR / "processed"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"

PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

# Umbral mínimo de PIM para el "Hall of Shame" (10 millones de soles)
MIN_PIM_SOLES = 10_000_000


def download_csv(url: str, dest: Path, chunk_size: int = 8192) -> Path:
    if dest.exists():
        return dest
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size):
                    f.write(chunk)
    return dest


def run_pipeline(
    url: str,
    periodo: str,
    col_monto: str = "PIM",
    col_devengado: str = "DEVENGADO",
    col_region: str = "DEPARTAMENTO",
) -> dict:
    # 1) Nombre de archivo local basado en período
    raw_name = f"mef_{periodo.replace('-', '_')}.csv"
    raw_path = DATA_DIR / "raw_pdfs" / raw_name  # reutilizamos raw_pdfs para CSVs también

    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path = download_csv(url, raw_path)

    # 2) DuckDB procesa el CSV directamente en disco — cero pandas en memoria completa
    con = duckdb.connect()

    # Registrar CSV como vista
    con.execute(f"CREATE VIEW raw AS SELECT * FROM read_csv_auto('{raw_path}', ignore_errors=true)")

    # Snapshot de esquema (solo para referencia del agente)
    schema = con.execute("DESCRIBE raw").fetchdf()
    snapshot = {
        "periodo": periodo,
        "url": url,
        "columnas": schema.to_dict(orient="records"),
        "muestra": con.execute("SELECT * FROM raw LIMIT 5").fetchdf().to_dict(orient="records"),
    }
    snap_path = SNAPSHOTS_DIR / f"snapshot_{periodo.replace('-', '_')}.json"
    snap_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    # 3) Detectar columnas reales (el portal puede usar nombres distintos)
    all_cols = [r[0] for r in con.execute("SELECT column_name FROM (DESCRIBE raw)").fetchall()]
    col_monto = _find_col(all_cols, [col_monto, "PIM", "PRESUPUESTO_INSTITUCIONAL_MODIFICADO", "pim"])
    col_devengado = _find_col(all_cols, [col_devengado, "DEVENGADO", "EJECUCION_DEVENGADO", "devengado"])
    col_region = _find_col(all_cols, [col_region, "DEPARTAMENTO", "REGION", "NOM_DEP", "departamento"])
    col_entidad = _find_col(all_cols, ["ENTIDAD", "NOM_ENTIDAD", "EJECUTORA", "nombre_ejecutora"], required=False)
    col_funcion = _find_col(all_cols, ["FUNCION", "NOM_FUNCION", "CATEGORIA_PRESUPUESTAL"], required=False)

    extra_cols = ""
    if col_entidad:
        extra_cols += f', "{col_entidad}" AS entidad'
    if col_funcion:
        extra_cols += f', "{col_funcion}" AS funcion'

    # 4) Agregación por región — calcula Avance % y Saldo No Devengado
    agg_sql = f"""
        SELECT
            "{col_region}" AS region,
            SUM(CAST("{col_monto}" AS DOUBLE))     AS pim_total,
            SUM(CAST("{col_devengado}" AS DOUBLE)) AS devengado_total,
            COUNT(*) AS n_entidades
        FROM raw
        WHERE "{col_monto}" IS NOT NULL
          AND "{col_devengado}" IS NOT NULL
        GROUP BY "{col_region}"
        ORDER BY pim_total DESC
    """
    df_region = con.execute(agg_sql).fetchdf()
    df_region["avance_pct"] = (df_region["devengado_total"] / df_region["pim_total"].replace(0, float("nan")) * 100).round(2)
    df_region["saldo_no_devengado"] = (df_region["pim_total"] - df_region["devengado_total"]).round(0)

    # 5) Hall of Shame — entidades con PIM > 10M y ejecución baja
    shame_extra = f'"{col_entidad}" AS entidad,' if col_entidad else ""
    shame_funcion = f'"{col_funcion}" AS funcion,' if col_funcion else ""
    shame_sql = f"""
        SELECT
            "{col_region}" AS region,
            {shame_extra}
            {shame_funcion}
            CAST("{col_monto}" AS DOUBLE)     AS pim,
            CAST("{col_devengado}" AS DOUBLE) AS devengado,
            ROUND(CAST("{col_devengado}" AS DOUBLE) /
                  NULLIF(CAST("{col_monto}" AS DOUBLE), 0) * 100, 2) AS avance_pct,
            ROUND(CAST("{col_monto}" AS DOUBLE) - CAST("{col_devengado}" AS DOUBLE), 0) AS saldo_no_devengado
        FROM raw
        WHERE CAST("{col_monto}" AS DOUBLE) >= {MIN_PIM_SOLES}
          AND "{col_monto}" IS NOT NULL
          AND "{col_devengado}" IS NOT NULL
        ORDER BY avance_pct ASC
        LIMIT 200
    """
    df_shame = con.execute(shame_sql).fetchdf()

    # 6) KPIs nacionales
    kpi_sql = f"""
        SELECT
            SUM(CAST("{col_monto}" AS DOUBLE))     AS pim_nacional,
            SUM(CAST("{col_devengado}" AS DOUBLE)) AS devengado_nacional
        FROM raw
        WHERE "{col_monto}" IS NOT NULL
    """
    kpi = con.execute(kpi_sql).fetchone()
    pim_nac = kpi[0] or 0
    dev_nac = kpi[1] or 0
    avance_nac = round(dev_nac / pim_nac * 100, 2) if pim_nac else 0

    # 7) Guardar archivos procesados
    out_region = PROCESSED_DIR / f"region_{periodo.replace('-', '_')}.parquet"
    out_shame = PROCESSED_DIR / f"shame_{periodo.replace('-', '_')}.parquet"
    out_kpis = PROCESSED_DIR / f"kpis_{periodo.replace('-', '_')}.json"

    df_region.to_parquet(out_region, index=False)
    df_shame.to_parquet(out_shame, index=False)
    out_kpis.write_text(
        json.dumps(
            {
                "periodo": periodo,
                "pim_nacional": pim_nac,
                "devengado_nacional": dev_nac,
                "avance_nacional_pct": avance_nac,
                "saldo_no_devengado_nacional": round(pim_nac - dev_nac, 0),
                "n_regiones": len(df_region),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    summary = {
        "periodo": periodo,
        "pim_nacional": pim_nac,
        "devengado_nacional": dev_nac,
        "avance_nacional_pct": avance_nac,
        "archivos_generados": {
            "regiones": str(out_region),
            "hall_of_shame": str(out_shame),
            "kpis": str(out_kpis),
        },
        "top5_regiones_por_pim": df_region.head(5)[["region", "pim_total", "avance_pct"]].to_dict(orient="records"),
        "peores5_ejecutores": df_shame.head(5).to_dict(orient="records"),
    }
    con.close()
    return summary


def _find_col(available: list[str], candidates: list[str], required: bool = True) -> str | None:
    available_upper = {c.upper(): c for c in available}
    for c in candidates:
        if c.upper() in available_upper:
            return available_upper[c.upper()]
    if required:
        raise ValueError(f"No se encontró ninguna columna de {candidates} en {available}")
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MEF Data Pipeline")
    parser.add_argument("--url", required=True)
    parser.add_argument("--periodo", required=True)
    parser.add_argument("--col-monto", default="PIM")
    parser.add_argument("--col-devengado", default="DEVENGADO")
    parser.add_argument("--col-region", default="DEPARTAMENTO")
    args = parser.parse_args()

    result = run_pipeline(
        url=args.url,
        periodo=args.periodo,
        col_monto=args.col_monto,
        col_devengado=args.col_devengado,
        col_region=args.col_region,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
