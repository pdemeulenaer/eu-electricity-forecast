# Environment and release plan

This plan covers catalog isolation, code delivery, and model registration. All
items below are **planned**. Dev deployments remain manual; QA and production
deliveries are automated.

## 1. Environments and data access

| Environment | Proposed catalog | Read | Write | Runs |
| --- | --- | --- | --- | --- |
| Dev | `eu_electricity_forecast_dev` | Dev, or selected production data | Dev only | Developer's manual runs |
| QA | `eu_electricity_forecast_qa` | QA, or selected production data | QA only | CI tests and stakeholder review |
| Prod | `eu_electricity_forecast_prod` | Prod only | Prod only | Production jobs |

Each catalog has `landing`, `bronze`, `silver`, and `gold` data schemas, plus
`ml_models` for registered models. Names are
proposals: the existing catalog is `eu_electricity_forecast`, currently shared
by the dev and prod bundle targets. Choose whether to keep it as dev or migrate
its data to the proposed dev catalog before changing existing jobs. Do not delete
or rename its volume as part of a bundle change without a migration plan.

~~~mermaid
flowchart LR
    PROD[(Prod data)] -->|Read only, if allowed| DEV[Dev jobs]
    PROD -->|Read only, if allowed| QA[QA jobs]
    DEV --> DEVOUT[(Dev catalog)]
    QA --> QAOUT[(QA catalog)]
    PROD --> PJ[Prod jobs] --> PROD
~~~

For each job, configure an **input catalog** and an **output catalog**. Dev and
QA can select their own catalog or an allowed prod source; output remains fixed
to their environment. Prod uses prod for both. Define inputs per stage: for
example, a QA Silver transformation may read prod Bronze but writes QA Silver.
An ingestion run that fetches ENTSO-E data writes to QA landing, not prod landing.

Give each environment a separate job identity. Dev and QA identities receive
write permissions only on their own catalog, and narrowly scoped read permissions
on approved prod tables or volumes. Prod jobs write only to prod. Checkpoint and
schema-tracking paths, landing files, quarantine, model output, and temporary
objects must also use the output environment. Test denied writes to prod. Avoid
concurrent dev jobs overwriting the same tables by isolating or coordinating
runs. Limit access to sensitive production data where applicable.

Add `dev`, `qa`, and `prod` bundle targets with distinct deployment state and
job names. Dev uses development mode; QA and prod use stable shared deployments
and service identities. Keep QA schedules paused and start test runs explicitly.
Provision catalogs, storage, schemas, grants, and service identities before the
bundle runs. The workspace's managed storage setup may require creating catalogs
in the UI or specifying a managed location. Validate the migration and bundle
plan before deployment.

## 2. Branches and delivery

~~~mermaid
flowchart TB
    subgraph A["Feature work"]
        direction LR
        F[Feature branch] -->|PR| C[CI] --> D[develop]
    end
    subgraph B["Release"]
        direction LR
        R[develop → main PR] --> Q[CI + QA deploy + jobs]
        Q --> S[Review results] --> M[Merge to main]
        M --> P[Prod deploy + version tag]
    end
    D --> R
~~~

1. Create `develop` from the agreed `main` commit and protect both branches.
   Feature branches merge into `develop` through reviewed pull requests with
   required CI checks. Developers deploy dev manually when needed.
2. A `develop` → `main` pull request runs CI, deploys the proposed release to
   QA, and runs the QA data and training jobs. Train and register a candidate
   model in QA, evaluate it, and publish its version, job links, and metrics to
   the PR. QA runs must not start prod schedules or change the prod alias.
3. Automated QA checks finish; stakeholder review remains pending until a person
   accepts the results. Use PR review and required checks as the merge gate;
   the workflow does not need to stay running while someone reviews.
4. After approval and merge, deploy the verified release to prod, copy the exact
   approved QA model into the prod registry, and assign its `@prod` alias. Create
   a `v<version>` tag from `pyproject.toml` on the released `main` commit. Reject
   an already-used version or tag. Define deployment failure and retry behavior
   before enabling production releases.

QA approval must apply to the exact release tested. Build the wheel once for the
QA candidate, retain it with its commit and checksum, and deploy that same wheel
to prod. Ensure the merged commit has the tested tree and bundle configuration;
if a merge or later change produces different content, rerun QA before release.
Serialize QA deployments or isolate them per release so concurrent PRs cannot
replace each other's jobs or results. Pin data versions and record inputs and job
run IDs so the reviewed evidence is reproducible. Protect release credentials;
do not expose them to untrusted fork pull requests.

Use separate deployment identities and GitHub environments with appropriate
approval rules. Prefer short-lived workload identity federation over stored
long-lived tokens. Check GitHub plan and repository support for required reviews
and protection rules. [GitHub deployment environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)

### CI and QA checks

