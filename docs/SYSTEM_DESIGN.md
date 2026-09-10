# system design

## problem

one disbursement shows up in three systems. the originator says what should be
paid. the bank says what moved. the LMS says what was booked. those records do
not always arrive together and they do not use the same schema.

the control tower needs to answer three things:

- what evidence do we have?
- what does not reconcile, including the INR value?
- who needs to act before we can close?

the dangerous shortcut is to force records together until totals look right.
we do the opposite. source data stays immutable. uncertain records stay visible.

## assumptions

- all data is synthetic
- money is INR and stored as integer paise
- timestamps include an offset; the configured timezone is `+05:30`
- the originator instruction is the expected disbursement
- bank settlement proves movement of money
- LMS booking proves creation of the loan record
- amount tolerance is zero for Phase 1
- a timing difference is pending only inside the configured grace window
- probable or fuzzy matching is not part of Phase 1

some of these rules may change. they need to stay in config or in a versioned
rule, not disappear into a query.

## design

Phase 1 is a modular monolith.

why not services? we have one small workload and several operations that need
the same financial snapshot. splitting ingestion, matching, exceptions, and
close into services would add message delivery and partial-failure problems.
what are we gaining from that in this assessment? not much.

the modules are still separate in code:

```text
src/control_tower/
  generator/       builds deterministic source data
  ingestion/       validates delivery and owns raw evidence
  canonical/       maps accepted rows to one event shape
  reconciliation/  owns match decisions and run identity
  exceptions/      owns investigation state and actions
  close_control/   owns close-or-hold decisions
```

one module should not update another module's state directly. for example,
reconciliation can reference an ingestion record. it cannot rewrite the raw
payload because a match became inconvenient.

## flow

```text
config/generator.json
        |
        v
generator.service.generate
        |
        +--> feeds/*.csv
        +--> manifests/*.json
        +--> truth/*.jsonl       evaluation only
        +--> quality-report.json
        |
        v
ingestion.service.ingest_generated_feeds
        |
        +--> validate header and fields
        +--> check hash, count, and amount controls
        +--> evidence/artifacts/<source>/<sha256>/source.csv
        +--> records.jsonl with ACCEPTED or QUARANTINED
        |
        v
canonical records
        |
        v
exact -> composite -> timing -> unresolved
        |
        +--> exception queue
        +--> close decision
```

ground truth is deliberately outside this runtime flow. if reconciliation can
read `truth/classifications.jsonl`, the evaluation tells us nothing.

## generator

`generator/config.py` parses the seed, dates, partners, amounts, and anomaly
rates. `_assign_anomalies` shuffles event indexes once and assigns disjoint
ranges. one event gets one primary truth label. this avoids unclear expected
results such as an event being both missing and an amount mismatch.

`generator/service.py` creates the business event and then changes the source
rows needed for that anomaly. composite bank records split the amount into two
integer values. the second value gets the remainder, so odd paise still balance.

`generator/io.py` fixes JSON ordering and CSV line endings. these details matter
because feed hashes must be identical for the same seed.

current output for the default config:

| item | value |
| --- | ---: |
| instructions | 2,000 |
| source rows | 6,030 |
| partners | 3 |
| business days | 3 |
| anomalous events | 240 |

## ingestion

`ingestion/contracts.py` owns source validation. it checks exact headers,
required IDs, positive paise amounts, INR, source statuses, and timezone-aware
timestamps. normalization does not get a chance to repair invalid input.

`ingestion/service.py` has two failure levels.

| failure | result |
| --- | --- |
| file cannot be decoded | reject artifact |
| header does not match | reject artifact |
| hash, row count, or amount total differs | preserve artifact, then reject |
| one row has an invalid field | quarantine row; keep valid rows accepted |

why preserve a failed delivery? because the operator needs to prove what was
received. otherwise a partner can resend a corrected file and erase the reason
the first batch failed.

evidence is content-addressed:

