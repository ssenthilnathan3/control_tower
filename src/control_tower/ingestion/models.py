from dataclasses import dataclass
from pathlib import Path


class IngestionError(ValueError):
    """Raised when a source artifact cannot pass the ingestion contract."""


@dataclass(frozen=True)
class IngestedArtifact:
    source: str
    artifact_hash: str
    row_count: int
    total_amount_paise: int
    evidence_path: Path


@dataclass(frozen=True)
class IngestionResult:
    artifacts: tuple[IngestedArtifact, ...]

    @property
    def accepted_row_count(self) -> int:
        return sum(artifact.row_count for artifact in self.artifacts)
