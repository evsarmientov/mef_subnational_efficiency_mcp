"""
Local MCP Server — MEF Subnational Efficiency Pipeline
Expone herramientas para consultar datosabiertos.gob.pe y procesar datos del MEF.
"""
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger(__name__)

BASE_URL = "https://www.datosabiertos.gob.pe"
CKAN_API = f"{BASE_URL}/api/3/action"
DATA_DIR = Path(__file__).parent.parent / "data"

app = Server("mef-subnational-mcp")


def _get(url: str, params: dict | None = None, timeout: int = 30) -> dict:
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="buscar_datasets",
            description="Busca datasets en datosabiertos.gob.pe por palabras clave.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Palabras clave de búsqueda"},
                    "rows": {"type": "integer", "description": "Número de resultados (default 5)", "default": 5},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="obtener_detalle_dataset",
            description="Obtiene URLs de descarga de recursos de un dataset por su ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string", "description": "ID o nombre del dataset en CKAN"},
                },
                "required": ["dataset_id"],
            },
        ),
        Tool(
            name="descargar_documento_1964",
            description="Descarga el PDF histórico de 1964 del Ministerio de Hacienda a data/raw_pdfs/.",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL directa al PDF de 1964"},
                },
                "required": ["url"],
            },
        ),
        Tool(
            name="listar_entidades_publicas",
            description="Lista ministerios, gobiernos regionales y municipalidades del portal.",
            inputSchema={
                "type": "object",
                "properties": {
                    "tipo": {"type": "string", "description": "Tipo de entidad: 'regional', 'municipal', 'ministerio'", "default": "regional"},
                },
                "required": [],
            },
        ),
        Tool(
            name="inspeccionar_esquema_csv",
            description="Descarga solo las primeras N filas de un CSV para mapear columnas sin cargar el archivo completo.",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL directa al archivo CSV"},
                    "filas": {"type": "integer", "description": "Número de filas de muestra (default 10)", "default": 10},
                },
                "required": ["url"],
            },
        ),
        Tool(
            name="consultar_datastore_filtrado",
            description="Ejecuta queries SQL-like en el datastore de CKAN para obtener slices filtrados.",
            inputSchema={
                "type": "object",
                "properties": {
                    "resource_id": {"type": "string", "description": "ID del recurso en el datastore de CKAN"},
                    "filters": {"type": "object", "description": "Filtros campo:valor a aplicar"},
                    "limit": {"type": "integer", "description": "Máximo de filas a retornar (default 100)", "default": 100},
                    "sql": {"type": "string", "description": "Query SQL opcional para el endpoint /datastore_search_sql"},
                },
                "required": ["resource_id"],
            },
        ),
        Tool(
            name="procesar_ocr_paginas_1964",
            description="Lanza el motor PaddleOCR sobre las páginas seleccionadas del PDF de 1964.",
            inputSchema={
                "type": "object",
                "properties": {
                    "pdf_path": {"type": "string", "description": "Ruta local al PDF de 1964 (relativa a data/raw_pdfs/)"},
                    "paginas": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Lista de índices de página (base 0) a procesar. Máximo 15.",
                    },
                },
                "required": ["pdf_path", "paginas"],
            },
        ),
        Tool(
            name="descargar_y_analizar_estadisticas",
            description="Descarga un recurso CSV/JSON, corre agregaciones ligeras con DuckDB y retorna un resumen estadístico.",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL del recurso a descargar"},
                    "periodo": {"type": "string", "description": "Período de análisis ej. '2025-12' o '2025-Q4'"},
                    "columna_monto": {"type": "string", "description": "Nombre de la columna de monto presupuestal"},
                    "columna_devengado": {"type": "string", "description": "Nombre de la columna de devengado"},
                    "columna_region": {"type": "string", "description": "Nombre de la columna de región/departamento"},
                },
                "required": ["url", "periodo"],
            },
        ),
        Tool(
            name="obtener_ultimas_actualizaciones",
            description="Retorna los datasets actualizados recientemente en el portal del MEF.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limite": {"type": "integer", "description": "Cantidad de datasets a retornar (default 10)", "default": 10},
                },
                "required": [],
            },
        ),
        Tool(
            name="listar_categorias_tematicas",
            description="Mapea los grupos temáticos disponibles en el portal de datos abiertos.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    try:
        result = await _dispatch(name, arguments)
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
    except Exception as exc:
        logger.error(f"Tool {name} falló: {exc}")
        return [TextContent(type="text", text=json.dumps({"error": str(exc)}, ensure_ascii=False))]


async def _dispatch(name: str, args: dict) -> Any:
    if name == "buscar_datasets":
        return _buscar_datasets(args["query"], args.get("rows", 5))

    if name == "obtener_detalle_dataset":
        return _obtener_detalle_dataset(args["dataset_id"])

    if name == "descargar_documento_1964":
        return _descargar_documento_1964(args["url"])

    if name == "listar_entidades_publicas":
        return _listar_entidades_publicas(args.get("tipo", "regional"))

    if name == "inspeccionar_esquema_csv":
        return _inspeccionar_esquema_csv(args["url"], args.get("filas", 10))

    if name == "consultar_datastore_filtrado":
        return _consultar_datastore_filtrado(
            args["resource_id"],
            args.get("filters", {}),
            args.get("limit", 100),
            args.get("sql"),
        )

    if name == "procesar_ocr_paginas_1964":
        return _procesar_ocr_paginas_1964(args["pdf_path"], args["paginas"])

    if name == "descargar_y_analizar_estadisticas":
        return _descargar_y_analizar_estadisticas(
            args["url"],
            args["periodo"],
            args.get("columna_monto", "PIM"),
            args.get("columna_devengado", "DEVENGADO"),
            args.get("columna_region", "DEPARTAMENTO"),
        )

    if name == "obtener_ultimas_actualizaciones":
        return _obtener_ultimas_actualizaciones(args.get("limite", 10))

    if name == "listar_categorias_tematicas":
        return _listar_categorias_tematicas()

    raise ValueError(f"Herramienta desconocida: {name}")


# ── Implementaciones ──────────────────────────────────────────────────────────

def _buscar_datasets(query: str, rows: int) -> dict:
    data = _get(f"{CKAN_API}/package_search", {"q": query, "rows": rows})
    results = data.get("result", {}).get("results", [])
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "title": r.get("title", ""),
            "num_resources": len(r.get("resources", [])),
            "last_modified": r.get("metadata_modified", ""),
        }
        for r in results
    ]


