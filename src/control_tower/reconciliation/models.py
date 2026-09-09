import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ReconciliationOutcome(str, Enum):
    EXACT_MATCH = "EXACT_MATCH"
    COMPOSITE_MATCH = "COMPOSITE_MATCH"
    TIMING_DIFFERENCE = "TIMING_DIFFERENCE"
    DUPLICATE_EVENT = "DUPLICATE_EVENT"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    STATUS_MISMATCH = "STATUS_MISMATCH"
    MISSING_EVENT = "MISSING_EVENT"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True)
class ReconciliationPolicy:
    rule_version: str
    grace_minutes: int

    @classmethod
    def load(cls, path: Path) -> "ReconciliationPolicy":
        raw = json.loads(path.read_text(encoding="utf-8"))
        policy = cls(raw["rule_version"], int(raw["grace_minutes"]))
        if not policy.rule_version.strip():
            raise ValueError("rule_version is required")
        if policy.grace_minutes <= 0:
            raise ValueError("grace_minutes must be positive")
        return policy


@dataclass(frozen=True)
class ReconciliationDecision:
    business_event_id: str
    partner_code: str
    outcome: ReconciliationOutcome
    amount_paise: int
    reason: str
    rule_version: str
    source_version_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        if (
            not self.business_event_id
            or not self.partner_code
            or not self.reason
            or not self.rule_version
        ):
            raise ValueError(
                "business event, partner, reason, and rule version are required"
            )
        if self.amount_paise <= 0:
            raise ValueError("decision amount must be positive")
        if not self.source_version_ids:
            raise ValueError("a decision must reference source evidence")
        if len(set(self.source_version_ids)) != len(self.source_version_ids):
            raise ValueError("decision source evidence cannot contain duplicates")
