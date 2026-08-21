app.py
Pulse el botón para copiar únicamente el código fuente, sin encabezados ni texto adicional.

Copiar todo el código
Código copiado correctamente
import base64
import os
import re
import tempfile
from typing import Any, Dict, List

import fitz
from flask import Flask, jsonify, request


app = Flask(__name__)

API_KEY = os.environ.get("PDF_API_KEY", "CAMBIAR_POR_UNA_CLAVE_SEGURA")
MAX_PDF_BYTES = int(os.environ.get("MAX_PDF_BYTES", str(45 * 1024 * 1024)))


def check_api_key(req) -> bool:
    incoming = req.headers.get("X-API-Key", "")
    return bool(API_KEY) and incoming == API_KEY


def unauthorized_response():
    return jsonify({"ok": False, "error": "API key invalida o ausente."}), 401


def normalize_text(value: Any) -> str:
    if value is None:
        return ""

    value = str(value)
    value = value.replace("\u00a0", " ")
    value = value.replace("\x00", " ")
    value = value.replace("\r", "\n")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)

    return value.strip()


def normalize_for_search(value: Any) -> str:
    value = normalize_text(value).lower()

    replacements = {
        "á": "a", "à": "a", "ä": "a", "â": "a",
        "é": "e", "è": "e", "ë": "e", "ê": "e",
        "í": "i", "ì": "i", "ï": "i", "î": "i",
        "ó": "o", "ò": "o", "ö": "o", "ô": "o",
        "ú": "u", "ù": "u", "ü": "u", "û": "u",
        "ñ": "n", "—": "-", "–": "-"
    }

    for src, dst in replacements.items():
        value = value.replace(src, dst)

    value = re.sub(r"\s+", " ", value)
    return value.strip()


def normalize_words(value: Any) -> List[str]:
    text = normalize_for_search(value)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return [p.strip() for p in text.split() if p.strip()]


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
            f"Tamano: {len(pdf_bytes)} bytes. "
            f"Maximo: {MAX_PDF_BYTES} bytes."
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


def get_page_text(page) -> str:
    try:
        return normalize_text(page.get_text("text") or "")
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

    hits = sum(1 for word in important_words if word in page_norm)
    ratio = hits / float(len(important_words))
    return ratio >= 0.72


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


def article_header_regex(article_number: str):
    article_number = str(article_number or "").strip()
    return re.compile(
        r"(^|[\n\r\s])art[íi]culo\s+" + re.escape(article_number) + r"\s*(\.|-|:|—|–)?",
        re.IGNORECASE,
    )


def any_article_header_regex():
    return re.compile(r"(^|[\n\r\s])art[íi]culo\s+\d+\s*(\.|-|:|—|–)?", re.IGNORECASE)


