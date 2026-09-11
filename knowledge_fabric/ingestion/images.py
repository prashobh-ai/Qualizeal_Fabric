"""Images (T41): OCR text + ONE ``model_large`` description → cited passages.

    images/<doc>/<name>.json = {doc_id, name, ocr_text, caption, text_present,
                                entities, kind, diagram, chart, model, citation_url}

* OCR — Tesseract via ``pytesseract`` + Pillow (the ``documents`` extra), both
  imported lazily; an injected ``ocr(data, media_type) -> str`` callable takes
  precedence (tests, or another OCR engine). SVG text is read from the markup
  (``<text>``/``<title>``/``<desc>``) with stdlib XML; rasterising an SVG for
  the model needs ``svglib`` + ``reportlab`` (BSD) and is skipped with a note
  when they are absent.
* Description — exactly one ``platform.model.messages(body,
  purpose="image_describe", doc_id=…)`` call with the image and the OCR text,
  asking for the JSON below, validated (Pydantic when installed, the same
  stdlib check otherwise). When the model is unavailable the step is SKIPPED
  deterministically and recorded as ``"model": "skipped (extractive)"`` — the
  OCR text is kept, nothing is invented. A ``ProviderError`` propagates.

    {caption, text_present, entities: [], kind ∈ photo|screenshot|diagram|chart|table,
     diagram: {components: [], connections: []}, chart: {type, series: [], readings: []}}
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import struct
import xml.etree.ElementTree as ET

from .. import fabric_data as fd
from ..contracts.types import ConvertedDocument, Coordinate, CoordinateKind, RawItem, Region

KINDS = ("photo", "screenshot", "diagram", "chart", "table")
MODEL_MEDIA = ("image/png", "image/jpeg", "image/gif", "image/webp")
_EXT_MEDIA = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "svg": "image/svg+xml",
    "bmp": "image/bmp",
    "tif": "image/tiff",
    "tiff": "image/tiff",
}
_MAX_MODEL_BYTES = 4_500_000  # the Messages API caps an image at 5 MB
_MAX_OCR_CHARS = 4000
SKIPPED = "skipped (extractive)"

PROMPT = (
    "Describe this image for a knowledge base used by QA engineers. Reply with ONLY a "
    "JSON object, no prose, of the form: "
    '{"caption": "<one sentence>", "text_present": true|false, '
    '"entities": ["<named things: products, systems, people, metrics>"], '
    '"kind": "photo"|"screenshot"|"diagram"|"chart"|"table", '
    '"diagram": {"components": ["<box or node names>"], "connections": ["<A -> B>"]}, '
    '"chart": {"type": "<bar|line|pie|…>", "series": ["<series names>"], '
    '"readings": ["<label: value>"]}}. '
    "Leave diagram/chart lists empty when they do not apply. Report only what is "
    "visible; do not guess values that cannot be read."
)


class OcrUnavailableError(RuntimeError):
    """No OCR engine: ``pytesseract``/Pillow (the ``documents`` extra) or the
    Tesseract binary is missing."""


# ---------------------------------------------------------------------------
# media helpers (stdlib)
# ---------------------------------------------------------------------------
def media_type_for(mime: str, uri: str) -> str:
    m = (mime or "").lower().split(";")[0].strip()
    if m.startswith("image/"):
        return "image/jpeg" if m == "image/jpg" else m
    ext = uri.lower().rsplit(".", 1)[-1] if "." in uri else ""
    return _EXT_MEDIA.get(ext, m or "application/octet-stream")


def image_size(data: bytes, media_type: str) -> tuple[int, int] | None:
    """Pixel size for PNG/GIF/JPEG from the header (no Pillow)."""
    try:
        if media_type == "image/png" and data[:8] == b"\x89PNG\r\n\x1a\n":
            w, h = struct.unpack(">II", data[16:24])
            return int(w), int(h)
        if media_type == "image/gif" and data[:6] in (b"GIF87a", b"GIF89a"):
            w, h = struct.unpack("<HH", data[6:10])
            return int(w), int(h)
        if media_type == "image/jpeg" and data[:2] == b"\xff\xd8":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2):
                    h, w = struct.unpack(">HH", data[i + 5 : i + 9])
                    return int(w), int(h)
                seg = struct.unpack(">H", data[i + 2 : i + 4])[0]
                i += 2 + seg
    except (struct.error, IndexError):
        return None
    return None


def svg_text(data: bytes) -> str:
    """Text carried by an SVG's markup — ``<text>``, ``<tspan>``, ``<title>``,
    ``<desc>`` — in document order."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return ""
    out: list[str] = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1].lower()
        if tag in ("text", "title", "desc"):
            t = " ".join("".join(el.itertext()).split())
            if t and t not in out:
                out.append(t)
    return "\n".join(out)


