"""Pre-flight media handling: spot content parts the selected model may not
be able to read, and normalize them so as many upstreams as possible can.

The gateway uses this for media routing: a request that carries images /
audio / video / documents is checked against the selected model's
capabilities; when the model can't read that media, the request is served
by a capable stand-in model instead — with the requested id spoofed back,
so the agent never sees an error or a model switch.

Recognised part shapes (scanned generically at any nesting depth, so chat /
messages / responses payloads all work without special cases):
  images:    {"type": "image_url"|"input_image"|"image", ...}     -> vision
  video:     {"type": "video_url"|"input_video", ...}            -> vision
  audio:     {"type": "input_audio"|"audio", ...}                -> audio_in
  documents: anthropic {"type": "document", "source": {...}}
             plain-text source -> inlined as a text block (any model reads it)
             pdf / url source  -> needs a vision-capable reader
  files:     {"type": "file", "file": {"filename", "file_data"}}
             plain-text file -> inlined as a text part (any model reads it)
             image file      -> image_url data URI
             pdf / binary    -> kept as a file part, needs a vision-capable reader

detect() must run after prepare(): inlined text no longer counts as media.
"""

import base64
import re
from typing import Any, Dict, List, Optional, Set, Tuple

VISION = "vision"
AUDIO_IN = "audio_in"

# part "type" values that carry media, by the capability needed to read them
_IMAGE_TYPES = frozenset({"image_url", "input_image", "image"})
_AUDIO_TYPES = frozenset({"input_audio", "audio"})
_VIDEO_TYPES = frozenset({"video_url", "input_video", "video"})

# mime types that can be inlined as plain text for any model to read
_TEXT_MIMES = frozenset({
    "text/plain", "text/markdown", "text/csv", "text/html", "text/xml",
    "application/json", "application/xml", "application/yaml",
    "application/x-yaml", "application/javascript", "application/toml",
    "application/sql", "application/x-sh",
})
_TEXT_EXT = re.compile(
    r"\.(txt|md|markdown|csv|tsv|json|jsonl|xml|yaml|yml|html?|py|js|mjs|ts|tsx|jsx|"
    r"c|h|cpp|hpp|java|rb|go|rs|php|sql|sh|bash|zsh|toml|ini|cfg|conf|log)$",
    re.I,
)

# binary extensions we can name a mime for when the part carries raw base64
_GUESS_MIME = {
    ".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
}

# never inline more than this many characters of a text document
MAX_INLINE_CHARS = 200_000


def covers(caps: Dict[str, bool], needs: Set[str]) -> bool:
    """True when a model capability map covers every needed capability."""
    return all(bool(caps.get(k)) for k in needs)


# ---------------------------------------------------------------------------
# detection
# ---------------------------------------------------------------------------

def detect(payload: Dict[str, Any]) -> Set[str]:
    """Capabilities a model must have to read this payload's media parts.

    Returns a subset of {vision, audio_in}. Runs after prepare(), so
    plain-text files/documents are already inlined and don't count.
    """
    needs: Set[str] = set()
    _scan(payload, needs)
    return needs


def _scan(node: Any, needs: Set[str]) -> None:
    if isinstance(node, list):
        for item in node:
            _scan(item, needs)
        return
    if not isinstance(node, dict):
        return
    t = node.get("type")
    if t in _IMAGE_TYPES or t in _VIDEO_TYPES:
        if _carries_media(node, t):
            needs.add(VISION)
    elif t in _AUDIO_TYPES:
        if _carries_media(node, t):
            needs.add(AUDIO_IN)
    elif t == "document":
        src = node.get("source")
        # plain-text documents were inlined by prepare(); pdf / url documents
        # need a multimodal reader
        if isinstance(src, dict) and src.get("type") in ("base64", "url"):
            needs.add(VISION)
    elif t == "file":
        f = node.get("file")
        if isinstance(f, dict) and f.get("file_data"):
            needs.add(VISION)  # binary media (text ones are inlined by prepare)
    for v in node.values():
        if isinstance(v, (dict, list)):
            _scan(v, needs)


def _carries_media(node: Dict[str, Any], t: str) -> bool:
    """True when the part actually carries media bytes / a url, not just a shape."""
    if t in ("image_url", "input_image"):
        holder = node.get("image_url")
        url = (holder.get("url") if isinstance(holder, dict) else "") or node.get("url") or ""
        return bool(url)
    if t in ("video_url", "input_video", "video"):
        holder = node.get("video_url")
        url = (holder.get("url") if isinstance(holder, dict) else "") or node.get("url") or ""
        return bool(url)
    if t == "input_audio":
        holder = node.get("input_audio")
        return bool(isinstance(holder, dict) and holder.get("data"))
    # anthropic "image" / "audio" blocks carry a source object
    src = node.get("source")
    return bool(isinstance(src, dict) and (src.get("data") or src.get("url")))


# ---------------------------------------------------------------------------
# normalization (in place)
# ---------------------------------------------------------------------------

