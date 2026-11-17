"""Deterministic claim-to-citation validation for web-assisted replies.

The validator intentionally makes no model call.  It checks that citation
numbers exist, compares each cited claim with the retrieved page text, and
keeps only a short supporting excerpt for the UI and chat metadata.
"""

import re
from typing import List, Sequence


_CITATION_RE = re.compile(r"\[(\d+(?:\s*(?:[,，]|[-–—])\s*\d+)*)\]")
_NUMBER_RE = re.compile(r"(?<![A-Za-z])\d+(?:[.,]\d+)*(?:%|％)?")
_EN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.+-]{1,}")
_ZH_RUN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
_PASSAGE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])\s*|(?<=\.)\s+|\n+")
_CLAIM_BOUNDARY_RE = re.compile(r"[。！？!?；;\n]|\.(?=\s|$)")

_STOPWORDS = {
    "about", "after", "also", "because", "before", "between", "could",
    "from", "have", "into", "more", "most", "official", "only", "other",
    "should", "source", "than", "that", "their", "there", "these", "they",
    "this", "those", "through", "under", "using", "were", "what", "when",
    "where", "which", "while", "with", "would", "以及", "但是", "其中", "因此",
    "因为", "如果", "对于", "已经", "可以", "可能", "目前", "相关", "这个", "这些",
}


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _tokens(value: str) -> set:
    text = str(value or "")
    tokens = {
        token.lower()
        for token in _EN_TOKEN_RE.findall(text)
        if len(token) >= 2 and token.lower() not in _STOPWORDS
    }
    for run in _ZH_RUN_RE.findall(text):
        if run in _STOPWORDS:
            continue
        if len(run) <= 4:
            tokens.add(run)
        tokens.update(run[index:index + 2] for index in range(len(run) - 1))
    tokens.update(_NUMBER_RE.findall(text))
    return tokens


def _passages(text: str, max_chars: int = 520) -> List[str]:
    """Split page text into bounded, sentence-like evidence passages."""
    clean = str(text or "").replace("\r", "\n")
    pieces = [_normalize_space(piece) for piece in _PASSAGE_SPLIT_RE.split(clean)]
    pieces = [piece for piece in pieces if piece]
    result = []
    current = ""
    for piece in pieces:
        if len(piece) > max_chars:
            if current:
                result.append(current)
                current = ""
            result.extend(
                piece[offset:offset + max_chars]
                for offset in range(0, len(piece), max_chars)
            )
            continue
        combined = f"{current} {piece}".strip() if current else piece
        if current and len(combined) > max_chars:
            result.append(current)
            current = piece
        else:
            current = combined
    if current:
        result.append(current)
    return result[:240]


def _claim_before(text: str, marker_start: int, previous_end: int) -> str:
    segment = str(text or "")[previous_end:marker_start]
    boundaries = list(_CLAIM_BOUNDARY_RE.finditer(segment))
    if boundaries:
        segment = segment[boundaries[-1].end():]
    claim = _normalize_space(_CITATION_RE.sub("", segment))
    if not claim:
        # A citation can be placed on a new line after the claim. Look back a
        # little farther, while still keeping the public record bounded.
        prefix = str(text or "")[max(0, marker_start - 600):marker_start]
        sentences = [item for item in _CLAIM_BOUNDARY_RE.split(prefix) if item.strip()]
        claim = _normalize_space(sentences[-1] if sentences else prefix)
    return claim[-600:]


def _source_passage_index(source) -> list:
    raw_text = source.text if getattr(source, "status", "") == "read" else source.snippet
    return [
        (passage, _tokens(passage), set(_NUMBER_RE.findall(passage)))
        for passage in _passages(raw_text)
    ]


def _best_passage(claim: str, source, passage_index=None) -> dict:
    claim_tokens = _tokens(claim)
    claim_numbers = set(_NUMBER_RE.findall(claim))
    best = ""
    best_score = 0.0
    best_number_match = not claim_numbers

    for passage, passage_tokens, passage_numbers in passage_index or _source_passage_index(source):
        if not claim_tokens:
            overlap = 0.0
        else:
            overlap = len(claim_tokens & passage_tokens) / max(1, len(claim_tokens))
        number_match = claim_numbers.issubset(passage_numbers)
        score = overlap
        if claim_numbers:
            score += 0.12 if number_match else -0.18
        if score > best_score or (score == best_score and number_match and not best_number_match):
            best = passage
            best_score = score
            best_number_match = number_match

    status = getattr(source, "status", "failed")
    if status != "read":
        support = "snippet_only" if best else "unavailable"
    elif best_score >= 0.34 and best_number_match:
        support = "supported"
    elif best_score >= 0.16:
        support = "partial"
    else:
        support = "unsupported"

    excerpt = _normalize_space(best)
    if len(excerpt) > 320:
        excerpt = excerpt[:319].rstrip() + "…"
    return {
        "support": support,
        "overlap_score": round(max(0.0, min(1.0, best_score)), 3),
        "passage": excerpt,
    }


