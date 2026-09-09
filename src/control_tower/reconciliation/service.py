import hashlib
import json
from collections import Counter
from dataclasses import dataclass

from control_tower.canonical import CanonicalRepository

from .matcher import reconcile
from .models import ReconciliationOutcome, ReconciliationPolicy
from .repository import ReconciliationRepository, ReconciliationWriteOutcome


@dataclass(frozen=True)
class ReconciliationResult:
    run_key: str
    write_outcome: ReconciliationWriteOutcome
    decision_count: int
    outcome_counts: dict[ReconciliationOutcome, int]


def _hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def run_reconciliation(
    canonical_repository: CanonicalRepository,
    policy: ReconciliationPolicy,
    repository: ReconciliationRepository | None = None,
) -> ReconciliationResult:
    events = canonical_repository.active_events()
    snapshot_hash = _hash(
        [
            (event.provenance.source_version_id, event.provenance.payload_hash)
            for event in events
        ]
    )
    config_hash = _hash(
        {"rule_version": policy.rule_version, "grace_minutes": policy.grace_minutes}
    )
    run_key = _hash(
        {
            "snapshot_hash": snapshot_hash,
            "config_hash": config_hash,
            "rule_version": policy.rule_version,
        }
    )
    decisions = reconcile(events, policy)
    accounted = {
        source_version_id
        for decision in decisions
        for source_version_id in decision.source_version_ids
    }
    expected = {event.provenance.source_version_id for event in events}
    if accounted != expected:
        raise ValueError(
            "reconciliation did not account for every active canonical record"
        )
    repository = repository or ReconciliationRepository(canonical_repository.engine)
    write_outcome = repository.save(
        run_key,
        snapshot_hash,
        config_hash,
        policy.rule_version,
        decisions,
    )
    return ReconciliationResult(
        run_key,
        write_outcome,
        len(decisions),
        dict(Counter(decision.outcome for decision in decisions)),
    )
