from pathlib import Path

from control_tower.canonical import (
    CanonicalizationPolicy,
    CanonicalRepository,
    canonicalize,
)
from control_tower.generator import generate
from control_tower.ingestion import IngestionRegistry, ingest_generated_feeds
from control_tower.reconciliation import ReconciliationPolicy, evaluate, reconcile


def test_fresh_seed_matches_generator_truth_without_false_matches(
    tmp_path: Path,
) -> None:
    generated = generate(
        Path("config/generator.json"), tmp_path / "generated", seed=987654
    )
    evidence = tmp_path / "evidence"
    ingest_generated_feeds(generated.output_dir, evidence)
    ingestion_registry = IngestionRegistry.local(evidence)
    canonical_repository = CanonicalRepository(ingestion_registry.engine)
    canonicalize(
        ingestion_registry,
        CanonicalizationPolicy.load(Path("config/canonicalization.json")),
        canonical_repository,
    )

    decisions = reconcile(
        canonical_repository.active_events(),
        ReconciliationPolicy.load(Path("config/reconciliation.json")),
    )
    scorecard = evaluate(
        decisions, generated.output_dir / "truth/classifications.jsonl"
    )

    assert scorecard.instruction_count == 2000
    assert scorecard.correct_count == scorecard.instruction_count
    assert scorecard.false_match_count == 0
    assert scorecard.false_match_value_paise == 0
    assert scorecard.failures == ()
    assert scorecard.composite_match_count == 40
