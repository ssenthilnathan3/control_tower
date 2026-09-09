import json
from dataclasses import dataclass
from pathlib import Path

from control_tower.reconciliation import ReconciliationOutcome


@dataclass(frozen=True)
class ExceptionClassPolicy:
    owner: str
    recommended_action: str
    escalation_path: str
    sla_hours: int


@dataclass(frozen=True)
class ExceptionPolicy:
    p1_threshold_paise: int
    p2_threshold_paise: int
    classes: dict[ReconciliationOutcome, ExceptionClassPolicy]

    @classmethod
    def load(cls, path: Path) -> "ExceptionPolicy":
        raw = json.loads(path.read_text(encoding="utf-8"))
        thresholds = raw["priority_thresholds_paise"]
        classes = {
            ReconciliationOutcome(name): ExceptionClassPolicy(
                value["owner"],
                value["recommended_action"],
                value["escalation_path"],
                int(value["sla_hours"]),
            )
            for name, value in raw["classes"].items()
        }
        policy = cls(int(thresholds["P1"]), int(thresholds["P2"]), classes)
        if policy.p1_threshold_paise <= policy.p2_threshold_paise:
            raise ValueError("P1 threshold must be greater than P2 threshold")
        if any(value.sla_hours <= 0 for value in classes.values()):
            raise ValueError("SLA hours must be positive")
        return policy

    def priority(self, amount_paise: int) -> str:
        if amount_paise >= self.p1_threshold_paise:
            return "P1"
        if amount_paise >= self.p2_threshold_paise:
            return "P2"
        return "P3"
