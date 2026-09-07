# system design

## problem

one disbursement appears in the originator, bank, and LMS. the records use
different IDs, statuses, and timestamps. some arrive late. some are wrong.

we need to retain what each source sent, reconcile only when the evidence is
strong enough, and route everything else to an owner. no forced matches. no
silent balancing entries.

## assumptions

- all data is synthetic
- money is INR stored as integer paise
- amount tolerance is zero in Phase 1
- timestamps include an offset
- probable matching is not part of the Phase 1 result

## design

use a modular monolith. the workload is small and ingestion, matching,
exceptions, and close need one consistent snapshot. services would add replay
and partial-failure problems without giving us useful isolation yet.

```text
generator -> source feeds + manifests -> ingestion
          -> isolated truth            -> raw evidence + quarantine
                                        -> canonical records
                                        -> deterministic reconciliation
                                        -> matches / pending / exceptions
                                        -> close or hold
```

planned modules:

```text
src/control_tower/
  generator/
  ingestion/
  canonical/
  reconciliation/
  exceptions/
  close_control/
```

## state

raw source bytes need to survive every retry and correction. store them by
SHA-256 and make every derived row point back to an artifact and location.

PostgreSQL will own operational state once canonicalization starts. unique
constraints will protect source identities and match membership. raw files stay
outside the database.

## invariants

- source evidence is immutable
- quarantined rows cannot be matched
- ground truth is unavailable to runtime matching
- one source row cannot be consumed by two final matches
- every accepted value is matched, pending, or unresolved
- the same snapshot and policy produce the same close result

## failure modes

- bad row: quarantine it and retain valid rows
- bad manifest: preserve the artifact and reject the batch
- replay: return the existing result
- same identity with changed bytes: retain both versions and raise a conflict
- partial reconciliation: rollback the run and retry from the same snapshot

## open questions

- what identity remains stable across partner corrections?
- which source statuses are final?
- what makes an override material?
- which unresolved items can be approved without blocking close?
- what is the retention period for source evidence and audit history?
