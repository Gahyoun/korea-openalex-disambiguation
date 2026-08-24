from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .model import stable_json


DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["true_paper", "namesake_paper"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "reasons": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
    },
    "required": ["decision", "confidence", "reasons"],
    "additionalProperties": False,
}


def extract_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict) and "decision" in value:
        return value
    if isinstance(value, dict):
        for key in ("structured_output", "result", "response"):
            nested = value.get(key)
            if isinstance(nested, dict):
                try:
                    return extract_json(nested)
                except ValueError:
                    pass
            if isinstance(nested, str):
                try:
                    return extract_json(json.loads(nested.strip()))
                except (json.JSONDecodeError, ValueError):
                    pass
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:].strip()
        try:
            return extract_json(json.loads(text))
        except json.JSONDecodeError as error:
            raise ValueError("provider did not return parseable JSON") from error
    raise ValueError("provider response did not contain a decision object")


def validate_decision(value: dict[str, Any]) -> dict[str, Any]:
    if value.get("decision") not in {"true_paper", "namesake_paper"}:
        raise ValueError("invalid decision")
    if value.get("confidence") not in {"high", "medium", "low"}:
        raise ValueError("invalid confidence")
    reasons = value.get("reasons")
    if not isinstance(reasons, list) or not all(isinstance(item, str) for item in reasons):
        raise ValueError("invalid reasons")
    return {
        "decision": value["decision"],
        "confidence": value["confidence"],
        "reasons": reasons[:5],
    }


@dataclass(slots=True)
class Provider:
    name: str
    executable: str

    def available(self) -> bool:
        return shutil.which(self.executable) is not None

    def command(
        self, prompt: str, model: str | None, max_budget_usd: float
    ) -> list[str]:
        schema = stable_json(DECISION_SCHEMA)
        if self.name == "claude":
            command = [
                self.executable, "-p", prompt, "--output-format", "json",
                "--json-schema", schema, "--tools", "", "--permission-mode", "plan",
                "--no-session-persistence", "--max-turns", "1",
                "--max-budget-usd", str(max_budget_usd),
            ]
        elif self.name == "gemini":
            command = [self.executable, "-p", prompt, "--output-format", "json"]
        elif self.name == "antigravity":
            command = [
                self.executable, "-p", prompt, "--output-format", "json",
                "--json-schema", schema, "--sandbox", "--print-timeout", "5m",
            ]
        else:
            raise ValueError(f"unsupported provider: {self.name}")
        if model:
            command.extend(["--model", model])
        return command


PROVIDERS = {
    "claude": Provider("claude", "claude"),
    "gemini": Provider("gemini", "gemini"),
    "antigravity": Provider("antigravity", "agy"),
}


def prompt_for(payload: dict[str, Any]) -> str:
    return (
        "You are adjudicating whether a candidate publication belongs to the same researcher as "
        "a verified doctoral-debut cluster. Use only the supplied evidence. ORCID alone is not "
        "conclusive because an OpenAlex author profile may be merged. Interdisciplinary work is valid "
        "when it has a coauthor, institution, or graph path to the verified cluster. Sabbatical and "
        "concurrent affiliations are valid when identity continuity exists. Return only the JSON object "
        "required by the schema. Evidence:\n" + stable_json(payload)
    )


def review_payloads(database: sqlite3.Connection) -> Iterable[dict[str, Any]]:
    database.row_factory = sqlite3.Row
    summaries = {
        row["professor_uid"]: row for row in database.execute("SELECT * FROM professor_summary")
    }
    seed_titles: dict[str, list[dict[str, Any]]] = {}
    for uid in summaries:
        seed_titles[uid] = [
            {
                "year": row["publication_year"], "title": row["title"],
                "topic": row["topic_name"], "field": row["field_name"],
            }
            for row in database.execute(
                """SELECT publication_year,title,topic_name,field_name FROM work_decision
                   WHERE professor_uid=? AND is_seed=1 ORDER BY publication_year,work_id LIMIT 8""",
                (uid,),
            )
        ]
    for row in database.execute(
        """SELECT * FROM work_decision WHERE recommended_action='review'
           ORDER BY professor_token,publication_year,work_id"""
    ):
        summary = summaries[row["professor_uid"]]
        yield {
            "work_id": row["work_id"],
            "professor_token": row["professor_token"],
            "researcher_context": {
                "subject": summary["subject"], "phd_year": summary["phd_year"],
                "doctoral_start_year": summary["doctoral_start_year"],
                "debut_year": summary["debut_year"], "verified_debut_papers": seed_titles[row["professor_uid"]],
            },
            "candidate": {
                "year": row["publication_year"], "title": row["title"],
                "topic": row["topic_name"], "field": row["field_name"],
                "domain": row["domain_name"],
            },
            "deterministic_evidence": {
                "core_coauthor_connected": bool(row["core_coauthor_connected"]),
                "institution_connected": bool(row["institution_connected"]),
                "department_compatible": (
                    None if row["department_compatible"] is None else bool(row["department_compatible"])
                ),
                "topic_similarity": row["topic_similarity"],
                "semantic_families": json.loads(row["semantic_families_json"]),
                "semantic_bridge_connected": bool(row["semantic_bridge_connected"]),
                "graph_path_connected": bool(row["graph_path_connected"]),
                "orcid_exact_nonabsolute": bool(row["orcid_exact"]),
                "orcid_only": bool(row["orcid_only"]),
                "stage1_decision": row["decision"],
                "stage1_confidence": row["confidence"],
                "stage1_reasons": json.loads(row["reasons_json"]),
            },
        }


def adjudicate(
    decision_database: Path,
    provider_name: str,
    output_path: Path,
    limit: int | None,
    model: str | None,
    max_budget_usd: float,
    dry_run: bool,
) -> dict[str, Any]:
    provider = PROVIDERS[provider_name]
    if not dry_run and not provider.available():
        raise SystemExit(
            f"{provider.executable} is not installed; use --dry-run or install/authenticate it first"
        )
    connection = sqlite3.connect(f"file:{decision_database.resolve()}?mode=ro", uri=True)
    payloads = review_payloads(connection)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    errors = 0
    with output_path.open("w", encoding="utf-8") as stream:
        for index, payload in enumerate(payloads):
            if limit is not None and index >= limit:
                break
            prompt = prompt_for(payload)
            record: dict[str, Any] = {
                "provider": provider_name, "model": model,
                "adjudicated_at": datetime.now(timezone.utc).isoformat(),
                "work_id": payload["work_id"],
                "professor_token": payload["professor_token"],
                "input_sha256": __import__("hashlib").sha256(prompt.encode()).hexdigest(),
            }
            if dry_run:
                record["dry_run"] = True
                record["command"] = provider.command("<PROMPT>", model, max_budget_usd)
                record["payload"] = payload
            else:
                try:
                    process = subprocess.run(
                        provider.command(prompt, model, max_budget_usd),
                        capture_output=True, text=True, timeout=360, check=False,
                    )
                    record["exit_code"] = process.returncode
                    if process.returncode != 0:
                        raise RuntimeError(process.stderr.strip() or "provider failed")
                    record.update(validate_decision(extract_json(json.loads(process.stdout))))
                except Exception as error:  # keep the batch reproducible and resumable
                    errors += 1
                    record["error"] = str(error)
            stream.write(stable_json(record) + "\n")
            written += 1
    connection.close()
    return {"provider": provider_name, "written": written, "errors": errors, "dry_run": dry_run}
