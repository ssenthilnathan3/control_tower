from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GeneratedData:
    output_dir: Path
    quality_report: dict[str, Any]


@dataclass
class Dataset:
    originator: list[dict[str, Any]]
    lms: list[dict[str, Any]]
    bank: list[dict[str, Any]]
    truth: list[dict[str, Any]]
    relationships: list[dict[str, Any]]

    @classmethod
    def empty(cls) -> "Dataset":
        return cls([], [], [], [], [])
