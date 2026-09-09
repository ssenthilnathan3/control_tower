import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .models import ReconciliationDecision, ReconciliationOutcome

TRUTH_OUTCOMES = {
    "none": ReconciliationOutcome.EXACT_MATCH,
    "composite_match": ReconciliationOutcome.COMPOSITE_MATCH,
    "timing_difference": ReconciliationOutcome.TIMING_DIFFERENCE,
    "duplicate_event": ReconciliationOutcome.DUPLICATE_EVENT,
    "missing_event": ReconciliationOutcome.MISSING_EVENT,
    "amount_mismatch": ReconciliationOutcome.AMOUNT_MISMATCH,
    "status_mismatch": ReconciliationOutcome.STATUS_MISMATCH,
}


@dataclass(frozen=True)
class EvaluationFailure:
    business_event_id: str
    expected: ReconciliationOutcome
    actual: ReconciliationOutcome | None
    amount_paise: int


@dataclass(frozen=True)
class ReconciliationScorecard:
    instruction_count: int
    correct_count: int
    exact_match_count: int
    exact_match_value_paise: int
    composite_match_count: int
    composite_match_value_paise: int
    false_match_count: int
    false_match_value_paise: int
    straight_through_rate: float
    failures: tuple[EvaluationFailure, ...]


def evaluate(
    decisions: list[ReconciliationDecision], truth_path: Path
) -> ReconciliationScorecard:
    truth = [json.loads(line) for line in truth_path.read_text().splitlines()]
    by_business_event = {
        decision.business_event_id: decision
        for decision in decisions
        if not decision.business_event_id.startswith("orphan:")
    }
    failures: list[EvaluationFailure] = []
    correct = 0
    false_match_count = 0
    false_match_value = 0
    for expected_record in truth:
        expected = TRUTH_OUTCOMES[expected_record["expected_classification"]]
        actual = by_business_event.get(expected_record["business_event_id"])
        if actual is not None and actual.outcome is expected:
            correct += 1
            continue
        failures.append(
            EvaluationFailure(
                expected_record["business_event_id"],
                expected,
                actual.outcome if actual else None,
                expected_record["expected_amount_paise"],
            )
        )
        if actual and actual.outcome in {
            ReconciliationOutcome.EXACT_MATCH,
            ReconciliationOutcome.COMPOSITE_MATCH,
        }:
            false_match_count += 1
            false_match_value += actual.amount_paise

    counts = Counter(decision.outcome for decision in decisions)
    values = Counter()
    for decision in decisions:
        values[decision.outcome] += decision.amount_paise
    straight_through = (
        counts[ReconciliationOutcome.EXACT_MATCH]
        + counts[ReconciliationOutcome.COMPOSITE_MATCH]
    ) / len(truth)
    return ReconciliationScorecard(
        instruction_count=len(truth),
        correct_count=correct,
        exact_match_count=counts[ReconciliationOutcome.EXACT_MATCH],
        exact_match_value_paise=values[ReconciliationOutcome.EXACT_MATCH],
        composite_match_count=counts[ReconciliationOutcome.COMPOSITE_MATCH],
        composite_match_value_paise=values[ReconciliationOutcome.COMPOSITE_MATCH],
        false_match_count=false_match_count,
        false_match_value_paise=false_match_value,
        straight_through_rate=straight_through,
        failures=tuple(failures),
    )
