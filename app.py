import base64
import os
import re
import tempfile
from typing import Any, Dict, List

import fitz
from flask import Flask, jsonify, request


app = Flask(__name__)


# =====================================================
# CONFIGURACION GENERAL
# =====================================================

API_KEY = os.environ.get("PDF_API_KEY", "CAMBIAR_POR_UNA_CLAVE_SEGURA")
MAX_PDF_BYTES = int(os.environ.get("MAX_PDF_BYTES", str(45 * 1024 * 1024)))


# =====================================================
# SEGURIDAD
# =====================================================

def check_api_key(req) -> bool:
    incoming = req.headers.get("X-API-Key", "")
    return bool(API_KEY) and incoming == API_KEY


def unauthorized_response():
    return jsonify({
        "ok": False,
        "error": "API key invalida o ausente."
    }), 401


# =====================================================
# NORMALIZACION DE TEXTO
# =====================================================

def normalize_text(value: Any) -> str:
    if value is None:
        return ""

    value = str(value)
    value = value.replace("\u00a0", " ")
    value = value.replace("\r", "\n")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)

    return value.strip()


def normalize_for_search(value: Any) -> str:
    value = normalize_text(value).lower()

    replacements = {
        "á": "a",
        "à": "a",
        "ä": "a",
        "â": "a",
        "é": "e",
        "è": "e",
        "ë": "e",
        "ê": "e",
        "í": "i",
        "ì": "i",
        "ï": "i",
        "î": "i",
        "ó": "o",
        "ò": "o",
        "ö": "o",
        "ô": "o",
        "ú": "u",
        "ù": "u",
        "ü": "u",
        "û": "u",
        "ñ": "n",
        "—": "-",
        "–": "-"
    }

    for src, dst in replacements.items():
        value = value.replace(src, dst)

    value = re.sub(r"\s+", " ", value)
    return value.strip()


def normalize_words(value: Any) -> Listtext = normalize_for_search(value)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    parts = [p.strip() for p in text.split() if p.strip()]
    return parts


# =====================================================
# PDF
# =====================================================

def decode_pdf_base64(pdf_base64: str) -> bytes:
    if not pdf_base64:
        raise ValueError("No se recibio pdf_base64.")

    try:
        pdf_bytes = base64.b64decode(pdf_base64)
    except Exception as exc:
        raise ValueError(f"No se pudo decodificar pdf_base64: {str(exc)}")

    if not pdf_bytes:
        raise ValueError("El PDF decodificado esta vacio.")

    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise ValueError(
            f"El PDF supera el tamano maximo permitido. "
            f"Tamano: {len(pdf_bytes)} bytes. Maximo: {MAX_PDF_BYTES} bytes."
        )

    return pdf_bytes


def open_pdf_from_bytes(pdf_bytes: bytes):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.write(pdf_bytes)
    tmp.flush()
    tmp.close()

    try:
        doc = fitz.open(tmp.name)
        return doc, tmp.name
    except Exception as exc:
        try:
            os.remove(tmp.name)
        except Exception:
            pass

        raise ValueError(f"No se pudo abrir el PDF con PyMuPDF: {str(exc)}")