def find_article_matches_in_pdf_bytes(pdf_bytes: bytes, article_number: str, include_text: bool = True) -> Dict[str, Any]:
    article_number = re.sub(r"[^0-9]", "", str(article_number or ""))
    if not article_number:
        return {"ok": False, "found": False, "error": "Debe indicar numero de articulo.", "matches": []}

    doc = None
    tmp_path = ""
    matches: List[Dict[str, Any]] = []
    total_pages = 0
    pages_with_text = 0

    try:
        doc, tmp_path = open_pdf_from_bytes(pdf_bytes)
        total_pages = doc.page_count
        target_re = article_header_regex(article_number)
        next_re = any_article_header_regex()

        for page_index in range(total_pages):
            page = doc.load_page(page_index)
            text = get_page_text(page)
            if not text:
                text = get_page_blocks_text(page)
            if normalize_text(text):
                pages_with_text += 1

            target_match = target_re.search(text or "")
            if not target_match:
                continue

            start = target_match.start()
            after = text[target_match.end():]
            next_match = next_re.search(after)
            end = target_match.end() + next_match.start() if next_match else len(text)
            article_text = normalize_text(text[start:end])

            if not next_match and page_index + 1 < total_pages:
                next_page = doc.load_page(page_index + 1)
                next_text = get_page_text(next_page) or get_page_blocks_text(next_page)
                next_header = next_re.search(next_text or "")
                extension = next_text[:next_header.start()] if next_header else next_text[:1600]
                if extension:
                    article_text = normalize_text(article_text + "\n" + extension)

            snippet = article_text[:520].strip()
            if len(article_text) > 520:
                snippet += "..."

            match_data = {
                "page_index": page_index,
                "page_number": page_index + 1,
                "article": article_number,
                "header": target_match.group(0).strip(),
                "snippet": snippet,
                "article_text": article_text,
                "text_length": len(text or ""),
                "match_type": "article_header_exact",
            }
            if include_text:
                match_data["page_text"] = text
            matches.append(match_data)
            break

        return {
            "ok": True,
            "found": len(matches) > 0,
            "message": "Articulo encontrado" if matches else "Articulo no encontrado",
            "article": article_number,
            "total_pages": total_pages,
            "pages_with_text": pages_with_text,
            "requires_ocr": total_pages > 0 and pages_with_text == 0,
            "matches": matches,
        }
    except Exception as exc:
        return {
            "ok": False,
            "found": False,
            "error": str(exc),
            "article": article_number,
            "total_pages": total_pages,
            "pages_with_text": pages_with_text,
            "requires_ocr": False,
            "matches": [],
        }
    finally:
        try:
            if doc:
                doc.close()
        except Exception:
            pass
        cleanup_temp(tmp_path)


def search_pdf_bytes(pdf_bytes: bytes, phrase: str, all_pages: bool = False, exact: bool = True, include_text: bool = False) -> Dict[str, Any]:
    phrase = normalize_text(phrase)
    if not phrase:
        return {"ok": False, "found": False, "error": "Debe indicar una frase a buscar.", "matches": []}

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
            found = exact_match(text, phrase) if exact else flexible_match(text, phrase)
            if found:
                match_data = {
                    "page_index": page_index,
                    "page_number": page_index + 1,
                    "snippet": extract_snippet(text, phrase),
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
# FUNCIONES MODULARES PARA INGESTA GENERAL
# =====================================================

def modular_detect_page_title(text: str) -> str:
    text = text or ""
    lines = []
    for line in text.split("\n"):
        clean = re.sub(r"\s+", " ", line).strip()
        if not clean or len(clean) < 4 or len(clean) > 140:
            continue
        if re.match(r"^\d+$", clean):
            continue
        lines.append(clean)
        if len(lines) >= 12:
            break
    return lines[0] if lines else "Pagina sin titulo detectado"


def modular_classify_fragment(text: str, title: str) -> str:
    combined = normalize_for_search((title or "") + " " + (text or ""))
    if any(term in combined for term in ["requisito", "requisitos", "debe contener", "debera contener"]):
        return "REQUISITOS"
    if any(term in combined for term in ["donde", "presentar", "sala constitucional", "gestion en linea", "fax"]):
        return "PRESENTACION"
    if any(term in combined for term in ["que es", "recurso de amparo", "amparo es"]):
        return "DEFINICION"
    if any(term in combined for term in ["hechos", "petitoria", "notificaciones", "recurrente", "recurrido"]):
        return "ESTRUCTURA_MACHOTE"
    return "CONTENIDO"


def modular_extract_keywords(text: str, title: str, documento_destino: str) -> str:
    combined = normalize_for_search((title or "") + " " + (text or ""))
    keywords = []
    candidates = [
        ("recurso de amparo", "recurso de amparo"),
        ("sala constitucional", "sala constitucional"),
        ("derechos fundamentales", "derechos fundamentales"),
        ("hechos", "hechos"),
        ("petitoria", "petitoria"),
        ("notificaciones", "notificaciones"),
        ("recurrente", "recurrente"),
        ("recurrido", "recurrido"),
        ("prueba", "prueba"),
        ("gestion en linea", "gestion en linea"),
        ("fax", "fax"),
        ("autoridad", "autoridad"),
        ("omision", "omision"),
        ("acto", "acto"),
        ("lesion", "lesion")
    ]
    for pattern, label in candidates:
        if pattern in combined and label not in keywords:
            keywords.append(label)
    if documento_destino and documento_destino not in keywords:
        keywords.append(str(documento_destino))
    return ";".join(keywords[:12])


def modular_split_page_text(text: str, size: int = 1800, overlap: int = 250) -> List[str]:
    text = normalize_text(text)
    if not text:
        return []
    try:
        size = int(size or 1800)
    except Exception:
        size = 1800
    try:
        overlap = int(overlap or 250)
    except Exception:
        overlap = 250
    if len(text) <= size:
        return [text]
    fragments = []
    start = 0
    total = len(text)
    while start < total:
        end = min(start + size, total)
        cut = end
        if end < total:
            possible_cuts = [
                text.rfind("\n\n", start, end),
                text.rfind(". ", start, end),
                text.rfind("\n", start, end)
            ]
            possible_cuts = [p for p in possible_cuts if p and p > start + int(size * 0.45)]
            if possible_cuts:
                cut = max(possible_cuts) + 1
        fragment = text[start:cut].strip()
        if fragment:
            fragments.append(fragment)
        if cut >= total:
            break
        start = max(0, cut - overlap)
    return fragments


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "ok": True,
        "service": "PDF Search API",
        "engine": "Python + PyMuPDF",
        "version": "1.2.0",
        "endpoints": {
            "health": "/health",
            "analyze": "/analyze",
            "search": "/search",
            "article": "/article",
            "ingestar_pdf_modular": "/ingestar_pdf_modular",
            "ingestar_pdf_modular_lote": "/ingestar_pdf_modular_lote"
        }
    })


