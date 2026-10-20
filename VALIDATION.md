# Validation — open-managed-agents

Harness: `python3 validate.py` (repo root). Deterministic, offline.
Exit 0 and a final line of `HARNESS GREEN` mean every stage passed.

## Stages

1. **Import/compile** — `compileall` over `src/`, `tests/`, `examples/`,
   `experiments/` and `demo.py`, then a clean subprocess imports the
   package named in `pyproject.toml` (`open_managed_agents`) plus all
   8 of its modules.
2. **Test suite** — `pytest tests/` in a subprocess: the pre-existing
   `tests/test_smoke.py`, 21 seeded tests covering the registry,
   planner shapes and plan hashing, cycle rejection, replan-to-backup,
   the tool whitelist/sandbox, scripted-provider tier behaviour and
   failure injection, and runtime end-to-end runs (approval gates,
   budget cap on the runaway task, replan recovery, JSON round-trip,
   provider swap on an identical plan hash).
3. **Employer-reference scan** — the standing gate scanner
   (`fis_scan.py`) over the whole tree; every candidate is judged in
   context by `validate.py`. A real reference fails the stage.
4. **Results/docs consistency** — every path referenced by the README
   exists, plus the documented entry points and result artefacts
   (`demo.py`, `examples/quickstart.py`,
   `experiments/run_experiments.py`, `results/results.md`,
   `results/demo_run.json`, both results charts). Existence only —
   file contents are not compared.
   Stage 4 also includes **deployment-config validation**: the tree is
   scanned for `render.yaml`/`render.yml`, `vercel.json`, `Procfile`,
   `Dockerfile*`, `docker-compose*.yml`/`yaml`, `netlify.toml`,
   `fly.toml`, and `.github/workflows/*.yml`/`yaml`. Any file found is
   parsed and schema-checked (e.g. a Render service with `type: static`
   fails — a static site is `type: web` with `env: static`); a broken
   deploy config fails the stage. Current result: **no deployment
   configs present as of this run** — the step passes with nothing to
   validate.

## What is NOT verified (honest non-coverage)

- **Experiments E1–E5 are not re-run.** `experiments/run_experiments.py`
  is compile-checked and its outputs are existence-checked, but the
  harness does not recompute the published figures in
  `results/results.md` (dynamic 8/8 vs static 4/8, 1.86× parallelism,
  provider-swap costs, 4/4 recovery, budget stop). The test suite
  verifies the underlying behaviours on individual tasks, not the
  aggregate experiment table.
- **`demo.py` is not executed** by the harness (compile-checked only);
  `results/demo_run.json` is existence-checked, its contents are not
  re-verified against a fresh run.
- **Hosted providers are never exercised.** The OpenAI/Anthropic
  adapters run only when API keys are present; the harness is offline
  and the scripted providers are the only execution path tested.
- **Token/cost figures** are the platform's deterministic estimates;
  the tests verify the accounting arithmetic (trace sums equal run
  totals), not any real provider pricing.
- **The scan** covers text files only (the scanner skips binaries) and
  excludes `validate.py` and this file, which must name the gate's
  patterns to enforce them; those self-references (pattern names, the
  scanner's own path, the `FIS_SCAN` env var) were judged benign in
  context — they describe the gate, they are not content references.
  The harness prints how many such excluded candidates it saw.

## Provenance

Harness added 2026-10-20. The test suite predates the harness and is
run unchanged; stage 4's reference list is derived from the README as
written.
