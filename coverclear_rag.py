"""Level-A keyword RAG helpers for CoverClear policy analysis."""

from __future__ import annotations

import re
from typing import Iterable, List, Optional, Sequence

CHUNK_MIN = 800
CHUNK_MAX = 1200
CHUNK_OVERLAP = 180

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.I)
_SSN_RE = re.compile(r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?1[\s.\-]?)?(?:\(?\d{3}\)?[\s.\-]?)\d{3}[\s.\-]?\d{4}(?!\d)"
)
_DATE_TOKEN = (
    r"(?:"
    r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    r"|\d{4}[/-]\d{1,2}[/-]\d{1,2}"
    r"|(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\s+\d{1,2},?\s+\d{4}"
    r")"
)
_DOB_RE = re.compile(
    r"\b(?:DOB|D\.O\.B\.|date\s+of\s+birth|birth\s*date|born(?:\s+on)?)"
    r"\s*[:#\-]?\s*" + _DATE_TOKEN,
    re.I,
)
_LABELED_ID_RE = re.compile(
    r"\b("
    r"(?:policy|account|claim|member|customer|file|reference|confirmation|"
    r"driver(?:'s)?\s*license|licence|license)"
    r"\s*(?:number|no\.?|num\.?|id|#)"
    r"|policy\s*#|acct(?:ount)?\s*#|claim\s*#|"
    r"ssn|ein|itin|vin|npi|"
    r"tax\s*id(?:entification)?(?:\s*number)?|"
    r"social\s*security(?:\s*number)?"
    r")\s*[:.#]?\s*[A-Z0-9][A-Z0-9\-_/]{2,}\b",
    re.I,
)
_HEADING_KEYWORD_RE = re.compile(
    r"^(?:#{1,6}\s+\S|"
    r"(?:SECTION|COVERAGE|PART|ARTICLE|EXCLUSIONS?|DEFINITIONS?|"
    r"CONDITIONS?|ENDORSEMENTS?|LIMITATIONS?|LIMITS|SCHEDULE|"
    r"DECLARATIONS?|DUTIES|GENERAL\s+PROVISIONS)\b)",
    re.I,
)
_NUMBERED_HEADING_RE = re.compile(r"^(?:\d+(?:\.\d+)*|[A-Z])[.)]\s+\S")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")

SUMMARY_TERMS = (
    "coverage",
    "covered",
    "insure",
    "insured",
    "limit",
    "limits",
    "deductible",
    "exclusion",
    "excluded",
    "not covered",
    "does not cover",
    "definition",
    "means",
    "named insured",
    "policy period",
    "declarations",
    "amount of insurance",
    "personal property",
    "liability",
    "dwelling",
    "medical payments",
    "additional coverage",
    "endorsement",
    "what is covered",
    "we cover",
    "we do not cover",
)

RISK_TERMS = (
    "flood",
    "flooding",
    "water",
    "surface water",
    "groundwater",
    "sewer",
    "backup",
    "sump",
    "mold",
    "fungus",
    "rot",
    "earth",
    "earthquake",
    "earth movement",
    "landslide",
    "mudslide",
    "sinkhole",
    "vacancy",
    "vacant",
    "unoccupied",
    "not covered",
    "does not cover",
    "we do not cover",
    "exclusion",
    "excluded",
    "except",
    "wear and tear",
    "deterioration",
    "neglect",
    "ordinance",
    "war",
    "nuclear",
    "intentional",
    "pollution",
    "collapse",
    "theft",
    "mysterious disappearance",
    "vermin",
    "rodent",
    "insect",
    "power failure",
    "government action",
    "nuclear hazard",
    "ice",
    "weight of ice",
)

_QUESTION_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "must",
        "can",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "from",
        "by",
        "with",
        "about",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "between",
        "under",
        "again",
        "further",
        "then",
        "once",
        "here",
        "there",
        "when",
        "where",
        "why",
        "how",
        "all",
        "each",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "no",
        "nor",
        "not",
        "only",
        "own",
        "same",
        "so",
        "than",
        "too",
        "very",
        "just",
        "and",
        "but",
        "if",
        "or",
        "because",
        "as",
        "until",
        "while",
        "this",
        "that",
        "these",
        "those",
        "i",
        "me",
        "my",
        "we",
        "our",
        "you",
        "your",
        "it",
        "its",
        "they",
        "them",
        "their",
        "what",
        "which",
        "who",
        "whom",
        "please",
        "tell",
        "explain",
        "document",
        "policy",
        "insurance",
    }
)


