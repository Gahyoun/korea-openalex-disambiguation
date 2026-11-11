"""Release-level identity rules added on 2026-11-05 (see docs/RELEASE_RULES.md).

KOAD's work decisions are the first gate. These rules run after it, on the works a public
release would publish, and are deterministic (stdlib only). They are mirrored by the
dashboard repository (backend/scripts/career_consistency.py and the patch tools) so that a
full rebuild and the patched release agree.

1. career_consistency()  drop namesake works that survived KOAD
2. accept_candidate()    add missing works from extra OpenAlex author IDs (institution x period)
3. Explicit decisions    verified_works / rejected_works / explicit keep / require_corroboration
                         (corroborated(): data formats in the doc)
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

# --------------------------------------------------------------------------- shared
MIN_ANCHORS = 3
HOME_DOMAINS = {
    "physics": {"Physical Sciences"},
    "mathematics": {"Physical Sciences"},
    "chemistry": {"Physical Sciences", "Life Sciences"},
    "biology": {"Life Sciences", "Health Sciences"},
}


def norm_institution(name: str | None) -> str:
    """'Umeå University' == 'UMEA university'; parentheses (campus) and 'the/of/campus' ignored."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"\b(the|of|campus)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", "", s)


# --------------------------------------------------------------------------- rule 1
def career_consistency(works, career_institutions, subject=None, min_anchors=MIN_ANCHORS):
    """works: [{'id', 'institutions': [names], 'coauthors': [author ids], 'field', 'subfield', 'domain'}]
    (OpenAlex primary topic; institutions of the researcher's own authorship).
    Returns {work_id: reason} for dropped works.

    anchor  = the authorship names an institution of the researcher's career.
    A non-anchor work is kept when its domain is in HOME_DOMAINS[subject] or it shares >= 2
    coauthors with the anchors. Otherwise it is dropped when it shares exactly one coauthor and
    its field is absent from the anchors ('career_field_mismatch'), or shares none, has
    coauthors, and its subfield is absent from the anchors ('career_subfield_mismatch').
    """
    career = {norm_institution(c) for c in career_institutions if c} - {""}
    home = HOME_DOMAINS.get(subject, set())
    anchors, rest = [], []
    for w in works:
        inst = {norm_institution(i) for i in w.get("institutions") or [] if i} - {""}
        (anchors if inst & career else rest).append(w)
    if len(anchors) < min_anchors:
        return {}
    anchor_coauthors = {c for w in anchors for c in w.get("coauthors") or [] if c}
    anchor_fields = {w.get("field") for w in anchors if w.get("field")}
    anchor_subfields = {w.get("subfield") for w in anchors if w.get("subfield")}
    out = {}
    for w in rest:
        if not w.get("domain") or w["domain"] in home:
            continue
        coauthors = {c for c in w.get("coauthors") or [] if c}
        shared = len(coauthors & anchor_coauthors)
        if shared >= 2:
            continue
        if shared == 1 and w.get("field") and w["field"] not in anchor_fields:
            out[w["id"]] = "career_field_mismatch"
        elif shared == 0 and coauthors and w.get("subfield") and w["subfield"] not in anchor_subfields:
            out[w["id"]] = "career_subfield_mismatch"
    return out


# --------------------------------------------------------------------------- rule 2
def name_signatures(raw: str | None) -> set:
    """'Sang-Kwon Lee' -> {('full','sangkwonlee'), ('init','sklee'), ...} in both name orders."""
    toks = [re.sub(r"[^a-z]", "", t) for t in re.split(r"[\s,.]+", (raw or "").replace("-", " ").lower())]
    toks = [t for t in toks if t]
    if len(toks) < 2:
        return set()
    out = set()
    for last, given in ((toks[-1], toks[:-1]), (toks[0], toks[1:])):
        out.add(("full", "".join(given) + last))
        out.add(("init", "".join(g[0] for g in given) + last))
    return out


def name_matches(raw: str | None, published_signatures: set) -> bool:
    """Full-name match, or an initials-only name whose initials match (no initials-only match
    for a written-out given name: 'Sangmin Lee' never matches 'Sang-Kwon Lee')."""
    mine = name_signatures(raw)
    if not mine or not published_signatures:
        return False
    if {s for s in mine if s[0] == "full"} & published_signatures:
        return True
    toks = [t for t in re.split(r"[\s,.]+", (raw or "").replace("-", " ")) if t]
    initials_only = sum(len(re.sub(r"[^A-Za-z]", "", t)) <= 2 for t in toks) >= len(toks) - 1
    return initials_only and bool({s for s in mine if s[0] == "init"} & published_signatures)


def allowed_fields(anchored_fields: Counter, subject_field="Physics and Astronomy", share=0.15, minimum=2) -> set:
    """Fields with >= share of the researcher's career-anchored published works (and >= minimum),
    plus the subject's own field."""
    total = sum(anchored_fields.values())
    return {subject_field} | {f for f, n in anchored_fields.items() if f and total and n / total >= share and n >= minimum}


def accept_candidate(work, authorship, career_segments, fields_ok, published_signatures,
                     types=("article", "review", "letter", "editorial")) -> bool:
    """A work found under an extra author ID is the researcher's when ALL hold:
    the authorship institution is a career institution and the year lies within that segment
    (+-1 year); the work's field is allowed; the author name matches the published works;
    the type is a journal type. career_segments: [(institution name, start_year, end_year)]."""
    year = work.get("publication_year")
    inst = {norm_institution(i.get("display_name")) for i in authorship.get("institutions") or []}
    anchored = any(i == norm_institution(n) and year and s - 1 <= year <= (e or 9999) + 1
                   for i in inst for n, s, e in career_segments if s)
    field = ((work.get("primary_topic") or {}).get("field") or {}).get("display_name")
    raw = authorship.get("raw_author_name") or (authorship.get("author") or {}).get("display_name")
    return bool(anchored and field in fields_ok and work.get("type") in types and name_matches(raw, published_signatures))


# --------------------------------------------------------------------------- rule 3: require_corroboration
def corroborated(works, career_segments, subject=None):
    """Explicit decision 'require_corroboration', for a researcher whose author ID is known to
    hold a namesake. Every work needs two mutually supporting affirmations:
      identity  the authorship institution is a career institution within that segment +-1 year,
                or the work shares a coauthor ID with the career-anchored works;
      subject   the work's subfield appears among the anchored works, or its domain is a home
                domain of the subject.
    Either alone is not enough (common-name coauthor IDs merge too; a namesake can work nearby).
    works: [{'id', 'year', 'institutions', 'coauthors', 'subfield', 'domain'}];
    career_segments: [(institution, start_year, end_year)]. Returns {work_id: 'uncorroborated'}."""
    segs = [(norm_institution(n), s, e) for n, s, e in career_segments if n and s]
    home = HOME_DOMAINS.get(subject, set())

    def anchored(w):
        y = w.get("year") or 0
        return any(norm_institution(i) == n and s - 1 <= y <= (e or 9999) + 1
                   for i in w.get("institutions") or [] for n, s, e in segs)
    anchors = [w for w in works if anchored(w)]
    subfields = {w.get("subfield") for w in anchors if w.get("subfield")}
    out = {}
    for w in works:
        co = {c for c in w.get("coauthors") or [] if c}
        others = {c for a in anchors if a is not w for c in a.get("coauthors") or [] if c}
        if not ((anchored(w) or co & others) and (w.get("subfield") in subfields or w.get("domain") in home)):
            out[w["id"]] = "uncorroborated"
    return out
