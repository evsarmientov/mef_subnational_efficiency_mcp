"""
Analytical Engine — Cálculo de métricas de ejecución presupuestal 2025
y procesamiento de resultados históricos 1964.
"""
import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from utils import DATA_DIR, PROCESSED_DIR, format_soles, load_kpis, load_ocr_results

PERU_DEPARTMENTS = [
    "AMAZONAS", "ANCASH", "APURIMAC", "AREQUIPA", "AYACUCHO", "CAJAMARCA",
    "CALLAO", "CUSCO", "HUANCAVELICA", "HUANUCO", "ICA", "JUNIN", "LA LIBERTAD",
    "LAMBAYEQUE", "LIMA", "LORETO", "MADRE DE DIOS", "MOQUEGUA", "PASCO",
    "PIURA", "PUNO", "SAN MARTIN", "TACNA", "TUMBES", "UCAYALI",
]


# ── Métricas 2025 ─────────────────────────────────────────────────────────────

def compute_execution_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Calcula Avance % y Saldo No Devengado sobre un DataFrame de regiones."""
    df = df.copy()
    df["avance_pct"] = (
        df["devengado_total"] / df["pim_total"].replace(0, float("nan")) * 100
    ).round(2)
    df["saldo_no_devengado"] = (df["pim_total"] - df["devengado_total"]).round(0)
    df["capital_paralizado_pct"] = (
        df["saldo_no_devengado"] / df["pim_total"].replace(0, float("nan")) * 100
    ).round(2)
    return df


def get_frozen_capital_summary(periodo: str) -> dict:
    """Retorna el capital paralizado total y por región para un período."""
    key = periodo.replace("-", "_")
    path = PROCESSED_DIR / f"region_{key}.parquet"
    if not path.exists():
        return {"error": f"No hay datos para el período {periodo}"}

    df = pd.read_parquet(path)
    df = compute_execution_metrics(df)

    total_pim = df["pim_total"].sum()
    total_dev = df["devengado_total"].sum()
    total_saldo = df["saldo_no_devengado"].sum()

    return {
        "periodo": periodo,
        "capital_paralizado_total": round(total_saldo, 0),
        "capital_paralizado_total_fmt": format_soles(total_saldo),
        "avance_nacional_pct": round(total_dev / total_pim * 100, 2) if total_pim else 0,
        "regiones_criticas": df[df["avance_pct"] < 50][["region", "pim_total", "avance_pct"]]
            .sort_values("pim_total", ascending=False)
            .head(10)
            .to_dict(orient="records"),
    }


# ── Visualizaciones 2025 ──────────────────────────────────────────────────────

def plot_execution_map(df_region: pd.DataFrame) -> go.Figure:
    """Mapa de calor de ejecución presupuestal por departamento (2025)."""
    df = df_region.copy()
    df["region_upper"] = df["region"].str.upper().str.strip()

    fig = px.choropleth(
        df,
        locations="region_upper",
        locationmode="country names",  # fallback — se puede reemplazar con GeoJSON de Perú
        color="avance_pct",
        color_continuous_scale=["#d73027", "#fdae61", "#fee08b", "#a6d96a", "#1a9850"],
        range_color=[0, 100],
        labels={"avance_pct": "Avance %", "region_upper": "Departamento"},
        title="Ejecución Presupuestal 2025 por Departamento (%)",
    )
    fig.update_layout(
        margin={"r": 0, "t": 40, "l": 0, "b": 0},
        coloraxis_colorbar={"title": "Avance %"},
    )
    return fig


def plot_regional_bar(df_region: pd.DataFrame, top_n: int = 25) -> go.Figure:
    """Barras horizontales de avance por región."""
    df = df_region.sort_values("avance_pct").head(top_n).copy()
    df["color"] = df["avance_pct"].apply(
        lambda x: "#d73027" if x < 30 else ("#fdae61" if x < 60 else "#1a9850")
    )
    fig = go.Figure(go.Bar(
        x=df["avance_pct"],
        y=df["region"],
        orientation="h",
        marker_color=df["color"],
        text=df["avance_pct"].apply(lambda x: f"{x:.1f}%"),
        textposition="outside",
    ))
    fig.update_layout(
        title="Regiones con Menor Avance de Ejecución — 2025",
        xaxis_title="Avance (%)",
        yaxis_title="",
        xaxis_range=[0, 110],
        height=500,
        margin={"l": 150},
    )
    return fig


def plot_frozen_capital_treemap(df_shame: pd.DataFrame) -> go.Figure:
    """Treemap de capital paralizado por ejecutora."""
    df = df_shame.copy()
    label_col = "entidad" if "entidad" in df.columns else "region"
    fig = px.treemap(
        df,
        path=[px.Constant("Perú"), "region", label_col],
        values="saldo_no_devengado",
        color="avance_pct",
        color_continuous_scale=["#d73027", "#fee08b", "#1a9850"],
        range_color=[0, 100],
        title="Capital Paralizado por Unidad Ejecutora (PIM > 10M PEN, 2025)",
        labels={"saldo_no_devengado": "Saldo No Devengado (PEN)", "avance_pct": "Avance %"},
    )
    fig.update_layout(margin={"t": 50, "l": 0, "r": 0, "b": 0})
    return fig


# ── Análisis histórico 1964 ───────────────────────────────────────────────────

def analyze_1964_ocr() -> dict:
    """Procesa los resultados OCR y devuelve métricas y figuras para Tab 1."""
    ocr_data = load_ocr_results()
    if not ocr_data:
        return {"error": "No hay resultados OCR disponibles. Ejecuta el OCR engine primero."}

    stats = ocr_data.get("estadisticas_historicas", {})
    keywords = stats.get("frecuencia_palabras_clave", {})
    lines_sample = stats.get("muestra_lineas", [])

    # Filtrar keywords con ocurrencias
    kw_df = pd.DataFrame(
        [(k, v) for k, v in keywords.items() if v > 0],
        columns=["termino", "frecuencia"]
    ).sort_values("frecuencia", ascending=False)

    return {
        "total_paginas_procesadas": ocr_data.get("total_paginas", 0),
        "total_lineas_extraidas": ocr_data.get("total_lineas", 0),
        "valores_numericos": stats.get("valores_numericos_detectados", 0),
        "suma_valores_numericos": stats.get("suma_valores_numericos", 0),
        "keywords_df": kw_df,
        "muestra_texto": lines_sample,
        "lineas_mayusculas": stats.get("lineas_en_mayusculas", []),
    }


def plot_1964_keywords(kw_df: pd.DataFrame) -> go.Figure:
    """Gráfico de barras de frecuencia de términos fiscales en el documento de 1964."""
    if kw_df.empty:
        fig = go.Figure()
        fig.update_layout(title="Sin datos OCR disponibles")
        return fig
    fig = px.bar(
        kw_df,
        x="termino",
        y="frecuencia",
        color="frecuencia",
        color_continuous_scale="Blues",
        title="Frecuencia de Términos Fiscales — Archivo Histórico 1964",
        labels={"termino": "Término", "frecuencia": "Ocurrencias"},
    )
    fig.update_layout(showlegend=False, xaxis_tickangle=-30)
    return fig


def plot_1964_numeric_distribution(ocr_data: dict) -> go.Figure:
    """Histograma de distribución de valores numéricos extraídos del documento de 1964."""
    import re

    all_values = []
    for page_data in ocr_data.get("pages", {}).values():
        for line in page_data.get("lines", []):
            nums = re.findall(r"[\d,\.]+", line.get("text", ""))
            for n in nums:
                try:
                    val = float(n.replace(",", ""))
                    if 10 < val < 1_000_000_000:
                        all_values.append(val)
                except ValueError:
                    pass

    if not all_values:
        fig = go.Figure()
        fig.update_layout(title="Sin valores numéricos detectados en el OCR")
        return fig

    fig = px.histogram(
        x=all_values,
        nbins=30,
        log_y=True,
        title="Distribución de Valores Numéricos — Documento Fiscal 1964",
        labels={"x": "Valor (Soles de 1964)", "y": "Frecuencia (escala log)"},
        color_discrete_sequence=["#2196F3"],
    )
    fig.update_layout(bargap=0.05)
    return fig
