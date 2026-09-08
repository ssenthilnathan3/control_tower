import csv
import hashlib
import json
import random
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any


def token(rng: random.Random, length: int = 12) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(rng.choice(alphabet) for _ in range(length))


def iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def write_csv(path: Path, fields: Iterable[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_summary(rows: list[dict[str, Any]], amount_field: str) -> dict[str, int]:
    return {
        "row_count": len(rows),
        "total_amount_paise": sum(int(row[amount_field]) for row in rows),
    }
