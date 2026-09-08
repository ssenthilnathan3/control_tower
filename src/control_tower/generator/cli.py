import argparse
import json
from pathlib import Path

from .service import generate


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate deterministic co-lending source feeds"
    )
    parser.add_argument("--config", type=Path, default=Path("config/generator.json"))
    parser.add_argument("--output", type=Path, default=Path("generated/development"))
    parser.add_argument("--seed", type=int, help="Override the configured seed")
    args = parser.parse_args()
    result = generate(args.config, args.output, args.seed)
    print(json.dumps(result.quality_report, indent=2, sort_keys=True))
