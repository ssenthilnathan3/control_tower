# Co-lending Control Tower

Evidence-first ingestion, deterministic reconciliation, owned exception workflow,
and reproducible close control for synthetic co-lending data.

## Run locally

```bash
uv sync
uv run alembic upgrade head
uv run control-tower-generate --help
uv run control-tower-web
```

The console is available at `http://127.0.0.1:8000`. Configure paths and bearer
identities with the variables shown in `.env.example`. Treat tokens as secrets;
the checked-in values are examples only.

`CONTROL_TOWER_INPUT_ROOT` is the only directory the ingestion endpoint can read.
The console's input field is relative to that root.
Generated output is stored under `generated/development`, so use `development` in
the console before selecting **Ingest**.

## Verify

```bash
uv run --with ruff ruff check src tests migrations
uv run pytest
uv run alembic check
```

The operator token can ingest, reconcile, investigate, and request resolution.
Only the approver token can approve, reject, or calculate close. Actor and role are
resolved from the server-side token map, never from request JSON.
