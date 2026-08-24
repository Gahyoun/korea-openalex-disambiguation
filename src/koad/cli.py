from __future__ import annotations

import argparse
import json
from pathlib import Path

from .adjudicators import PROVIDERS, adjudicate
from .engine import run_classification
from .overlay import build_overlay


def main() -> None:
    parser = argparse.ArgumentParser(prog="koad")
    subparsers = parser.add_subparsers(dest="command", required=True)
    classify = subparsers.add_parser("classify", help="Run deterministic stage-1 classification")
    classify.add_argument("--db", type=Path, required=True)
    classify.add_argument("--output", type=Path, required=True)
    classify.add_argument(
        "--config", type=Path,
        default=Path(__file__).resolve().parents[2] / "config" / "default.json",
    )
    classify.add_argument("--professor-uid")
    classify.add_argument("--limit", type=int)
    classify.add_argument(
        "--with-works-only", action="store_true",
        help="When sampling with --limit, skip roster rows without publication evidence",
    )

    provider_status = subparsers.add_parser("providers", help="Show optional stage-2 CLI availability")

    review = subparsers.add_parser("adjudicate", help="Review ambiguous stage-1 decisions")
    review.add_argument("--decision-db", type=Path, required=True)
    review.add_argument("--provider", choices=sorted(PROVIDERS), required=True)
    review.add_argument("--output", type=Path, required=True)
    review.add_argument("--limit", type=int)
    review.add_argument("--model")
    review.add_argument("--max-budget-usd", type=float, default=0.05)
    review.add_argument("--dry-run", action="store_true")

    overlay = subparsers.add_parser(
        "build-overlay", help="Merge stage 1 and optional stage-2 decisions without mutating source data"
    )
    overlay.add_argument("--decision-db", type=Path, required=True)
    overlay.add_argument("--adjudication", type=Path, action="append", default=[])
    overlay.add_argument("--output", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "classify":
        result = run_classification(
            args.db, args.output, args.config, args.professor_uid, args.limit,
            args.with_works_only,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "providers":
        print(json.dumps({
            name: {"executable": provider.executable, "available": provider.available()}
            for name, provider in PROVIDERS.items()
        }, ensure_ascii=False, indent=2))
    elif args.command == "adjudicate":
        result = adjudicate(
            args.decision_db, args.provider, args.output, args.limit, args.model,
            args.max_budget_usd, args.dry_run,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "build-overlay":
        result = build_overlay(args.decision_db, args.adjudication, args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
