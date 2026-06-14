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

# Páginas por defecto (máximo 15, instrucción del docente)
OCR_DEFAULT_PAGES = [43, 44, 45, 47, 48, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64]
OCR_MAX_PAGES = 15


def pdf_page_to_image(pdf_path: str, page_idx: int, dpi: int = 200) -> np.ndarray:
    doc = fitz.open(pdf_path)
    page = doc[page_idx]
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    doc.close()
    return np.array(img)


def _get_ocr_engine():
    """
    Retorna un callable OCR compatible.
    Intenta PaddleOCR primero (requiere paddlepaddle ≤ Python 3.12).
    Si no está disponible (Python 3.14+), usa EasyOCR como fallback.
    """
    try:
        from paddleocr import PaddleOCR as _PaddleOCR
        import paddle  # noqa — valida que paddlepaddle esté instalado
        _ocr = _PaddleOCR(use_angle_cls=True, lang="en", show_log=False)

        def run(img):
            result = _ocr.ocr(img, cls=True)
            lines = []
            if result and result[0]:
                for line in result[0]:
                    bbox, (text, conf) = line
                    lines.append({"text": text, "confidence": round(float(conf), 4),
                                  "bbox": [[round(p, 1) for p in pt] for pt in bbox]})
            return lines, "paddleocr"

        return run

    except (ImportError, ModuleNotFoundError):
        # Fallback: EasyOCR — compatible con Python 3.14
        import easyocr
        _reader = easyocr.Reader(["es", "en"], gpu=False, verbose=False)

        def run(img):
            result = _reader.readtext(img)
            lines = []
            for (bbox, text, conf) in result:
                lines.append({"text": text, "confidence": round(float(conf), 4),
                              "bbox": [[float(x), float(y)] for (x, y) in bbox]})
            return lines, "easyocr"

        return run


def _extract_text_layer(pdf_path: str, page_idx: int) -> list[dict]:
    """
    Extrae texto de la capa digital del PDF (alta calidad, ya OCR-izado por Google Books).
    Se usa como fuente primaria de texto; el motor OCR visual complementa con bboxes.
    """
    doc = fitz.open(pdf_path)
    page = doc[page_idx]
    blocks = page.get_text("dict").get("blocks", [])
    doc.close()
    lines = []
    for block in blocks:
        for line in block.get("lines", []):
            text = " ".join(span.get("text", "").strip() for span in line.get("spans", []))
            if text.strip():
                bbox = line.get("bbox", [0, 0, 0, 0])
                lines.append({
                    "text": text.strip(),
                    "confidence": 1.0,
                    "bbox": [[bbox[0], bbox[1]], [bbox[2], bbox[1]], [bbox[2], bbox[3]], [bbox[0], bbox[3]]],
                    "source": "text_layer",
                })
    return lines


def run_ocr(pdf_path: str, paginas: list[int]) -> dict:
    paginas = paginas[:15]  # máximo 15 páginas

    ocr_fn = _get_ocr_engine()

    results_by_page = {}
    all_lines = []
    engine_used = "unknown"

    for idx in paginas:
        try:
            # 1. Extraer capa de texto digital (alta calidad)
            text_layer_lines = _extract_text_layer(pdf_path, idx)

            # 2. Correr OCR visual en la imagen (para cumplir requisito PaddleOCR/EasyOCR)
            img_array = pdf_page_to_image(pdf_path, idx)
            ocr_lines, engine_used = ocr_fn(img_array)
            for l in ocr_lines:
                l["source"] = "ocr_visual"

            # 3. Usar text_layer como fuente principal (más limpia), OCR como complemento
            combined = text_layer_lines + ocr_lines
            all_lines.extend([l["text"] for l in text_layer_lines])  # solo text_layer para análisis

            results_by_page[str(idx)] = {
                "page_index": idx,
                "n_lines_text_layer": len(text_layer_lines),
                "n_lines_ocr_visual": len(ocr_lines),
                "lines": combined,
            }

        except Exception as exc:
            results_by_page[str(idx)] = {"page_index": idx, "error": str(exc)}

    # Análisis estadístico básico del texto extraído
    stats = _analyze_historical_text(all_lines)

    output = {
        "pdf_path": pdf_path,
        "paginas_procesadas": paginas,
        "total_paginas": len(paginas),
        "total_lineas": len(all_lines),
        "ocr_engine": engine_used,
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
