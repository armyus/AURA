"""Safe local extraction and source-preserving chunking for supported files."""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePath
from typing import Any

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
SUPPORTED_EXTENSIONS = {".txt", ".md", ".markdown", ".pdf"}
CHUNK_CHARS = 1200
OVERLAP_CHARS = 160


class IngestionError(ValueError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _text_chunks(text: str, page_no: int | None) -> list[dict[str, Any]]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.strip():
        return []
    chunks, start, n = [], 0, len(normalized)
    while start < n:
        hard_end = min(n, start + CHUNK_CHARS)
        end = hard_end
        if hard_end < n:
            boundary = max(normalized.rfind("\n\n", start, hard_end), normalized.rfind(". ", start, hard_end), normalized.rfind(" ", start, hard_end))
            if boundary > start + CHUNK_CHARS // 2:
                end = boundary + (1 if normalized[boundary:boundary + 1] == "." else 0)
        fragment = normalized[start:end].strip()
        if fragment:
            actual_start = start + len(normalized[start:end]) - len(normalized[start:end].lstrip())
            chunks.append({"chunkNo": len(chunks), "pageNo": page_no, "start": actual_start, "end": actual_start + len(fragment), "text": fragment})
        if end >= n:
            break
        start = max(start + 1, end - OVERLAP_CHARS)
    return chunks


def extract_document(filename: str, content_type: str, data: bytes) -> tuple[str, list[dict[str, Any]]]:
    if len(data) > MAX_UPLOAD_BYTES:
        raise IngestionError("File exceeds the 10 MB upload limit.")
    if not data:
        raise IngestionError("The uploaded file is empty.")
    suffix = PurePath(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise IngestionError("Supported formats are PDF, TXT, and Markdown.")
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
            from io import BytesIO
            reader = PdfReader(BytesIO(data), strict=True)
            if len(reader.pages) > 200:
                raise IngestionError("PDF exceeds the 200-page limit.")
            pages = [(i + 1, page.extract_text() or "") for i, page in enumerate(reader.pages)]
        except IngestionError:
            raise
        except Exception as exc:
            raise IngestionError(f"Unable to read PDF: {type(exc).__name__}.") from None
        chunks = []
        all_text = []
        for page_no, text in pages:
            all_text.append(text)
            chunks.extend(_text_chunks(text, page_no))
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise IngestionError("Text files must use UTF-8 encoding.") from None
        pages = [(None, text)]
        all_text = [text]
        chunks = _text_chunks(text, None)
    if not any(c["text"].strip() for c in chunks):
        raise IngestionError("No selectable text was found. Scanned PDFs and image files are not supported yet.")
    safe_type = "application/pdf" if suffix == ".pdf" else "text/markdown" if suffix in {".md", ".markdown"} else "text/plain"
    return safe_type, chunks


_TOKEN = re.compile(r"[a-z0-9][a-z0-9'-]*", re.I)


def search_chunks(chunks: list[dict[str, Any]], query: str, limit: int = 8) -> list[dict[str, Any]]:
    terms = list(dict.fromkeys(t.casefold() for t in _TOKEN.findall(query) if len(t) > 1))
    if not terms:
        return []
    documents = [(chunk, [t.casefold() for t in _TOKEN.findall(chunk["text"])]) for chunk in chunks]
    average = sum(len(words) for _, words in documents) / max(1, len(documents))
    df = {term: sum(1 for _, words in documents if term in set(words)) for term in set(terms)}
    phrase = " ".join(terms)
    scored = []
    for chunk, words in documents:
        if not words: continue
        counts = {term: words.count(term) for term in set(terms)}
        score = 0.0
        for term, count in counts.items():
            if count:
                inverse = 1 + (len(documents) - df[term] + 0.5) / (df[term] + 0.5)
                norm = count * 2.2 / (count + 1.2 * (0.25 + 0.75 * len(words) / max(1, average)))
                score += inverse * norm
        if score:
            matched = sum(1 for count in counts.values() if count)
            coverage = matched / len(terms)
            score += coverage * 1.25
            normalized_text = " ".join(words)
            reasons = ["keyword match"]
            if phrase in normalized_text:
                score += 2.5
                reasons.append("exact phrase")
            elif matched > 1:
                positions = [[i for i, word in enumerate(words) if word == term] for term in terms if any(word == term for word in words)]
                if positions:
                    earliest = min(items[0] for items in positions)
                    latest = max(items[-1] for items in positions)
                    span = max(1, latest - earliest + 1)
                    score += min(1.0, matched / span)
                    reasons.append("query terms appear close together")
            if coverage == 1:
                reasons.append("all query terms matched")
            scored.append((score, chunk, reasons))
    scored.sort(key=lambda item: (-item[0], item[1].get("filename", ""), item[1].get("chunk_no", 0)))
    return [{**chunk, "relevance": round(score, 4), "matchReasons": reasons}
            for score, chunk, reasons in scored[:max(1, min(limit, 20))]]