| Check | Runs | Examples |
| --- | --- | --- |
| Lint and formatting | Every PR | Ruff; fail on violations |
| Security | Every PR | Dependency and secret scanning; code scanning where configured |
| Unit tests | Every PR | Parsing, revision handling, features, lag cutoffs, splits, metrics, DST |
| Build and configuration | Every PR | Build wheel; strict docs build; validate dev, QA, and prod bundle targets |
| Integration | Every PR | Small controlled data through landing → Bronze → Silver → Gold, idempotency, and permissions; no production writes |
| QA deployment and jobs | Release PR | Run bounded pipeline and training tests, verify four zones, logs, output locations, and acceptance metrics |

Use deterministic fixtures for regular integration checks. Run Databricks
integration tests in an isolated QA namespace with a service identity, bounded
compute and test data. Make the required checks explicit; where credentials or
compute are unavailable, report the check as blocked rather than passing it.
Set retention and cleanup rules for test data.

## 3. Model registry and promotion

A release PR typically trains a new candidate in QA. Training records an MLflow
experiment and saves the model; registration adds a version to the QA model object.
The current rolling job does not yet save a deployable model. Add a candidate fit
that records data versions, code revision, feature version, parameters, metrics,
dependencies, and an input/output signature.

~~~mermaid
flowchart TB
    subgraph QA["QA · release pull request"]
        direction LR
        FIT[Train candidate] --> REG[Register QA version]
        REG --> EVAL[Evaluate against<br/>benchmarks and Champion]
        EVAL --> REVIEW[Stakeholder reviews PR]
    end
    subgraph PROD["Production · after approval and merge"]
        direction LR
        COPY[Copy approved QA version] --> CHECK[Validate copied model]
        CHECK --> ALIAS[Assign prod alias]
    end
    REVIEW -->|Approved and merged| COPY
~~~

Use these Unity Catalog model objects:

| Environment | Registered model |
| --- | --- |
| Dev | `eu_electricity_forecast_dev.ml_models.day_ahead_price_model` |
| QA | `eu_electricity_forecast_qa.ml_models.day_ahead_price_model` |
| Prod | `eu_electricity_forecast_prod.ml_models.day_ahead_price_model` |

For example, QA may register version 7. That version is a candidate; it does
not have the production `@prod` alias. On approval and merge, copy that exact
artifact to the prod model object. The copy receives its own version number,
which may differ from 7. After deployment and checks succeed, assign `@prod`
to the copied version; it is then the Champion used by production inference.
Record both version IDs, source run, PR commit, evaluation, and approval.

Compare the candidate, existing production Champion, and benchmarks on the
same reserved rows, overall and by zone. Give QA only the necessary read access
to the production model; use a separate promotion identity for prod model writes
and alias updates. Agree minimum coverage and acceptance thresholds in advance.
With no Champion, use the benchmarks and an initial acceptance gate. Reload and
validate the artifact before promotion; do not refit it afterward. If the gate,
review, deployment, or copy fails, keep the existing `@prod` alias unchanged.
Keep the previous version for rollback. Repeated release attempts must not
promote an unreviewed model. [Databricks model lifecycle](https://docs.databricks.com/aws/en/machine-learning/manage-model-lifecycle/)

A code deployment alone does not change the production model: the release must
also successfully complete the approved model promotion step.

### Code version and model lineage

Record the following on **every MLflow training run**, including experiments,
rolling evaluations, and candidate fits. Put the same identifiers on each
registered model version as searchable version tags:

| Identifier | Purpose |
| --- | --- |
| Package version | Value from `pyproject.toml`, for example `0.0.1` |
| Git commit | Exact source revision used to build the wheel |
| Wheel checksum | Identifies the built artifact used by the run |
| Environment and run ID | Where the model was trained and its MLflow run |
| Feature and source Delta versions | Identifies the training features and data |

Read the package version from the installed wheel metadata; CI verifies that it
matches `pyproject.toml` and records the commit and checksum at build time.
Pass build metadata to Databricks jobs with the artifact. A Git tag such as
`v0.0.1` identifies the released `main` commit; it is created later and is not
required for an earlier QA run. Several commits may share a package version, so
use the commit and checksum to distinguish them. For manual dev runs, record the
commit and whether the source had uncommitted changes; mark unavailable details
explicitly. Require complete, clean metadata for QA and production releases.

When copying an approved QA model into production, explicitly set and verify the
lineage tags on the **production model version**, including the QA model name and
version, original run ID, code version, commit, and artifact checksum. Do not
assume tags are automatically copied. Record the production version and approval
as well. This makes both model versions traceable to the same training run and
code even when their registry version numbers differ. Tracking this metadata is
planned; existing runs do not currently log the full set.

## Implementation order

1. Inventory existing resources and data; decide catalog migration and grants.
2. Parameterize and test input/output catalogs and environment-specific paths.
3. Add QA/prod bundle targets, identities, isolation checks, and manual QA runs.
4. Add CI, branch protection, release QA, stakeholder gate, tagging, and prod deployment.
5. Package and register candidates, implement evaluation gates and model promotion.
6. Exercise release failure, repeat deployment, model rejection, and rollback.
