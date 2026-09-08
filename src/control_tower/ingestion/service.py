from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
from pathlib import Path
from uuid import uuid4

from .contracts import CONTRACTS, SourceContract
from .models import IngestedArtifact, IngestionError, IngestionResult


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load_manifest(path: Path, source: str) -> dict[str, object]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise IngestionError(f"{source} manifest is unreadable: {error}") from error
    if manifest.get("source") != source:
        raise IngestionError(
            f"{source} manifest declares source {manifest.get('source')!r}"
        )
    return manifest


def _read_and_validate(
    feed_path: Path, source: str, contract: SourceContract
) -> tuple[bytes, list[dict[str, str]]]:
    try:
        raw = feed_path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise IngestionError(f"{source} feed is unreadable: {error}") from error
    reader = csv.DictReader(text.splitlines())
    if tuple(reader.fieldnames or ()) != contract.fields:
        raise IngestionError(f"{source} headers do not match the published contract")
    rows = list(reader)
    errors: list[str] = []
    for line_number, row in enumerate(rows, start=2):
        errors.extend(
            f"line {line_number}: {message}" for message in contract.validate(row)
        )
    if errors:
        preview = "; ".join(errors[:10])
        raise IngestionError(
            f"{source} has {len(errors)} validation error(s): {preview}"
        )
    return raw, rows


def _verify_controls(
    source: str,
    raw: bytes,
    rows: list[dict[str, str]],
    contract: SourceContract,
    manifest: dict[str, object],
) -> tuple[str, int]:
    artifact_hash = _sha256_bytes(raw)
    total = sum(int(row[contract.amount_field]) for row in rows)
    expected = {
        "file_sha256": artifact_hash,
        "declared_row_count": len(rows),
        "declared_total_amount_paise": total,
    }
    mismatches = [
        f"{key}: declared {manifest.get(key)!r}, actual {value!r}"
        for key, value in expected.items()
        if manifest.get(key) != value
    ]
    if mismatches:
        raise IngestionError(f"{source} manifest mismatch: {'; '.join(mismatches)}")
    return artifact_hash, total


def _preserve_evidence(
    evidence_root: Path,
    source: str,
    raw: bytes,
    rows: list[dict[str, str]],
    contract: SourceContract,
    artifact_hash: str,
    total: int,
) -> Path:
    destination = evidence_root / "artifacts" / source / artifact_hash

    # Content-addressed storage makes an already preserved artifact an immutable no-op.
    if destination.exists():
        return destination
    temporary = destination.with_name(f".{artifact_hash}.{uuid4().hex}.tmp")
    temporary.mkdir(parents=True)
    artifact_path = temporary / "source.csv"
    artifact_path.write_bytes(raw)

    # Published feeds contain one physical line per record; retaining that line allows
    # the payload hash and source location to be reproduced from untouched bytes.
    physical_lines = raw.splitlines(keepends=True)
    with (temporary / "records.jsonl").open(
        "w", encoding="utf-8", newline="\n"
    ) as output:
        for line_number, (row, raw_line) in enumerate(
            zip(rows, physical_lines[1:]), start=2
        ):
            record = {
                "artifact_hash": artifact_hash,
                "payload_hash": _sha256_bytes(raw_line),
                "record_id": row[contract.record_id_field],
                "source": source,
                "source_location": f"{artifact_path.name}#line={line_number}",
            }
            output.write(
                json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
            )
    receipt = {
        "artifact_hash": artifact_hash,
        "row_count": len(rows),
        "source": source,
        "total_amount_paise": total,
    }
    (temporary / "receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)

    try:
        os.replace(temporary, destination)
    except OSError:
        # A concurrent writer may have published the same content-addressed artifact.
        if not destination.exists():
            raise
        shutil.rmtree(temporary)
    return destination


def ingest_generated_feeds(input_dir: Path, evidence_root: Path) -> IngestionResult:
    artifacts: list[IngestedArtifact] = []
    for source, contract in CONTRACTS.items():
        feed_path = input_dir / "feeds" / f"{source}.csv"
        manifest = _load_manifest(input_dir / "manifests" / f"{source}.json", source)
        raw, rows = _read_and_validate(feed_path, source, contract)
        artifact_hash, total = _verify_controls(source, raw, rows, contract, manifest)
        evidence_path = _preserve_evidence(
            evidence_root, source, raw, rows, contract, artifact_hash, total
        )
        artifacts.append(
            IngestedArtifact(source, artifact_hash, len(rows), total, evidence_path)
        )
    return IngestionResult(tuple(artifacts))