def rasterise_svg(data: bytes) -> bytes | None:
    """SVG → PNG bytes via ``svglib`` + ``reportlab`` when installed; else None."""
    try:
        from reportlab.graphics import renderPM
        from svglib.svglib import svg2rlg
    except ImportError:
        return None
    try:
        drawing = svg2rlg(io.BytesIO(data))
        if drawing is None:
            return None
        return renderPM.drawToString(drawing, fmt="PNG")
    except Exception:  # a malformed SVG must not abort ingestion of the record
        return None


def default_ocr(data: bytes, media_type: str) -> str:
    """Tesseract OCR through ``pytesseract`` + Pillow. Raises
    ``OcrUnavailableError`` when either package or the binary is missing."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError as e:
        raise OcrUnavailableError(
            "OCR needs pytesseract + Pillow: install the documents extra "
            "(pip install 'qualizeal-knowledge-fabric[documents]') and the tesseract binary"
        ) from e
    try:
        img = Image.open(io.BytesIO(data))
        return pytesseract.image_to_string(img)
    except pytesseract.TesseractNotFoundError as e:
        raise OcrUnavailableError("tesseract binary not found on PATH") from e


def fit_for_model(data: bytes, media_type: str) -> tuple[bytes, str] | None:
    """The bytes the Messages API will accept: a supported media type under the
    size cap. Oversize images are downscaled with Pillow when available."""
    if media_type in MODEL_MEDIA and len(data) <= _MAX_MODEL_BYTES:
        return data, media_type
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        img = Image.open(io.BytesIO(data))
        img.thumbnail((1568, 1568))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=85)
        out = buf.getvalue()
    except Exception:
        return None
    return (out, "image/jpeg") if len(out) <= _MAX_MODEL_BYTES else None


# ---------------------------------------------------------------------------
# description schema
# ---------------------------------------------------------------------------
def _strs(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    out = []
    for v in value if isinstance(value, (list, tuple)) else [value]:
        if isinstance(v, dict):
            a, b = v.get("from") or v.get("source"), v.get("to") or v.get("target")
            s = f"{a} -> {b}" if a and b else v.get("label") or v.get("name") or json.dumps(v)
        else:
            s = str(v)
        s = s.strip()
        if s:
            out.append(s)
    return out


def normalise_description(obj) -> dict:
    """The canonical description dict (stdlib). Raises ``ValueError`` when the
    object is not a description at all."""
    if not isinstance(obj, dict):
        raise ValueError("description must be a JSON object")
    kind = str(obj.get("kind", "") or "").strip().lower()
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)} (got {kind!r})")
    caption = obj.get("caption", "")
    if not isinstance(caption, str):
        raise ValueError("caption must be a string")
    diagram = obj.get("diagram") or {}
    chart = obj.get("chart") or {}
    if not isinstance(diagram, dict) or not isinstance(chart, dict):
        raise ValueError("diagram and chart must be objects")
    return {
        "caption": " ".join(caption.split()),
        "text_present": bool(obj.get("text_present", False)),
        "entities": _strs(obj.get("entities")),
        "kind": kind,
        "diagram": {
            "components": _strs(diagram.get("components")),
            "connections": _strs(diagram.get("connections")),
        },
        "chart": {
            "type": str(chart.get("type", "") or "").strip(),
            "series": _strs(chart.get("series")),
            "readings": _strs(chart.get("readings")),
        },
    }


def validate_description(obj) -> dict:
    """Normalise, then validate with Pydantic when it is installed (the declared
    runtime validator); the stdlib check above is the same contract."""
    desc = normalise_description(obj)
    try:
        from typing import Literal

        from pydantic import BaseModel
    except ImportError:
        return desc

    class Diagram(BaseModel):
        components: list[str]
        connections: list[str]

    class Chart(BaseModel):
        type: str
        series: list[str]
        readings: list[str]

    class ImageDescription(BaseModel):
        caption: str
        text_present: bool
        entities: list[str]
        kind: Literal["photo", "screenshot", "diagram", "chart", "table"]
        diagram: Diagram
        chart: Chart

    return ImageDescription.model_validate(desc).model_dump()


def parse_model_json(text: str) -> dict | None:
    """The first JSON object in a model reply (tolerates code fences / prose)."""
    text = (text or "").strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def model_ready(model) -> bool:
    try:
        return bool(model is not None and model.available() and hasattr(model, "messages"))
    except Exception:
        return False


def describe_with_model(
    model, data: bytes, media_type: str, ocr_text: str, doc_id: str
) -> tuple[dict | None, str]:
    """ONE ledgered ``image_describe`` call. Returns ``(description, model_label)``:
    ``(None, "skipped (extractive)")`` when no model, ``(None, "<id> (invalid
    JSON; extractive)")`` when the reply did not validate. Provider failures
    raise ``ProviderError`` — loud, never swallowed."""
    from ..adapters.model import resolve_models

    if not model_ready(model):
        return None, SKIPPED
    fit = fit_for_model(data, media_type)
    if fit is None:
        return None, f"{SKIPPED}: image not sendable ({media_type}, {len(data)} bytes)"
    payload, sent_type = fit
    _small, large = resolve_models()
    text = PROMPT
    if ocr_text.strip():
        text += "\n\nOCR text already extracted from the image:\n" + ocr_text[:_MAX_OCR_CHARS]
    body = {
        "model": large,
        "max_tokens": 1024,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": sent_type,
                            "data": base64.b64encode(payload).decode("ascii"),
                        },
                    },
                    {"type": "text", "text": text},
                ],
            }
        ],
    }
    data_ = model.messages(body, purpose="image_describe", doc_id=doc_id)
    reply = "".join(b.get("text", "") for b in data_.get("content", []) if b.get("type") == "text")
    used = data_.get("model") or large
    obj = parse_model_json(reply)
    if obj is None:
        return None, f"{used} (invalid JSON; extractive)"
    try:
        return validate_description(obj), used
    except (ValueError, TypeError) as e:
        return None, f"{used} (invalid description: {str(e)[:80]}; extractive)"


# ---------------------------------------------------------------------------
# the conversion
# ---------------------------------------------------------------------------
def _name_of(uri: str) -> str:
    base = re.sub(r"^[a-z]+://", "", uri or "").rstrip("/").rsplit("/", 1)[-1]
    stem = base.rsplit(".", 1)[0] if "." in base else base
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("._") or "image"


def description_text(desc: dict) -> str:
    parts = [f"Kind: {desc['kind']}."]
    if desc["entities"]:
        parts.append("Entities: " + ", ".join(desc["entities"]) + ".")
    d, c = desc["diagram"], desc["chart"]
    if d["components"]:
        parts.append("Components: " + ", ".join(d["components"]) + ".")
    if d["connections"]:
        parts.append("Connections: " + "; ".join(d["connections"]) + ".")
    if c["type"] or c["series"] or c["readings"]:
        head = f"Chart type: {c['type']}." if c["type"] else "Chart."
        parts.append(head)
        if c["series"]:
            parts.append("Series: " + ", ".join(c["series"]) + ".")
        if c["readings"]:
            parts.append("Readings: " + "; ".join(c["readings"]) + ".")
    parts.append("Text present." if desc["text_present"] else "No text visible.")
    return " ".join(parts)


def convert_image(
    raw: RawItem,
    *,
    doc_id: str,
    model=None,
    ocr=None,
    persist: bool = True,
) -> ConvertedDocument:
    """Image bytes → passages (OCR text, caption, structured description) and the
    ``images/<doc>/<name>.json`` record. ``ocr(data, media_type) -> str`` may be
    injected; otherwise Tesseract is used when installed."""
    data = raw.bytes_
    media_type = media_type_for(raw.mime, raw.uri)
    name = _name_of(raw.uri)
    citation_url = raw.meta.get("citation_url") or raw.uri
    notes: list[str] = []

    # -- what the OCR/model actually see (SVG needs a rasteriser) ------------
    ocr_input, ocr_type = data, media_type
    ocr_text = ""
    if media_type == "image/svg+xml":
        ocr_text = svg_text(data)
        png = rasterise_svg(data)
        if png is not None:
            ocr_input, ocr_type = png, "image/png"
            notes.append("svg rasterised (svglib)")
        else:
            notes.append("svg not rasterised (text read from markup; install svglib+reportlab)")
    size = image_size(ocr_input, ocr_type)

    # -- OCR ----------------------------------------------------------------
    ocr_note = "markup" if media_type == "image/svg+xml" else ""
    if ocr_type != "image/svg+xml":
        engine = ocr or default_ocr
        try:
            extracted = engine(ocr_input, ocr_type) or ""
            ocr_text = (ocr_text + "\n" + extracted).strip() if ocr_text else extracted.strip()
            ocr_note = "injected" if ocr is not None else "tesseract"
        except OcrUnavailableError as e:
            ocr_note = f"skipped: {e}"
    elif not ocr_text:
        ocr_note = "skipped: svg without text elements"

    # -- ONE model call (or a deterministic skip) ---------------------------
    desc, model_label = describe_with_model(model, ocr_input, ocr_type, ocr_text, doc_id)

    record = {
        "doc_id": doc_id,
        "name": name,
        "media_type": media_type,
        "width": size[0] if size else None,
        "height": size[1] if size else None,
        "ocr_text": ocr_text,
        "ocr": ocr_note,
        "caption": desc["caption"] if desc else "",
        "text_present": desc["text_present"] if desc else bool(ocr_text.strip()),
        "entities": desc["entities"] if desc else [],
        "kind": desc["kind"] if desc else "",
        "diagram": desc["diagram"] if desc else {"components": [], "connections": []},
        "chart": desc["chart"] if desc else {"type": "", "series": [], "readings": []},
        "model": model_label,
        "citation_url": citation_url,
        "notes": notes,
    }
    if persist:
        fd.write_json(fd.path("images", doc_id, f"{name}.json", mkdir=True), record)

    # -- passages -----------------------------------------------------------
    bbox = [0, 0, size[0], size[1]] if size else [0, 0, 0, 0]

    def region(text: str, part: str) -> Region:
        return Region(
            text=text,
            coordinate=Coordinate(
                CoordinateKind.BBOX,
                {"page": 1, "bbox": bbox, "part": part, "image": name},
            ),
        )

    regions: list[Region] = []
    if desc and desc["caption"]:
        regions.append(region(f"Image {name}: {desc['caption']}", "caption"))
    for block in re.split(r"\n\s*\n", ocr_text.strip()):
        block = block.strip()
        if block:
            regions.append(region(block, "ocr"))
    if desc:
        regions.append(region(description_text(desc), "description"))
    if not regions:
        dims = f"{size[0]}×{size[1]}" if size else "unknown size"
        regions.append(
            region(
                f"Image {name} ({media_type}, {dims}): no text extracted (OCR {ocr_note}); "
                f"description {model_label}.",
                "record",
            )
        )
    return ConvertedDocument(language=raw.language, regions=regions, media_refs=[citation_url])


def read_record(doc_id: str, name: str) -> dict | None:
    return fd.read_json(fd.path("images", doc_id, f"{name}.json"), None)


def _env_flag(name: str) -> bool:
    return (os.environ.get(name) or "").lower() in ("1", "true", "yes")
