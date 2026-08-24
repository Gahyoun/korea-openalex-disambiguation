from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable


TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")
STOPWORDS = {
    "a", "an", "and", "as", "at", "based", "by", "for", "from", "in",
    "of", "on", "or", "study", "the", "to", "using", "with", "analysis",
    "approach", "effects", "research", "대한", "관한", "연구", "분석", "및",
}


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def anonymized_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def semantic_families_for_work(work: "Work", config: dict[str, Any] | None) -> set[str]:
    if not config:
        return set()
    text = " ".join(filter(None, (
        work.title, work.topic_name, work.subfield_name, work.field_name, work.domain_name,
    )))
    values = {token.casefold() for token in TOKEN_RE.findall(text)}

    def has_stem(stems: list[str]) -> bool:
        return any(token.startswith(stem.casefold()) for token in values for stem in stems)

    return {
        name for name, rule in config.get("semantic_families", {}).items()
        if has_stem(rule.get("primary", [])) and has_stem(rule.get("context", []))
    }


def tokens_for_work(work: "Work", config: dict[str, Any] | None = None) -> list[str]:
    title_tokens = [
        token.casefold() for token in TOKEN_RE.findall(work.title or "")
        if len(token) > 1 and token.casefold() not in STOPWORDS
    ]
    bigrams = [f"bi:{a}_{b}" for a, b in zip(title_tokens, title_tokens[1:])]
    metadata = []
    for prefix, value in (
        ("topic", work.topic_name),
        ("subfield", work.subfield_name),
        ("field", work.field_name),
        ("domain", work.domain_name),
    ):
        for token in TOKEN_RE.findall(value or ""):
            if len(token) > 1:
                metadata.extend([f"{prefix}:{token.casefold()}"] * (3 if prefix == "topic" else 1))
    if work.topic_id:
        metadata.extend([f"topic-id:{work.topic_id}"] * 4)
    for family_name in semantic_families_for_work(work, config):
        metadata.extend([f"semantic-family:{family_name}"] * 5)
    return title_tokens + bigrams + metadata


def tfidf_vectors(
    works: list["Work"], config: dict[str, Any] | None = None
) -> list[dict[str, float]]:
    documents = [tokens_for_work(work, config) for work in works]
    df: Counter[str] = Counter()
    for document in documents:
        df.update(set(document))
    size = max(1, len(documents))
    vectors = []
    for document in documents:
        counts = Counter(document)
        vector = {
            token: (1.0 + math.log(count)) * (math.log((size + 1) / (df[token] + 1)) + 1.0)
            for token, count in counts.items()
        }
        norm = math.sqrt(sum(value * value for value in vector.values())) or 1.0
        vectors.append({token: value / norm for token, value in vector.items()})
    return vectors


def cosine(left: dict[str, float], right: dict[str, float]) -> float:
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(token, 0.0) for token, value in left.items())


def centroid(vectors: Iterable[dict[str, float]]) -> dict[str, float]:
    total: Counter[str] = Counter()
    count = 0
    for vector in vectors:
        total.update(vector)
        count += 1
    if not count:
        return {}
    averaged = {token: value / count for token, value in total.items()}
    norm = math.sqrt(sum(value * value for value in averaged.values())) or 1.0
    return {token: value / norm for token, value in averaged.items()}


@dataclass(slots=True)
class Work:
    professor_uid: str
    work_id: str
    publication_year: int | None
    publication_date: str | None
    title: str | None
    raw_orcid: str | None
    topic_id: str | None
    topic_name: str | None
    subfield_name: str | None
    field_name: str | None
    domain_name: str | None
    prior_decision: str
    prior_score: int
    anchor_flags: dict[str, bool]
    coauthors: set[str] = field(default_factory=set)
    institutions: set[str] = field(default_factory=set)
    institution_names: set[str] = field(default_factory=set)
    unit_labels: set[str] = field(default_factory=set)


@dataclass(slots=True)
class Decision:
    professor_uid: str
    professor_token: str
    work_id: str
    publication_year: int | None
    publication_date: str | None
    title: str | None
    topic_name: str | None
    field_name: str | None
    domain_name: str | None
    decision: str
    confidence: str
    recommended_action: str
    score: int
    debut_year: int | None
    is_seed: bool
    core_coauthor_connected: bool
    institution_connected: bool
    department_compatible: bool | None
    topic_similarity: float
    semantic_families: list[str]
    semantic_bridge_connected: bool
    graph_path_connected: bool
    orcid_exact: bool
    orcid_only: bool
    prior_decision: str
    reasons: list[str]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