def prepare(payload: Dict[str, Any]) -> bool:
    """Make media parts readable by as many upstreams as possible, in place.

    * plain-text file parts / document blocks -> text parts (every model)
    * image file parts -> image_url data URIs
    Returns True when the payload changed. No-op when nothing matches.
    """
    if not _has_types(payload, ("file", "document")):
        return False
    changed: List[int] = []

    def rebuild(node: Any) -> Any:
        if isinstance(node, list):
            return [rebuild(x) for x in node]
        if not isinstance(node, dict):
            return node
        t = node.get("type")
        new = _file_to_part(node) if t == "file" else (
            _document_to_block(node) if t == "document" else None
        )
        if new is not None:
            changed.append(1)
            return new
        return {k: rebuild(v) for k, v in node.items()}

    out = rebuild(payload)
    if changed:
        payload.clear()
        payload.update(out)
    return bool(changed)


def _has_types(node: Any, wanted: Tuple[str, ...]) -> bool:
    if isinstance(node, list):
        return any(_has_types(x, wanted) for x in node)
    if isinstance(node, dict):
        if node.get("type") in wanted:
            return True
        return any(_has_types(v, wanted) for v in node.values())
    return False


def _file_to_part(part: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """OpenAI-style file part -> text part (plain text) | image_url (image).

    Pdfs and other binaries stay file parts: openai-kind upstreams read
    them natively, anthropic-kind via file_part_to_anthropic().
    """
    f = part.get("file")
    if not isinstance(f, dict):
        return None
    data = str(f.get("file_data") or "")
    if not data:
        return None  # file_id reference: only the upstream storing it can read it
    name = str(f.get("filename") or f.get("name") or "")
    mime, b64 = _split_data_uri(data)
    if _is_textish(mime, name):
        text = _decode(b64) or ""
        if len(text) > MAX_INLINE_CHARS:
            text = text[:MAX_INLINE_CHARS] + "\n…[file truncated]"
        return {"type": "text", "text": (f"[file: {name}]\n" if name else "") + text}
    if not mime:
        mime = next((m for ext, m in _GUESS_MIME.items() if name.lower().endswith(ext)), "")
    if mime.startswith("image/"):
        return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
    return None  # pdf / unknown binary: leave for the upstream


def _document_to_block(block: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Anthropic document block: plain-text content -> text block; else None.

    url / pdf documents stay in place for the upstream (native readers
    handle them; translated flows convert them in adapters).
    """
    src = block.get("source")
    if not isinstance(src, dict):
        return None
    text = ""
    if src.get("type") == "text":
        text = str(src.get("text") or "")
    elif src.get("type") == "base64" and _is_textish(str(src.get("media_type") or ""), ""):
        text = _decode(str(src.get("data") or "")) or ""
    else:
        return None
    if len(text) > MAX_INLINE_CHARS:
        text = text[:MAX_INLINE_CHARS] + "\n…[document truncated]"
    if not text:
        text = "[empty document]"
    title = block.get("title") or block.get("filename") or "document"
    return {"type": "text", "text": f"[document: {title}]\n{text}"}


# ---------------------------------------------------------------------------
# cross-protocol conversion
# ---------------------------------------------------------------------------

def file_part_to_anthropic(part: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """OpenAI file part (post-prepare: binary only) -> anthropic content block.

    images -> image block, pdf -> document block, text leftovers -> text.
    Returns None when nothing sensible can be built (caller drops the part).
    """
    f = part.get("file")
    if not isinstance(f, dict):
        return None
    data = str(f.get("file_data") or "")
    if not data:
        return None
    name = str(f.get("filename") or f.get("name") or "")
    mime, b64 = _split_data_uri(data)
    if not mime:
        mime = next((m for ext, m in _GUESS_MIME.items() if name.lower().endswith(ext)), "")
    if not mime:
        return None
    if _is_textish(mime, name):
        text = (_decode(b64) or "")[:MAX_INLINE_CHARS]
        return {"type": "text", "text": f"[file: {name}]\n{text}"}
    if mime.startswith("image/"):
        return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}}
    if mime == "application/pdf":
        block: Dict[str, Any] = {
            "type": "document",
            "source": {"type": "base64", "media_type": mime, "data": b64},
        }
        if name:
            block["title"] = name
        return block
    return None


# ---------------------------------------------------------------------------
# data helpers
# ---------------------------------------------------------------------------

def _split_data_uri(data: str) -> Tuple[str, str]:
    if data.startswith("data:"):
        header, _, b64 = data.partition(",")
        mime = header[5:].split(";", 1)[0].strip().lower()
        return mime, b64
    return "", data


def _decode(b64: str) -> Optional[str]:
    if not b64:
        return ""
    try:
        raw = base64.b64decode(re.sub(r"\s+", "", b64), validate=False)
        return raw.decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return None


def _is_textish(mime: str, name: str) -> bool:
    if mime:
        return mime.startswith("text/") or mime in _TEXT_MIMES
    return bool(_TEXT_EXT.search(name or ""))
