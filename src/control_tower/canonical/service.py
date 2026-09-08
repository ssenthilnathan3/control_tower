from __future__ import annotations

import csv
import hashlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from control_tower.ingestion import IngestionRegistry

from .adapters import adapt
from .config import CanonicalizationPolicy
from .models import SourceProvenance
from .repository import CanonicalRepository, CanonicalWrite, CanonicalWriteOutcome


@dataclass(frozen=True)
class CanonicalizationResult:
    created_count: int
    replayed_count: int


class CanonicalizationError(ValueError):
    pass


@dataclass(frozen=True)
class EvidenceArtifact:
    rows: list[dict[str, str]]
    raw_lines: list[bytes]


def _read_evidence_row(
    source_location: str,
    expected_artifact_hash: str,
    expected_payload_hash: str,
    cache: dict[Path, EvidenceArtifact],
) -> dict[str, str]:
    path_value, separator, line_value = source_location.rpartition("#line=")
    if not separator:
        raise ValueError(f"invalid source location: {source_location}")
    line_number = int(line_value)
    if line_number < 2:
        raise ValueError(f"invalid source line: {line_number}")
    path = Path(path_value)
    if path not in cache:
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected_artifact_hash:
            raise ValueError("artifact hash does not match preserved evidence")
        text = raw.decode("utf-8")
        cache[path] = EvidenceArtifact(
            list(csv.DictReader(text.splitlines())), raw.splitlines(keepends=True)[1:]
        )
    try:
        artifact = cache[path]
        index = line_number - 2
        raw_line = artifact.raw_lines[index]
        row = artifact.rows[index]
    except IndexError as error:
        raise ValueError(f"source line does not exist: {source_location}") from error
    if hashlib.sha256(raw_line).hexdigest() != expected_payload_hash:
        raise ValueError("payload hash does not match preserved evidence")
    return row


def canonicalize(
    ingestion_registry: IngestionRegistry,
    policy: CanonicalizationPolicy,
    repository: CanonicalRepository | None = None,
) -> CanonicalizationResult:
    repository = repository or CanonicalRepository(ingestion_registry.engine)
    repository.sync_eligibility()
    writes: list[CanonicalWrite] = []
    artifact_rows: dict[Path, EvidenceArtifact] = {}
    for candidate in ingestion_registry.canonical_candidates():
        try:
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
                        _read_evidence_row(
                            candidate.source_location,
                            candidate.artifact_hash,
                            candidate.payload_hash,
                            artifact_rows,
                        ),
                        provenance,
                        policy,
                    )
                )
            )
        except (KeyError, OSError, TypeError, ValueError) as error:
            raise CanonicalizationError(
                f"cannot canonicalize {candidate.source} version "
                f"{candidate.source_version_id}: {error}"
            ) from error
    outcomes = Counter(repository.save_many(writes))
    return CanonicalizationResult(
        outcomes[CanonicalWriteOutcome.CREATED],
        outcomes[CanonicalWriteOutcome.REPLAY],
    )