@app.route("/health", methods=["GET"])
def health():
    if not check_api_key(request):
        return unauthorized_response()
    return jsonify({"ok": True, "service": "PDF Search API", "engine": "PyMuPDF", "version": "1.2.0"})


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
        return jsonify({"ok": False, "error": str(exc), "filename": filename, "mimeType": mime_type, "sizeBytes": size_bytes}), 400
    result = analyze_pdf_bytes(pdf_bytes)
    result["filename"] = filename
    result["mimeType"] = mime_type
    result["sizeBytes"] = size_bytes
    return jsonify(result)


@app.route("/article", methods=["POST"])
def article():
    if not check_api_key(request):
        return unauthorized_response()
    data = request.get_json(silent=True) or {}
    try:
        pdf_bytes = decode_pdf_base64(data.get("pdf_base64", ""))
        article_number = str(data.get("article_number") or data.get("articulo") or data.get("article") or "").strip()
        include_text = bool(data.get("include_text", True))
        result = find_article_matches_in_pdf_bytes(pdf_bytes, article_number, include_text=include_text)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"ok": False, "found": False, "error": str(exc), "matches": []}), 400


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
        return jsonify({"ok": False, "found": False, "error": "Debe indicar phrase.", "matches": []}), 400
    try:
        pdf_bytes = decode_pdf_base64(pdf_base64)
    except Exception as exc:
        return jsonify({"ok": False, "found": False, "error": str(exc), "matches": [], "filename": filename, "mimeType": mime_type, "sizeBytes": size_bytes}), 400
    result = search_pdf_bytes(pdf_bytes=pdf_bytes, phrase=phrase, all_pages=all_pages, exact=exact, include_text=include_text)
    result["filename"] = filename
    result["mimeType"] = mime_type
    result["sizeBytes"] = size_bytes
    return jsonify(result)




