"""Helpers y logging compartido para el pipeline MEF."""
import json
import logging
import sys
from pathlib import Path
from datetime import datetime

DATA_DIR = Path(__file__).parent.parent / "data"
PROCESSED_DIR = DATA_DIR / "processed"


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


def format_soles(value: float) -> str:
    """Formatea un valor numérico como soles peruanos."""
    if value >= 1_000_000_000:
        return f"S/ {value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"S/ {value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"S/ {value / 1_000:.1f}K"
    return f"S/ {value:.0f}"


def parse_period(period_str: str) -> dict:
    """
    Parsea un string de período como '2025-12', '2025-Q4', '2025'.
    Retorna dict con año, mes, trimestre según corresponda.
    """
    p = period_str.strip()
    if "-Q" in p:
        year, q = p.split("-Q")
        quarter = int(q)
        return {"year": int(year), "quarter": quarter, "type": "quarterly",
                "month_start": (quarter - 1) * 3 + 1, "month_end": quarter * 3}
    if "-" in p:
        parts = p.split("-")
        return {"year": int(parts[0]), "month": int(parts[1]), "type": "monthly"}
    return {"year": int(p), "type": "annual"}


def load_processed(filename: str) -> "pd.DataFrame | None":
    """Carga un archivo parquet o JSON desde data/processed/."""
    import pandas as pd

    path = PROCESSED_DIR / filename
    if not path.exists():
        return None
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    if path.suffix == ".json":
        return pd.DataFrame([json.loads(path.read_text(encoding="utf-8"))])
    return None


def load_kpis(periodo: str) -> dict | None:
    key = periodo.replace("-", "_")
    path = PROCESSED_DIR / f"kpis_{key}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_ocr_results() -> dict | None:
    path = PROCESSED_DIR / "ocr_1964_results.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_available_periods() -> list[str]:
    """Lista los períodos para los que hay datos procesados."""
    periods = set()
    for f in PROCESSED_DIR.glob("kpis_*.json"):
        period_key = f.stem.replace("kpis_", "")
        # Convertir de vuelta: 2025_12 → 2025-12
        periods.add(period_key.replace("_", "-", 1))
    return sorted(periods)


def append_audit_log(entry: dict) -> None:
    """Agrega una entrada al log de auditoría del Evaluator."""
    log_path = PROCESSED_DIR / "audit_log.json"
    entries = []
    if log_path.exists():
        try:
            entries = json.loads(log_path.read_text(encoding="utf-8"))
        except Exception:
            entries = []
    entry["timestamp"] = datetime.utcnow().isoformat()
    entries.append(entry)
    log_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def load_audit_log() -> list[dict]:
    log_path = PROCESSED_DIR / "audit_log.json"
    if not log_path.exists():
        return []
    try:
        return json.loads(log_path.read_text(encoding="utf-8"))
    except Exception:
        return []
