"""
OCR Engine — PaddleOCR sobre el PDF histórico de 1964 del Ministerio de Hacienda.
Procesa exactamente las páginas indicadas (máximo 15) y guarda resultados en data/processed/.

Uso:
    python ocr_engine.py <ruta_pdf> --paginas 0,1,2,...,14
"""
import argparse
import json
import sys
from pathlib import Path

import fitz  # PyMuPDF
import numpy as np
from PIL import Image

DATA_DIR = Path(__file__).parent.parent / "data"
PROCESSED_DIR = DATA_DIR / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)


def pdf_page_to_image(pdf_path: str, page_idx: int, dpi: int = 200) -> np.ndarray:
    doc = fitz.open(pdf_path)
    page = doc[page_idx]
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    doc.close()
    return np.array(img)


def run_ocr(pdf_path: str, paginas: list[int]) -> dict:
    paginas = paginas[:15]  # máximo 15 páginas

    # Importar PaddleOCR aquí para no cargar el modelo si no hace falta
    from paddleocr import PaddleOCR

    ocr = PaddleOCR(use_angle_cls=True, lang="es", show_log=False)

    results_by_page = {}
    all_lines = []

    for idx in paginas:
        try:
            img_array = pdf_page_to_image(pdf_path, idx)
            ocr_result = ocr.ocr(img_array, cls=True)

            lines = []
            if ocr_result and ocr_result[0]:
                for line in ocr_result[0]:
                    bbox, (text, confidence) = line
                    lines.append({
                        "text": text,
                        "confidence": round(float(confidence), 4),
                        "bbox": [[round(p, 1) for p in point] for point in bbox],
                    })

            results_by_page[str(idx)] = {
                "page_index": idx,
                "n_lines": len(lines),
                "lines": lines,
            }
            all_lines.extend([l["text"] for l in lines])

        except Exception as exc:
            results_by_page[str(idx)] = {"page_index": idx, "error": str(exc)}

    # Análisis estadístico básico del texto extraído
    stats = _analyze_historical_text(all_lines)

    output = {
        "pdf_path": pdf_path,
        "paginas_procesadas": paginas,
        "total_paginas": len(paginas),
        "total_lineas": len(all_lines),
        "pages": results_by_page,
        "estadisticas_historicas": stats,
    }

    out_path = PROCESSED_DIR / "ocr_1964_results.json"
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    return output


def _analyze_historical_text(lines: list[str]) -> dict:
    """Extrae métricas descriptivas del texto OCR del documento de 1964."""
    import re

    # Buscar patrones numéricos (montos, cantidades)
    number_pattern = re.compile(r"[\d,\.]+")
    numbers_found = []
    for line in lines:
        nums = number_pattern.findall(line)
        for n in nums:
            try:
                val = float(n.replace(",", ""))
                if val > 0:
                    numbers_found.append(val)
            except ValueError:
                pass

    # Palabras clave del dominio presupuestal de 1964
    keywords = {
        "ingresos": 0, "egresos": 0, "presupuesto": 0, "hacienda": 0,
        "ministerio": 0, "departamento": 0, "total": 0, "soles": 0,
        "credito": 0, "fondo": 0, "tesoro": 0, "gobierno": 0,
    }
    for line in lines:
        lower = line.lower()
        for kw in keywords:
            if kw in lower:
                keywords[kw] += 1

    # Detectar posibles departamentos o entidades (líneas en mayúsculas)
    upper_lines = [l for l in lines if l.isupper() and len(l) > 3]

    stats = {
        "total_lineas_texto": len(lines),
        "valores_numericos_detectados": len(numbers_found),
        "valor_maximo_encontrado": max(numbers_found) if numbers_found else 0,
        "valor_minimo_encontrado": min(numbers_found) if numbers_found else 0,
        "suma_valores_numericos": round(sum(numbers_found), 2),
        "frecuencia_palabras_clave": keywords,
        "lineas_en_mayusculas": upper_lines[:20],
        "muestra_lineas": lines[:30],
    }
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PaddleOCR Engine — 1964 Historical PDF")
    parser.add_argument("pdf_path", help="Ruta al PDF de 1964")
    parser.add_argument("--paginas", required=True, help="Índices de página separados por coma, ej: 0,1,2")
    args = parser.parse_args()

    paginas = [int(p.strip()) for p in args.paginas.split(",")]
    result = run_ocr(args.pdf_path, paginas)
    print(json.dumps(result, ensure_ascii=False, indent=2))