def cleanup_temp(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


# =====================================================
# EXTRACCION Y BUSQUEDA
# =====================================================

def get_page_text(page) -> str:
    try:
        text = page.get_text("text") or ""
        return normalize_text(text)
    except Exception:
        return ""


def get_page_blocks_text(page) -> str:
    try:
        blocks = page.get_text("blocks") or []
        parts = []

        for block in blocks:
            if len(block) >= 5:
                text = str(block[4] or "").strip()
                if text:
                    parts.append(text)

        return normalize_text("\n".join(parts))
    except Exception:
        return ""


def extract_snippet(page_text: str, phrase: str, radius: int = 260) -> str:
    page_text = normalize_text(page_text)

    if not page_text:
        return ""

    search_text = normalize_for_search(page_text)
    search_phrase = normalize_for_search(phrase)

    idx = search_text.find(search_phrase)

    if idx == -1:
        words = normalize_words(phrase)
        if words:
            idx = search_text.find(words[0])

    if idx == -1:
        return normalize_text(page_text[:520])

    start = max(0, idx - radius)
    end = min(len(page_text), idx + len(phrase) + radius)

    return normalize_text(page_text[start:end])


def exact_match(page_text: str, phrase: str) -> bool:
    return normalize_for_search(phrase) in normalize_for_search(page_text)


def flexible_match(page_text: str, phrase: str) -> bool:
    page_norm = normalize_for_search(page_text)
    words = normalize_words(phrase)

    if not words:
        return False

    important_words = [w for w in words if len(w) >= 4]

    if not important_words:
        important_words = words

    hits = 0

    for word in important_words:
        if word in page_norm:
            hits += 1

    ratio = hits / float(len(important_words))

    return ratio >= 0.72


def search_pdf_bytes(
    pdf_bytes: bytes,
    phrase: str,
    all_pages: bool = False,
    exact: bool = True,
    include_text: bool = False
) -> Dict[str, Any]:
    phrase = normalize_text(phrase)

    if not phrase:
        return {
            "ok": False,
            "found": False,
            "error": "Debe indicar una frase a buscar.",
            "matches": []
        }

    doc = None
    tmp_path = ""

    matches: List[Dict[str, Any]] = []
    total_pages = 0
    pages_with_text = 0

    try:
        doc, tmp_path = open_pdf_from_bytes(pdf_bytes)
        total_pages = doc.page_count

        for page_index in range(total_pages):
            page = doc.load_page(page_index)
            text = get_page_text(page)

            if not text:
                text = get_page_blocks_text(page)

            if normalize_text(text):
                pages_with_text += 1

            if exact:
                found = exact_match(text, phrase)
            else:
                found = flexible_match(text, phrase)

            if found:
                snippet = extract_snippet(text, phrase)

                match_data = {
                    "page_index": page_index,
                    "page_number": page_index + 1,
                    "snippet": snippet,
                    "text_length": len(text),
                    "match_type": "exact" if exact else "flexible"
                }

                if include_text:
                    match_data["page_text"] = text

                matches.append(match_data)

                if not all_pages:
                    break

        requires_ocr = total_pages > 0 and pages_with_text == 0

        return {
            "ok": True,
            "found": len(matches) > 0,
            "message": "Encontrado" if matches else "No encontrado",
            "phrase": phrase,
            "exact": exact,
            "all_pages": all_pages,
            "total_pages": total_pages,
            "pages_with_text": pages_with_text,
            "requires_ocr": requires_ocr,
            "matches": matches
        }

    except Exception as exc:
        return {
            "ok": False,
            "found": False,
            "error": str(exc),
            "total_pages": total_pages,
            "pages_with_text": pages_with_text,
            "requires_ocr": False,
            "matches": []
        }

    finally:
        try:
            if doc:
                doc.close()
        except Exception:
            pass

        cleanup_temp(tmp_path)


def analyze_pdf_bytes(pdf_bytes: bytes) -> Dict[str, Any]:
    doc = None
    tmp_path = ""

    total_pages = 0
    pages_with_text = 0
    pages_empty = 0
    sample_pages = []

    try:
        doc, tmp_path = open_pdf_from_bytes(pdf_bytes)
        total_pages = doc.page_count

        for page_index in range(total_pages):
            page = doc.load_page(page_index)
            text = get_page_text(page)

            if not text:
                text = get_page_blocks_text(page)

            length = len(normalize_text(text))

            if length > 0:
                pages_with_text += 1
            else:
                pages_empty += 1

            if page_index < 5:
                sample_pages.append({
                    "page_index": page_index,
                    "page_number": page_index + 1,
                    "text_length": length,
                    "preview": normalize_text(text[:360]) if text else ""
                })

        requires_ocr = total_pages > 0 and pages_with_text == 0

        return {
            "ok": True,
            "total_pages": total_pages,
            "pages_with_text": pages_with_text,
            "pages_empty": pages_empty,
            "requires_ocr": requires_ocr,
            "text_coverage_ratio": pages_with_text / float(total_pages) if total_pages else 0,
            "sample_pages": sample_pages
        }

    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "total_pages": total_pages,
            "pages_with_text": pages_with_text,
            "pages_empty": pages_empty,
            "requires_ocr": False,
            "sample_pages": sample_pages
        }

    finally:
        try:
            if doc:
                doc.close()
        except Exception:
            pass

        cleanup_temp(tmp_path)


# =====================================================
# RUTAS API
# =====================================================

@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "ok": True,
        "service": "PDF Search API",
        "engine": "Python + PyMuPDF",
        "version": "1.0.0",
        "endpoints": {
            "health": "/health",
            "analyze": "/analyze",
            "search": "/search"
        }
    })


@app.route("/health", methods=["GET"])
def health():
    if not check_api_key(request):
        return unauthorized_response()

    return jsonify({
        "ok": True,
        "service": "PDF Search API",
        "engine": "PyMuPDF",
        "version": "1.0.0"
    })


@app.route("/analyze", methods=["POST"])
def analyze():
    if not check_api_key(request):
        return unauthorized_response()

    data = request.get_json(silent=True) or {}

    pdf_base64 = data.get("pdf_base64", "")
    filename = data.get("filename", "")
    mime_type = data.get("mimeType", "")
    size_bytes = data.get("sizeBytes", 0)

    try:
        pdf_bytes = decode_pdf_base64(pdf_base64)
    except Exception as exc:
        return jsonify({
            "ok": False,
            "error": str(exc),
            "filename": filename,
            "mimeType": mime_type,
            "sizeBytes": size_bytes
        }), 400

    result = analyze_pdf_bytes(pdf_bytes)
    result["filename"] = filename
    result["mimeType"] = mime_type
    result["sizeBytes"] = size_bytes

    return jsonify(result)


@app.route("/search", methods=["POST"])
def search():
    if not check_api_key(request):
        return unauthorized_response()

    data = request.get_json(silent=True) or {}

    pdf_base64 = data.get("pdf_base64", "")
    phrase = data.get("phrase", "")
    filename = data.get("filename", "")
    mime_type = data.get("mimeType", "")
    size_bytes = data.get("sizeBytes", 0)

    all_pages = bool(data.get("all_pages", False))
    exact = bool(data.get("exact", True))
    include_text = bool(data.get("include_text", False))

    if not phrase:
        return jsonify({
            "ok": False,
            "found": False,
            "error": "Debe indicar phrase.",
            "matches": []
        }), 400

    try:
        pdf_bytes = decode_pdf_base64(pdf_base64)
    except Exception as exc:
        return jsonify({
            "ok": False,
            "found": False,
            "error": str(exc),
            "matches": [],
            "filename": filename,
            "mimeType": mime_type,
            "sizeBytes": size_bytes
        }), 400

    result = search_pdf_bytes(
        pdf_bytes=pdf_bytes,
        phrase=phrase,
        all_pages=all_pages,
        exact=exact,
        include_text=include_text
    )

    result["filename"] = filename
    result["mimeType"] = mime_type
    result["sizeBytes"] = size_bytes

    return jsonify(result)


# =====================================================
# EJECUCION LOCAL
# =====================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
