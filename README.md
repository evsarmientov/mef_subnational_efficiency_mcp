# MEF Subnational Efficiency — Multi-Agent Analytics Pipeline

Pipeline multi-agente local para auditar la ejecución del presupuesto subnacional peruano (2025) e historizar el archivo fiscal de 1964 mediante PaddleOCR.

## Arquitectura

```
Claude Code CLI
    ├── executor_skill   →  MCP Server  →  datosabiertos.gob.pe
    │                            └──────→  data_pipeline.py (DuckDB)
    │                            └──────→  ocr_engine.py (PaddleOCR)
    └── evaluator_skill  →  app.py audit + data/processed/ validation
                              └──────→  evaluator_report.md
Streamlit app.py  ←  data/processed/*.parquet + *.json
```

## Requisitos

```bash
pip install -r requirements.txt
```

> PaddleOCR requiere `paddlepaddle`. En Windows usar `paddlepaddle` CPU; en Linux con GPU usar `paddlepaddle-gpu`.

## Uso rápido

### 1. Iniciar el MCP Server (en terminal separada)
```bash
python src/mcp_server.py
```

### 2. Ejecutar el pipeline de datos
```bash
claude "run executor_skill for period 2025-12"
```

### 3. Auditar y optimizar
```bash
claude "run evaluator_skill for period 2025-12"
```

### 4. Lanzar el dashboard
```bash
streamlit run app.py
```

## Flujo CLI parametrizado

El sistema acepta períodos en estos formatos:
- Mensual: `2025-12`
- Trimestral: `2025-Q4`
- Anual: `2025`

## Estructura de datos procesados

| Archivo | Contenido |
|---|---|
| `data/processed/region_{periodo}.parquet` | Agregación PIM/Devengado por departamento |
| `data/processed/shame_{periodo}.parquet` | Ejecutoras con PIM > 10M y bajo avance |
| `data/processed/kpis_{periodo}.json` | Indicadores nacionales |
| `data/processed/ocr_1964_results.json` | Texto y métricas extraídas por PaddleOCR |
| `data/processed/evaluator_report.md` | Reporte de auditoría del Evaluator Agent |

## Regla anti-context flooding

**Nunca** se cargan CSVs completos en el contexto del LLM. El pipeline:
1. Inspecciona solo las primeras 10 filas para mapear columnas
2. Ejecuta `src/data_pipeline.py` como proceso local (DuckDB sobre disco)
3. Guarda solo archivos Parquet/JSON pequeños en `data/processed/`
4. `app.py` lee exclusivamente esos archivos procesados