def _obtener_detalle_dataset(dataset_id: str) -> dict:
    data = _get(f"{CKAN_API}/package_show", {"id": dataset_id})
    pkg = data.get("result", {})
    resources = [
        {
            "id": r["id"],
            "name": r.get("name", ""),
            "format": r.get("format", ""),
            "url": r.get("url", ""),
            "last_modified": r.get("last_modified", ""),
        }
        for r in pkg.get("resources", [])
    ]
    return {"title": pkg.get("title", ""), "resources": resources}


def _descargar_documento_1964(url: str) -> dict:
    dest = DATA_DIR / "raw_pdfs" / "hacienda_1964.pdf"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return {"status": "ya_existe", "path": str(dest)}
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=8192):
                    f.write(chunk)
    return {"status": "descargado", "path": str(dest), "size_mb": round(dest.stat().st_size / 1e6, 2)}


def _listar_entidades_publicas(tipo: str) -> dict:
    query_map = {"regional": "gobierno regional", "municipal": "municipalidad", "ministerio": "ministerio"}
    query = query_map.get(tipo, tipo)
    data = _get(f"{CKAN_API}/organization_list", {"all_fields": True, "q": query, "limit": 50})
    orgs = data.get("result", [])
    return {"tipo": tipo, "total": len(orgs), "entidades": [{"name": o.get("name"), "title": o.get("title")} for o in orgs]}


def _inspeccionar_esquema_csv(url: str, filas: int) -> dict:
    import io
    import pandas as pd

    with httpx.Client(timeout=60, follow_redirects=True) as client:
        # Solo descargamos los primeros bytes (~50KB) para evitar cargar el archivo completo
        headers = {"Range": "bytes=0-51200"}
        resp = client.get(url, headers=headers)

    try:
        df = pd.read_csv(io.StringIO(resp.text), nrows=filas, encoding="utf-8", on_bad_lines="skip")
    except Exception:
        df = pd.read_csv(io.StringIO(resp.text), nrows=filas, encoding="latin-1", on_bad_lines="skip")

    snapshot_path = DATA_DIR / "snapshots" / f"snapshot_{url.split('/')[-1][:40]}.json"
    snapshot = {
        "url": url,
        "columnas": list(df.columns),
        "tipos": {c: str(df[c].dtype) for c in df.columns},
        "muestra": df.head(filas).to_dict(orient="records"),
    }
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return snapshot


def _consultar_datastore_filtrado(resource_id: str, filters: dict, limit: int, sql: str | None) -> dict:
    if sql:
        data = _get(f"{CKAN_API}/datastore_search_sql", {"sql": sql})
    else:
        params = {"resource_id": resource_id, "limit": limit}
        if filters:
            params["filters"] = json.dumps(filters)
        data = _get(f"{CKAN_API}/datastore_search", params)
    result = data.get("result", {})
    return {
        "total": result.get("total", 0),
        "fields": result.get("fields", []),
        "records": result.get("records", [])[:limit],
    }


def _procesar_ocr_paginas_1964(pdf_path: str, paginas: list[int]) -> dict:
    import subprocess

    paginas = paginas[:15]  # máximo 15 páginas por instrucción del docente
    full_path = DATA_DIR / "raw_pdfs" / pdf_path
    if not full_path.exists():
        return {"error": f"PDF no encontrado en {full_path}"}

    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parent / "ocr_engine.py"),
            str(full_path),
            "--paginas",
            ",".join(map(str, paginas)),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        return {"error": result.stderr}
    return json.loads(result.stdout)


def _descargar_y_analizar_estadisticas(
    url: str, periodo: str, col_monto: str, col_devengado: str, col_region: str
) -> dict:
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parent / "data_pipeline.py"),
            "--url", url,
            "--periodo", periodo,
            "--col-monto", col_monto,
            "--col-devengado", col_devengado,
            "--col-region", col_region,
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        return {"error": result.stderr}
    return json.loads(result.stdout)


def _obtener_ultimas_actualizaciones(limite: int) -> dict:
    data = _get(f"{CKAN_API}/recently_changed_packages_activity_list", {"limit": limite})
    activities = data.get("result", [])
    return [
        {
            "dataset": a.get("data", {}).get("package", {}).get("title", ""),
            "timestamp": a.get("timestamp", ""),
            "activity_type": a.get("activity_type", ""),
        }
        for a in activities[:limite]
    ]


def _listar_categorias_tematicas() -> dict:
    data = _get(f"{CKAN_API}/group_list", {"all_fields": True})
    groups = data.get("result", [])
    return [{"name": g.get("name"), "title": g.get("title"), "count": g.get("package_count", 0)} for g in groups]


# ── Entry point ───────────────────────────────────────────────────────────────

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