def modular_process_page_range(
    doc,
    data: Dict[str, Any],
    page_start: int,
    page_end: int,
    fragment_size: int,
    fragment_overlap: int,
) -> Dict[str, Any]:
    total_pages = int(doc.page_count or 0)

    if total_pages <= 0:
        return {
            "fragmentos": [],
            "totalCaracteresTextoLote": 0,
            "paginaInicioProcesada": 0,
            "paginaFinProcesada": 0,
        }

    page_start = max(1, int(page_start or 1))
    page_end = min(total_pages, int(page_end or page_start))

    if page_start > total_pages:
        raise ValueError(
            f"paginaInicio ({page_start}) supera el total de paginas ({total_pages})."
        )

    if page_end < page_start:
        raise ValueError(
            f"Rango de paginas invalido: {page_start}-{page_end}."
        )

    id_documento = str(
        data.get("idDocumento") or data.get("id_documento") or ""
    ).strip()

    fragmentos: List[Dict[str, Any]] = []
    total_text_chars = 0

    for page_index in range(page_start - 1, page_end):
        page = doc.load_page(page_index)
        page_number = page_index + 1
        page_text = get_page_text(page)

        if not page_text:
            page_text = get_page_blocks_text(page)

        page_text = normalize_text(page_text)
        total_text_chars += len(page_text)

        if not page_text:
            continue

        title = modular_detect_page_title(page_text)
        page_fragments = modular_split_page_text(
            page_text,
            size=fragment_size,
            overlap=fragment_overlap,
        )

        for fragment_index, fragment_text in enumerate(
            page_fragments,
            start=1,
        ):
            fragment_type = modular_classify_fragment(
                fragment_text,
                title,
            )
            fragment_id = (
                f"PYMUPDF-{id_documento}-P{page_number}-F{fragment_index}"
            )
            keywords = modular_extract_keywords(
                fragment_text,
                title,
                data.get("documentoDestino") or "",
            )

            fragmentos.append({
                "idFragmento": fragment_id,
                "idDocumento": id_documento,
                "moduloPrincipal": data.get("moduloPrincipal") or "",
                "modulosRelacionados": data.get("modulosRelacionados") or "",
                "documentoDestino": data.get("documentoDestino") or "",
                "tipoFuente": data.get("tipoFuente") or "",
                "pagina": page_number,
                "tituloDetectado": title,
                "subtituloDetectado": "",
                "tipoFragmento": fragment_type,
                "textoFragmento": fragment_text,
                "textoNormalizado": normalize_for_search(fragment_text),
                "palabrasClave": keywords,
                "usoIA": data.get("tipoUsoIA") or "BUSQUEDA;CONTEXTO_GEMINI",
                "prioridad": data.get("prioridad") or "",
                "fuenteVisible": (
                    data.get("nombreFuente")
                    or data.get("filename")
                    or "Documento PDF"
                ),
                "estado": "ACTIVO",
                "motorExtraccion": "PYTHON_PYMUPDF_LOTE",
                "observaciones": (
                    "Fragmento extraido por /ingestar_pdf_modular_lote."
                ),
            })

    return {
        "fragmentos": fragmentos,
        "totalCaracteresTextoLote": total_text_chars,
        "paginaInicioProcesada": page_start,
        "paginaFinProcesada": page_end,
    }


