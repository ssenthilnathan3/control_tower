# roadmap

phase 1 first. each milestone needs a runnable output and tests.

| milestone | demo | status |
| --- | --- | --- |
| generate source data | same seed produces the same three feeds and truth | pending |
| ingest source data | validate manifests and retain untouched evidence | pending |
| isolate bad rows | quarantine one bad row without dropping valid rows | pending |
| make ingestion idempotent | replay is a no-op; changed payload becomes a conflict | pending |
| normalize records | every canonical event links to one raw record | pending |
| reconcile | exact, composite, timing, and unresolved outcomes are separate | pending |
| route exceptions | every unresolved INR value has an owner and evidence | pending |
| control close | same snapshot and policy produce the same close-or-hold result | pending |
| expose operator flow | run the required journey through API and UI | pending |
| verify release | reproduce tests and metrics from a clean checkout | pending |

Phase 2 starts only after these gates pass on a fresh seed.
