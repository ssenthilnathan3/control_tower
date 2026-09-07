from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from .contracts import ANOMALIES


def _timezone(value: str) -> timezone:
    sign = 1 if value[0] == "+" else -1
    hours, minutes = (int(part) for part in value[1:].split(":"))
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def _business_dates(start: date, count: int) -> list[date]:
    result: list[date] = []
    current = start
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current)
        current += timedelta(days=1)
    return result


@dataclass(frozen=True)
class GeneratorConfig:
    version: str
    seed: int
    instruction_count: int
    business_dates: list[date]
    timezone: timezone
    cutoff_time: time
    grace: timedelta
    currency: str
    partners: list[str]
    amount_min_paise: int
    amount_max_paise: int
    anomaly_counts: dict[str, int]

    @classmethod
    def load(cls, path: Path, seed_override: int | None = None) -> GeneratorConfig:
        raw = json.loads(path.read_text(encoding="utf-8"))
        count = int(raw["instruction_count"])
        if count < 1000:
            raise ValueError("instruction_count must be at least 1000")
        if set(raw["anomaly_rates"]) != set(ANOMALIES):
            raise ValueError(
                f"anomaly_rates must define exactly: {', '.join(ANOMALIES)}"
            )
        anomaly_counts = {
            name: int(count * Decimal(str(raw["anomaly_rates"][name])))
            for name in ANOMALIES
        }
        if sum(anomaly_counts.values()) > count:
            raise ValueError(
                "configured anomaly populations cannot exceed instruction_count"
            )
        partners = list(raw["partners"])
        if len(partners) < 3:
            raise ValueError("at least three partners are required")
        return cls(
            version=raw["generator_version"],
            seed=raw["seed"] if seed_override is None else seed_override,
            instruction_count=count,
            business_dates=_business_dates(
                date.fromisoformat(raw["start_business_date"]),
                int(raw["business_days"]),
            ),
            timezone=_timezone(raw["timezone"]),
            cutoff_time=time.fromisoformat(raw["cutoff_time"]),
            grace=timedelta(minutes=int(raw["grace_minutes"])),
            currency=raw["currency"],
            partners=partners,
            amount_min_paise=int(raw["amount_min_paise"]),
            amount_max_paise=int(raw["amount_max_paise"]),
            anomaly_counts=anomaly_counts,
        )
