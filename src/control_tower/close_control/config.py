import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ClosePolicy:
    policy_version: str
    max_pending_value_paise: int
    max_unresolved_value_paise: int
    block_on_quarantine: bool
    block_on_control_failure: bool

    @classmethod
    def load(cls, path: Path) -> "ClosePolicy":
        data = json.loads(path.read_text(encoding="utf-8"))
        policy = cls(
            str(data["policy_version"]),
            int(data["max_pending_value_paise"]),
            int(data["max_unresolved_value_paise"]),
            bool(data["block_on_quarantine"]),
            bool(data["block_on_control_failure"]),
        )
        if not policy.policy_version.strip():
            raise ValueError("close policy version is required")
        if policy.max_pending_value_paise < 0:
            raise ValueError("pending threshold cannot be negative")
        if policy.max_unresolved_value_paise < 0:
            raise ValueError("unresolved threshold cannot be negative")
        return policy

    def canonical(self) -> dict[str, object]:
        return {
            "block_on_control_failure": self.block_on_control_failure,
            "block_on_quarantine": self.block_on_quarantine,
            "max_pending_value_paise": self.max_pending_value_paise,
            "max_unresolved_value_paise": self.max_unresolved_value_paise,
            "policy_version": self.policy_version,
        }
