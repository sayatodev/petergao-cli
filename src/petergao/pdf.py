from __future__ import annotations

from pathlib import Path
import re

from pypdf import PdfReader

from petergao.models import Paper


SOLUTION_TEXT_PATTERNS = [
    r"\b(sample|worked|official|suggested|model)\s+solutions?\b",
    r"\bsolutions?\s+(to|for)\b",
    r"\bmarking\s+scheme\b",
    r"\banswer\s+key\b",
    r"\b(model|suggested)\s+answers?\b",
    r"\bsolution\s+manual\b",
    r"\bwith\s+solutions?\b",
    r"\bwith\s+answers?\b",
    r"^\s*solutions?\b",
    r"^\s*answers?\b",
]

SOLUTION_NAME_PATTERNS = [
    r"(^|[^a-z])(sol|solution|solutions|answer|answers|marking)([^a-z]|$)",
]
SOLUTION_COUNT_RE = re.compile(r"\b(solution|solutions|scheme|schemes)\b", re.IGNORECASE)


def infer_solution_marker(path: Path, paper: Paper) -> bool:
    if looks_like_solution_name(paper.raw_filename) or looks_like_solution_name(paper.paper_type):
        return True
    if path.suffix.lower() != ".pdf":
        return False
    first_pages_text, repeated_solution_terms = extract_pdf_solution_signals(path)
    if repeated_solution_terms > 15:
        return True
    if not first_pages_text:
        return False
    return looks_like_solution_text(first_pages_text)


def looks_like_solution_name(value: str) -> bool:
    lowered = value.lower()
    return any(re.search(pattern, lowered) for pattern in SOLUTION_NAME_PATTERNS)


def looks_like_solution_text(text: str) -> bool:
    if count_solution_terms(text) > 15:
        return True
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    sample = "\n".join(lines[:40]).lower()
    normalized = re.sub(r"\s+", " ", sample)
    return any(re.search(pattern, normalized, flags=re.IGNORECASE | re.MULTILINE) for pattern in SOLUTION_TEXT_PATTERNS)


def count_solution_terms(text: str) -> int:
    return len(SOLUTION_COUNT_RE.findall(text))


def extract_pdf_solution_signals(path: Path, preview_pages: int = 3) -> tuple[str, int]:
    try:
        reader = PdfReader(str(path))
    except Exception:
        return "", 0
    preview_chunks: list[str] = []
    total_count = 0
    for index, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception:
            continue
        if index < preview_pages:
            preview_chunks.append(text)
        total_count += count_solution_terms(text)
        if total_count > 15 and index >= preview_pages - 1:
            break
    return "\n".join(preview_chunks), total_count
