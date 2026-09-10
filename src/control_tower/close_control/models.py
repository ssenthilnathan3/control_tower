from dataclasses import dataclass
from enum import Enum


class CloseOutcome(str, Enum):
    CLOSE = "CLOSE"
    HOLD = "HOLD"


class CloseWriteOutcome(str, Enum):
    CREATED = "CREATED"
    REPLAY = "REPLAY"


class BlockerType(str, Enum):
    UNRESOLVED = "UNRESOLVED"
    PENDING = "PENDING"
    QUARANTINE = "QUARANTINE"
    CONTROL_FAILURE = "CONTROL_FAILURE"


@dataclass(frozen=True)
class CloseBlocker:
    blocker_type: BlockerType
    reference: str
    amount_paise: int
    reason: str
    evidence_reference: str


@dataclass(frozen=True)
class CloseScorecard:
    accepted_count: int
    accepted_value_paise: int
    matched_count: int
    matched_value_paise: int
    pending_count: int
    pending_value_paise: int
    unresolved_count: int
    unresolved_value_paise: int
    quarantined_count: int
    control_failure_count: int


@dataclass(frozen=True)
class CloseResult:
    decision_hash: str
    outcome: CloseOutcome
    write_outcome: CloseWriteOutcome
    reconciliation_run_key: str
    snapshot_hash: str
    policy_version: str
    actor: str
    scorecard: CloseScorecard
    blockers: tuple[CloseBlocker, ...]
