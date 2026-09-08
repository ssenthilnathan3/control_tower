from dataclasses import dataclass
from pathlib import Path


class IngestionError(ValueError):
    """Raised when a source artifact cannot pass the ingestion contract."""


@dataclass(frozen=True)
class QuarantinedRecord:
    source: str
    record_id: str
    line_number: int
    payload_hash: str
    source_location: str
    errors: tuple[str, ...]


@dataclass(frozen=True)
class IngestedArtifact:
    source: str
    artifact_hash: str
    row_count: int
    total_amount_paise: int
    evidence_path: Path
    accepted_count: int
    quarantined_records: tuple[QuarantinedRecord, ...]

    @property
    def quarantined_count(self) -> int:
        return len(self.quarantined_records)


@dataclass(frozen=True)
class IngestionResult:
    artifacts: tuple[IngestedArtifact, ...]

    @property
    def accepted_row_count(self) -> int:
        return sum(artifact.accepted_count for artifact in self.artifacts)

    @property
    def quarantined_row_count(self) -> int:
        return sum(artifact.quarantined_count for artifact in self.artifacts)
