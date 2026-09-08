import json
from dataclasses import dataclass
from datetime import time, timedelta, timezone
from pathlib import Path


def _timezone(value: str) -> timezone:
    if len(value) != 6 or value[0] not in "+-" or value[3] != ":":
        raise ValueError("timezone must use +HH:MM or -HH:MM")
    sign = 1 if value[0] == "+" else -1
    hours, minutes = int(value[1:3]), int(value[4:6])
    if hours > 23 or minutes > 59:
        raise ValueError("timezone offset is out of range")
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


@dataclass(frozen=True)
class CanonicalizationPolicy:
    timezone: timezone
    cutoff_time: time

    @classmethod
    def load(cls, path: Path) -> "CanonicalizationPolicy":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(_timezone(raw["timezone"]), time.fromisoformat(raw["cutoff_time"]))
