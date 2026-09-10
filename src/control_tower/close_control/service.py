import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from control_tower.ingestion.registry import IngestionRegistry
from control_tower.reconciliation import (
    ReconciliationOutcome,
    ReconciliationRepository,
)

from .config import ClosePolicy
from .models import BlockerType, CloseBlocker, CloseOutcome, CloseScorecard
from .repository import CloseControlRepository

MATCHED = {
    ReconciliationOutcome.EXACT_MATCH,
    ReconciliationOutcome.COMPOSITE_MATCH,
}
PENDING = {ReconciliationOutcome.TIMING_DIFFERENCE}


def _hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def calculate_close(
    run_key: str,
    ingestion_run_key: str,
    actor: str,
    reconciliation_repository: ReconciliationRepository,
    ingestion_registry: IngestionRegistry,
    close_repository: CloseControlRepository | None = None,
    policy: ClosePolicy | None = None,
    decided_at: datetime | None = None,
):
    if not actor.strip():
        raise ValueError("close actor is required")
    policy = policy or ClosePolicy.load(Path("config/close_control.json"))
    close_repository = close_repository or CloseControlRepository(
        reconciliation_repository.engine
    )
    run = reconciliation_repository.run(run_key)
    decisions = reconciliation_repository.decisions_for_run(run_key)
    ingestion_run = ingestion_registry.ingestion_run(ingestion_run_key)
    if ingestion_run.status not in {"COMPLETED", "FAILED"}:
        raise ValueError("ingestion run is not complete")
    control_ids = ingestion_run.control_ids
    controls = ingestion_registry.delivery_controls(control_ids)
    source_version_ids = tuple(
        sorted({item for decision in decisions for item in decision.source_version_ids})
    )
    used_artifacts = set(
        ingestion_registry.artifact_hashes_for_versions(source_version_ids)
    )
    passed_artifacts = {
        control.artifact_hash for control in controls if control.status == "PASSED"
    }
    missing_controls = used_artifacts - passed_artifacts
    if missing_controls:
        raise ValueError("selected controls do not cover the reconciliation evidence")
    selected_artifacts = tuple(sorted({control.artifact_hash for control in controls}))
    quarantines = ingestion_registry.quarantines_for_artifacts(selected_artifacts)

    matched = [item for item in decisions if item.outcome in MATCHED]
    pending = [item for item in decisions if item.outcome in PENDING]
    unresolved = [item for item in decisions if item.outcome not in MATCHED | PENDING]
    scorecard = CloseScorecard(
        accepted_count=len(decisions),
        accepted_value_paise=sum(item.amount_paise for item in decisions),
        matched_count=len(matched),
        matched_value_paise=sum(item.amount_paise for item in matched),
        pending_count=len(pending),
        pending_value_paise=sum(item.amount_paise for item in pending),
        unresolved_count=len(unresolved),
        unresolved_value_paise=sum(item.amount_paise for item in unresolved),
        quarantined_count=len(quarantines),
        control_failure_count=sum(control.status == "FAILED" for control in controls),
    )
    if scorecard.accepted_count != (
        scorecard.matched_count + scorecard.pending_count + scorecard.unresolved_count
    ) or scorecard.accepted_value_paise != (
        scorecard.matched_value_paise
        + scorecard.pending_value_paise
        + scorecard.unresolved_value_paise
    ):
        raise ValueError("close scorecard does not account for every accepted decision")

    blockers: list[CloseBlocker] = []
    if scorecard.unresolved_value_paise > policy.max_unresolved_value_paise:
        blockers.extend(
            CloseBlocker(
                BlockerType.UNRESOLVED,
                f"decision:{decision.decision_id}",
                decision.amount_paise,
                decision.reason,
                "source-versions:" + ",".join(map(str, decision.source_version_ids)),
            )
            for decision in unresolved
        )
    if scorecard.pending_value_paise > policy.max_pending_value_paise:
        blockers.extend(
            CloseBlocker(
                BlockerType.PENDING,
                f"decision:{decision.decision_id}",
                decision.amount_paise,
                decision.reason,
                "source-versions:" + ",".join(map(str, decision.source_version_ids)),
            )
            for decision in pending
        )
    if policy.block_on_quarantine:
        blockers.extend(
            CloseBlocker(
                BlockerType.QUARANTINE,
                f"source-version:{item.source_version_id}",
                0,
                "source row failed validation; amount may be unknown",
                item.source_location,
            )
            for item in quarantines
        )
    if policy.block_on_control_failure:
        blockers.extend(
            CloseBlocker(
                BlockerType.CONTROL_FAILURE,
                f"delivery-control:{control.control_id}",
                control.total_amount_paise or 0,
                control.failure_reason or "delivery control failed",
                control.evidence_path,
            )
            for control in controls
            if control.status == "FAILED"
        )
    blockers.sort(key=lambda item: (item.blocker_type.value, item.reference))
    policy_hash = _hash(policy.canonical())
    decision_hash = _hash(
        {
            "blockers": [
                {
                    "amount_paise": item.amount_paise,
                    "evidence_reference": item.evidence_reference,
                    "reason": item.reason,
                    "reference": item.reference,
                    "type": item.blocker_type.value,
                }
                for item in blockers
            ],
            "control_ids": sorted(set(control_ids)),
            "ingestion_run_key": ingestion_run_key,
            "policy_hash": policy_hash,
            "reconciliation_run_key": run.run_key,
            "snapshot_hash": run.snapshot_hash,
            "scorecard": scorecard.__dict__,
        }
    )
    return close_repository.save(
        decision_hash,
        run.run_key,
        run.snapshot_hash,
        policy.policy_version,
        policy_hash,
        actor,
        CloseOutcome.HOLD if blockers else CloseOutcome.CLOSE,
        scorecard,
        tuple(blockers),
        decided_at or datetime.now(timezone.utc),
    )
