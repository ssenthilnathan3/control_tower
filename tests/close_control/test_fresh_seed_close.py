from pathlib import Path

from control_tower.canonical import (
    CanonicalizationPolicy,
    CanonicalRepository,
    canonicalize,
)
from control_tower.close_control import CloseOutcome, calculate_close
from control_tower.generator import generate
from control_tower.ingestion import IngestionRegistry, ingest_generated_feeds
from control_tower.reconciliation import (
    ReconciliationPolicy,
    ReconciliationRepository,
    run_reconciliation,
)


def test_fresh_seed_publishes_accounted_phase_one_scorecard(tmp_path) -> None:
    generated = generate(
        Path("config/generator.json"), tmp_path / "generated", seed=987654
    )
    evidence = tmp_path / "evidence"
    ingestion_result = ingest_generated_feeds(generated.output_dir, evidence)
    ingestion = IngestionRegistry.local(evidence)
    canonical = CanonicalRepository(ingestion.engine)
    canonicalize(
        ingestion,
        CanonicalizationPolicy.load(Path("config/canonicalization.json")),
        canonical,
    )
    reconciliation = ReconciliationRepository(ingestion.engine)
    run = run_reconciliation(
        canonical,
        ReconciliationPolicy.load(Path("config/reconciliation.json")),
        reconciliation,
    )

    result = calculate_close(
        run.run_key,
        tuple(artifact.control_id for artifact in ingestion_result.artifacts),
        "phase1-evaluation",
        reconciliation,
        ingestion,
    )

    scorecard = result.scorecard
    assert result.outcome is CloseOutcome.HOLD
    assert scorecard.accepted_count == 2000
    assert scorecard.accepted_count == (
        scorecard.matched_count + scorecard.pending_count + scorecard.unresolved_count
    )
    assert scorecard.accepted_value_paise == (
        scorecard.matched_value_paise
        + scorecard.pending_value_paise
        + scorecard.unresolved_value_paise
    )
    assert scorecard.unresolved_value_paise > 0
    assert result.blockers
