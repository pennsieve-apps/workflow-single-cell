# App variants

Status: design, approved (2026-10-29). Joost's decisions on the open questions are in
section 12. Nothing is implemented yet.

One repository and one release tag can produce **several images**. Each image is a
separately runnable flavour of the same app, called a **variant**. The pilot is
`processor-seizure-dynasd`. It gets a `cpu` variant (the default: CPU PyTorch,
`computeTypes [standard]`) and a `gpu` variant (CUDA PyTorch, `computeTypes [gpu]`,
`MODEL` default `oncet`). Both are built from the same Dockerfile, which already takes
`ARG TORCH_FLAVOUR=cuda|cpu`.

Apps without `variants` behave exactly as they do today, and so do their versions,
images, workflows and runs.

## Summary of decisions

| Topic | Decision |
|---|---|
| Schema | Optional top-level `variants: [...]` in app.yml. Each variant has an `id` matching `[a-z0-9-]`. The default is the variant with `default: true`, otherwise the first. A variant may override `name`, `description`, `build`, `runtime` (cpu, memory, computeTypes, timeoutSeconds, llm) and the *defaults and choices* of existing parameters. It may **not** change inputs, outputs, the parameter set, interactive/session settings, or tags. |
| App Store identity | **Option B**: one application per repository (sourceUrl), with the variants inside each version. No new application records and no migration. |
| Builds | One release tag starts **N builds**, one per variant, each with its own deployment record and status. They go through the `buildStarter` from PR #204 (kaniko or CodeBuild/BuildKit). |
| ECR tags | The default variant keeps today's tag, `<hash>-<tag>`. Other variants get `<hash>-<tag>--<id>`. Each tag gets its own `<…>-soci` index from the existing SOCI index builder. `image_resolver` needs no change. |
| Partial failure | The version's status follows the **default** variant (as today). Each variant has its own status. The registry API refuses a variant whose build is not `deployed`. |
| Workflow step | `DagNode.variant`, optional with `omitempty`; empty means the default. Run start resolves the variant's manifest, stamps the resolved id onto the run snapshot when the version has variants, and defaults the execution target to the variant's first compute type. |
| Registry API | `GET /applications/store/registry?sourceUrl&version&variant`, where `variant` is optional. The provisioner passes it through and adds it to its cache key. |
| UI | The palette shows **one entry per app**, with variant chips. The step inspector gets a variant switch next to the version picker. Ports are identical by construction, so a switch never breaks links. |
| Version gate | workflow-service refuses a run (422) when a step uses a **non-default** variant and the compute node's `provisionerImageTag` is older than `VARIANTS_MIN_PROVISIONER_VERSION` (a config value), or cannot be read. |
| Notebooks | Notebook apps get variants in v1 (CPU and GPU kernels). There is one official notebook workflow per kernel and variant. Superseded (2026-10-30): a run may pick the variant (`processorConfigs[].variant`), so one notebook workflow per kernel serves every variant and the wizard shows a Hardware choice (section 6a). In v1, GPU sessions need a **secure**-mode node with GPUs. Variants may not change interactive or session settings, because CPU/GPU kernels don't need to. |
| Sequencing | PR #204 merges first. Variants are built on its `buildStarter` / `imagebuild.Request`, and both builders get build args and a target. |

---

## 1. What exists today (findings)

### app-deploy-service
- **Schema.** `lambda/service/manifestschema/app-manifest.v1.json` has top-level
  `additionalProperties: false`, so a `variants` key is a manifest problem today. In
  `warn` mode it is recorded on the version. In `enforce` mode the release is refused
  with 422. The schema must ship before any app uses `variants`. pennsieve-app keeps its
  own copy, `Applications/app-manifest.v1.schema.json`, for the manifest builder and
  validation.
- **Parsing.** `handler/app_config.go` maps app.yml to an `appConfig`. `appManifest(c)` is
  stored per version (`AppStoreVersion.manifest`). When the version is the newest,
  `appParameters(c)` is copied onto the application (`params`). `buildStorageGiB` gives
  100 GiB when `computeTypes` contains `gpu`.
- **Identity.** `appstore-applications` holds one record per `sourceUrl`, looked up through
  the `sourceUrl-index` GSI (`GetBySourceUrl`). Every caller takes `apps[0]`.
  `appstore-versions` holds one record per release, looked up through
  `applicationId-version-index`. Its fields are `version` (tag), `destinationUrl`,
  `registrationStatus`, `manifest`, `manifestProblems` and `license`.