@app.route("/ingestar_pdf_modular_lote", methods=["POST"])
def ingestar_pdf_modular_lote():
    if not check_api_key(request):
        return unauthorized_response()

    data = request.get_json(silent=True) or {}
    pdf_base64 = data.get("pdf_base64") or data.get("pdfBase64") or ""

    if not pdf_base64:
        return jsonify({
            "ok": False,
            "error": "PDF_BASE64_REQUERIDO",
            "mensaje": "Debe enviar pdf_base64 en el cuerpo JSON.",
        }), 400

    id_documento = str(
        data.get("idDocumento") or data.get("id_documento") or ""
    ).strip()

    if not id_documento:
        return jsonify({
            "ok": False,
            "error": "ID_DOCUMENTO_REQUERIDO",
            "mensaje": "Debe enviar idDocumento.",
        }), 400

    try:
        pagina_inicio = int(
            data.get("paginaInicio") or data.get("pagina_inicio") or 1
        )
        pagina_fin = int(
            data.get("paginaFin") or data.get("pagina_fin") or pagina_inicio
        )
    except Exception:
        return jsonify({
            "ok": False,
            "error": "RANGO_PAGINAS_INVALIDO",
            "mensaje": "paginaInicio y paginaFin deben ser numeros enteros.",
        }), 400

    try:
        pdf_bytes = decode_pdf_base64(pdf_base64)
    except Exception as exc:
        return jsonify({
            "ok": False,
            "error": "PDF_INVALIDO",
            "mensaje": str(exc),
        }), 400

    instrucciones = data.get("instruccionesExtraccion") or {}

    try:
        fragment_size = int(
            instrucciones.get("tamanoFragmentoCaracteres") or 1800
        )
    except Exception:
        fragment_size = 1800

    try:
        fragment_overlap = int(
            instrucciones.get("solapamientoCaracteres") or 250
        )
    except Exception:
        fragment_overlap = 250

    doc = None
    tmp_path = ""

    try:
        doc, tmp_path = open_pdf_from_bytes(pdf_bytes)
        total_pages = int(doc.page_count or 0)
        processed = modular_process_page_range(
            doc=doc,
            data=data,
            page_start=pagina_inicio,
            page_end=pagina_fin,
            fragment_size=fragment_size,
            fragment_overlap=fragment_overlap,
        )

        pagina_fin_procesada = processed["paginaFinProcesada"]
        hay_mas_paginas = pagina_fin_procesada < total_pages
        siguiente_pagina = (
            pagina_fin_procesada + 1 if hay_mas_paginas else None
        )

        return jsonify({
            "ok": True,
            "modoProcesamiento": "LOTE_PAGINAS",
            "idDocumento": id_documento,
            "moduloPrincipal": data.get("moduloPrincipal") or "",
            "modulosRelacionados": data.get("modulosRelacionados") or "",
            "tipoFuente": data.get("tipoFuente") or "",
            "documentoDestino": data.get("documentoDestino") or "",
            "nombreFuente": data.get("nombreFuente") or data.get("filename") or "",
            "driveId": data.get("driveId") or "",
            "urlDrive": data.get("urlDrive") or "",
            "prioridad": data.get("prioridad") or "",
            "tipoUsoIA": data.get("tipoUsoIA") or "BUSQUEDA;CONTEXTO_GEMINI",
            "motorExtraccion": "PYTHON_PYMUPDF_LOTE",
            "totalPaginas": total_pages,
            "paginaInicio": processed["paginaInicioProcesada"],
            "paginaFin": pagina_fin_procesada,
            "paginasProcesadasLote": (
                pagina_fin_procesada
                - processed["paginaInicioProcesada"]
                + 1
            ),
            "totalFragmentos": len(processed["fragmentos"]),
            "totalCaracteresTexto": processed["totalCaracteresTextoLote"],
            "hayMasPaginas": hay_mas_paginas,
            "siguientePagina": siguiente_pagina,
            "loteCompletado": True,
            "documentoCompletado": not hay_mas_paginas,
            "fragmentos": processed["fragmentos"],
        }), 200

    except Exception as exc:
        return jsonify({
            "ok": False,
            "error": "ERROR_INGESTA_PDF_MODULAR_LOTE",
            "mensaje": str(exc),
            "idDocumento": id_documento,
            "paginaInicio": pagina_inicio,
            "paginaFin": pagina_fin,
        }), 500

    finally:
        try:
            if doc:
                doc.close()
        except Exception:
            pass
        cleanup_temp(tmp_path)


