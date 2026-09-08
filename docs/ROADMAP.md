# roadmap

phase 1 first. each milestone should leave behind something we can run and
show. phase 2 does not start until the close result is reproducible on a fresh
seed.

## 1. generate the source data

goal: create enough controlled data to test reconciliation without using real
customer data.

build:

- generate originator, LMS, and bank feeds from `config/generator.json`
- generate at least 2,000 instructions and 5,000 source records
- inject missing, duplicate, amount, status, timing, and composite cases
- write truth separately from the feeds
- publish manifests and a quality report

done when:

- the same seed produces byte-identical output
- a fresh seed changes the records but keeps the configured distribution
- every composite split adds back to the instruction amount

status: done

## 2. ingest without losing evidence

goal: know exactly what arrived before trying to reconcile it.

build:

- validate the three source contracts
- check file hash, row count, and amount total against each manifest
- store the untouched artifact by SHA-256
- store a payload hash and source location for every row
- quarantine invalid rows and keep valid rows usable
- make reprocessing a no-op
- reject the same source identity if its payload changes

done when:

- one bad row does not discard valid rows
- a manifest mismatch fails but the received artifact still exists as evidence
- running the same input twice does not create another business effect
- a changed payload under an existing identity is visible as a conflict

status: quarantine is done. persisted idempotency is next.

## 3. normalize source records

goal: give matching one stable shape without hiding source differences.

build:

- add one adapter per source
- store money as integer paise
- map source statuses to explicit canonical statuses
- retain source IDs, timestamps, partner, batch, and evidence location
- keep quarantined rows out of canonicalization

done when:

- every canonical record resolves to one raw source row
- no normalization step changes the source evidence
- status and timestamp mappings have direct tests

## 4. reconcile deterministically

goal: classify each instruction without guessing.

build:

- detect duplicate delivery first
- apply exact matching
- apply only documented composite relationships
- keep items inside the grace window pending
- route contradictory or missing evidence to unresolved
- version each rule and store the evidence used

done when:

- no source record is consumed by two matches
- fresh-seed evaluation reports zero false deterministic matches
- every accepted amount is matched, pending, or unresolved

## 5. route exceptions

goal: make every unresolved amount somebody's work.

build:

- store classification, INR exposure, age, priority, SLA, and evidence
- assign a default owner and next action by exception class
- support investigate, request resolution, approve, reject, and reopen
- keep an append-only action history
- stop an operator from approving their own material override

done when:

- every unresolved rupee appears in the queue
- the UI or API cannot bypass role checks
- a resolved item can be reconstructed from its action history

## 6. decide close or hold

goal: produce the same close decision from the same snapshot and policy.

build:

- account for accepted records as exact, composite, pending, or unresolved
- keep quarantine and control-total failures visible
- list every blocking record and its value
- store the snapshot, policy version, actor, and decision hash
- publish the Phase 1 scorecard

done when:

- rerunning the close calculation returns the same result
- changing a threshold through config changes only the expected blockers
- every scorecard number can be traced to source rows and decisions

## 7. expose the operator flow

goal: demonstrate the required journey without turning the project into a
dashboard-only build.

build:

- add APIs for batches, reconciliation, exceptions, audit, and close
- add a small UI around those APIs
- link aggregate numbers to the records behind them

done when:

- the demo can ingest, reconcile, investigate, approve, and produce a close result
- backend authorization still applies when the UI is bypassed

## 8. prove the release

goal: make evaluation boring and repeatable.

build:

- document clean setup and one-command flows
- run development and fresh-seed evaluation separately
- document monitoring, retry, rollout, and rollback
- write the Phase 2 scale note after Phase 1 passes

done when:

- a clean checkout can reproduce feeds, tests, metrics, and close output
- the 12-minute demo covers every gate without manual data repair