def redact_pii(text: str) -> str:
    """Replace SSN, email, phone, DOB-like dates, and labeled IDs with placeholders."""
    if not text:
        return ""

    redacted = _EMAIL_RE.sub("[EMAIL]", text)
    redacted = _SSN_RE.sub("[SSN]", redacted)
    redacted = _PHONE_RE.sub("[PHONE]", redacted)
    redacted = _DOB_RE.sub("[DOB]", redacted)

    def _id_sub(match: re.Match) -> str:
        label = re.sub(r"\s+", " ", match.group(1)).strip()
        return f"{label}: [ID]"

    return _LABELED_ID_RE.sub(_id_sub, redacted)


def _is_heading(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > 120:
        return False
    if _HEADING_KEYWORD_RE.match(s):
        return True
    if _NUMBERED_HEADING_RE.match(s) and len(s) <= 90:
        return True
    if len(s) >= 8 and s == s.upper() and any(c.isalpha() for c in s):
        return True
    return False


def _starts_major_heading(text: str) -> bool:
    first = text.strip().split("\n", 1)[0]
    if not first:
        return False
    if _HEADING_KEYWORD_RE.match(first.strip()):
        return True
    s = first.strip()
    return len(s) >= 8 and s == s.upper() and any(c.isalpha() for c in s)


def _hard_wrap(text: str, max_len: int) -> List[str]:
    words = text.split()
    if not words:
        return []
    out: List[str] = []
    buf = words[0]
    for word in words[1:]:
        candidate = f"{buf} {word}"
        if len(candidate) <= max_len:
            buf = candidate
        else:
            out.append(buf)
            buf = word if len(word) <= max_len else word[:max_len]
    if buf:
        out.append(buf)
    return out


def _split_long(text: str, max_len: int) -> List[str]:
    if len(text) <= max_len:
        return [text]
    sentences = _SENTENCE_RE.split(text)
    out: List[str] = []
    buf = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if not buf:
            if len(sentence) <= max_len:
                buf = sentence
            else:
                out.extend(_hard_wrap(sentence, max_len))
            continue
        candidate = f"{buf} {sentence}"
        if len(candidate) <= max_len:
            buf = candidate
        else:
            out.append(buf)
            if len(sentence) <= max_len:
                buf = sentence
            else:
                out.extend(_hard_wrap(sentence, max_len))
                buf = ""
    if buf:
        out.append(buf)
    if len(out) <= 1:
        return out
    overlapped = [out[0]]
    for part in out[1:]:
        prefix = _overlap_prefix(overlapped[-1])
        if prefix and not part.startswith(prefix):
            candidate = f"{prefix} {part}"
            overlapped.append(candidate if len(candidate) <= max_len + CHUNK_OVERLAP else part)
        else:
            overlapped.append(part)
    return overlapped


def _overlap_prefix(prev: str, size: int = CHUNK_OVERLAP) -> str:
    if not prev or size <= 0:
        return ""
    tail = prev[-size:]
    space = tail.find(" ")
    if 0 <= space < min(48, len(tail)):
        tail = tail[space + 1 :]
    return tail.strip()


def chunk_policy(text: str) -> List[str]:
    """Split policy text on headings/blank lines into ~800–1200 char overlapping chunks."""
    if not text or not str(text).strip():
        return []

    normalized = str(text).replace("\r\n", "\n").replace("\r", "\n").strip()
    raw_blocks = re.split(r"\n\s*\n", normalized)
    units: List[str] = []
    for block in raw_blocks:
        block = block.strip()
        if not block:
            continue
        current: List[str] = []
        for line in block.split("\n"):
            if current and _is_heading(line):
                units.append("\n".join(current).strip())
                current = [line]
            else:
                current.append(line)
        if current:
            units.append("\n".join(current).strip())

    pieces: List[str] = []
    for unit in units:
        pieces.extend(_split_long(unit, CHUNK_MAX) if len(unit) > CHUNK_MAX else [unit])

    chunks: List[str] = []
    buf = ""
    for piece in pieces:
        if buf and _starts_major_heading(piece) and len(buf) >= 200:
            chunks.append(buf)
            buf = piece
            continue
        if not buf:
            buf = piece
            continue
        joined = f"{buf}\n\n{piece}"
        if len(joined) <= CHUNK_MAX:
            buf = joined
            continue
        if len(buf) < CHUNK_MIN and not _starts_major_heading(piece) and len(joined) <= CHUNK_MAX + 150:
            buf = joined
            continue
        chunks.append(buf)
        buf = piece
    if buf:
        chunks.append(buf)
    return chunks


def _term_hits(text_lower: str, term: str) -> int:
    if " " in term:
        return text_lower.count(term)
    return len(re.findall(r"\b" + re.escape(term) + r"\b", text_lower))


def _score_terms(chunk: str, terms: Sequence[str]) -> int:
    lower = chunk.lower()
    first = chunk.strip().split("\n", 1)[0].lower()
    score = 0
    for term in terms:
        hits = _term_hits(lower, term)
        if not hits:
            continue
        score += hits
        if term in first:
            score += 3
    return score


def _select_scored(chunks: Sequence[str], scores: Sequence[int]) -> List[str]:
    ranked = sorted(
        ((score, index, chunk) for index, (chunk, score) in enumerate(zip(chunks, scores))),
        key=lambda item: (-item[0], item[1]),
    )
    selected = [(index, chunk) for score, index, chunk in ranked if score > 0]
    if not selected:
        return list(chunks)
    if len(selected) < 3:
        seen = {index for index, _ in selected}
        for index, chunk in enumerate(chunks):
            if index in seen:
                continue
            selected.append((index, chunk))
            if len(selected) >= 3:
                break
    # Keep score-first order so build_context fills the budget with the best matches.
    selected.sort(key=lambda item: (-scores[item[0]], item[0]))
    return [chunk for _, chunk in selected]


def retrieve_chunks(
    chunks: Sequence[str],
    task: str,
    question: Optional[str] = None,
) -> List[str]:
    """Pick chunks for summary, risks, or a follow-up question."""
    if not chunks:
        return []

    kind = (task or "summary").strip().lower()
    if kind == "question":
        return _retrieve_question(chunks, question or "")
    if kind == "risks":
        scores = [_score_terms(chunk, RISK_TERMS) for chunk in chunks]
        return _select_scored(chunks, scores)

    scores = [_score_terms(chunk, SUMMARY_TERMS) for chunk in chunks]
    if chunks:
        # Declarations / first page usually hold named insured, limits, and period.
        scores[0] += 2
    return _select_scored(chunks, scores)


def _retrieve_question(chunks: Sequence[str], question: str) -> List[str]:
    tokens = [
        token
        for token in re.findall(r"[a-z0-9']{3,}", question.lower())
        if token not in _QUESTION_STOPWORDS
    ]
    if not tokens:
        return list(chunks)

    phrase = re.sub(r"\s+", " ", question.strip().lower())
    scores = []
    for chunk in chunks:
        lower = chunk.lower()
        score = sum(_term_hits(lower, token) for token in tokens)
        if len(phrase) >= 8 and phrase in lower:
            score += 8
        scores.append(score)
    return _select_scored(chunks, scores)


def build_context(chunks: Iterable[str], max_chars: int) -> str:
    """Join selected chunks until max_chars is reached."""
    if max_chars is None or max_chars <= 0:
        return ""

    parts: List[str] = []
    used = 0
    sep = "\n\n---\n\n"
    for chunk in chunks:
        piece = (chunk or "").strip()
        if not piece:
            continue
        extra = len(sep) if parts else 0
        available = max_chars - used - extra
        if available <= 0:
            break
        if len(piece) <= available:
            parts.append(piece)
            used += extra + len(piece)
            continue
        if available < 80 and parts:
            break
        clipped = piece[:available]
        if " " in clipped and available < len(piece):
            clipped = clipped.rsplit(" ", 1)[0]
        if clipped:
            parts.append(clipped)
        break
    return sep.join(parts)