@app.route("/ingestar_pdf_modular", methods=["POST"])
def ingestar_pdf_modular():
    if not check_api_key(request):
        return unauthorized_response()
    data = request.get_json(silent=True) or {}
    pdf_base64 = data.get("pdf_base64") or data.get("pdfBase64") or ""
    if not pdf_base64:
        return jsonify({"ok": False, "error": "PDF_BASE64_REQUERIDO", "mensaje": "Debe enviar pdf_base64 en el cuerpo JSON."}), 400
    id_documento = str(data.get("idDocumento") or data.get("id_documento") or "").strip()
    if not id_documento:
        return jsonify({"ok": False, "error": "ID_DOCUMENTO_REQUERIDO", "mensaje": "Debe enviar idDocumento."}), 400
    try:
        pdf_bytes = decode_pdf_base64(pdf_base64)
    except Exception as exc:
        return jsonify({"ok": False, "error": "PDF_INVALIDO", "mensaje": str(exc)}), 400

    instrucciones = data.get("instruccionesExtraccion") or {}
    try:
        fragment_size = int(instrucciones.get("tamanoFragmentoCaracteres") or 1800)
    except Exception:
        fragment_size = 1800
    try:
        fragment_overlap = int(instrucciones.get("solapamientoCaracteres") or 250)
    except Exception:
        fragment_overlap = 250

    doc = None
    tmp_path = ""
    try:
        doc, tmp_path = open_pdf_from_bytes(pdf_bytes)
        fragmentos = []
        total_text_chars = 0
        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            page_number = page_index + 1
            page_text = get_page_text(page)
            if not page_text:
                page_text = get_page_blocks_text(page)
            page_text = normalize_text(page_text)
            total_text_chars += len(page_text)
            if not page_text:
                continue
            title = modular_detect_page_title(page_text)
            page_fragments = modular_split_page_text(page_text, size=fragment_size, overlap=fragment_overlap)
            for fragment_index, fragment_text in enumerate(page_fragments, start=1):
                fragment_type = modular_classify_fragment(fragment_text, title)
                fragment_id = f"PYMUPDF-{id_documento}-P{page_number}-F{fragment_index}"
                keywords = modular_extract_keywords(fragment_text, title, data.get("documentoDestino") or "")
                fragmentos.append({
                    "idFragmento": fragment_id,
                    "idDocumento": id_documento,
                    "moduloPrincipal": data.get("moduloPrincipal") or "",
                    "modulosRelacionados": data.get("modulosRelacionados") or "",
                    "documentoDestino": data.get("documentoDestino") or "",
                    "tipoFuente": data.get("tipoFuente") or "",
                    "pagina": page_number,
                    "tituloDetectado": title,
                    "subtituloDetectado": "",
                    "tipoFragmento": fragment_type,
                    "textoFragmento": fragment_text,
                    "textoNormalizado": normalize_for_search(fragment_text),
                    "palabrasClave": keywords,
                    "usoIA": data.get("tipoUsoIA") or "BUSQUEDA;CONTEXTO_GEMINI",
                    "prioridad": data.get("prioridad") or "",
                    "fuenteVisible": data.get("nombreFuente") or data.get("filename") or "Documento PDF",
                    "estado": "ACTIVO",
                    "motorExtraccion": "PYTHON_PYMUPDF",
                    "observaciones": "Fragmento extraido por /ingestar_pdf_modular."
                })
        return jsonify({
            "ok": True,
            "idDocumento": id_documento,
            "moduloPrincipal": data.get("moduloPrincipal") or "",
            "modulosRelacionados": data.get("modulosRelacionados") or "",
            "tipoFuente": data.get("tipoFuente") or "",
            "documentoDestino": data.get("documentoDestino") or "",
            "nombreFuente": data.get("nombreFuente") or data.get("filename") or "",
            "driveId": data.get("driveId") or "",
            "urlDrive": data.get("urlDrive") or "",
            "prioridad": data.get("prioridad") or "",
            "tipoUsoIA": data.get("tipoUsoIA") or "BUSQUEDA;CONTEXTO_GEMINI",
            "motorExtraccion": "PYTHON_PYMUPDF",
            "totalPaginas": doc.page_count,
            "totalFragmentos": len(fragmentos),
            "totalCaracteresTexto": total_text_chars,
            "fragmentos": fragmentos
        }), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": "ERROR_INGESTA_PDF_MODULAR", "mensaje": str(exc)}), 500
    finally:
        try:
            if doc:
                doc.close()
        except Exception:
            pass
        cleanup_temp(tmp_path)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
