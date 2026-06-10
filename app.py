"""
MEF Subnational Efficiency Dashboard
4-tab Streamlit application — Fiscal Year 2025 + Historical 1964 Track
"""
import json
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# Asegurar que src/ esté en el path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from utils import (
    DATA_DIR,
    PROCESSED_DIR,
    format_soles,
    list_available_periods,
    load_audit_log,
    load_kpis,
    load_ocr_results,
)

# ── Configuración de página ───────────────────────────────────────────────────

st.set_page_config(
    page_title="MEF — Eficiencia Subnacional 2025",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .main { background-color: #f8f9fa; }
    .block-container { padding-top: 1.5rem; padding-bottom: 1rem; }
    .metric-card {
        background: white;
        border-radius: 8px;
        padding: 1rem;
        box-shadow: 0 1px 4px rgba(0,0,0,.08);
        margin-bottom: .5rem;
    }
    .stMetric { background: white; border-radius: 8px; padding: .75rem; }
    .status-red { color: #d73027; font-weight: 600; }
    .status-yellow { color: #f4a623; font-weight: 600; }
    .status-green { color: #1a9850; font-weight: 600; }
    h1, h2, h3 { color: #1a237e; }
    .historical-box {
        background: #fff8e1;
        border-left: 4px solid #f9a825;
        border-radius: 4px;
        padding: 1rem 1.25rem;
        margin-bottom: 1rem;
    }
    .audit-entry {
        background: #f3f4f6;
        border-radius: 6px;
        padding: .6rem 1rem;
        margin-bottom: .4rem;
        font-size: .85rem;
        font-family: monospace;
    }
</style>
""", unsafe_allow_html=True)


# ── Funciones con caché ───────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def load_region_data(periodo: str) -> pd.DataFrame | None:
    key = periodo.replace("-", "_")
    path = PROCESSED_DIR / f"region_{key}.parquet"
    return pd.read_parquet(path) if path.exists() else None


@st.cache_data(ttl=300)
def load_shame_data(periodo: str) -> pd.DataFrame | None:
    key = periodo.replace("-", "_")
    path = PROCESSED_DIR / f"shame_{key}.parquet"
    return pd.read_parquet(path) if path.exists() else None


@st.cache_data(ttl=300)
def load_kpis_cached(periodo: str) -> dict | None:
    return load_kpis(periodo)


@st.cache_data(ttl=3600)
def load_ocr_cached() -> dict | None:
    return load_ocr_results()


@st.cache_data(ttl=300)
def load_audit_log_cached() -> list[dict]:
    return load_audit_log()


@st.cache_data(ttl=3600)
def load_evaluator_report() -> str:
    path = PROCESSED_DIR / "evaluator_report.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


@st.cache_data
def build_regional_bar(df_region: pd.DataFrame, top_n: int = 20) -> go.Figure:
    df = df_region.sort_values("avance_pct").head(top_n).copy()
    colors = ["#d73027" if x < 30 else ("#fdae61" if x < 60 else "#1a9850") for x in df["avance_pct"]]
    fig = go.Figure(go.Bar(
        x=df["avance_pct"],
        y=df["region"],
        orientation="h",
        marker_color=colors,
        text=[f"{x:.1f}%" for x in df["avance_pct"]],
        textposition="outside",
    ))
    fig.update_layout(
        title="Regiones con Menor Avance de Ejecución (2025)",
        xaxis_title="Avance (%)", yaxis_title="",
        xaxis_range=[0, 115], height=480,
        margin={"l": 160, "r": 40, "t": 50, "b": 40},
        plot_bgcolor="white", paper_bgcolor="white",
    )
    return fig


@st.cache_data
def build_frozen_capital_bar(df_region: pd.DataFrame) -> go.Figure:
    df = df_region.sort_values("saldo_no_devengado", ascending=False).head(15)
    fig = px.bar(
        df, x="region", y="saldo_no_devengado",
        color="avance_pct",
        color_continuous_scale=["#1a9850", "#fdae61", "#d73027"],
        range_color=[100, 0],
        title="Capital Paralizado por Región — Top 15 (2025)",
        labels={"saldo_no_devengado": "Saldo No Devengado (PEN)", "region": "Región", "avance_pct": "Avance %"},
    )
    fig.update_layout(xaxis_tickangle=-40, plot_bgcolor="white", paper_bgcolor="white")
    return fig


@st.cache_data
def build_1964_keywords_chart(keywords: dict) -> go.Figure:
    kw = {k: v for k, v in keywords.items() if v > 0}
    if not kw:
        return go.Figure().update_layout(title="Sin términos detectados")
    df = pd.DataFrame(sorted(kw.items(), key=lambda x: -x[1]), columns=["Término", "Frecuencia"])
    fig = px.bar(
        df, x="Término", y="Frecuencia",
        color="Frecuencia", color_continuous_scale="Blues",
        title="Términos Fiscales en el Archivo Histórico 1964",
    )
    fig.update_layout(showlegend=False, xaxis_tickangle=-25, plot_bgcolor="white", paper_bgcolor="white")
    return fig


@st.cache_data
def build_1964_numeric_histogram(all_values: list[float]) -> go.Figure:
    if not all_values:
        return go.Figure().update_layout(title="Sin valores numéricos detectados")
    fig = px.histogram(
        x=all_values, nbins=25, log_y=True,
        title="Distribución de Valores Numéricos — Documento Fiscal 1964",
        labels={"x": "Valor (Soles de 1964)", "y": "Frecuencia (log)"},
        color_discrete_sequence=["#1565C0"],
    )
    fig.update_layout(bargap=0.05, plot_bgcolor="white", paper_bgcolor="white")
    return fig


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.image("https://upload.wikimedia.org/wikipedia/commons/thumb/c/cf/Flag_of_Peru.svg/120px-Flag_of_Peru.svg.png", width=80)
    st.title("MEF Analytics")
    st.caption("Pipeline Multi-Agente — Eficiencia Subnacional")
    st.divider()

    available = list_available_periods()
    if available:
        periodo_sel = st.selectbox("Período de análisis", available, index=len(available) - 1)
    else:
        periodo_sel = st.text_input("Período (ej: 2025-12)", value="2025-12")
        st.info("Sin datos procesados. Ejecuta:\n`claude \"run executor_skill for period 2025-12\"`")

    st.divider()
    st.markdown("**Ejecutar pipeline:**")
    st.code('claude "run executor_skill for period 2025-12"', language="bash")
    st.code('claude "run evaluator_skill for period 2025-12"', language="bash")


# ── Tabs ──────────────────────────────────────────────────────────────────────

tab1, tab2, tab3, tab4 = st.tabs([
    "📊 Resumen Ejecutivo",
    "🗺️ Distribución Territorial",
    "🚨 Hall of Shame",
    "🤖 Auditoría Multi-Agente",
])


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — Resumen Ejecutivo Dual (2025 + 1964)
# ═══════════════════════════════════════════════════════════════════════════════
with tab1:
    st.header("Resumen Ejecutivo Macro — Período Fiscal 2025")

    kpis = load_kpis_cached(periodo_sel)
    if kpis:
        pim = kpis.get("pim_nacional", 0)
        dev = kpis.get("devengado_nacional", 0)
        avance_raw = kpis.get("avance_nacional_pct", 0)
        # Cap display at 100% — avance >100 ocurre cuando devengado > comprometido_anual
        avance_display = min(avance_raw, 100.0)
        color_class = "status-green" if avance_display >= 70 else ("status-yellow" if avance_display >= 40 else "status-red")
        saldo = abs(kpis.get("saldo_no_devengado_nacional", 0))

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Comprometido Anual", format_soles(pim),
                      help="MONTO_COMPROMETIDO_ANUAL acumulado — proxy del presupuesto disponible")
        with col2:
            st.metric("Devengado Nacional", format_soles(dev))
        with col3:
            st.metric(
                "Avance de Ejecución",
                f"{avance_display:.1f}%",
                delta=f"{avance_display - 75:.1f}% vs meta 75%",
                delta_color="normal",
            )
        with col4:
            st.metric("Capital Paralizado", format_soles(saldo),
                      help="Unidades con PIM > 10M PEN y bajo avance de ejecución")

        nota_avance = f" (avance real: {avance_raw:.1f}% — devengado supera comprometido registrado)" if avance_raw > 100 else ""
        st.markdown(f"""
        <div class="metric-card">
        <b>🤖 Análisis del Agente Ejecutor — {periodo_sel}</b><br><br>
        El período fiscal <b>{periodo_sel}</b> registra un avance de ejecución nacional de
        <span class="{color_class}">{avance_display:.1f}%{nota_avance}</span>.
        El comprometido anual asciende a <b>{format_soles(pim)}</b> y el devengado a
        <b>{format_soles(dev)}</b>. Las unidades ejecutoras con presupuesto mayor a 10M de soles y ejecución
        inferior al 30% representan el principal cuello de botella de la descentralización fiscal peruana.
        Se identificaron <b>{kpis.get("n_ejecutoras_hall_of_shame", 0)} ejecutoras</b> en zona crítica.
        </div>
        """, unsafe_allow_html=True)
    else:
        st.warning(f"Sin datos para el período **{periodo_sel}**. Ejecuta el Executor Skill primero.")

    st.divider()

    # ── Sección Histórica 1964 (independiente, sin comparaciones cross-epoch) ──
    st.header("📜 Archivo Histórico — Ministerio de Hacienda 1964")
    st.markdown('<div class="historical-box">Análisis independiente del documento fiscal de 1964. '
                'Los datos de esta sección NO son comparables con los datos modernos 2025 — '
                'corresponden a marcos contables y monetarios completamente distintos.</div>',
                unsafe_allow_html=True)

    ocr_data = load_ocr_cached()
    if ocr_data and not ocr_data.get("error"):
        stats = ocr_data.get("estadisticas_historicas", {})
        keywords = stats.get("frecuencia_palabras_clave", {})

        c1, c2, c3 = st.columns(3)
        c1.metric("Páginas procesadas", ocr_data.get("total_paginas", 0))
        c2.metric("Líneas de texto extraídas", ocr_data.get("total_lineas", 0))
        c3.metric("Valores numéricos detectados", stats.get("valores_numericos_detectados", 0))

        # Conclusiones textuales del OCR
        with st.expander("📄 Muestra de texto extraído por PaddleOCR", expanded=False):
            sample_lines = stats.get("muestra_lineas", [])
            if sample_lines:
                st.text("\n".join(sample_lines[:25]))
            else:
                st.info("Sin líneas de muestra disponibles.")

        upper_lines = stats.get("lineas_en_mayusculas", [])
        if upper_lines:
            st.markdown("**Entidades/Departamentos detectados en el documento:**")
            st.write(" · ".join(upper_lines[:15]))

        # Gráfico 1 — Frecuencia de términos fiscales
        col_a, col_b = st.columns(2)
        with col_a:
            st.plotly_chart(build_1964_keywords_chart(keywords), use_container_width=True)

        # Gráfico 2 — Distribución de valores numéricos
        with col_b:
            import re
            all_values = []
            for page_data in ocr_data.get("pages", {}).values():
                for line in page_data.get("lines", []):
                    for n in re.findall(r"[\d,\.]+", line.get("text", "")):
                        try:
                            val = float(n.replace(",", ""))
                            if 10 < val < 1_000_000_000:
                                all_values.append(val)
                        except ValueError:
                            pass
            st.plotly_chart(build_1964_numeric_histogram(all_values), use_container_width=True)

        st.markdown(f"""
        <div class="historical-box">
        <b>📌 Conclusiones del Análisis OCR — 1964</b><br><br>
        El motor PaddleOCR procesó <b>{ocr_data.get("total_paginas", 0)} páginas</b> del archivo
        <i>Presupuesto, Balance y Cuenta General de la República</i> del Ministerio de Hacienda (1964),
        extrayendo <b>{ocr_data.get("total_lineas", 0)} líneas de texto</b> y
        <b>{stats.get("valores_numericos_detectados", 0)} valores numéricos</b>.<br><br>
        Los términos más frecuentes corresponden a categorías de ingresos, egresos y asignaciones
        departamentales propias del sistema presupuestal pre-reforma del Estado peruano de los años 60.
        La suma total de valores cuantificados asciende a
        <b>S/ {stats.get("suma_valores_numericos", 0):,.0f}</b> (en soles de 1964).
        </div>
        """, unsafe_allow_html=True)
    else:
        st.info("Sin resultados OCR disponibles. Ejecuta: `claude \"run executor_skill for period 2025-12\"` con `run_ocr=true`")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — Distribución Territorial 2025
# ═══════════════════════════════════════════════════════════════════════════════
with tab2:
    st.header("🗺️ Distribución Territorial — Ejecución Presupuestal 2025")
    st.caption(f"Período: **{periodo_sel}** · Fuente: datosabiertos.gob.pe / MEF-SIAF")

    df_region = load_region_data(periodo_sel)
    if df_region is not None and not df_region.empty:
        # KPI rápido
        worst = df_region.loc[df_region["avance_pct"].idxmin()]
        best = df_region.loc[df_region["avance_pct"].idxmax()]
        c1, c2, c3 = st.columns(3)
        c1.metric("Total regiones", len(df_region))
        c2.metric(f"Peor ejecutor", f"{worst['region']} ({worst['avance_pct']:.1f}%)")
        c3.metric(f"Mejor ejecutor", f"{best['region']} ({best['avance_pct']:.1f}%)")

        st.plotly_chart(build_regional_bar(df_region), use_container_width=True)
        st.plotly_chart(build_frozen_capital_bar(df_region), use_container_width=True)

        with st.expander("Tabla completa por región"):
            show_df = df_region[["region", "pim_total", "devengado_total", "avance_pct", "saldo_no_devengado", "n_registros"]].copy()
            show_df.columns = ["Región", "PIM Total", "Devengado", "Avance %", "Saldo No Devengado", "N° Registros"]
            for col in ["PIM Total", "Devengado", "Saldo No Devengado"]:
                show_df[col] = show_df[col].apply(format_soles)
            st.dataframe(show_df, use_container_width=True, hide_index=True)
    else:
        st.warning(f"Sin datos territoriales para {periodo_sel}. Ejecuta el Executor Skill.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — Hall of Shame 2025
# ═══════════════════════════════════════════════════════════════════════════════
with tab3:
    st.header("🚨 Hall of Shame — Peores Ejecutores 2025")
    st.caption("Unidades con PIM > 10M PEN y baja ejecución presupuestal")

    df_shame = load_shame_data(periodo_sel)
    if df_shame is not None and not df_shame.empty:
        # Filtros interactivos
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            max_avance = st.slider("Mostrar ejecutores con avance máximo de:", 0, 100, 40, step=5, format="%d%%")
        with col_f2:
            min_pim_m = st.number_input("PIM mínimo (millones PEN):", min_value=10, max_value=1000, value=10, step=10)

        df_filtered = df_shame[
            (df_shame["avance_pct"] <= max_avance) &
            (df_shame["pim"] >= min_pim_m * 1_000_000)
        ].copy()

        st.metric("Ejecutoras en zona crítica", len(df_filtered), delta=f"de {len(df_shame)} con PIM > 10M")

        if not df_filtered.empty:
            # Treemap
            label_col = "entidad" if "entidad" in df_filtered.columns else "region"
            fig_tree = px.treemap(
                df_filtered.head(80),
                path=[px.Constant("Perú"), "region", label_col],
                values="saldo_no_devengado",
                color="avance_pct",
                color_continuous_scale=["#d73027", "#fdae61", "#1a9850"],
                range_color=[0, max_avance],
                title=f"Capital Paralizado — Ejecutoras con Avance ≤ {max_avance}% y PIM > {min_pim_m}M PEN",
                labels={"saldo_no_devengado": "Saldo No Devengado", "avance_pct": "Avance %"},
            )
            st.plotly_chart(fig_tree, use_container_width=True)

            # Tabla interactiva
            display_cols = ["region", label_col, "pim", "devengado", "avance_pct", "saldo_no_devengado"]
            display_cols = [c for c in display_cols if c in df_filtered.columns]
            col_labels = {
                "region": "Región", label_col: "Ejecutora",
                "pim": "PIM (PEN)", "devengado": "Devengado (PEN)",
                "avance_pct": "Avance %", "saldo_no_devengado": "Capital Paralizado (PEN)",
            }
            show = df_filtered[display_cols].rename(columns=col_labels)
            st.dataframe(
                show.style.background_gradient(subset=["Avance %"], cmap="RdYlGn", vmin=0, vmax=100),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.success("No hay ejecutoras en zona crítica con los filtros seleccionados.")
    else:
        st.warning(f"Sin datos de Hall of Shame para {periodo_sel}. Ejecuta el Executor Skill.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4 — Auditoría Multi-Agente + Playground 2025
# ═══════════════════════════════════════════════════════════════════════════════
with tab4:
    st.header("🤖 Log de Auditoría Multi-Agente")
    st.caption("Registro de ejecuciones del Executor Skill y correcciones del Evaluator Skill")

    # ── Benchmarks de rendimiento ─────────────────────────────────────────────
    kpis_t4 = load_kpis_cached(periodo_sel)
    if kpis_t4:
        t_exec = kpis_t4.get("tiempo_ejecucion_seg")
        meses_acc = kpis_t4.get("meses_acumulados", [])
        c1, c2, c3 = st.columns(3)
        c1.metric("Tiempo pipeline", f"{t_exec}s" if t_exec else "N/A",
                  help="Tiempo de ejecución de DuckDB sobre el CSV de 10+ GB")
        c2.metric("Meses acumulados", len(meses_acc),
                  help=f"Período acumulado: meses {meses_acc[0] if meses_acc else '?'}–{meses_acc[-1] if meses_acc else '?'}")
        c3.metric("Registros procesados", f"{kpis_t4.get('n_regiones', 0)} regiones",
                  help="Grupos por departamento generados por DuckDB")

    st.divider()

    # ── Reporte del Evaluator ─────────────────────────────────────────────────
    report_md = load_evaluator_report()
    if report_md:
        with st.expander("📋 Reporte del Evaluator Agent", expanded=True):
            st.markdown(report_md)
    else:
        st.info("Sin reporte del Evaluator aún. Ejecuta desde la CLI:")
        st.code(f'python src/evaluator.py --periodo {periodo_sel}', language="bash")

    st.divider()

    # ── Log de auditoría cronológico ──────────────────────────────────────────
    st.subheader("📝 Historial de Ejecuciones")
    audit_log = load_audit_log_cached()
    if audit_log:
        for entry in reversed(audit_log[-20:]):
            ts = entry.get("timestamp", "")[:19].replace("T", " ")
            action = entry.get("action", "event")
            periodo_entry = entry.get("periodo", "")
            veredicto = entry.get("veredicto", "")
            t_eval = entry.get("tiempo_evaluacion_seg", "")
            errors = entry.get("errors", 0)
            warns = entry.get("warnings", 0)
            color = "#1a9850" if veredicto == "OK" else "#d73027"
            detail_str = f"período {periodo_entry} | {veredicto} | {errors} errores, {warns} advertencias | {t_eval}s"
            st.markdown(
                f'<div class="audit-entry"><b>{ts}</b> · '
                f'<span style="color:#1565C0">{action}</span> · '
                f'<span style="color:{color}">{detail_str}</span></div>',
                unsafe_allow_html=True,
            )
    else:
        st.caption("Sin entradas en el log todavía. Ejecuta el Evaluator Skill para generar.")

    st.divider()

    # ── Playground — CLI en vivo ──────────────────────────────────────────────
    st.subheader("🎮 Playground — Cambio de Período en Vivo")
    st.markdown("Selecciona un período y ejecuta los comandos desde la CLI de Claude Code:")

    play_periodo = st.text_input("Período a analizar (acumulado):", value="2025-09", key="playground_periodo",
                                 help="2025-09 = enero–septiembre | 2025-Q3 = Q1+Q2+Q3 | 2025 = año completo")

    col_p1, col_p2, col_p3 = st.columns(3)
    with col_p1:
        st.markdown("**1. Pipeline de datos:**")
        st.code(f'python src/data_pipeline.py --periodo {play_periodo}', language="bash")
    with col_p2:
        st.markdown("**2. Auditoría:**")
        st.code(f'python src/evaluator.py --periodo {play_periodo}', language="bash")
    with col_p3:
        st.markdown("**3. Via Claude Code CLI:**")
        st.code(f'claude "run executor_skill for period {play_periodo}"', language="bash")

    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        if st.button("🔄 Recargar dashboard", type="primary"):
            st.cache_data.clear()
            st.rerun()
    with col_btn2:
        if st.button("📊 Ver períodos disponibles"):
            from utils import list_available_periods
            periodos = list_available_periods()
            st.write("Períodos procesados:", periodos if periodos else "Ninguno aún")

    st.divider()
    st.caption("Pipeline MEF Subnational Efficiency · Powered by Claude Code + MCP · "
               "Datos: datosabiertos.mef.gob.pe · OCR: PaddleOCR/EasyOCR")
