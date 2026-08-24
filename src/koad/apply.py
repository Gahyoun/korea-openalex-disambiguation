from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .engine import source_fingerprint
from .model import stable_json


def _remove_sqlite_sidecars(path: Path) -> None:
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        if candidate.exists():
            candidate.unlink()


def _manifest_value(connection: sqlite3.Connection, key: str) -> Any:
    row = connection.execute(
        "SELECT value_json FROM run_manifest WHERE key=?", (key,)
    ).fetchone()
    return json.loads(row[0]) if row else None


def apply_to_copy(
    source_database: Path,
    decision_database: Path,
    output_database: Path,
    replace: bool = False,
) -> dict[str, Any]:
    """Apply stage-1 actions to a copy; review rows preserve the source decision."""
    if output_database.exists() and not replace:
        raise FileExistsError(f"output already exists: {output_database}")
    with sqlite3.connect(f"file:{decision_database.resolve()}?mode=ro", uri=True) as decisions:
        expected_hash = _manifest_value(decisions, "source_database_sha256")
    actual_hash = source_fingerprint(source_database)
    if expected_hash and actual_hash != expected_hash:
        raise ValueError(
            "source DB fingerprint differs from the DB used for classification; rerun classify"
        )

    output_database.parent.mkdir(parents=True, exist_ok=True)
    _remove_sqlite_sidecars(output_database)
    shutil.copy2(source_database, output_database)
    connection = sqlite3.connect(output_database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("ATTACH DATABASE ? AS koad", (str(decision_database.resolve()),))
    applied_at = datetime.now(timezone.utc).isoformat()
    connection.executescript("""
      CREATE TABLE IF NOT EXISTS koad_application_audit(
        audit_id INTEGER PRIMARY KEY,professor_uid TEXT NOT NULL,work_id TEXT NOT NULL,
        previous_decision TEXT NOT NULL,applied_decision TEXT NOT NULL,
        classifier_decision TEXT NOT NULL,classifier_confidence TEXT NOT NULL,
        classifier_action TEXT NOT NULL,classifier_score INTEGER NOT NULL,
        classifier_reasons_json TEXT NOT NULL,previous_candidate_json TEXT,
        applied_at TEXT NOT NULL,UNIQUE(professor_uid,work_id,applied_at)
      );
      CREATE TABLE IF NOT EXISTS koad_run_metadata(
        key TEXT PRIMARY KEY,value_json TEXT NOT NULL
      );
    """)
    connection.execute("DELETE FROM koad_run_metadata")
    connection.executemany(
        "INSERT INTO koad_run_metadata VALUES(?,?)",
        [
            ("source_database_sha256", stable_json(actual_hash)),
            ("decision_database", stable_json(str(decision_database.resolve()))),
            ("applied_at", stable_json(applied_at)),
            ("policy", stable_json({
                "keep": "set keep",
                "drop": "set duplicate_drop_candidate",
                "review": "preserve source decision",
            })),
        ],
    )
    connection.execute("""
      INSERT INTO koad_application_audit(
        professor_uid,work_id,previous_decision,applied_decision,
        classifier_decision,classifier_confidence,classifier_action,classifier_score,
        classifier_reasons_json,previous_candidate_json,applied_at
      )
      SELECT w.professor_uid,w.work_id,w.identity_decision,
             CASE d.recommended_action WHEN 'keep' THEN 'keep'
                  ELSE 'duplicate_drop_candidate' END,
             d.decision,d.confidence,d.recommended_action,d.score,d.reasons_json,
             (SELECT json_object(
                'candidate_score',c.candidate_score,'severity',c.severity,
                'reasons_json',json(c.reasons_json)
              ) FROM duplicate_drop_candidate c
              WHERE c.professor_uid=w.professor_uid AND c.work_id=w.work_id),
             ?
      FROM work_authorship_evidence w
      JOIN koad.work_decision d USING(professor_uid,work_id)
      WHERE d.recommended_action IN ('keep','drop')
        AND w.identity_decision <> CASE d.recommended_action WHEN 'keep' THEN 'keep'
                                  ELSE 'duplicate_drop_candidate' END
    """, (applied_at,))

    # Restore deterministic keep rows first so they leave the active candidate table.
    connection.execute("""
      DELETE FROM duplicate_drop_candidate
      WHERE EXISTS(
        SELECT 1 FROM koad.work_decision d
        WHERE d.professor_uid=duplicate_drop_candidate.professor_uid
          AND d.work_id=duplicate_drop_candidate.work_id
          AND d.recommended_action='keep'
      )
    """)
    connection.execute("""
      UPDATE work_authorship_evidence AS w
      SET identity_decision='keep',duplicate_candidate_score=0,
          duplicate_candidate_severity=NULL,
          decision_reasons_json=(SELECT d.reasons_json FROM koad.work_decision d
            WHERE d.professor_uid=w.professor_uid AND d.work_id=w.work_id)
      WHERE EXISTS(SELECT 1 FROM koad.work_decision d
        WHERE d.professor_uid=w.professor_uid AND d.work_id=w.work_id
          AND d.recommended_action='keep')
    """)
    connection.execute("""
      UPDATE raw_affiliation_units AS r
      SET identity_decision='keep'
      WHERE EXISTS(SELECT 1 FROM koad.work_decision d
        WHERE d.professor_uid=r.professor_uid AND d.work_id=r.work_id
          AND d.recommended_action='keep')
    """)

    # Add every high-confidence drop to the active quarantine table.
    connection.execute("""
      INSERT OR REPLACE INTO duplicate_drop_candidate(
        professor_uid,openalex_id,work_id,doi,title,publication_date,period,
        candidate_score,severity,topic_name,field_name,domain_name,
        institution_ids_json,institution_names_json,raw_affiliation_strings_json,
        reasons_json,source_url
      )
      SELECT w.professor_uid,w.openalex_id,w.work_id,w.doi,w.title,w.publication_date,w.period,
             100,'high',w.topic_name,w.field_name,w.domain_name,
             COALESCE((SELECT json_group_array(institution_unit_id) FROM (
               SELECT DISTINCT institution_unit_id FROM raw_affiliation_units r
               WHERE r.professor_uid=w.professor_uid AND r.work_id=w.work_id
                 AND institution_unit_id IS NOT NULL ORDER BY institution_unit_id
             )),'[]'),
             COALESCE((SELECT json_group_array(institution_name) FROM (
               SELECT DISTINCT institution_name FROM raw_affiliation_units r
               WHERE r.professor_uid=w.professor_uid AND r.work_id=w.work_id
                 AND institution_name IS NOT NULL ORDER BY institution_name
             )),'[]'),
             COALESCE((SELECT json_group_array(raw_affiliation_string) FROM (
               SELECT DISTINCT raw_affiliation_string FROM raw_affiliation_units r
               WHERE r.professor_uid=w.professor_uid AND r.work_id=w.work_id
                 AND raw_affiliation_string IS NOT NULL ORDER BY raw_affiliation_string
             )),'[]'),
             d.reasons_json,w.source_url
      FROM work_authorship_evidence w
      JOIN koad.work_decision d USING(professor_uid,work_id)
      WHERE d.recommended_action='drop'
    """)
    connection.execute("""
      UPDATE work_authorship_evidence AS w
      SET identity_decision='duplicate_drop_candidate',duplicate_candidate_score=100,
          duplicate_candidate_severity='high',
          decision_reasons_json=(SELECT d.reasons_json FROM koad.work_decision d
            WHERE d.professor_uid=w.professor_uid AND d.work_id=w.work_id)
      WHERE EXISTS(SELECT 1 FROM koad.work_decision d
        WHERE d.professor_uid=w.professor_uid AND d.work_id=w.work_id
          AND d.recommended_action='drop')
    """)
    connection.execute("""
      UPDATE raw_affiliation_units AS r
      SET identity_decision='duplicate_drop_candidate'
      WHERE EXISTS(SELECT 1 FROM koad.work_decision d
        WHERE d.professor_uid=r.professor_uid AND d.work_id=r.work_id
          AND d.recommended_action='drop')
    """)

    # Cached half-year periods must not retain work IDs that were soft-dropped.
    keep_dates = {
        (row["professor_uid"], row["work_id"]): row["publication_date"]
        for row in connection.execute(
            "SELECT professor_uid,work_id,publication_date FROM work_authorship_evidence "
            "WHERE identity_decision='keep'"
        )
    }
    period_updates: list[tuple[int, str | None, str | None, str, int]] = []
    period_deletes: list[tuple[int]] = []
    for row in connection.execute(
        "SELECT affiliation_period_id,professor_uid,source_work_ids_json FROM affiliation_periods"
    ):
        source_ids = json.loads(row["source_work_ids_json"] or "[]")
        remaining = sorted({
            work_id for work_id in source_ids
            if (row["professor_uid"], work_id) in keep_dates
        })
        if not remaining:
            period_deletes.append((row["affiliation_period_id"],))
            continue
        dates = sorted(filter(None, (
            keep_dates[(row["professor_uid"], work_id)] for work_id in remaining
        )))
        period_updates.append((
            len(remaining), dates[0] if dates else None, dates[-1] if dates else None,
            stable_json(remaining), row["affiliation_period_id"],
        ))
    connection.executemany(
        "UPDATE affiliation_periods SET work_count=?,first_publication_date=?,"
        "last_publication_date=?,source_work_ids_json=? WHERE affiliation_period_id=?",
        period_updates,
    )
    connection.executemany(
        "DELETE FROM affiliation_periods WHERE affiliation_period_id=?", period_deletes
    )
    connection.commit()
    changed = connection.execute("SELECT COUNT(*) FROM koad_application_audit").fetchone()[0]
    counts = dict(connection.execute(
        "SELECT identity_decision,COUNT(*) FROM work_authorship_evidence GROUP BY identity_decision"
    ).fetchall())
    candidate_count = connection.execute(
        "SELECT COUNT(*) FROM duplicate_drop_candidate"
    ).fetchone()[0]
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    foreign_key_errors = len(connection.execute("PRAGMA foreign_key_check").fetchall())
    connection.execute("DETACH DATABASE koad")
    connection.close()
    return {
        "output_database": str(output_database.resolve()),
        "changed_work_decisions": changed,
        "identity_decision_counts": counts,
        "duplicate_drop_candidate_count": candidate_count,
        "affiliation_periods_updated": len(period_updates),
        "affiliation_periods_deleted": len(period_deletes),
        "integrity_check": integrity,
        "foreign_key_errors": foreign_key_errors,
    }
