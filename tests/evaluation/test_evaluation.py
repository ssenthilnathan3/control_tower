import json
from pathlib import Path

from control_tower.evaluation import EvaluationConfig, evaluate_phase_one


def test_loads_evaluation_config() -> None:
    config = EvaluationConfig.load(Path("config/evaluation.json"))

    assert config.development_seed != config.fresh_seed
    assert config.generator_config == Path("config/generator.json")


def test_evaluation_writes_deterministic_scorecards(
    tmp_path: Path, monkeypatch
) -> None:
    scorecard = {
        "scenario": "unused",
        "instruction_count": 10,
        "correct_count": 10,
        "exact_match": {"count": 8, "value_paise": 800, "rate": 0.8},
        "composite_match": {"count": 1, "value_paise": 100, "rate": 0.1},
        "false_matches": {"count": 0, "exposure_paise": 0},
        "exception_coverage": {
            "eligible_count": 1,
            "eligible_value_paise": 100,
            "queued_count": 1,
            "queued_value_paise": 100,
        },
        "control_totals": {"count_difference": 0, "value_difference_paise": 0},
        "close": {"accepted_value_paise": 1000},
        "failed_cases": [],
    }

    def fake_run(name, seed, root, config):
        result = {**scorecard, "scenario": name, "seed": seed}
        directory = root / name
        directory.mkdir(parents=True)
        (directory / "scorecard.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return result

    monkeypatch.setattr("control_tower.evaluation.service._run", fake_run)
    output = tmp_path / "evaluation"
    first = evaluate_phase_one(Path("config/evaluation.json"), output)
    first_bytes = (output / "summary.json").read_bytes()
    for child in output.iterdir():
        if child.is_dir():
            for item in child.iterdir():
                item.unlink()
            child.rmdir()
    second = evaluate_phase_one(Path("config/evaluation.json"), output)

    assert first == second
    assert (output / "summary.json").read_bytes() == first_bytes
    assert first["passed"] is True
    assert first["straight_through_rate"] == 0.9
    assert first["exception_coverage"]["value_rate"] == 1.0
