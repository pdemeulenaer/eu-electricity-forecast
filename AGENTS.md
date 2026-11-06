# EU Electricity Forecast

## Working in this repository

- Before any other action, read the `databricks-core` skill when available.
  For Databricks work, also read the relevant product skill. If the skills are
  unavailable, see `databricks aitools install` and the
  [Databricks CLI installation guide](https://docs.databricks.com/dev-tools/cli/install).
- This Python 3.12 and Databricks Declarative Automation Bundles project aims to
  forecast day-ahead electricity prices using ENTSO-E data.
- Read the relevant code, bundle resources, tests, and documentation before
  changing behavior. Treat `docs/implementation-plan.md` as a roadmap, not
  proof that planned work is deployed.
- The main Python package is `src/eu_electricity_forecast/`; bundle resources
  are in `resources/`; documentation is in `docs/`.
- The repository also contains Databricks starter taxi examples. Do not assume
  they are part of the electricity forecasting pipeline.

## Documentation style

- Write for a reader who understands basic data concepts but may be new to
  electricity markets, forecasting, or Databricks.
- Use simple, direct English. Explain unfamiliar terms and acronyms on first
  use. Prefer concrete examples to abstract descriptions.
- Start each page or section with its purpose and the outcome for the reader.
  Explain what happens, why it matters, and any important limitations.
- When documenting or changing a flow, include or update a Mermaid diagram:
  for example, for ingestion, pipeline stages, data dependencies, training,
  scoring, or deployment. Keep diagrams focused and readable. Reuse an
  existing relevant diagram rather than duplicating it for every mention.
- Place a short explanation next to each diagram. Make arrows, labels,
  conditions, and dependencies consistent with the text and implementation.
- Distinguish implemented locally, verified in a workspace, and planned
  behavior. Do not imply that a resource is deployed or a model is
  production-ready without evidence.
- Update related documentation when behavior, configuration, commands, table
  contracts, or operational steps change. Check the home page, architecture,
  operations, implementation plan, and API reference where relevant.
- Keep API docstrings useful and consistent with the configured Google-style
  mkdocstrings reference. Add new documentation pages to `mkdocs.yml` when
  they should appear in site navigation.
- Run `make docs-build` after documentation or MkDocs configuration changes.
  Report any failure or check that could not be run.

## Data and forecasting invariants

- Preserve UTC timestamps and handle zone-local delivery times and daylight
  saving transitions explicitly. Preserve source resolution and auction
  identity; do not combine distinct market series silently.
- Keep source files immutable and retain lineage, source revisions, and
  first-seen timestamps where applicable. Preserve idempotent replay and
  revision handling.
- Treat missing observations as missing, not zero. Preserve negative prices.
- For historical price lags, use the latest Silver current value at 24 and 168
  elapsed hours before the target and assume it was available by issue time.
  Backfill first-seen time is download time, not proof of original publication;
  document that later corrections are not replayed as-of each historical issue.
- For other forecast inputs, use only vintages available by issue time. Keep
  the target price itself out of predictors.
- Keep target prices and unavailable future information out of predictors.
  Use chronological evaluation splits and explain any changes to the target,
  issue cutoff, feature availability, or evaluation method.
- Check the current code and configuration before documenting exact cutoff
  times, auction choices, resource names, or deployment status.

## Development and validation

- Use `uv` and follow the dependencies and tooling in `pyproject.toml`.
  Keep changes consistent with the existing code and tests.
- Run tests relevant to the change; use `uv run pytest` for the full suite
  when its Databricks Connect requirements are available. Some tests may
  initialize remote compute. State what was and was not verified.
- For bundle changes, use the `databricks-dabs` skill and validate the
  intended target before deployment. Use the relevant jobs, pipelines, and
  compute skills when applicable.
- Do not select a Databricks profile automatically. Have the user choose a
  profile for workspace operations and pass it explicitly with `--profile`.
- Never commit credentials or expose ENTSO-E tokens. Follow the existing
  local environment and Databricks secret configuration.
