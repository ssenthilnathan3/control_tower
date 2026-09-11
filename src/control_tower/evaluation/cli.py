import argparse
import json
from pathlib import Path

from .service import evaluate_phase_one


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the complete Phase 1 evaluation")
    parser.add_argument("--config", type=Path, default=Path("config/evaluation.json"))
    parser.add_argument(
        "--output", type=Path, help="Override the evaluation output root"
    )
    args = parser.parse_args()
    result = evaluate_phase_one(args.config, args.output)
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result["passed"]:
        raise SystemExit(1)
