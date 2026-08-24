from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections import Counter, defaultdict, deque
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import __version__
from .model import Decision, Work, anonymized_token, centroid, cosine, stable_json, tfidf_vectors


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def source_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def connect_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def parse_json(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except json.JSONDecodeError:
        return fallback


def _index_sets(works: list[Work], attribute: str, maximum: int) -> dict[str, list[int]]:
    result: dict[str, list[int]] = defaultdict(list)
    for index, work in enumerate(works):
        for value in getattr(work, attribute):
            result[value].append(index)
    return {key: value for key, value in result.items() if 1 < len(value) <= maximum}


def _subject_compatible(subject: str, field_name: str | None, config: dict[str, Any]) -> bool:
    allowed = config.get("subject_fields", {}).get(subject, [])
    return (field_name or "").casefold() in {value.casefold() for value in allowed}


def _department_compatible(subject: str, unit_labels: set[str]) -> bool | None:
    labels = " ".join(unit_labels).casefold().strip()
    if not labels:
        return None
    patterns = {
        "physics": ("physics", "physical science", "물리"),
        "mathematics": ("mathemat", "statistics", "수학", "통계"),
        "chemistry": ("chem", "molecular science", "화학"),
        "biology": (
            "bio", "life science", "ecolog", "genetic", "microbiolog", "botan",
            "zoolog", "agricultur", "medicine", "pharmacy", "생명", "생물", "의학",
        ),
    }
    return any(pattern in labels for pattern in patterns.get(subject, ()))


def choose_seeds(
    works: list[Work],
    vectors: list[dict[str, float]],
    subject: str,
    phd_year: int | None,
    phd_unit: str | None,
    config: dict[str, Any],
) -> tuple[set[int], int | None, int | None, str]:
    doctoral_start = phd_year - int(config["doctoral_window_years"]) if phd_year else None
    in_window = [
        index for index, work in enumerate(works)
        if work.publication_year is not None
        and (doctoral_start is None or work.publication_year >= doctoral_start)
        and (phd_year is None or work.publication_year <= phd_year)
    ]
    anchored = []
    for index in in_window:
        work = works[index]
        at_phd_institution = work.anchor_flags.get("phd_institution") or (
            phd_unit is not None and phd_unit in work.institutions
        )
        department_match = _department_compatible(subject, work.unit_labels)
        if at_phd_institution and (
            department_match is True
            or (department_match is None and _subject_compatible(subject, work.field_name, config))
        ):
            anchored.append(index)
    source = "doctoral_institution"
    if not anchored:
        anchored = [
            index for index in in_window
            if works[index].prior_decision == "keep"
            and _subject_compatible(subject, works[index].field_name, config)
            and not works[index].anchor_flags.get("orcid_exact", False)
        ]
        source = "subject_compatible_fallback"
    if not anchored:
        anchored = [
            index for index, work in enumerate(works)
            if work.prior_decision == "keep" and work.publication_year is not None
        ]
        source = "earliest_prior_keep_fallback"
    if not anchored:
        return set(), None, doctoral_start, "no_seed"

    debut_year = min(works[index].publication_year for index in anchored if works[index].publication_year is not None)
    seeds = {index for index in anchored if works[index].publication_year == debut_year}
    initial_centroid = centroid(vectors[index] for index in seeds)
    seed_coauthors = set().union(*(works[index].coauthors for index in seeds)) if seeds else set()
    seed_institutions = set().union(*(works[index].institutions for index in seeds)) if seeds else set()
    for index, work in enumerate(works):
        if work.publication_year != debut_year or index in seeds:
            continue
        department_match = _department_compatible(subject, work.unit_labels)
        if (
            work.coauthors & seed_coauthors
            or (work.institutions & seed_institutions and department_match is not False)
            or cosine(vectors[index], initial_centroid) >= float(config["near_duplicate_similarity"])
        ):
            seeds.add(index)
    return seeds, debut_year, doctoral_start, source


def graph_reachable(
    works: list[Work], vectors: list[dict[str, float]], seeds: set[int],
    subject: str, config: dict[str, Any]
) -> set[int]:
    if not seeds:
        return set()
    maximum = int(config["max_shared_entity_component"])
    coauthor_index = _index_sets(works, "coauthors", maximum)
    institution_index = _index_sets(works, "institutions", maximum)
    topic_index: dict[str, list[int]] = defaultdict(list)
    for index, work in enumerate(works):
        if work.topic_id:
            topic_index[work.topic_id].append(index)
    topic_index = {key: value for key, value in topic_index.items() if 1 < len(value) <= maximum}

    reached = set(seeds)
    queue = deque(seeds)
    while queue:
        current = queue.popleft()
        work = works[current]
        neighbors: set[int] = set()
        for value in work.coauthors:
            neighbors.update(coauthor_index.get(value, ()))
        for value in work.institutions:
            neighbors.update(institution_index.get(value, ()))
        if work.topic_id:
            neighbors.update(topic_index.get(work.topic_id, ()))
        for other in neighbors - reached:
            candidate = works[other]
            shared_coauthor = bool(work.coauthors & candidate.coauthors)
            shared_institution = bool(work.institutions & candidate.institutions)
            if shared_institution:
                work_department = _department_compatible(subject, work.unit_labels)
                candidate_department = _department_compatible(subject, candidate.unit_labels)
                work_consistent = work_department is True or (
                    work_department is None and _subject_compatible(
                        subject, work.field_name, config
                    )
                )
                candidate_consistent = candidate_department is True or (
                    candidate_department is None and _subject_compatible(
                        subject, candidate.field_name, config
                    )
                )
                shared_institution = work_consistent and candidate_consistent
            year_gap = (
                abs(work.publication_year - candidate.publication_year)
                if work.publication_year is not None and candidate.publication_year is not None
                else 999
            )
            similarity = cosine(vectors[current], vectors[other])
            topic_edge = (
                work.topic_id is not None
                and work.topic_id == candidate.topic_id
                and year_gap <= int(config["graph_max_topic_year_gap"])
                and similarity >= float(config["graph_topic_similarity"])
            )
            duplicate_edge = (
                year_gap <= int(config["graph_max_topic_year_gap"])
                and similarity >= float(config["near_duplicate_similarity"])
            )
            if shared_coauthor or shared_institution or topic_edge or duplicate_edge:
                reached.add(other)
                queue.append(other)
    return reached


def classify_professor(
    professor: sqlite3.Row,
    works: list[Work],
    phd_unit: str | None,
    career_units: set[str],
    config: dict[str, Any],
) -> tuple[list[Decision], dict[str, Any]]:
    if not works:
        return [], {
            "professor_uid": professor["professor_uid"], "name": professor["name"],
            "professor_token": anonymized_token(professor["professor_uid"]),
            "subject": professor["subject"], "openalex_id": professor["openalex_id"],
            "phd_year": professor["phd_year"], "doctoral_start_year": (
                professor["phd_year"] - int(config["doctoral_window_years"])
                if professor["phd_year"] else None
            ),
            "debut_year": None, "seed_source": "no_works", "seed_work_ids": [],
            "paper_count": 0, "true_paper_count": 0, "namesake_paper_count": 0,
            "auto_drop_count": 0, "review_count": 0, "confidence_counts": {},
        }
    vectors = tfidf_vectors(works)
    seeds, debut_year, doctoral_start, seed_source = choose_seeds(
        works, vectors, professor["subject"], professor["phd_year"], phd_unit, config
    )
    root_centroid = centroid(vectors[index] for index in seeds)
    reached = graph_reachable(works, vectors, seeds, professor["subject"], config)
    core_coauthors = set().union(*(works[index].coauthors for index in seeds)) if seeds else set()
    known_institutions = set(career_units)
    if phd_unit:
        known_institutions.add(phd_unit)
    professor_token = anonymized_token(professor["professor_uid"])
    decisions: list[Decision] = []

    for index, work in enumerate(works):
        is_seed = index in seeds
        core_connected = bool(work.coauthors & core_coauthors) or is_seed
        department_compatible = _department_compatible(professor["subject"], work.unit_labels)
        institution_raw_match = bool(work.institutions & known_institutions) or any(
            work.anchor_flags.get(key, False)
            for key in ("phd_institution", "current_institution", "faculty_chain")
        )
        institution_connected = institution_raw_match and (
            department_compatible is True
            or (
                department_compatible is None
                and _subject_compatible(professor["subject"], work.field_name, config)
            )
        )
        topic_similarity = cosine(vectors[index], root_centroid) if root_centroid else 0.0
        topic_connected = topic_similarity >= float(config["topic_similarity_keep"])
        topic_far = topic_similarity < float(config["topic_similarity_far"])
        path_connected = index in reached
        orcid_exact = bool(work.anchor_flags.get("orcid_exact"))
        independent_positive = sum((core_connected, institution_connected, topic_connected, path_connected))
        orcid_only = orcid_exact and independent_positive == 0
        before_window = (
            doctoral_start is not None
            and work.publication_year is not None
            and work.publication_year < doctoral_start
        )
        before_debut_unlinked = (
            debut_year is not None
            and work.publication_year is not None
            and work.publication_year < debut_year
            and not core_connected and not institution_connected and not path_connected
        )
        negative_signature = (
            not core_connected and not institution_connected and topic_far and not path_connected
        )
        field_compatible = _subject_compatible(
            professor["subject"], work.field_name, config
        )
        verified_seed = seed_source == "doctoral_institution"
        strong_field_conflict = department_compatible is False or (
            department_compatible is None and not field_compatible
        )
        reasons: list[str] = []
        score = 0
        if is_seed:
            score += 8; reasons.append("doctoral_debut_seed:+8")
        if core_connected:
            score += 3; reasons.append("core_coauthor_connected:+3")
        else:
            reasons.append("no_core_coauthor_connection:-3")
        if institution_connected:
            score += 3; reasons.append("career_institution_connected:+3")
        else:
            reasons.append("no_career_institution_connection:-3")
        if department_compatible is False:
            score -= 4; reasons.append("department_conflict:-4")
        elif department_compatible is True:
            score += 1; reasons.append("department_compatible:+1")
        if topic_connected:
            score += 2; reasons.append("topic_embedding_close:+2")
        elif topic_far:
            score -= 3; reasons.append("topic_embedding_far:-3")
        else:
            reasons.append("topic_embedding_borderline:0")
        if path_connected:
            score += 3; reasons.append("verified_cluster_path:+3")
        else:
            reasons.append("no_verified_cluster_path:-3")
        if orcid_exact:
            score += 1; reasons.append("orcid_exact_nonabsolute:+1")
        if orcid_only:
            reasons.append("orcid_only_without_independent_evidence")
        if not verified_seed:
            reasons.append("doctoral_institution_seed_unavailable:review_required")
        if strong_field_conflict:
            reasons.append("subject_or_department_conflict")
        if before_window:
            score -= 20; reasons.append("before_phd_minus_5_hard_gate:-20")
        elif before_debut_unlinked:
            score -= 10; reasons.append("before_verified_debut_without_path:-10")

        if before_window or (before_debut_unlinked and verified_seed):
            decision, confidence = "namesake_paper", "high"
        elif is_seed:
            decision, confidence = "true_paper", "high"
        elif negative_signature and verified_seed and strong_field_conflict:
            decision, confidence = "namesake_paper", "high"
        elif path_connected and (core_connected or institution_connected):
            decision, confidence = "true_paper", "high"
        elif independent_positive >= 2:
            decision, confidence = "true_paper", "medium"
        elif not core_connected and not institution_connected and not path_connected:
            decision = "namesake_paper"
            confidence = "medium" if (topic_far or before_debut_unlinked) else "low"
        elif work.prior_decision == "duplicate_drop_candidate":
            decision, confidence = "namesake_paper", "low"
        else:
            decision, confidence = "true_paper", "low"

        if decision == "namesake_paper" and confidence in config["auto_drop_confidence"]:
            action = "drop"
        elif decision == "true_paper" and confidence in config["auto_keep_confidence"]:
            action = "keep"
        else:
            action = "review"
        decisions.append(Decision(
            professor_uid=work.professor_uid,
            professor_token=professor_token,
            work_id=work.work_id,
            publication_year=work.publication_year,
            publication_date=work.publication_date,
            title=work.title,
            topic_name=work.topic_name,
            field_name=work.field_name,
            domain_name=work.domain_name,
            decision=decision,
            confidence=confidence,
            recommended_action=action,
            score=score,
            debut_year=debut_year,
            is_seed=is_seed,
            core_coauthor_connected=core_connected,
            institution_connected=institution_connected,
            department_compatible=department_compatible,
            topic_similarity=round(topic_similarity, 6),
            graph_path_connected=path_connected,
            orcid_exact=orcid_exact,
            orcid_only=orcid_only,
            prior_decision=work.prior_decision,
            reasons=reasons,
        ))

    counts = Counter((decision.decision, decision.confidence) for decision in decisions)
    summary = {
        "professor_uid": professor["professor_uid"],
        "professor_token": professor_token,
        "name": professor["name"],
        "subject": professor["subject"],
        "openalex_id": professor["openalex_id"],
        "phd_year": professor["phd_year"],
        "doctoral_start_year": doctoral_start,
        "debut_year": debut_year,
        "seed_source": seed_source,
        "seed_work_ids": [works[index].work_id for index in sorted(seeds)],
        "paper_count": len(decisions),
        "true_paper_count": sum(d.decision == "true_paper" for d in decisions),
        "namesake_paper_count": sum(d.decision == "namesake_paper" for d in decisions),
        "auto_drop_count": sum(d.recommended_action == "drop" for d in decisions),
        "review_count": sum(d.recommended_action == "review" for d in decisions),
        "confidence_counts": {f"{key[0]}:{key[1]}": value for key, value in sorted(counts.items())},
    }
    return decisions, summary


class OutputWriter:
    def __init__(self, output_dir: Path, manifest: dict[str, Any]):
        self.output_dir = output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        self.paths = {
            "all": output_dir / "paper_identity_decisions.jsonl",
            "true": output_dir / "true_papers.jsonl",
            "namesake": output_dir / "namesake_papers.jsonl",
            "review": output_dir / "review_queue.jsonl",
            "summary": output_dir / "professor_identity_summary.jsonl",
        }
        self.streams = {key: path.open("w", encoding="utf-8") for key, path in self.paths.items()}
        self.database_path = output_dir / "identity_decisions.sqlite"
        if self.database_path.exists():
            self.database_path.unlink()
        self.database = sqlite3.connect(self.database_path)
        self.database.executescript("""
          PRAGMA journal_mode=WAL;
          CREATE TABLE run_manifest(key TEXT PRIMARY KEY,value_json TEXT NOT NULL);
          CREATE TABLE professor_summary(
            professor_uid TEXT PRIMARY KEY,professor_token TEXT NOT NULL,name TEXT NOT NULL,
            subject TEXT NOT NULL,openalex_id TEXT,phd_year INTEGER,doctoral_start_year INTEGER,
            debut_year INTEGER,seed_source TEXT NOT NULL,seed_work_ids_json TEXT NOT NULL,
            paper_count INTEGER NOT NULL,true_paper_count INTEGER NOT NULL,
            namesake_paper_count INTEGER NOT NULL,auto_drop_count INTEGER NOT NULL,
            review_count INTEGER NOT NULL,confidence_counts_json TEXT NOT NULL
          );
          CREATE TABLE work_decision(
            professor_uid TEXT NOT NULL,professor_token TEXT NOT NULL,work_id TEXT NOT NULL,
            publication_year INTEGER,publication_date TEXT,title TEXT,topic_name TEXT,
            field_name TEXT,domain_name TEXT,decision TEXT NOT NULL,confidence TEXT NOT NULL,
            recommended_action TEXT NOT NULL,score INTEGER NOT NULL,debut_year INTEGER,
            is_seed INTEGER NOT NULL,core_coauthor_connected INTEGER NOT NULL,
            institution_connected INTEGER NOT NULL,topic_similarity REAL NOT NULL,
            department_compatible INTEGER,
            graph_path_connected INTEGER NOT NULL,orcid_exact INTEGER NOT NULL,
            orcid_only INTEGER NOT NULL,prior_decision TEXT NOT NULL,reasons_json TEXT NOT NULL,
            PRIMARY KEY(professor_uid,work_id)
          );
          CREATE INDEX idx_decision_action ON work_decision(recommended_action,confidence);
          CREATE INDEX idx_decision_prof_year ON work_decision(professor_uid,publication_year);
        """)
        self.database.executemany(
            "INSERT INTO run_manifest(key,value_json) VALUES(?,?)",
            ((key, stable_json(value)) for key, value in manifest.items()),
        )

    def write(self, decisions: list[Decision], summary: dict[str, Any]) -> None:
        summary_line = stable_json(summary) + "\n"
        self.streams["summary"].write(summary_line)
        self.database.execute(
            """INSERT INTO professor_summary VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                summary["professor_uid"], summary["professor_token"], summary["name"],
                summary["subject"], summary["openalex_id"], summary["phd_year"],
                summary["doctoral_start_year"], summary["debut_year"], summary["seed_source"],
                stable_json(summary["seed_work_ids"]), summary["paper_count"],
                summary["true_paper_count"], summary["namesake_paper_count"],
                summary["auto_drop_count"], summary["review_count"],
                stable_json(summary["confidence_counts"]),
            ),
        )
        for decision in decisions:
            record = decision.as_dict()
            line = stable_json(record) + "\n"
            self.streams["all"].write(line)
            self.streams["true" if decision.decision == "true_paper" else "namesake"].write(line)
            if decision.recommended_action == "review":
                self.streams["review"].write(line)
            self.database.execute(
                """INSERT INTO work_decision VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    decision.professor_uid, decision.professor_token, decision.work_id,
                    decision.publication_year, decision.publication_date, decision.title,
                    decision.topic_name, decision.field_name, decision.domain_name,
                    decision.decision, decision.confidence, decision.recommended_action,
                    decision.score, decision.debut_year, int(decision.is_seed),
                    int(decision.core_coauthor_connected), int(decision.institution_connected),
                    decision.topic_similarity,
                    None if decision.department_compatible is None else int(decision.department_compatible),
                    int(decision.graph_path_connected),
                    int(decision.orcid_exact), int(decision.orcid_only), decision.prior_decision,
                    stable_json(decision.reasons),
                ),
            )

    def close(self) -> None:
        self.database.commit()
        self.database.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.database.close()
        for stream in self.streams.values():
            stream.close()