def _looks_specific_uncited(segment: str) -> bool:
    clean = _normalize_space(segment)
    if len(clean) < 12 or _CITATION_RE.search(clean):
        return False
    if _NUMBER_RE.search(clean):
        return True
    lowered = clean.lower()
    markers = (
        "according to", "announced", "confirmed", "released", "reported",
        "version", "price", "percent", "研究显示", "官方", "发布", "宣布",
        "截至", "版本", "价格", "增长", "下降", "数据", "报告显示",
    )
    return any(marker in lowered for marker in markers)


def _citation_ids(raw_value: str) -> list:
    ids = []
    for part in re.split(r"[,，]", str(raw_value or "")):
        range_match = re.fullmatch(r"\s*(\d+)\s*[-–—]\s*(\d+)\s*", part)
        if range_match:
            start, end = int(range_match.group(1)), int(range_match.group(2))
            values = range(start, end + 1) if 0 <= end - start <= 5 else (start, end)
        else:
            values = [int(value) for value in re.findall(r"\d+", part)[:1]]
        for source_id in values:
            if source_id not in ids:
                ids.append(source_id)
    return ids[:12]


def validate_response_citations(response_text: str, sources: Sequence) -> dict:
    """Return a bounded public validation report for one assistant reply."""
    source_by_id = {int(source.id): source for source in sources or [] if getattr(source, "id", None)}
    passage_indexes = {
        source_id: _source_passage_index(source)
        for source_id, source in source_by_id.items()
    }
    claims = []
    previous_end = 0

    for match in list(_CITATION_RE.finditer(str(response_text or "")))[:24]:
        ids = _citation_ids(match.group(1))
        claim = _claim_before(response_text, match.start(), previous_end)
        previous_end = match.end()
        citation_results = []
        for source_id in ids:
            source = source_by_id.get(source_id)
            if source is None:
                citation_results.append({
                    "source_id": source_id,
                    "support": "invalid_reference",
                    "overlap_score": 0.0,
                    "passage": "",
                })
                continue
            result = _best_passage(claim, source, passage_indexes.get(source_id))
            result.update({
                "source_id": source_id,
                "source_status": source.status,
                "title": str(source.title or "")[:180],
            })
            citation_results.append(result)
        claims.append({
            "claim": claim,
            "citations": citation_results,
        })

    flat = [citation for claim in claims for citation in claim["citations"]]
    counts = {
        "cited_claims": len(claims),
        "supported": sum(item.get("support") == "supported" for item in flat),
        "partial": sum(item.get("support") == "partial" for item in flat),
        "unsupported": sum(item.get("support") == "unsupported" for item in flat),
        "snippet_only": sum(item.get("support") == "snippet_only" for item in flat),
        "invalid_references": sum(item.get("support") == "invalid_reference" for item in flat),
    }
    sentences = [
        item for item in re.split(
            r"(?<=[。！？!?；;])\s*|(?<=\.)\s+|\n+",
            str(response_text or ""),
        )
        if item.strip()
    ]
    cited_claim_texts = [_normalize_space(item.get("claim")) for item in claims if item.get("claim")]
    uncited_specific = 0
    for sentence in sentences:
        normalized_sentence = _normalize_space(_CITATION_RE.sub("", sentence))
        covered = any(
            normalized_sentence and (
                normalized_sentence in cited_claim
                or cited_claim in normalized_sentence
            )
            for cited_claim in cited_claim_texts
        )
        if not covered and _looks_specific_uncited(sentence):
            uncited_specific += 1
    counts["uncited_specific_claims"] = min(20, uncited_specific)

    if not claims:
        status = "no_citations"
    elif counts["invalid_references"] or counts["unsupported"]:
        status = "needs_review"
    elif counts["partial"] or counts["snippet_only"] or counts["uncited_specific_claims"]:
        status = "partial"
    else:
        status = "pass"

    return {
        "version": 1,
        "method": "deterministic_lexical",
        "status": status,
        "summary": counts,
        "claims": claims,
    }
