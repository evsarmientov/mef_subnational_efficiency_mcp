"""
Procesa todos los meses disponibles en el CSV MEF 2025 de una sola vez.
Genera los parquets en data/processed/ para que el dashboard cargue sin re-procesar.

Uso:
    python src/run_all_periods.py
    python src/run_all_periods.py --meses 1 2 3 9 12   # solo esos meses
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Añadir src/ al path
sys.path.insert(0, str(Path(__file__).parent))
from data_pipeline import run_pipeline
from evaluator import generate_report

PROCESSED = Path(__file__).parent.parent / "data" / "processed"


def main(meses: list[int]):
    print(f"Procesando {len(meses)} períodos: {meses}")
    print("=" * 50)

    resultados = []
    t_total = time.time()

    for mes in meses:
        periodo = f"2025-{mes:02d}"
        key = f"2025_{mes:02d}"

        # Saltar si ya existe
        if (PROCESSED / f"kpis_{key}.json").exists():
            print(f"[SKIP] {periodo} — ya procesado")
            resultados.append({"periodo": periodo, "status": "skip"})
            continue

        print(f"\n[RUN]  {periodo} (acumulado enero–{mes:02d})...")
        t0 = time.time()

        try:
            result = run_pipeline(periodo)
            t_pipe = round(time.time() - t0, 1)
            avance = result.get("avance_nacional_pct", 0)
            n_reg = result.get("n_registros_procesados", 0)
            print(f"       ✓ {t_pipe}s | {n_reg:,} registros | avance {avance:.1f}%")

            # Evaluator
            eval_result = generate_report(periodo)
            errores = eval_result.get("errors", 0)
            print(f"       ✓ Evaluator: {eval_result['veredicto']} ({errores} errores)")

            resultados.append({
                "periodo": periodo,
                "status": "ok",
                "avance_pct": avance,
                "tiempo_seg": t_pipe,
                "errores_evaluator": errores,
            })

        except Exception as e:
            print(f"       ✗ Error: {e}")
            resultados.append({"periodo": periodo, "status": "error", "detalle": str(e)})

    t_elapsed = round(time.time() - t_total, 1)
    print("\n" + "=" * 50)
    print(f"Completado en {t_elapsed}s\n")

    ok = [r for r in resultados if r["status"] == "ok"]
    skip = [r for r in resultados if r["status"] == "skip"]
    err = [r for r in resultados if r["status"] == "error"]

    print(f"  ✅ Procesados: {len(ok)}")
    print(f"  ⏭  Saltados (ya existían): {len(skip)}")
    print(f"  ❌ Errores: {len(err)}")

    if ok:
        print("\nResumen por período:")
        for r in ok:
            print(f"  {r['periodo']}: avance {r['avance_pct']:.1f}% | {r['tiempo_seg']}s")

    # Guardar resumen
    summary_path = PROCESSED / "all_periods_summary.json"
    summary_path.write_text(
        json.dumps({"resultados": resultados, "tiempo_total_seg": t_elapsed}, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"\nResumen guardado en: {summary_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--meses", nargs="+", type=int,
        default=list(range(1, 10)),  # enero–septiembre (meses con datos en el CSV)
        help="Lista de meses a procesar (default: 1-9)"
    )
    args = parser.parse_args()
    main(args.meses)