def load_professor_evidence(
    connection: sqlite3.Connection, professor_uid: str
) -> tuple[list[Work], str | None, set[str]]:
    phd = connection.execute(
        """SELECT institution_unit_id FROM education
           WHERE professor_uid=? AND degree_level='phd'
           ORDER BY confidence='high' DESC,education_id LIMIT 1""",
        (professor_uid,),
    ).fetchone()
    phd_unit = phd[0] if phd else None
    career_units = {
        row[0] for row in connection.execute(
            """SELECT DISTINCT institution_unit_id FROM career_positions_v2
               WHERE professor_uid=? AND institution_unit_id IS NOT NULL""",
            (professor_uid,),
        )
    }
    affiliation_rows: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"ids": set(), "names": set(), "units": set()}
    )
    for row in connection.execute(
        """SELECT work_id,institution_unit_id,institution_name,unit_label
           FROM raw_affiliation_units WHERE professor_uid=?""",
        (professor_uid,),
    ):
        if row["institution_unit_id"]:
            affiliation_rows[row["work_id"]]["ids"].add(row["institution_unit_id"])
        if row["institution_name"]:
            affiliation_rows[row["work_id"]]["names"].add(row["institution_name"])
        if row["unit_label"]:
            affiliation_rows[row["work_id"]]["units"].add(row["unit_label"])
    works = []
    for row in connection.execute(
        """SELECT professor_uid,work_id,publication_year,publication_date,title,raw_orcid,
                  topic_id,topic_name,subfield_name,field_name,domain_name,
                  identity_decision,duplicate_candidate_score,anchor_flags_json,coauthor_ids_json
           FROM work_authorship_evidence WHERE professor_uid=?
           ORDER BY publication_year,publication_date,work_id""",
        (professor_uid,),
    ):
        evidence = affiliation_rows.get(
            row["work_id"], {"ids": set(), "names": set(), "units": set()}
        )
        works.append(Work(
            professor_uid=row["professor_uid"], work_id=row["work_id"],
            publication_year=row["publication_year"], publication_date=row["publication_date"],
            title=row["title"], raw_orcid=row["raw_orcid"], topic_id=row["topic_id"],
            topic_name=row["topic_name"], subfield_name=row["subfield_name"],
            field_name=row["field_name"], domain_name=row["domain_name"],
            prior_decision=row["identity_decision"], prior_score=row["duplicate_candidate_score"],
            anchor_flags=parse_json(row["anchor_flags_json"], {}),
            coauthors=set(parse_json(row["coauthor_ids_json"], [])),
            institutions=set(evidence["ids"]), institution_names=set(evidence["names"]),
            unit_labels=set(evidence["units"]),
        ))
    return works, phd_unit, career_units