```text
evidence/artifacts/<source>/<artifact_hash>/
  source.csv       exact received bytes
  records.jsonl    row IDs, hashes, locations, validation state
  receipt.json     count, amount, and quarantine summary
```

the directory is built under a temporary name and published with `os.replace`.
a reader either sees the complete artifact or nothing. if another process has
already published the same hash, the second write reuses it.

`ingestion.registry.IngestionRegistry` stores the stable identity as
`(source, batch_id, source_record_id)`. same identity plus same payload is a
replay. same identity plus different payload appends a version and moves the
identity to `CONFLICT`. replaying either version stays blocked.

## state

raw evidence and generator truth live on the filesystem. operational ingestion
state lives in a relational database through SQLAlchemy. local runs use
`evidence/ingestion.db`, which survives process restarts.

SQLite owns local operational state through SQLAlchemy. PostgreSQL is the
deployment target for the same schema. the relational store owns:

- ingestion registrations and identity conflicts
- canonical events
- reconciliation runs and match membership
- exceptions and action history
- close decisions and approvals

Alembic owns schema upgrades. repository `create_all` calls keep isolated tests
small, but they are not a deployment migration strategy. production startup must
upgrade to the checked-in revision before serving traffic.

why PostgreSQL? these records need unique constraints and transactions. using a
queue or separate database for each module would make it harder to answer a
basic question: which exact snapshot produced this close decision?

raw files should remain outside relational rows. locally that is the evidence
directory. in production it would be versioned object storage with retention
and encryption policies.

## invariants

- source bytes do not change after receipt
- every accepted or quarantined row points to retrievable evidence
- quarantined rows cannot enter canonicalization
- manifest mismatches cannot be reported as successful ingestion
- ground truth cannot be read by runtime matching
- a canonical record must point to one raw record
- one source record cannot belong to two final matches
- every accepted value ends as matched, pending, or unresolved
- unresolved value cannot disappear when an exception is resolved
- the same snapshot, config, and rule version produce the same close result

exception transitions, approval separation, action immutability, and close
reproducibility have direct tests.

## matching

implemented order:

```text
duplicate full-value delivery
  -> documented composite with exact sum
  -> exact reference + currency + status + amount before cutoff
  -> missing source
  -> contradictory status
  -> amount mismatch
  -> timing difference inside grace
  -> unresolved, including orphan source records
```

order matters. if duplicate rows reach composite matching first, they can make
an incorrect sum look valid. arbitrary subset-sum search is also excluded. a
shared reference and exact amount relationship are required.

each decision stores the rule version and source-version IDs it used. the run
key hashes the active canonical snapshot, policy config, and rule version.
rerunning unchanged input returns `REPLAY` instead of another run.

`reconciliation.evaluation.evaluate` reads generator truth only after runtime
matching finishes. seed `987654` currently produces 2,000 correct
classifications and zero false matches.

## exceptions

blocking reconciliation outcomes create one exception per partner and business
event. duplicate, amount mismatch, status mismatch, missing, and unresolved are
blocking. timing differences inside grace stay pending outside the queue.

`config/exceptions.json` maps each class to its owner, recommended action,
escalation path, and SLA. amount thresholds assign priority. amounts remain integer
paise in storage; INR conversion is presentation only.

the workflow is `OPEN -> INVESTIGATING -> PENDING_APPROVAL -> RESOLVED`. rejection
returns the item to investigation. a later reconciliation decision updates the
evidence and reopens a resolved item. material overrides require a different
approver from the person who requested resolution.

each detection, assignment, transition, approval, rejection, and reopen appends an
action with actor, role, reason, timestamp, and before/after state. application
writes reject updates and deletes to these rows. production database permissions
must give the runtime role insert-only access to action history as defense in depth.

## close control

the Phase 1 close scope is explicit: one persisted reconciliation run and the
persisted ingestion run that owns its delivery-control receipts. this avoids both a
global rule where an old failed delivery blocks every future close and a caller
omitting a failed receipt. passed receipts must cover every artifact used by the
reconciliation evidence.

the close equation uses one originator instruction decision as the count unit:

```text
accepted count = exact + composite + pending + unresolved
accepted value = exact + composite + pending + unresolved
```

quarantined rows and artifact control failures sit outside accepted totals, but
remain visible blockers. malformed quarantine amounts are recorded as unknown
rather than guessed. each blocker stores its type, stable record ID, known paise
value, reason, and evidence reference.

`config/close_control.json` versions pending and unresolved value thresholds and
whether quarantine or delivery-control failures block. The demo permits up to INR 10
crore of `TIMING_DIFFERENCE` exposure because that outcome is emitted only while the
event remains inside the configured grace window. The amount stays explicitly
pending in the scorecard and above-threshold pending exposure still blocks close.
Approved confirmed exceptions remain linked to their original reconciliation
decisions and audit history; close treats those authorised resolutions as cleared
without rewriting source evidence or the deterministic decision.

the persisted result contains the reconciliation run and snapshot hashes, normalized
policy hash and version, actor, outcome, complete scorecard, ordered blockers, and a
deterministic decision hash. actor and time are recording metadata, not calculation
inputs, so another operator replaying the same scope receives the original result.

## operator API

FastAPI exposes the Python service boundaries under `/api`. bearer tokens are
looked up in `CONTROL_TOWER_IDENTITIES_JSON`; the resulting principal supplies the
actor and role for every mutation. request bodies cannot choose either value.
operators may ingest, reconcile, investigate, and request resolution. approvers
are additionally required for approval, rejection, and close decisions.

ingestion paths resolve below `CONTROL_TOWER_INPUT_ROOT` before files are read. the
small browser console is served by the same process and calls these authenticated
endpoints. bypassing the UI therefore does not bypass role checks. production must
replace the static token map with an identity-provider integration while preserving
the same server-derived `Principal` boundary.

## failure modes

### process stops while writing evidence

only the temporary directory exists. retry from the original feed. incomplete
directories are never published as artifact hashes.

### the same artifact arrives twice

reuse the content-addressed evidence. the registry returns `REPLAY`, so no new
source identity or payload version is created.

### the same source identity has different bytes

keep both artifacts, append the second payload as a new version, and mark the
identity `CONFLICT`. replaying either version remains conflicted. do not
overwrite the first version.

### one row is malformed

store its payload hash, source location, and validation errors. continue with
valid rows. a bad amount that prevents control-total verification must also
surface as an artifact control failure.

### reconciliation fails halfway

the run and all decision memberships are written in one database transaction.
retry uses the same run key. exception sync can then be retried: unchanged
decisions replay, while changed
decisions refresh evidence and reopen resolved work. the close path must still not
treat reconciliation alone as a complete control.

### a close calculation is retried

blockers are sorted before hashing. the reconciliation and ingestion run identities,
scorecard, and normalized policy produce the same decision hash, so retry returns
the stored result instead of creating another close decision.

### optional AI is unavailable

nothing changes in Phase 1. ingestion, deterministic matching, exceptions, and
close must not depend on it.

## tradeoffs

| choice | what we gain | what we give up |
| --- | --- | --- |
| modular monolith | one snapshot and simpler local runs | modules cannot scale independently yet |
| CSV feeds | easy generation and inspection | weaker typing than Parquet or an API schema |
| filesystem evidence | exact bytes and simple replay | no built-in retention or multi-host access |
| zero amount tolerance | no hidden financial difference | harmless rounding needs an explicit future rule |
| disjoint anomalies | clean evaluation labels | fewer multi-failure scenarios |
| deterministic matching | explainable decisions | lower recall when references are damaged |

## open questions

- do partner files really share one contract, or do we need one adapter per partner and source?
- what source identity is stable across corrected deliveries?
- should a quarantined non-financial field always block close?
- which statuses are final for each source?
- what makes an override material?
- how long must raw evidence and audit history be retained?
- should Phase 2 replace explicit receipt scope with partner, batch, and business-date fields?

these need answers before their behavior is coded. until then, assumptions stay
explicit and configurable.
