import json
import shutil
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from control_tower.canonical import (
    CanonicalizationPolicy,
    CanonicalRepository,
    canonicalize,
)
from control_tower.close_control import ClosePolicy, calculate_close
from control_tower.exceptions import (
    ExceptionPolicy,
    ExceptionRepository,
    create_exceptions,
)
from control_tower.generator import generate
from control_tower.ingestion import IngestionRegistry, ingest_generated_feeds
from control_tower.reconciliation import (
    ReconciliationOutcome,
    ReconciliationPolicy,
    ReconciliationRepository,
    run_reconciliation,
)
from control_tower.reconciliation.evaluation import TRUTH_OUTCOMES

MATCHED = {ReconciliationOutcome.EXACT_MATCH, ReconciliationOutcome.COMPOSITE_MATCH}
FIXED_TIME = datetime(2000, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class EvaluationConfig:
    development_seed: int
    fresh_seed: int
    output_root: Path
    generator_config: Path
    canonicalization_config: Path
    reconciliation_config: Path
    exceptions_config: Path
    close_config: Path

    @classmethod
    def load(cls, path: Path) -> "EvaluationConfig":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            int(raw["development_seed"]),
            int(raw["fresh_seed"]),
            Path(raw["output_root"]),
            Path(raw["generator_config"]),
            Path(raw["canonicalization_config"]),
            Path(raw["reconciliation_config"]),
            Path(raw["exceptions_config"]),
            Path(raw["close_config"]),
        )


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _write(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _run(name: str, seed: int, root: Path, config: EvaluationConfig) -> dict[str, Any]:
    workspace = root / name
    if workspace.exists():
        shutil.rmtree(workspace)
    feeds = workspace / "generated"
    evidence = workspace / "evidence"
    workspace.mkdir(parents=True)

    generated = generate(config.generator_config, feeds, seed=seed)
    ingested = ingest_generated_feeds(generated.output_dir, evidence)
    ingestion = IngestionRegistry.local(evidence)
    canonical = CanonicalRepository(ingestion.engine)
    canonicalized = canonicalize(
        ingestion,
        CanonicalizationPolicy.load(config.canonicalization_config),
        canonical,
    )
    reconciliation = ReconciliationRepository(ingestion.engine)
    run = run_reconciliation(
        canonical,
        ReconciliationPolicy.load(config.reconciliation_config),
        reconciliation,
    )
    # Truth remains unopened until the persisted reconciliation run is complete.
    truth = [
        json.loads(line)
        for line in (generated.output_dir / "truth/classifications.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    decisions = {
        item.business_event_id: item
        for item in reconciliation.decisions_for_run(run.run_key)
        if not item.business_event_id.startswith("orphan:")
    }

    expected_counts: Counter[str] = Counter()
    actual_counts: Counter[str] = Counter()
    actual_values: Counter[str] = Counter()
    confusion: dict[str, Counter[str]] = {}
    failures = []
    false_count = false_value = correct = 0
    for record in truth:
        event_id = record["business_event_id"]
        expected = TRUTH_OUTCOMES[record["expected_classification"]].value
        decision = decisions.get(event_id)
        actual = decision.outcome.value if decision else "MISSING_DECISION"
        expected_counts[expected] += 1
        confusion.setdefault(expected, Counter())[actual] += 1
        if actual == expected:
            correct += 1
        else:
            failures.append(
                {
                    "actual": actual,
                    "amount_paise": record["expected_amount_paise"],
                    "business_event_id": event_id,
                    "expected": expected,
                }
            )
            if decision and decision.outcome in MATCHED:
                false_count += 1
                false_value += decision.amount_paise

    persisted = reconciliation.decisions_for_run(run.run_key)
    for decision in persisted:
        actual_counts[decision.outcome.value] += 1
        actual_values[decision.outcome.value] += decision.amount_paise
    exact_count = actual_counts[ReconciliationOutcome.EXACT_MATCH.value]
    composite_count = actual_counts[ReconciliationOutcome.COMPOSITE_MATCH.value]
    exact_value = actual_values[ReconciliationOutcome.EXACT_MATCH.value]
    composite_value = actual_values[ReconciliationOutcome.COMPOSITE_MATCH.value]
    instruction_value = sum(record["expected_amount_paise"] for record in truth)

    exceptions = ExceptionRepository(ingestion.engine)
    exception_sync = create_exceptions(
        run.run_key,
        reconciliation,
        exceptions,
        detected_at=FIXED_TIME,
        policy=ExceptionPolicy.load(config.exceptions_config),
    )
    blocking = [
        item
        for item in persisted
        if item.outcome not in MATCHED | {ReconciliationOutcome.TIMING_DIFFERENCE}
    ]
    queued = []
    offset = 0
    while page := exceptions.list(limit=200, offset=offset):
        queued.extend(page)
        offset += len(page)
    control_totals = []
    for artifact in sorted(ingested.artifacts, key=lambda item: item.source):
        expected = generated.quality_report["sources"][artifact.source]
        control_totals.append(
            {
                "count_difference": artifact.row_count - expected["row_count"],
                "source": artifact.source,
                "value_difference_paise": artifact.total_amount_paise
                - expected["total_amount_paise"],
            }
        )

    close = calculate_close(
        run.run_key,
        ingested.run_key,
        "phase1-evaluation",
        reconciliation,
        ingestion,
        policy=ClosePolicy.load(config.close_config),
        decided_at=FIXED_TIME,
        exception_repository=exceptions,
    )
    unresolved = [
        {
            "amount_paise": item.amount_paise,
            "business_event_id": item.business_event_id,
            "outcome": item.outcome.value,
            "reason": item.reason,
        }
        for item in persisted
        if item.outcome not in MATCHED | {ReconciliationOutcome.TIMING_DIFFERENCE}
    ]
    scorecard = {
        "scenario": name,
        "seed": seed,
        "instruction_count": len(truth),
        "correct_count": correct,
        "correct_rate": _rate(correct, len(truth)),
        "exact_match": {
            "count": exact_count,
            "value_paise": exact_value,
            "rate": _rate(exact_count, len(truth)),
            "value_rate": _rate(exact_value, instruction_value),
        },
        "composite_match": {
            "count": composite_count,
            "value_paise": composite_value,
            "rate": _rate(composite_count, len(truth)),
            "value_rate": _rate(composite_value, instruction_value),
        },
        "false_matches": {
            "count": false_count,
            "count_rate": _rate(false_count, len(truth)),
            "exposure_paise": false_value,
            "exposure_rate": _rate(false_value, instruction_value),
        },
        "straight_through_rate": _rate(exact_count + composite_count, len(truth)),
        "exception_coverage": {
            "eligible_count": len(blocking),
            "eligible_value_paise": sum(item.amount_paise for item in blocking),
            "queued_count": len(queued),
            "queued_value_paise": sum(item.amount_paise for item in queued),
            "count_rate": _rate(len(queued), len(blocking)),
            "value_rate": _rate(
                sum(item.amount_paise for item in queued),
                sum(item.amount_paise for item in blocking),
            ),
        },
        "control_totals": {
            "count_difference": sum(
                item["count_difference"] for item in control_totals
            ),
            "value_difference_paise": sum(
                item["value_difference_paise"] for item in control_totals
            ),
            "sources": control_totals,
        },
        "confusion_matrix": {
            expected: dict(sorted(actual.items()))
            for expected, actual in sorted(confusion.items())
        },
        "outcome_counts": dict(sorted(actual_counts.items())),
        "outcome_values_paise": dict(sorted(actual_values.items())),
        "unresolved": unresolved,
        "failed_cases": failures,
        "close": {"outcome": close.outcome.value, **close.scorecard.__dict__},
        "pipeline": {
            "ingested_count": ingested.accepted_row_count,
            "canonicalized_count": canonicalized.created_count,
            "reconciliation_decision_count": run.decision_count,
            "exceptions_created_count": exception_sync.created_count,
        },
    }
    _write(workspace / "scorecard.json", scorecard)
    return scorecard


def evaluate_phase_one(config_path: Path, output: Path | None = None) -> dict[str, Any]:
    config = EvaluationConfig.load(config_path)
    root = output or config.output_root
    root.mkdir(parents=True, exist_ok=True)
    scorecards = [
        _run("development", config.development_seed, root, config),
        _run("fresh-seed", config.fresh_seed, root, config),
    ]
    instruction_count = sum(item["instruction_count"] for item in scorecards)
    instruction_value = sum(
        item["close"]["accepted_value_paise"] for item in scorecards
    )
    exact_count = sum(item["exact_match"]["count"] for item in scorecards)
    exact_value = sum(item["exact_match"]["value_paise"] for item in scorecards)
    composite_count = sum(item["composite_match"]["count"] for item in scorecards)
    composite_value = sum(item["composite_match"]["value_paise"] for item in scorecards)
    eligible_count = sum(
        item["exception_coverage"]["eligible_count"] for item in scorecards
    )
    eligible_value = sum(
        item["exception_coverage"]["eligible_value_paise"] for item in scorecards
    )
    queued_count = sum(
        item["exception_coverage"]["queued_count"] for item in scorecards
    )
    queued_value = sum(
        item["exception_coverage"]["queued_value_paise"] for item in scorecards
    )
    false_match_count = sum(item["false_matches"]["count"] for item in scorecards)
    control_count_difference = sum(
        item["control_totals"]["count_difference"] for item in scorecards
    )
    control_value_difference = sum(
        item["control_totals"]["value_difference_paise"] for item in scorecards
    )
    passed = (
        all(not item["failed_cases"] for item in scorecards)
        and false_match_count == 0
        and queued_count == eligible_count
        and queued_value == eligible_value
        and control_count_difference == 0
        and control_value_difference == 0
    )
    summary = {
        "passed": passed,
        "scenario_count": len(scorecards),
        "instruction_count": instruction_count,
        "correct_count": sum(item["correct_count"] for item in scorecards),
        "exact_match": {
            "count": exact_count,
            "value_paise": exact_value,
            "rate": _rate(exact_count, instruction_count),
            "value_rate": _rate(exact_value, instruction_value),
        },
        "composite_match": {
            "count": composite_count,
            "value_paise": composite_value,
            "rate": _rate(composite_count, instruction_count),
            "value_rate": _rate(composite_value, instruction_value),
        },
        "straight_through_rate": _rate(
            exact_count + composite_count, instruction_count
        ),
        "false_match_count": false_match_count,
        "false_match_exposure_paise": sum(
            item["false_matches"]["exposure_paise"] for item in scorecards
        ),
        "failed_case_count": sum(len(item["failed_cases"]) for item in scorecards),
        "exception_coverage": {
            "eligible_count": eligible_count,
            "eligible_value_paise": eligible_value,
            "queued_count": queued_count,
            "queued_value_paise": queued_value,
            "count_rate": _rate(queued_count, eligible_count),
            "value_rate": _rate(queued_value, eligible_value),
        },
        "control_total_count_difference": control_count_difference,
        "control_total_value_difference_paise": control_value_difference,
        "scorecards": [f"{item['scenario']}/scorecard.json" for item in scorecards],
    }
    _write(root / "summary.json", summary)
    return summary
