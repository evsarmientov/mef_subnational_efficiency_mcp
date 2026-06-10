"""
Evaluator Agent — audita los datos procesados y genera evaluator_report.md + audit_log.json.
Se ejecuta como script independiente tras el pipeline.

Uso:
    python evaluator.py --periodo 2025-09
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

DATA_DIR    = Path(__file__).parent.parent / "data"
PROCESSED   = DATA_DIR / "processed"
SNAPSHOTS   = DATA_DIR / "snapshots"


# ── Validaciones ──────────────────────────────────────────────────────────────

def _check_files(periodo: str) -> dict:
    key = periodo.replace("-", "_")
    files = {
        "kpis":   PROCESSED / f"kpis_{key}.json",
        "region": PROCESSED / f"region_{key}.parquet",
        "shame":  PROCESSED / f"shame_{key}.parquet",
        "ocr":    PROCESSED / "ocr_1964_results.json",
    }
    results = {}
    for name, path in files.items():
        exists = path.exists()
        size_kb = round(path.stat().st_size / 1024, 1) if exists else 0
        results[name] = {"exists": exists, "path": str(path), "size_kb": size_kb}
    return results


def _check_data_integrity(periodo: str) -> list[dict]:
    issues = []
    key = periodo.replace("-", "_")

    # Verificar KPIs
    kpis_path = PROCESSED / f"kpis_{key}.json"
    if not kpis_path.exists():
        issues.append({"severity": "ERROR", "check": "KPIs", "detail": "Archivo kpis no encontrado"})
        return issues

    kpis = json.loads(kpis_path.read_text(encoding="utf-8"))
    pim = kpis.get("pim_nacional", 0)
    dev = kpis.get("devengado_nacional", 0)
    avance = kpis.get("avance_nacional_pct", 0)

    if pim == 0:
        issues.append({"severity": "WARN", "check": "PIM Nacional",
                       "detail": "PIM = 0. Se usa COMPROMETIDO_ANUAL como proxy."})
    if dev == 0:
        issues.append({"severity": "ERROR", "check": "Devengado",
                       "detail": "Devengado Nacional = 0. Sin datos de ejecución."})
    if avance > 100:
        issues.append({"severity": "INFO", "check": "Avance %",
                       "detail": f"Avance {avance}% > 100% — devengado supera comprometido registrado. "
                                 "Normal cuando COMPROMETIDO_ANUAL es parcial. Se muestra capeado en 100% en UI."})

    # Verificar región
    region_path = PROCESSED / f"region_{key}.parquet"
    if region_path.exists():
        df_r = pd.read_parquet(region_path)
        n_zero_pim = (df_r["pim_total"] == 0).sum()
        if n_zero_pim > 0:
            issues.append({"severity": "WARN", "check": "PIM por región",
                           "detail": f"{n_zero_pim} regiones con pim_total = 0"})
        neg_avance = (df_r["avance_pct"] < 0).sum()
        if neg_avance:
            issues.append({"severity": "ERROR", "check": "Avance negativo",
                           "detail": f"{neg_avance} regiones con avance_pct < 0 (division error)"})
        if len(df_r) < 10:
            issues.append({"severity": "WARN", "check": "Cobertura regional",
                           "detail": f"Solo {len(df_r)} regiones. Esperado: 25."})
        else:
            issues.append({"severity": "OK", "check": "Cobertura regional",
                           "detail": f"{len(df_r)} regiones cubiertas."})
    else:
        issues.append({"severity": "ERROR", "check": "Parquet región", "detail": "Archivo no encontrado"})

    # Verificar Hall of Shame
    shame_path = PROCESSED / f"shame_{key}.parquet"
    if shame_path.exists():
        df_s = pd.read_parquet(shame_path)
        if len(df_s) == 0:
            issues.append({"severity": "WARN", "check": "Hall of Shame",
                           "detail": "Sin ejecutoras con PIM > 10M. Revisar umbral."})
        else:
            issues.append({"severity": "OK", "check": "Hall of Shame",
                           "detail": f"{len(df_s)} ejecutoras identificadas con PIM > 10M."})
    else:
        issues.append({"severity": "ERROR", "check": "Parquet Hall of Shame", "detail": "Archivo no encontrado"})

    # Verificar OCR 1964
    ocr_path = PROCESSED / "ocr_1964_results.json"
    if ocr_path.exists():
        ocr = json.loads(ocr_path.read_text(encoding="utf-8"))
        n_pags = ocr.get("total_paginas", 0)
        n_lineas = ocr.get("total_lineas", 0)
        if n_pags < 15:
            issues.append({"severity": "WARN", "check": "OCR 1964",
                           "detail": f"Solo {n_pags} páginas procesadas. Mínimo requerido: 15."})
        else:
            issues.append({"severity": "OK", "check": "OCR 1964",
                           "detail": f"{n_pags} páginas procesadas, {n_lineas} líneas extraídas."})
    else:
        issues.append({"severity": "WARN", "check": "OCR 1964",
                       "detail": "ocr_1964_results.json no encontrado."})

    return issues


def _check_app_cache() -> list[dict]:
    """Verifica que app.py use @st.cache_data en todas las funciones de carga."""
    app_path = Path(__file__).parent.parent / "app.py"
    if not app_path.exists():
        return [{"severity": "ERROR", "check": "app.py", "detail": "No encontrado"}]

    src = app_path.read_text(encoding="utf-8")
    load_fns = [l.strip() for l in src.split("\n") if l.strip().startswith("def load_")]
    cached_fns = []
    issues = []
    lines = src.split("\n")
    for i, line in enumerate(lines):
        if line.strip().startswith("def load_"):
            prev = lines[i - 1].strip() if i > 0 else ""
            prev2 = lines[i - 2].strip() if i > 1 else ""
            has_cache = "@st.cache_data" in prev or "@st.cache_data" in prev2
            fn_name = line.strip().split("(")[0].replace("def ", "")
            if has_cache:
                cached_fns.append(fn_name)
            else:
                issues.append({"severity": "WARN", "check": f"Cache: {fn_name}",
                               "detail": f"{fn_name} no tiene @st.cache_data"})

    if cached_fns:
        issues.insert(0, {"severity": "OK", "check": "Cache @st.cache_data",
                          "detail": f"Funciones cacheadas: {', '.join(cached_fns)}"})
    return issues


# ── Generación del reporte ─────────────────────────────────────────────────────

def generate_report(periodo: str) -> dict:
    t0 = time.time()

    file_checks  = _check_files(periodo)
    data_issues  = _check_data_integrity(periodo)
    cache_issues = _check_app_cache()

    all_issues = data_issues + cache_issues
    errors   = [i for i in all_issues if i["severity"] == "ERROR"]
    warnings = [i for i in all_issues if i["severity"] == "WARN"]
    oks      = [i for i in all_issues if i["severity"] == "OK"]
    infos    = [i for i in all_issues if i["severity"] == "INFO"]

    # Cargar KPIs para el reporte
    key = periodo.replace("-", "_")
    kpis_path = PROCESSED / f"kpis_{key}.json"
    kpis = json.loads(kpis_path.read_text(encoding="utf-8")) if kpis_path.exists() else {}

    t_pipeline = kpis.get("tiempo_ejecucion_seg", "N/A")
    meses = kpis.get("meses_acumulados", [])

    # ── Markdown report ──────────────────────────────────────────────────────
    md_lines = [
        f"# Evaluator Agent Report — Período {periodo}",
        f"_Generado: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_",
        "",
        "## 1. Archivos Generados",
        "",
    ]
    for name, info in file_checks.items():
        icon = "✅" if info["exists"] else "❌"
        md_lines.append(f"- {icon} `{Path(info['path']).name}` — {info['size_kb']} KB")

    md_lines += [
        "",
        "## 2. Bugs Encontrados",
        "",
    ]
    if errors:
        for e in errors:
            md_lines.append(f"- ❌ **[{e['check']}]** {e['detail']}")
    else:
        md_lines.append("- ✅ Sin errores críticos detectados.")

    md_lines += ["", "## 3. Advertencias", ""]
    if warnings:
        for w in warnings:
            md_lines.append(f"- ⚠️ **[{w['check']}]** {w['detail']}")
    else:
        md_lines.append("- Sin advertencias.")

    md_lines += ["", "## 4. Optimizaciones UI/UX Aplicadas", ""]
    for o in oks + infos:
        icon = "✅" if o["severity"] == "OK" else "ℹ️"
        md_lines.append(f"- {icon} **[{o['check']}]** {o['detail']}")

    md_lines += [
        "",
        "## 5. Verificación de Integridad de Datos",
        "",
        f"- **Período analizado:** {periodo} (meses acumulados: {meses})",
        f"- **PIM Nacional:** S/ {kpis.get('pim_nacional', 0):,.0f}",
        f"- **Devengado Nacional:** S/ {kpis.get('devengado_nacional', 0):,.0f}",
        f"- **Avance Nacional:** {kpis.get('avance_nacional_pct', 0):.1f}%",
        f"- **Regiones cubiertas:** {kpis.get('n_regiones', 0)}",
        f"- **Ejecutoras en Hall of Shame:** {kpis.get('n_ejecutoras_hall_of_shame', 0)}",
        "",
        "## 6. Benchmarks de Rendimiento",
        "",
        f"- **Tiempo de ejecución del pipeline:** {t_pipeline}s",
        f"- **Fuente de datos:** CSV local de {(DATA_DIR / 'raw_pdfs' / '2025-Gasto-Mensual.csv').stat().st_size / 1e9:.1f} GB procesado con DuckDB",
        "- **Estrategia anti-context-flooding:** DuckDB lee el CSV directamente en disco sin cargarlo en memoria del LLM.",
        "",
        "## 7. Veredicto Final",
        "",
    ]

    if not errors:
        md_lines.append("✅ **Pipeline validado.** Todos los archivos críticos presentes y datos íntegros.")
    else:
        md_lines.append(f"⚠️ **{len(errors)} errores críticos** requieren atención antes de la presentación.")

    md_lines += [
        "",
        "---",
        "_MEF Subnational Efficiency — Evaluator Skill v1.0_",
    ]

    report_md = "\n".join(md_lines)
    report_path = PROCESSED / "evaluator_report.md"
    report_path.write_text(report_md, encoding="utf-8")

    # ── Audit log ────────────────────────────────────────────────────────────
    log_path = PROCESSED / "audit_log.json"
    entries = []
    if log_path.exists():
        try:
            entries = json.loads(log_path.read_text(encoding="utf-8"))
        except Exception:
            entries = []

    entries.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": "evaluator_run",
        "periodo": periodo,
        "errors": len(errors),
        "warnings": len(warnings),
        "tiempo_evaluacion_seg": round(time.time() - t0, 1),
        "veredicto": "OK" if not errors else "ERRORES",
    })
    log_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "periodo": periodo,
        "report_path": str(report_path),
        "errors": len(errors),
        "warnings": len(warnings),
        "veredicto": "OK" if not errors else "ERRORES",
        "tiempo_evaluacion_seg": round(time.time() - t0, 1),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluator Agent — audita pipeline MEF")
    parser.add_argument("--periodo", required=True)
    args = parser.parse_args()
    result = generate_report(args.periodo)
    print(json.dumps(result, ensure_ascii=False, indent=2))
