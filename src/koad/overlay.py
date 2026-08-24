from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .model import stable_json


def _read_adjudications(paths: list[Path]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for path in paths:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                record = json.loads(line)
                if record.get("dry_run") or record.get("error"):
                    continue
                if record.get("decision") not in {"true_paper", "namesake_paper"}:
                    raise ValueError(f"{path}:{line_number}: invalid decision")
                if record.get("confidence") not in {"high", "medium", "low"}:
                    raise ValueError(f"{path}:{line_number}: invalid confidence")
                key = (record["professor_token"], record["work_id"])
                grouped[key].append(record)
    return grouped


def _stage2_consensus(records: list[dict[str, Any]]) -> tuple[str | None, str, str]:
    """Return decision, confidence, rule. A single provider must be high confidence."""
    if not records:
        return None, "none", "no_stage2_decision"
    counts = Counter(record["decision"] for record in records)
    winner, votes = counts.most_common(1)[0]
    if len(counts) > 1 and votes * 2 <= len(records):
        return None, "low", "provider_disagreement"
    winner_records = [record for record in records if record["decision"] == winner]
    if len(winner_records) >= 2:
        confidence = "high" if all(
            record["confidence"] in {"high", "medium"} for record in winner_records
        ) else "medium"
        return winner, confidence, "multi_provider_majority"
    only = winner_records[0]
    if only["confidence"] == "high":
        return winner, "medium", "single_provider_high_confidence"
    return None, "low", "single_provider_not_high_confidence"


def build_overlay(
    decision_database: Path,
    adjudication_paths: list[Path],
    output_database: Path,
) -> dict[str, int]:
    stage2 = _read_adjudications(adjudication_paths)
    source = sqlite3.connect(f"file:{decision_database.resolve()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    output_database.parent.mkdir(parents=True, exist_ok=True)
    if output_database.exists():
        output_database.unlink()
    target = sqlite3.connect(output_database)
    target.executescript("""
      CREATE TABLE overlay_manifest(key TEXT PRIMARY KEY,value_json TEXT NOT NULL);
      CREATE TABLE final_identity_decision(
        professor_token TEXT NOT NULL,work_id TEXT NOT NULL,
        stage1_decision TEXT NOT NULL,stage1_confidence TEXT NOT NULL,
        stage1_action TEXT NOT NULL,stage2_decision TEXT,stage2_confidence TEXT NOT NULL,
        stage2_rule TEXT NOT NULL,stage2_records_json TEXT NOT NULL,
        final_decision TEXT NOT NULL,final_confidence TEXT NOT NULL,
        final_action TEXT NOT NULL,PRIMARY KEY(professor_token,work_id)
      );
      CREATE INDEX idx_final_action ON final_identity_decision(final_action,final_confidence);
    """)
    generated_at = datetime.now(timezone.utc).isoformat()
    target.executemany(
        "INSERT INTO overlay_manifest VALUES(?,?)",
        [
            ("generated_at", stable_json(generated_at)),
            ("stage1_database", stable_json(str(decision_database.resolve()))),
            ("adjudication_files", stable_json([str(path.resolve()) for path in adjudication_paths])),
            ("policy", stable_json({
                "stage1": "only deterministic drop/keep actions are applied",
                "stage2": "one high-confidence provider or a provider majority may resolve review",
                "disagreement": "remains review",
            })),
        ],
    )
    counts: Counter[str] = Counter()
    for row in source.execute(
        "SELECT professor_token,work_id,decision,confidence,recommended_action FROM work_decision"
    ):
        key = (row["professor_token"], row["work_id"])
        records = stage2.get(key, [])
        second_decision, second_confidence, second_rule = _stage2_consensus(records)
        if row["recommended_action"] in {"keep", "drop"}:
            final_decision = row["decision"]
            final_confidence = row["confidence"]
            final_action = row["recommended_action"]
        elif second_decision is not None:
            final_decision = second_decision
            final_confidence = second_confidence
            final_action = "keep" if second_decision == "true_paper" else "drop"
        else:
            final_decision = row["decision"]
            final_confidence = "low"
            final_action = "review"
        target.execute(
            "INSERT INTO final_identity_decision VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                key[0], key[1], row["decision"], row["confidence"],
                row["recommended_action"], second_decision, second_confidence,
                second_rule, stable_json(records), final_decision, final_confidence,
                final_action,
            ),
        )
        counts[final_action] += 1
    target.commit()
    source.close()
    target.close()
    return dict(counts)