- **Release.** `post_appstore_handler.go` finds or creates the application (the initial
  sharing from #202 applies only on creation; an archived app is refused). It then
  inserts a version (`registering`), syncs the repo and reads app.yml. Next it creates
  one deployment record (`applicationId = versionUuid`) and runs the
  **app-provisioner** Fargate task with `ACTION=ADD_TO_APPSTORE`.
- **Build.** `fargate/app-provisioner` `AddToAppstore` computes
  `destinationUrl = <APPSTORE_PRIVATE_ECR_URL>:<GenerateHash(sourceUrl)>-<tag>`, writes
  it to the version and starts **one** kaniko task
  (`--context <git>#refs/tags/<tag> --destination … --force`). PR #204 puts this behind
  `buildStarter`, which is either `kanikoStarter` (the same task) or `codeBuildStarter`
  (`imagebuild.Start` → `codebuild.StartBuild` running `build.sh`/`buildctl`). The
  BuildKit cache is per app: `<cache-repo>:app-<sha256(sourceUrl)>`, `mode=max`. A
  private repo's token goes into an SSM parameter named after the deployment id.
- **Status.** The status lambda follows ECS task-state events (and, with #204, CodeBuild
  build-state events). It reads `ApplicationId`/`DeploymentId`/`ApplicationsTable` from
  task tags or build variables, and sets the version's `registrationStatus` to
  `deployed` or `error`.
- **SOCI.** The AWS SOCI index builder indexes pushes to appstore-private and writes
  `<tag>-soci`. `image_resolver.resolveAppStoreImageURL` takes the tag from
  `destinationUrl`, looks up `<tag>-soci` and returns the SOCI-enabled digest. It works
  for any tag. (PR #204 keeps its build cache in a separate repository so cache pushes
  never reach the SOCI builder.)
- **Registry API.** `get_appstore_registry_handler.go`: sourceUrl → `apps[0]` (refused if
  archived) → version by tag → `destinationUrl` and `status == deployed` (otherwise
  *"version is not yet deployed"*) → `CanAccessAppIn` (visibility and grants from #202)
  → SOCI resolve.

### workflow-service
- **`models.DagNode`** has `sourceUrl`, `tag`, `computeType`, `inputs` (ports, with
  `omitempty` so old hashes are unchanged), `defaultParams` and `runtimeConfig`.
  `dag.ValidateDAG` requires a tag on processors.
- **`utils.ComputeConfigHash`** hashes the whole resolved DAG snapshot (minus
  position, paramSchema and resolvedParams). A new field therefore needs `omitempty`
  to keep existing instances.
- **Run start (`post_run_handler.go`).** Each step's manifest comes from
  `pinnedManifest(app, tag)` (`AppStoreVersionDBStore.GetManifest`). It drives
  cpu/memory defaults, compute-type validation, param defaults (`appParams`),
  `stampDeclared` (interactive, resources, LLM), `stampPortTargets` and
  `resolveLinkFolders`. The execution target falls back from `processorConfig` to
  `node.ComputeType` to `"standard"`. GPU steps are refused on nodes with
  `maxGpuInstances == 0`.
- **Other manifest readers.** `get_workflow_handler.go` enriches definitions with
  manifests and params, and `notebook.go` resolves the interactive app's manifest.
- **Usage.** Samples are kept under `APPUSAGE#<sourceUrl>` (`app_usage_store.go`), one
  per step per run, and aggregated by version.

### compute-node-aws-provisioner-v2
- `aslconverter.ResolveContainerImages` calls
  `/applications/store/registry?sourceUrl&version` once per `sourceUrl|tag` (cached),
  with the callback token.
- The compute type is the `processorConfigs` override, otherwise
  `node.RuntimeConfig.ComputeTypes[0]`, otherwise `standard`. `stepOverrides` sizes
  steps, and `capToGPUTier` caps GPU steps to the tier's instance.
- `DAGNode` has no variant. The provisioner is the only caller of the registry API.

### pennsieve-app (feat/builder-redesign)
- `appManifestInfo.versionManifest(app, tag)` reads `app.versions[].manifest`.
  `BuilderPalette` lists one entry per application.
- The step stores `sourceUrl`, `tag`, `computeType`, `cpu`, `memory` and params.
  `manifestDiff` compares two manifests when the version changes.
- `ApplicationDetail` lists versions. `ReleaseDialog` publishes one tag through
  github-service.

### github-service
- Tracked repositories, `PublishingToAppStore`, the release webhook and `POST
  /repository/{owner}/{repo}/release` are all keyed per repository and send
  `{source: {url, tag}}` to `POST /store`.
- With option B, **nothing changes** here: one release is still one call.

---

## 2. Schema

```yaml
# yaml-language-server: $schema=https://schemas.pennsieve.io/app-manifest/v1.json
application: { name: Seizure Onset (DynaSD), ... }
runtime:            # shared by all variants unless a variant overrides a field
  cpu: 4096
  memory: 8192
  timeoutSeconds: 14400
build:              # optional, shared build settings (also usable without variants)
  args: { DYNASD_COMMIT: 48b2b959... }
parameters: [ ... MODEL (default lindda), DEVICE (default auto), ... ]
inputs:  [ ... ]
outputs: [ ... ]
variants:
  - id: cpu
    name: CPU
    default: true
    build:   { args: { TORCH_FLAVOUR: cpu } }
    runtime: { computeTypes: [standard] }
  - id: gpu
    name: GPU (ONCET)
    description: CUDA PyTorch on an NVIDIA T4; runs the pretrained ONCET model by default.
    build:   { args: { TORCH_FLAVOUR: cuda } }
    runtime: { computeTypes: [gpu], memory: 14336 }
    parameters:
      MODEL: { defaultValue: oncet }
```

### Fields
- **`variants`**: an array of 1–8 items. Order matters: it is the display order, and the
  first item is the default unless another says `default: true`.
- **`id`** (required): `^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$`. It is unique within the
  file, and `default` is reserved.
  - It is the step's reference, part of the ECR tag and part of the usage key.
  - Keep it stable across releases. A workflow that refers to `gpu` keeps working when
    the next version still has `gpu`.
- **`default`** (boolean): at most one variant may set it. Without one, the first
  variant is the default.
- **`name`**: a short label for the selector and step card, such as "GPU (ONCET)". It
  defaults to the id. Cards show `<app name> · <variant name>`.
- **`description`**: a sentence on when to pick this variant. It does not replace the
  app's description.
- **`build`**, at the top level and per variant:
  - `args`: a map from `^[A-Za-z_][A-Za-z0-9_]*$` to a scalar, passed as `--build-arg`
    (kaniko) or `--opt build-arg:K=V` (BuildKit). A variant's args are merged over the
    top-level args.
  - `target`: a stage name, passed as `--target` (kaniko) or `--opt target=` (BuildKit).
  - `dockerfile`: a path relative to the repository root, without `..`, defaulting to
    `Dockerfile`. It is passed as `--dockerfile` (kaniko) or `--opt filename=` (BuildKit).
  - A top-level `build` without `variants` applies to the single image. It uses the same
    code path and is a free addition.
- **`runtime`** (per variant): `cpu`, `memory`, `computeTypes`, `timeoutSeconds` and
  `llm`. Each field replaces the top-level value; the fields are not deep-merged.
  `interactive`, `sessionType` and `kernel` are **forbidden** per variant, because a
  variant switch must not turn a batch step into a waitForTaskToken session.
- **`parameters`** (per variant): a **map** from the name of an existing top-level
  parameter to overrides of `defaultValue`, `validValues`, `required`, `label` and
  `description`. A variant cannot add or remove parameters. With the map shape
  (instead of a list), "override, don't declare" is visible in the file.

### Inheritance
A variant's **resolved manifest** is the top-level manifest with these changes applied:
- the variant's runtime fields replaced;
- the overrides applied to the named parameters;
- the variant's `name` and `description` added as `variant: {id, name, description}`.

`application`, `inputs`, `outputs` and `tags` always come from the top level.

### Validation (schema and server)
1. `variants[].id` matches the pattern, is unique and is not `default`. At most one
   variant has `default: true`.
2. Each key of `variants[].parameters` names a top-level parameter. An overridden
   `defaultValue` must be within any `validValues` and within `min`/`max`, whether those
   are overridden or inherited.
3. `variants[].runtime` contains no `interactive`, `sessionType` or `kernel`, and no
   `inputs`, `outputs` or `tags` appear on a variant (`additionalProperties: false`).
4. Build args are scalars, and `dockerfile` is relative without `..`.
5. A release tag of an app with variants must not contain `--`, which keeps ECR tags
   unambiguous (section 4). The server checks this and refuses with 422 in either
   validation mode.
6. Rules 1–4 are in JSON Schema where they can be expressed. The rest are server checks
   reported as `manifestProblems`, with the same warn/enforce behaviour as today.

**Why identical ports?** Switching a step between CPU and GPU is the whole point of the
feature. With identical inputs and outputs, `resolveLinkFolders`, `stampPortTargets`, the
builder's links and fan-in stay valid with no compatibility check, and a run snapshot
never mixes port shapes. An app that genuinely needs different ports is a different app
and belongs in a different repository.

**Why no tags per variant?** Tags place the app in the gallery. The GPU mark already
comes from the variant's `computeTypes`, so per-variant tags would only add noise to
search.

---

## 3. App Store identity: one application, variants inside versions (option B)

| | A: one application per variant (`sourceUrl` + variant) | **B: one application, variants in versions** |
|---|---|---|
| Listing and search | Each variant is its own card (DynaSD CPU, DynaSD GPU), which duplicates README, tags and summary. | One card with variant chips. Search also matches variant names. |
| Visibility and sharing (#202) | Grants per record: sharing DynaSD would mean sharing N records, and they can drift. | One set of grants, as today. A variant is not a separate permission. |
| Archiving | N records to archive. | One record, as today. |
| Usage stats | Need a new key or N keys. | `APPUSAGE#<sourceUrl>`, with `variant` on each sample and in the aggregation. |
| Identity lookups | `GetBySourceUrl` returns several records, and every `apps[0]` (registry, release, workflow-service `appStoreApp`) becomes wrong. | Unchanged. |
| Variants change between releases | Records appear and disappear across versions, so the versions of one app are split across records. | Each version lists its own variants. |
| Builder palette | N entries. | One entry, and a step switches variant in place. |
| Existing records | Need a composite key, a new GSI and a backfill. | No migration. A version without `variants` is a one-variant version. |
| github-service | One repo maps to N apps, so tracked-repo and publishing assumptions break. | No change. |

**Recommendation: B.** A variant is a build flavour of one piece of software, not a
separate product. Its README, license, citation, maintainers, sharing and lifecycle are
the app's.

### Stored shape (appstore-versions)
These fields are added to `AppStoreVersion` and all are optional:

```
manifest:        <resolved manifest of the DEFAULT variant>   # unchanged meaning for old readers
defaultVariant:  "cpu"
variantOrder:    ["cpu", "gpu"]
variants:                                       # DynamoDB map, keyed by id (atomic nested updates)
  cpu: { name, description, manifest: <resolved>, build: {args,target,dockerfile},
         destinationUrl, status, deploymentId, failureReason? }
  gpu: { ... }
```

- **`manifest` stays the default variant's resolved manifest.** Every existing reader
  (the app's `versionManifest`, workflow-service `GetManifest`, `UpdateParams`, the
  backfill) keeps working without knowing about variants, and simply sees the default.
- **Top-level `destinationUrl` and `registrationStatus` are the default variant's.** The
  registry API without `variant`, older provisioners and the existing status flow keep
  working.
- **`variants` is a map**, not a list, so the status lambda can update
  `variants.#id.status` atomically while N builds finish concurrently.
- **Application `params`** (newest version) are the default variant's.
- **API model.** `AppStoreVersion` JSON gains `defaultVariant` and
  `variants: [{id, name, description, default, status, manifest}]`, ordered by
  `variantOrder`. As now, it never includes `destinationUrl`. Deployments are listed
  with a `variant` field.

---

## 4. Versions and builds

### One release, N builds
`PostAppStoreHandler` changes as follows:
- It parses `variants`, validates them and stores the `variants` map with every status
  `registering` and one `deploymentId` per variant. It creates **one deployment record
  per variant**: `applicationId = versionUuid`, plus a new `variant` field.
- It still starts **one** app-provisioner task. That task reads `variants` from the
  version record instead of from environment variables, which avoids the 8 KB ECS
  override limit and keeps the lambda unchanged in shape.
- For each variant the task calls `buildStarter.Start` (PR #204) with an
  `imagebuild.Request`, extended with these fields:

```go
Variant    string            // "" for an app without variants
BuildArgs  map[string]string
Target     string
Dockerfile string
StorageGiB int32             // per variant: 100 when THIS variant's computeTypes has gpu
```

Joost's sequencing applies: **#204 merges first**, and variants are built on its
`buildStarter`. Both builders get the new fields:

- **kaniko** (`kanikoStarter` → `PrivateDeploy`): appends `--build-arg K=V` per arg (in
  sorted order, for reproducibility), `--target T` and `--dockerfile F`. It tags the
  task with `Variant=<id>`.
- **CodeBuild** (`codeBuildStarter` → `imagebuild.StartBuildInput`): adds the variables
  `BUILD_ARGS` (newline-separated `K=V`), `BUILD_TARGET`, `DOCKERFILE` and `VARIANT`.
  `build.sh` turns them into `--opt build-arg:K=V`, `--opt target=…` and
  `--opt filename=…`. The git-token SSM parameter is already per deployment id, so with
  per-variant deployment ids each build gets and deletes its own parameter.
- **Dockerfile guard** (#204, kaniko only): runs on each *distinct* `dockerfile`.

**N builds, not one multi-target job.** BuildKit could build both targets in one
CodeBuild job, but N builds are better here:
- **Status and logs.** Each build ends in its own state, and the status lambda contract
  stays "one build = one image = one status". The `stoppedReason` and log link are per
  variant.
- **Wall time.** The builds run in parallel, so wall time is the slowest variant's
  rather than the sum.
- **Sizing.** Each build gets its own compute: the GPU variant can need XLARGE disk
  while the CPU variant fits LARGE.
- **Same model for kaniko**, which has no multi-target mode anyway.
- **Retries.** A failed variant can later be rebuilt alone (follow-up ticket).

The cost is N git clones and N base-image pulls, and the cache recovers most of that.

**Build cache (CodeBuild).** Sharing between variants is fine because they come from the
same source, but **concurrent exports to one ref clobber each other**:
- The default variant keeps `app-<sha>`, so the cache stays warm when an app adopts
  variants.
- Other variants export to `app-<sha>--<id>`.
- Each build imports **its own ref and the default ref** (`--import-cache` twice), so
  shared layers (base image, requirements.txt, DynaSD install) hit across variants.

**Status lambda.** It reads `Variant` from the task tag or build variable.
- **With a variant:** it sets `variants.#id.status` (and `failureReason`). When the
  variant is the default, it also sets `registrationStatus` exactly as today.
- **Without a variant:** behaviour is unchanged.

Live updates (AppSync/Pusher) carry `variant`.

### ECR tag naming

| | Image tag in appstore-private | SOCI index |
|---|---|---|
| App without variants | `<hash>-<tag>` (unchanged) | `<hash>-<tag>-soci` |
| Default variant | `<hash>-<tag>` (unchanged) | `<hash>-<tag>-soci` |
| Other variant `gpu` | `<hash>-<tag>--gpu` | `<hash>-<tag>--gpu-soci` |

- **The default variant keeps today's tag.** A provisioner or any caller that doesn't
  know about variants gets a working image (the default), and adopting variants doesn't
  move an app's main image.
- **`--` is the separator.** A git tag could contain `--`, so release tags of apps with
  variants are refused when they contain it (validation rule 5). Variant ids have no
  leading, trailing or double dashes.
- **SOCI needs no change.** The SOCI index builder indexes every push to
  appstore-private, and `image_resolver` derives `<tag>-soci` from whatever tag is in
  `destinationUrl`.
  - Check during the pilot that a small CPU image is still indexed: the builder skips
    layers under its size threshold. A missing index only means a full pull.
- **Lifecycle rules.** Confirm that no tag-pattern lifecycle rule on appstore-private
  (managed outside this repo) treats `--` tags differently.

### Deployment status per variant
- **Version status = the default variant's status.** If the default fails, the version
  is `error`, as today. Non-default variants that succeed are still recorded and can be
  authorized explicitly, but the UI presents the version as failed.
- **A non-default variant that fails** leaves the version `deployed`, with that variant
  `error`. The registry API refuses that variant with
  *"variant gpu of v0.3.0 failed to build"*, and the builder greys it out with the
  reason.
- **No all-or-nothing rule** (decided). One flaky CUDA build should not take away the
  CPU app.
- **Recovery** today means a new release. A follow-up ticket adds
  `POST /applications/store/versions/{uuid}/variants/{id}/rebuild` for owners.

### Registry / authorize API
`GET /applications/store/registry?sourceUrl=…&version=…[&variant=…]`

- **`variant` empty or absent:** the top-level `destinationUrl` and status (today's
  behaviour, which is the default variant's).
- **`variant` given, version has variants:** `variants[variant]`.
  - If it is missing: 404 *"variant X not found for this version"*.
  - If its status is not `deployed`: 404 *"variant X of <tag> is not yet deployed"* (or
    *"failed to build"*).
- **`variant` given, version without variants:** 404 *"version <tag> has no variants"*.
  - The one exception is `variant == ""`. Explicitly asking for the default id of an
    app that later adopted variants does not apply here, because the version itself has
    none.
- **Access (#202) and archiving** are checked on the application, unchanged.
- **SOCI resolution** runs on the chosen `destinationUrl`.

---

## 5. Workflow step reference

### DagNode
```go
// Variant is the app variant the step runs (app.yml variants[].id); empty
// runs the version's default. omitempty keeps the config hash of existing
// workflows unchanged.
Variant string `json:"variant,omitempty" dynamodbav:"variant,omitempty"`
```

- **Definitions API** (POST/PUT workflow and versions): `variant` is accepted on
  processor nodes and must match the id pattern.
  - If the pinned version's manifest is available and declares variants, the id must be
    one of them. If the version declares none, `variant` must be empty (400 with a
    clear message).
  - This sits in the same place port links are validated against manifests.
  - An app that drops a variant in a later version does not break existing workflows,
    because they stay pinned to their tag. Changing the tag in the builder surfaces it
    (section 6).
- **Config hash.**
  - **Existing workflows** have an empty variant, so their hash is unchanged.
  - **Stamping the run snapshot.** When the step's pinned version declares variants,
    run start stamps the **resolved** id on the snapshot (empty becomes `cpu`), so the
    run records what ran. The version is immutable, so the stamp is deterministic and
    causes no instance churn. When the version has no variants, nothing is stamped.
- **Manifest resolution.**
  - `pinnedManifest(app, tag)` becomes `pinnedManifest(app, tag, variant)`, and
    `VersionManifest` gains `Variants map[string]VersionManifest`, `DefaultVariant` and
    `VariantOrder`.
  - With a variant, it returns `variants[variant].manifest`. Otherwise it returns
    `manifest`, which is the default.
  - Every consumer goes through it:
    - cpu/memory defaults;
    - compute-type validation;
    - `appParams` (param defaults such as `MODEL=oncet`);
    - `stampDeclared`;
    - `stampPortTargets` and `resolveLinkFolders` (identical across variants, so they
      are safe either way);
    - `get_workflow_handler` enrichment;
    - `notebook.go`.
- **Execution target.**
  - **Fallback chain:** `processorConfig.executionTarget` → `node.ComputeType` → the
    **variant's `computeTypes[0]`** → `standard`.
  - **Validation:** `node.ComputeType` is now checked against the variant's
    computeTypes too, not only an explicit request target. This catches a step switched
    to `gpu` that still says `standard`.
  - **Snapshot:** when a variant is set, `stampDeclared` also stamps the variant's
    `runtimeConfig.computeTypes`. The provisioner's own fallback
    (`RuntimeConfig.ComputeTypes[0]`) then agrees for steps the run didn't configure.
- **Run override** (added 2026-10-30, after the v1 rollout; first decided "not in v1").
  `processorConfigs[].variant` picks the variant for one run. The step runs the run's
  pick, else the definition's `variant`, else the version's default. The pick is checked
  like a saved variant (it must exist on the step's version; not on data nodes) and the
  same gates apply to the effective variant. A pick equal to what the step runs anyway is
  dropped, so it doesn't change the config hash. Definitions expose
  `notebook.variants` so the notebook wizard can offer them.
- **GPU gate.** The existing check (`executionTarget == gpu && maxGpuInstances == 0`)
  already refuses a GPU variant on a node without GPUs.
- **Provisioner version gate.** See section 5a.
- **Usage.** `AppUsageSample` gains `variant` (taken from the snapshot), and
  `AggregateAppUsage` groups by `(version, variant)`. The key stays
  `APPUSAGE#<sourceUrl>`. Older samples have no variant and count as the default.

### compute-node-aws-provisioner-v2
- `aslconverter.DAGNode` gains `Variant string json:"variant,omitempty"`.
- `fetchRegistryImage` adds `&variant=<id>` when the variant is non-empty.
- The cache key in `ResolveContainerImages` becomes `sourceURL|tag|variant`, and the
  log line includes the variant.
- The compute type logic is unchanged: workflow-service stamps the variant's
  computeTypes, and `capToGPUTier` still applies. DynaSD gpu at 14336 MiB fits the
  small tier's 15360.
- Notebook sessions are workflow steps, interactive ones run as `waitForTaskToken`. They
  go through the same `ResolveContainerImages`, so they get variants with no extra code
  (section 6a).

**Older provisioners.** A node running a provisioner without this change ignores
`variant` and gets the **default** image. A GPU-variant step would then run the CPU
image on a GPU instance, slowly, without an error. workflow-service therefore refuses
such runs up front (section 5a).

### 5a. Provisioner version gate (workflow-service)

The **trigger** is any run (batch or notebook) where some processor step's resolved
variant is **not** the version's default. The default variant's image is today's tag, so
any provisioner runs it correctly.

**Source.** The compute node's `provisionerImageTag`.
- account-service records it from the provisioner's EventBridge events
  (`eventbridge_handler.go`). It is in the compute-nodes table and returned by
  `GET api2/compute/resources/compute-nodes/{id}`.
- workflow-service already reads that table directly (`store_dynamodb.Node`), so it only
  adds the field `ProvisionerImageTag string dynamodbav:"provisionerImageTag"`. No API
  call is needed.

**Rule.** Read in post-run, next to the GPU and interactive gates, after the variants
are resolved and before the instance is built.
- **Semver tags:** a tag `vX.Y.Z` that parses as semver must be
  `>= VARIANTS_MIN_PROVISIONER_VERSION`.
- **The minimum is an environment variable** in the workflow-service Terraform, set to
  the first provisioner release with PROV-1 (for example `v1.26.0`). Raising it needs a
  config change, not code.
- **Other values:** `latest`, a commit sha, or a missing value cannot be compared. Dev
  has nodes on `latest`, on old semver tags and with no tag at all (checked read-only on
  2026-10-29). These values are refused, unless `VARIANTS_ALLOW_UNVERSIONED_PROVISIONER=true`,
  which is meant for dev only.
- **An empty `VARIANTS_MIN_PROVISIONER_VERSION`** turns the gate off. It is never empty
  in prod.

**Refusal.** HTTP **422** with a message naming the step, the node and the fix:

> Step "seizure-onset" runs the GPU variant of Seizure Onset (DynaSD), which needs
> compute node "Lab GPU" on provisioner v1.26.0 or later (it runs v1.24.0). Update the
> compute node, or switch the step to its default variant.

**UI.** The compute-node list also returns `provisionerImageTag`, so the run dialog and
the notebook wizard apply the same rule ahead of time. They disable such nodes, with the
same reason as a tooltip. The server check stays authoritative.

---

## 6. UI (pennsieve-app)

### Shared helpers
- `appManifestInfo.versionManifest(app, tag, variant)` returns
  `version.variants.find(id)?.manifest ?? version.manifest`.
- New helpers: `variantsOf(app, tag)` and `defaultVariant(app, tag)`.
- The bundled schema copy and `manifestValidation.js` accept `variants` and `build`.
- `AppManifestBuilder` keeps an existing `variants` block intact when editing. A full
  editor for variants is a later ticket.

### Builder palette
- **One entry per app**, which matches option B.
  - The entry shows variant chips (`CPU · GPU`). GPU marks come from each variant's
    computeTypes, in the title row, following the card style.
  - Search matches variant names and descriptions.
- **Adding a step.** Dragging or clicking the entry adds the default variant. Clicking a
  chip adds that variant.
- "Fits after" uses the default manifest, which is correct because ports are identical.

### Step inspector
- **Variant switch.** A segmented control appears next to the version picker when the
  pinned version has more than one variant. Each option shows its name, its compute
  mark, and its description as a tooltip. A switch sets `node.data.variant` and then:
  - **Compute type:** keeps `computeType` if the new variant supports it; otherwise
    sets it to the variant's first type.
  - **CPU and memory:** raised to the variant's minimum, the same rule as
    `applyDeclaredResources`.
  - **Parameters the user has not changed** follow the new variant's defaults (`MODEL`
    lindda → oncet). Values the user edited are kept, with a one-line note when they
    differ from the variant's default.
  - **Ports:** identical by schema, so links are untouched. As a guard against bad data,
    `manifestDiff` runs between the two variant manifests, and any input or output
    difference shows the same warning as a version change.
  - **No GPU on the node:** on a compute node with `maxGpuInstances == 0`, GPU variants
    are disabled with "This compute node has no GPUs".
- **Version switch.** If the chosen version lacks the step's variant, the step falls
  back to that version's default, with a notice ("v0.2.2 has no GPU variant; using the
  default").
- **Card.** The title row reads `<app> · <variant name>` when the step isn't renamed,
  with GPU, λ and LLM marks from the variant. Cards keep their shape and get no badge.
- **Showing the model and other variant-set parameters clearly.** Joost asked that the
  model is always visible. DynaSD's CPU variant defaults to `MODEL=lindda` and its GPU
  variant to `MODEL=oncet`, so the two give different results by default.
  - **Card:** the card's second line shows the value of every parameter that *any*
    variant of the app overrides, using the parameter's label: "Detector: ONCET". This
    holds whether the value came from the default or from the user.
  - **Inspector:** such parameters are listed first, under the variant switch. A note
    reads "the GPU variant defaults this to oncet; the CPU variant to lindda", so a
    switch never changes the model silently.
  - **Run views:** the step details show the same line.
- **Saving.** `variant` is sent only when it is non-default, or, to be explicit, when the
  user picked it. Empty means the default.

### Apps tab and ApplicationDetail
- **Cards** get the variant chips.
- **Versions list:** each version row shows a status per variant (deployed, building,
  error with reason). The version status is the default variant's.
- **New "Variants" section** for the newest version: id, name, description, computeTypes,
  cpu/memory, timeout, the parameter defaults that differ from the top level, and build
  args (shown to owners only).
- **Usage panel:** per version, split by variant.

### Release dialog
- **No new inputs:** one tag still releases all variants.
- After release, the progress view lists the variants with live status.
- The dialog warns, before publishing, when the tag contains `--` and the app.yml at the
  target has variants.

### Run views
- The run graph and step details show the variant that ran (stamped on the snapshot).

---

## 6a. Notebooks: CPU and GPU kernels (in v1)

Decided: notebook apps get variants in v1. The pilot is the Jupyter session app
(`pennsieve-jupyter-session`, `sessionType: jupyter`, `kernel: python`), with a `cpu`
variant (today's image) and a `gpu` variant (CUDA base and CUDA PyTorch).

### How sessions run today (findings)
- **The session is a run.** A notebook is a run of an **official** public notebook
  workflow: one per kernel, "Pennsieve - Jupyter Notebook", marked `official` by
  Pennsieve. The app's `NewNotebookDialog` wizard goes Kernel → Compute node → Dataset
  → Input files → Output → Review, and posts an ordinary run. It sends no
  `executionTarget`.
- **workflow-service** marks the step interactive from the pinned app.yml
  (`stampDeclared` / `applyDeclaredInteractive`). `annotateNotebooks` (through
  `manifestResolver`, cached per `app@tag`) sets each workflow's `notebook` kernel for
  the wizard.
- **Provisioner, task definition.** The image comes from the same
  `ResolveContainerImages`. `registerTaskDefinition` adds the session-proxy sidecar
  (`sessionType != ""`).
- **Provisioner, state machine.** The `InteractiveTarget` choice sends a step with
  `$.step.gpu` to `EnsureInteractiveGPU` → `RunInteractiveGPU`: `runTask.waitForTaskToken`
  on the GPU capacity provider, using `setLaunch(gpu)`.
- **Provisioner, gate.** `CheckInteractiveAllowed` refuses interactive steps when there
  are no sessions, on compliant nodes, and on basic nodes without the gateway.
- **Connect.** `GET /runs/{id}/session/connect` (workflow-service) discovers the task.
  - **Secure nodes:** registers an ALB rule to the task's **private IP**.
  - **Basic nodes:** reads the task ENI's **public IP**, writes an
    `interactive-routes` entry for the platform gateway, and the gateway refuses
    private addresses.
- **Environments.** The session-proxy (`environments.go`) unpacks an
  environments-service archive and restarts the kernels. Environments are built for a
  language runtime (Python 3.12) and know nothing about accelerators.

### What GPU sessions need, and the decisions

1. **Superseded (2026-10-30): per-run variant choice.** The wizard shows a Hardware
   choice from `notebook.variants` and sends the pick on the session step's
   processorConfig, so one notebook workflow per kernel serves CPU and GPU; the GPU twin
   workflows below were the v1 stopgap and are retired. Originally: per-run variant selection
   was out of v1, so the variant lived in the workflow definition: Pennsieve published
   "Pennsieve - Jupyter Notebook (GPU)" next to the CPU one. Its step has `variant: gpu`.
   - Everything downstream works unchanged: the execution target defaults to the
     variant's `[gpu]`, so `$.step.gpu` is set, the image is resolved with
     `&variant=gpu`, and the version gate applies.
   - The two workflows share the same app (`sourceUrl`) and tag, so they move together
     when the app is released.
2. **Interactive and session settings stay top-level only.** The schema rule from
   section 2 is kept. CPU and GPU kernels have the same `interactive`, `sessionType`
   and `kernel`; only the image (build args), `computeTypes`, `cpu`/`memory` and maybe
   `timeoutSeconds` differ.
   - A variant that changes the session kind or language would be a different app.
   - An R GPU kernel would be a variant of the R session app, not of the Python one.
3. **GPU sessions need a *secure*-mode node in v1.**
   - **Why basic nodes can't work yet:** on the GPU capacity provider (EC2, `awsvpc`),
     the task ENI gets **no public IP**. `setLaunch(gpu)` passes
     `AssignPublicIp: DISABLED`, and EC2 ignores it anyway. The basic-mode gateway route
     needs exactly that public IP and refuses private ones, so a basic GPU session would
     start and never connect.
   - **Secure nodes work:** they route through the account ALB to the private IP, which
     works for EC2 `awsvpc` tasks just as for Fargate.
   - **Where it is refused:**
     - workflow-service, at run start: an interactive step with a gpu execution target on
       a basic node gets 422, "GPU notebooks need a secure-mode compute node; this node
       is basic".
     - the converter, as defence in depth: `CheckInteractiveAllowed` gains the same rule
       (`gpu && basic`).
     - the wizard, ahead of time.
   - **Follow-up:** support basic-mode GPU sessions. Options are a gateway route to the
     GPU instance's public IP with a host port (bridge networking for interactive GPU
     tasks), or giving basic GPU instances a path the gateway can reach. This is
     separate work.
4. **The version gate (section 5a) applies** because `gpu` is not the default variant.
5. **Environments are variant-agnostic in v1.** An environment is a set of
   site-packages for Python 3.12, and works on either kernel.
   - The GPU kernel's CUDA PyTorch comes from the **image**.
   - An environment that brings its own `torch`, `tensorflow` or `jax` takes over the
     image's build when it comes first on `sys.path`. On a GPU kernel that can silently
     drop to a CPU build.
   - v1: the session's environment panel warns when the selected environment's
     `packages` list contains one of these on a GPU session.
   - Later: environments-service could record an accelerator target on a version and
     build against the CUDA wheel index.
6. **Cost and wait time.**
   - A GPU instance boots in about 3–5 minutes, and sessions run up to the 12 h
     interactive cap on a g4dn.
   - The wizard's review step says so: "GPU (NVIDIA T4). Starting takes a few minutes;
     the instance is billed while the notebook is open."
   - The existing heartbeat and teardown (`Teardown` stops the task) and the GPU reaper
     end the cost when the session ends.

### Notebook launch UI (`NewNotebookDialog`)
- **Kernel step.** Official notebook workflows are grouped by `notebook.kernel`. When a
  kernel has CPU and GPU workflows (the same `sourceUrl`, different `variant`), the step
  shows one kernel card with a **Hardware: CPU | GPU** toggle, and the toggle picks the
  workflow.
  - `annotateNotebooks` adds `notebook.variant` and `notebook.gpu` (from the variant's
    computeTypes) to each workflow so the app can pair them without reading manifests.
    Its cache key becomes `app@tag#variant`.
- **Compute node step.**
  - **CPU:** today's `interactiveCapable` filter.
  - **GPU:** additionally `maxGpuInstances > 0`, `deploymentMode == "secure"` and
    `provisionerImageTag >= min` (section 5a). Nodes that fail are disabled with the
    reason ("no GPUs", "basic-mode node", "provisioner v1.24.0; update the node").
  - The minimum version comes from workflow-service config, exposed in the run
    capabilities the app already reads, or a small config endpoint.
- **Review step:** the GPU wait and cost line from item 6.
- **Session page.** The header shows "GPU (T4)". The environment panel shows the
  warning from item 5.
- **Notebooks list:** GPU sessions carry the GPU mark.

### Jupyter session app
- app.yml gets `variants: [cpu (default), gpu]`.
  - **gpu:** build args select a CUDA PyTorch requirements set, `computeTypes: [gpu]`,
    and memory within the small GPU tier (≤ 15360 MiB, else `capToGPUTier` caps it).
  - The Dockerfile takes `ARG FLAVOUR=cpu|cuda`, as DynaSD does.
- The kernel image must keep the session contract for both flavours: port 8888 behind
  the sidecar, the IPython startup file for environments, uid 1000, and
  `NVIDIA_DRIVER_CAPABILITIES=compute,utility` for the GPU flavour.

---

## 7. Backward compatibility and migration

- **No data migration.**
  - Versions without `variants` are read exactly as now.
  - New fields are optional (`omitempty`) everywhere: the DynamoDB models, `DagNode`,
    the provisioner's `DAGNode` and the API JSON.
- **Existing workflows** have no variant. Their config hash, instances and snapshots are
  unchanged.
- **Existing runs** and their usage samples are untouched.
- **Old readers** of `version.manifest`, `destinationUrl` and `registrationStatus` see
  the default variant: older app builds, older workflow-service, older provisioners.
- **Older provisioners** ignore `variant` and get the default image (section 5).
- **Release order matters only for `enforce` mode:** an app.yml with `variants` released
  before the schema update is refused. Under `warn` mode it is recorded as a problem and
  built as a single image from the Dockerfile's default args.
- **`backfill-manifests`:** re-parsing old versions picks up `variants` only if their
  app.yml had them (none do), so it is harmless. It must use the new resolver so the
  default stays in `manifest`.

---

## 8. Rollout

**Sequencing:** app-deploy-service **PR #204 merges first** (default kaniko, so it
changes nothing until it is switched), and variants build on its `buildStarter`.

1. **app-deploy-service: schema and parsing**
   - Schema v1 gains `variants` and `build`, plus resolution, validation and storage.
   - No new builds yet: with the flag off, only the default variant builds and the
     others are recorded with status `disabled`.
   - pennsieve-app schema copy and validation update (-dev PR into dev plus a draft PR
     into main).
2. **app-deploy-service: builds**
   - Per-variant builds for kaniko and CodeBuild, per-variant status in the status
     lambda, the registry `variant` parameter, and API models.
   - Flag `APP_VARIANTS_ENABLED` per environment (Terraform variable, default `false`).
     Turn it on for dev.
3. **compute-node-aws-provisioner-v2:**
   - pass `variant` through;
   - the basic-mode GPU-interactive refusal;
   - release it, say v1.26.0, and update the dev nodes (shared and user-owned).
4. **workflow-service:**
   - `DagNode.variant`, validation and variant-aware manifest resolution;
   - execution-target default and snapshot stamping;
   - the **provisioner version gate**, with `VARIANTS_MIN_PROVISIONER_VERSION` set to
     the release from step 3;
   - the GPU-notebook refusal on basic nodes;
   - notebook annotations and usage by variant.
5. **pennsieve-app:**
   - palette, inspector (with the model line), app page, release progress and run views;
   - the notebook wizard's CPU/GPU toggle.

   These are data-driven: nothing shows until a version has more than one variant, or a
   GPU notebook workflow exists.
6. **Pilots on dev:**
   - DynaSD v0.3.0 (section 9).
   - The Jupyter session app with a gpu variant, plus the official
     "Pennsieve - Jupyter Notebook (GPU)" workflow, created only after the session app
     release is deployed.
7. **Production:**
   - apply app-deploy-service with the flag on;
   - release the provisioner, and update the nodes that want variants (the gate protects
     the rest);
   - deploy workflow-service with the prod minimum, then the app;
   - release DynaSD and the session app, and publish the GPU notebook workflow.

**Feature flags and configuration**
- `APP_VARIANTS_ENABLED` (app-deploy-service) gates the extra builds.
- `VARIANTS_MIN_PROVISIONER_VERSION` (workflow-service) sets the gate; empty turns it
  off.
- `VARIANTS_ALLOW_UNVERSIONED_PROVISIONER` (workflow-service; `true` on dev only)
  admits nodes whose tag is `latest`, missing or not semver.
- No UI flag is needed: the selector and toggle appear only for multi-variant versions.

---

## 9. Test plan (pilot: DynaSD)

**Today:** v0.2.2 is one CUDA image with `DEVICE=auto` and
`computeTypes [standard, gpu]`. Its Dockerfile has `ARG TORCH_FLAVOUR=cuda` and
requirements files per flavour.

**v0.3.0 app.yml:** the example in section 2, with `cpu` as the default
(`TORCH_FLAVOUR=cpu`, `[standard]`) and `gpu` (`TORCH_FLAVOUR=cuda`, `[gpu]`,
memory 14336, `MODEL=oncet`). `DEVICE` stays `auto` in both.

### Unit tests
- **app-deploy-service**
  - Schema accepts and rejects per the validation rules.
  - Resolution covers runtime replacement and parameter overrides, and the default goes
    into `manifest`.
  - Kaniko command lines (sorted build args, target, dockerfile) and CodeBuild variables.
  - Cache refs: own export plus default import.
  - The status lambda's nested update, and default → version status.
  - Registry API: no variant, a known variant, an unknown variant, a variant not yet
    deployed, and a variant on a version without variants.
  - Release refused when the tag contains `--`.
- **workflow-service**
  - A `DagNode` without a variant marshals byte-identically, with the same
    `ComputeConfigHash` as before.
  - Variant manifest resolution, including param defaults (`MODEL` = oncet for gpu).
  - The execution-target chain and node computeType validation.
  - Snapshot stamping only when the version has variants.
  - Definitions API rejects unknown variants.
  - Usage aggregation by variant.
- **Provisioner:** the registry URL gets `&variant=` only when non-empty, and the cache
  key includes the variant.
- **pennsieve-app:**
  - `versionManifest` with a variant;
  - variant switch effects (computeType, resources, untouched versus edited params);
  - version switch fallback.

### Dev end-to-end (kaniko, then again with `image_builder = codebuild`)
1. **Release v0.3.0.** Expect two builds, two deployment records, and ECR tags
   `<hash>-v0.3.0` (about 0.43 GB) and `<hash>-v0.3.0--gpu` (about 3.8 GB), each with a
   `-soci` index.
   - The version is `deployed`, with `variants.cpu/gpu.status = deployed`.
   - Check `TORCH_FLAVOUR` in each image's config (read-only `aws ecr batch-get-image`
     with the pennsieve-non-prod profile).
2. **Registry API.**
   - No variant returns the cpu image (SOCI digest); `variant=gpu` returns the cuda image.
   - `variant=nope` and `variant=gpu&version=v0.2.2` return 404 with clear messages.
3. **Existing workflow pinned to v0.2.2:** it runs unchanged and reuses its existing
   instance, because the hash is unchanged.
4. **New workflow with DynaSD v0.3.0, default variant:**
   - runs on Fargate standard;
   - `provenance.json` shows device cpu;
   - the image is the small one, so the image pull is much shorter than v0.2.2's.
5. **Switch the step to `gpu` in the builder.**
   - The compute type becomes gpu, memory 14336, and `MODEL` shows oncet. Links are
     unchanged.
   - The run lands on the GPU capacity provider (g4dn), `provenance.json` shows cuda,
     and ONCET finishes well under the CPU's ~5 minutes per recording.
6. **Same workflow on a compute node without GPUs:** refused with the
   `maxGpuInstances` message, and the UI disables the GPU option.
7. **Failure path.**
   - Release a test tag whose gpu variant uses a non-existent `target`. The version is
     `deployed`, gpu is `error` with its `stoppedReason`, and the UI shows the reason.
   - A run with the gpu variant fails at authorization with "failed to build".
   - Then break the default instead: the version is `error`.
8. **CodeBuild only:** release again and check cache hits for both variants (the gpu
   build imports the cpu cache for shared layers). Check the build times.
9. **Usage:** the app page shows separate rows for v0.3.0 cpu and gpu.
10. **Sharing and archiving (#202):** a user without access is refused for both
    variants. Archiving the app refuses both.
11. **Version gate.**
    - Run the gpu variant on a dev node with an older `provisionerImageTag` (dev has
      v1.24.0 and v1.20.1 basic nodes): 422 with the node, version and fix.
    - The default variant on the same node runs.
    - A `latest` node is refused unless `VARIANTS_ALLOW_UNVERSIONED_PROVISIONER=true`.
12. **Model display.** In the builder, the DynaSD cpu step shows "Detector: LiNDDA" and
    the gpu step "Detector: ONCET". Switching variants moves the default and shows the
    note. A user-set model survives the switch.

### Notebook pilot (dev)
13. Release the Jupyter session app with `cpu` and `gpu` variants. Publish the official
    GPU notebook workflow.
14. **Wizard.**
    - One Python kernel card with a CPU|GPU toggle.
    - With GPU selected:
      - basic nodes are disabled ("basic-mode node");
      - nodes without GPUs are disabled;
      - old-provisioner nodes are disabled with the version.
    - The review step shows the GPU wait and cost line.
15. **GPU session on a secure dev node with GPUs.**
    - The task runs on the GPU capacity provider (`RunInteractiveGPU`) and connects
      through the ALB.
    - `torch.cuda.is_available()` is True, and `nvidia-smi` shows the T4.
    - Closing the session stops the task, and the GPU instance is reaped.
16. **Environments on the GPU kernel.**
    - A plain environment (pandas) works on the GPU kernel.
    - An environment pinning `torch` shows the warning.
17. **CPU session** behaves exactly as today. Old official notebook workflows are
    unchanged and keep their hash.
18. **Forced basic GPU run** (API, bypassing the UI): 422 from workflow-service. With
    workflow-service bypassed, the converter's refusal applies.

---

## 10. Work breakdown (tickets)

Sizes: S ≤ 1 day, M 2–3 days, L about a week. "→" marks the ticket ids a ticket depends
on.

| Id | Title | Repo | Description | Size | Depends on |
|---|---|---|---|---|---|
| ADS-0 | Merge CodeBuild/BuildKit image builder | app-deploy-service | Merge PR #204 (`image_builder`, default kaniko). Variants build on its `buildStarter`. | — (in review) | — |
| ADS-1 | app.yml `variants` + `build`: schema, parsing, storage | app-deploy-service | Manifest schema v1 gains `variants` (max 8) and `build`. Resolve each variant's manifest, validate (ids, parameter overrides, no ports/interactive/tags, `--` tag rule), and store `variants`/`defaultVariant`/`variantOrder` on the version. `manifest` and app `params` hold the default. Backfill uses the resolver. Manifest docs. | M | — |
| ADS-2 | Build every variant on release | app-deploy-service | One deployment record per variant. app-provisioner reads the variants and calls `buildStarter` per variant. `imagebuild.Request` gets BuildArgs/Target/Dockerfile/Variant. kaniko gets `--build-arg`/`--target`/`--dockerfile`. Storage per variant. ECR tags: `<hash>-<tag>` for the default, `<hash>-<tag>--<id>` for the rest. `APP_VARIANTS_ENABLED` flag. | M | ADS-0, ADS-1 |
| ADS-3 | BuildKit build args, target and per-variant cache | app-deploy-service | `build.sh` gets `build-arg:`/`target=`/`filename=` options and a `VARIANT` variable. Each build exports its own cache ref and imports the default's. | S | ADS-2 |
| ADS-4 | Per-variant build status | app-deploy-service | The status lambda takes `Variant` from the ECS tag or CodeBuild variable and updates `variants.<id>.status` and the failure reason. The version status follows the default. Live events carry the variant. | M | ADS-2 |
| ADS-5 | Registry API `variant` + API models | app-deploy-service | `GET /applications/store/registry?…&variant=`, with clear 404s (unknown, not deployed, failed, version has none). `variants` in the version JSON and `variant` on deployments. OpenAPI/readme.io. | S | ADS-1 |
| ADS-6 | Rebuild one variant (follow-up) | app-deploy-service | Owner endpoint and UI action to rebuild a failed variant of an existing version. | S–M | ADS-4 |
| PROV-1 | Pass the step variant to the registry | compute-node-aws-provisioner-v2 | `DAGNode.Variant`. Registry call adds `&variant=` when set. Cache key `sourceUrl\|tag\|variant`. Logs and tests. Released as the version the gate requires. | S | ADS-5 |
| PROV-2 | Refuse GPU interactive steps on basic nodes | compute-node-aws-provisioner-v2 | `CheckInteractiveAllowed`: an interactive step with a gpu target on a basic-mode node gets a clear error, because the GPU task has no public IP for the gateway. | S | — |
| PROV-3 | Basic-mode GPU notebooks (follow-up) | compute-node-aws-provisioner-v2 + workflow-service | Make GPU sessions reachable through the platform gateway on basic nodes, for example with a host-port route to the GPU instance. Design first. | L | PROV-2 |
| WFS-1 | `DagNode.variant` + definitions validation | workflow-service | Field with `omitempty`. The definitions API accepts and validates it against the pinned version (unknown, or set on a version without variants: 400). Test that the hash is unchanged. | S | ADS-1 |
| WFS-2 | Variant-aware manifest resolution | workflow-service | `VersionManifest` gains variants; `pinnedManifest(app, tag, variant)`. Used by run start (resources, parameter defaults, `stampDeclared`, ports), get workflow, and `manifestResolver`/`annotateNotebooks` (cache key with the variant; `notebook.variant`/`gpu`). | M | WFS-1 |
| WFS-3 | Execution target and snapshot from the variant | workflow-service | Execution target falls back to the variant's `computeTypes[0]`. Validate `node.ComputeType` against the variant. Stamp the resolved variant and computeTypes on the run snapshot only when the version has variants. | S | WFS-2 |
| WFS-4 | Provisioner version gate for variants | workflow-service | Read `provisionerImageTag` from the compute-nodes table. A run with a non-default variant needs semver ≥ `VARIANTS_MIN_PROVISIONER_VERSION`, else 422 with the step, node, version and fix. `VARIANTS_ALLOW_UNVERSIONED_PROVISIONER` for dev. Terraform variables. | S | WFS-3, PROV-1 (release number) |
| WFS-5 | Refuse GPU notebooks on basic nodes | workflow-service | Run start: an interactive step with a gpu target on a basic-mode node gets 422 with an explanation. Mirrors PROV-2. | S | WFS-3 |
| WFS-6 | App usage by variant | workflow-service | `AppUsageSample.variant` from the snapshot. Aggregation by (version, variant). Old samples count as the default. | S | WFS-3 |
| APP-1 | Variant helpers + schema copy | pennsieve-app | Bundled schema and `manifestValidation` accept `variants`/`build`. `versionManifest(app, tag, variant)`, `variantsOf`, `defaultVariant`. The manifest builder keeps the `variants` block. | S | ADS-1 |
| APP-2 | Palette variant chips | pennsieve-app | One entry per app with variant chips and GPU marks. A chip adds that variant. Search matches variant names. | S | APP-1 |
| APP-3 | Step inspector variant switch + model line | pennsieve-app | Variant switch beside the version. Compute type, resources and untouched parameter defaults follow the variant. Variant-set parameters (DynaSD "Detector") are shown on the card and first in the inspector, with a note. GPU options disabled on nodes without GPUs. Version-switch fallback. Card title `<app> · <variant>`. | M | APP-1, WFS-1 |
| APP-4 | Apps page, release progress, run views | pennsieve-app | Variant chips on cards. Status per variant per version. Variants section. Usage by variant. Release progress per variant. Run views show the variant and model line. | M | ADS-5, WFS-6 |
| APP-5 | Run/launch node gating | pennsieve-app | In the run dialog, disable compute nodes that fail the version gate (non-default variants), with the reason. The minimum comes from workflow-service config. | S | WFS-4 |
| APP-6 | Notebook wizard CPU/GPU toggle | pennsieve-app | `NewNotebookDialog`: pair official kernel workflows by `sourceUrl`/variant into one card with a Hardware toggle. GPU node filter (GPUs, secure mode, provisioner version) with reasons. GPU wait and cost line. GPU mark in the session header and notebooks list. | M | WFS-2, WFS-4, WFS-5 |
| APP-7 | Environment warning on GPU sessions | pennsieve-app | The environment panel warns when the selected environment ships torch/tensorflow/jax on a GPU session. | S | APP-6 |
| APP-8 | Manifest builder editor for variants (later) | pennsieve-app | Edit variants in `AppManifestBuilder`. | M | APP-1 |
| JUP-1 | GPU variant of the Jupyter session app | pennsieve-jupyter-session | Dockerfile `ARG FLAVOUR=cpu\|cuda` (CUDA PyTorch). app.yml `variants: [cpu (default), gpu]` with `computeTypes [gpu]` and memory within the small tier. Session contract kept. Release on dev. | M | ADS-2 |
| JUP-2 | Official GPU notebook workflow | workflow-service (ops) | Create "Pennsieve - Jupyter Notebook (GPU)" (official, public; step `variant: gpu`) on dev, then prod, after JUP-1 is deployed. | S | JUP-1, WFS-1 |
| DYN-1 | DynaSD cpu/gpu variants | processor-seizure-dynasd | app.yml: `cpu` (default, `TORCH_FLAVOUR=cpu`, `[standard]`, MODEL lindda) and `gpu` (`TORCH_FLAVOUR=cuda`, `[gpu]`, 14336 MiB, MODEL oncet). README "Variants". Release v0.3.0 on dev, then prod. | S | ADS-2, ADS-4, ADS-5 |
| E2E-1 | Variants end-to-end on dev | (all) | Run section 9: DynaSD plus the notebook pilot, with kaniko and then CodeBuild, including the version gate and the failure paths. | M | DYN-1, JUP-2, PROV-1, WFS-4, APP-3, APP-6 |
| GH-0 | (no change) | github-service | One release is still one `POST /store`. | — | — |

Total: about 4–5 weeks of work across six repositories, most of it parallelisable after
ADS-1.

---

## 11. Remaining open points

None block the start of work:
- **Prod minimum provisioner version:** the release number of PROV-1, filled in when it
  is cut.
- **Basic-mode GPU notebooks (PROV-3)** need their own short design before ticketing in
  detail.

---

## 12. Decisions (Joost, 2026-10-29)

1. **The default variant keeps the unsuffixed tag** `<hash>-<tag>`. Other variants are
   `<hash>-<tag>--<id>`.
2. **Partial failure:** a failed non-default variant leaves the version usable. Only that
   variant is refused.
3. **Per-run variant selection** (v1 shipped without it; added 2026-10-30): a run may
   pick the variant (`processorConfigs[].variant`); notebooks get CPU/GPU through one
   workflow per kernel with a Hardware choice.
4. **Notebook apps get variants in v1** (CPU and GPU kernels), as designed in section
   6a.
   - Variants still may not change interactive or session settings; CPU/GPU kernels
     don't need it.
   - GPU sessions need a secure-mode node until PROV-3.
5. **Refuse runs whose node can't honour variants.** workflow-service compares the
   node's `provisionerImageTag` with the configurable `VARIANTS_MIN_PROVISIONER_VERSION`
   and refuses with a clear 422 (section 5a).
6. **DynaSD:** the GPU variant defaults `MODEL=oncet` and the CPU variant stays `lindda`.
   The step UI shows the model clearly (section 6, "Showing the model").
7. **Every variant is built on every release, with a cap of 8 variants.**
