from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from control_tower.ingestion import IngestionRegistry

from .adapters import adapt
from .models import SourceProvenance
from .repository import CanonicalRepository, CanonicalWrite, CanonicalWriteOutcome


@dataclass(frozen=True)
class CanonicalizationResult:
    created_count: int
    replayed_count: int


def _read_evidence_row(
    source_location: str, cache: dict[Path, list[dict[str, str]]]
) -> dict[str, str]:
    path_value, separator, line_value = source_location.rpartition("#line=")
    if not separator:
        raise ValueError(f"invalid source location: {source_location}")
    line_number = int(line_value)
    if line_number < 2:
        raise ValueError(f"invalid source line: {line_number}")
    path = Path(path_value)
    if path not in cache:
        with path.open(encoding="utf-8", newline="") as source_file:
            cache[path] = list(csv.DictReader(source_file))
    try:
        return cache[path][line_number - 2]
    except IndexError as error:
        raise ValueError(f"source line does not exist: {source_location}") from error


def canonicalize(
    ingestion_registry: IngestionRegistry,
    repository: CanonicalRepository | None = None,
) -> CanonicalizationResult:
    repository = repository or CanonicalRepository(ingestion_registry.engine)
    writes: list[CanonicalWrite] = []
    artifact_rows: dict[Path, list[dict[str, str]]] = {}
    for candidate in ingestion_registry.canonical_candidates():
        provenance = SourceProvenance(
            source_version_id=candidate.source_version_id,
            payload_hash=candidate.payload_hash,
            artifact_hash=candidate.artifact_hash,
            source_location=candidate.source_location,
        )
        writes.append(
            CanonicalWrite(
                adapt(
                    candidate.source,
                    _read_evidence_row(candidate.source_location, artifact_rows),
                    provenance,
                )
            )
        )
    outcomes = Counter(repository.save_many(writes))
    return CanonicalizationResult(
        outcomes[CanonicalWriteOutcome.CREATED],
        outcomes[CanonicalWriteOutcome.REPLAY],
    )