def run_classification(
    database_path: Path,
    output_dir: Path,
    config_path: Path,
    professor_uid: str | None = None,
    limit: int | None = None,
    with_works_only: bool = False,
) -> dict[str, Any]:
    config = load_json(config_path)
    generated_at = datetime.now(timezone.utc).isoformat()
    manifest = {
        "library_version": __version__, "schema_version": config["schema_version"],
        "generated_at": generated_at, "source_database": str(database_path.resolve()),
        "source_database_sha256": source_fingerprint(database_path), "config": config,
        "privacy": "Outputs are private; Git ignores outputs/. External adjudicators receive anonymized evidence only.",
    }
    writer = OutputWriter(output_dir, manifest)
    totals: Counter[str] = Counter()
    with connect_read_only(database_path) as connection:
        query = "SELECT professor_uid,name,subject,openalex_id,phd_year FROM professors"
        params: tuple[Any, ...] = ()
        if professor_uid:
            query += " WHERE professor_uid=?"
            params = (professor_uid,)
        elif with_works_only:
            query += (
                " WHERE EXISTS(SELECT 1 FROM work_authorship_evidence w "
                "WHERE w.professor_uid=professors.professor_uid)"
            )
        query += " ORDER BY professor_uid"
        professors = connection.execute(query, params)
        for professor_index, professor in enumerate(professors):
            if limit is not None and professor_index >= limit:
                break
            works, phd_unit, career_units = load_professor_evidence(
                connection, professor["professor_uid"]
            )
            decisions, summary = classify_professor(
                professor, works, phd_unit, career_units, config
            )
            writer.write(decisions, summary)
            totals["professors"] += 1
            totals["works"] += len(decisions)
            totals["true_papers"] += summary.get("true_paper_count", 0)
            totals["namesake_papers"] += summary.get("namesake_paper_count", 0)
            totals["auto_drop"] += summary.get("auto_drop_count", 0)
            totals["review"] += summary.get("review_count", 0)
    writer.close()
    result = {**manifest, "totals": dict(totals)}
    (output_dir / "run_manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result
