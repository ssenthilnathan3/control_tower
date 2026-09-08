from pathlib import Path

from control_tower.canonical import CanonicalRepository, canonicalize
from control_tower.generator import generate
from control_tower.ingestion import IngestionRegistry, ingest_generated_feeds

CONFIG = Path("config/generator.json")


def test_normalizes_eligible_ingestion_versions_with_lineage(tmp_path: Path) -> None:
    generated = generate(CONFIG, tmp_path / "generated")
    evidence = tmp_path / "evidence"
    ingested = ingest_generated_feeds(generated.output_dir, evidence)
    registry = IngestionRegistry.local(evidence)
    repository = CanonicalRepository(registry.engine)

    first = canonicalize(registry, repository)
    second = canonicalize(registry, repository)

    assert first.created_count == ingested.accepted_row_count
    assert first.replayed_count == 0
    assert second.created_count == 0
    assert second.replayed_count == ingested.accepted_row_count
    assert repository.count() == ingested.accepted_row_count
