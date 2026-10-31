# Brief for building an app of the single-cell workflows

You are building one or more Pennsieve processor apps, each in its own git repo under
`~/code/pennsieve/<repo>` (remote `git@github.com:pennsieve-apps/<repo>.git`, public, already
created and empty). Repo names: `sc-prep`, `sc-qc`, `sc-integrate`, `sc-annotate`, `sc-markers`,
`sc-report`, `atac-prep`, `atac-regulome`; `st-compare` exists already and is reused. The apps
are for broad use (any tissue, human/mouse/rat, scRNA-seq or snRNA-seq, 10x 3'/5'/Flex/Multiome),
not only the demo data. The first users are the HEAL RE-JOIN (knee, spine, TMJ: cartilage,
synovium, meniscus, bone, fat pad, masseter) and PRECISION Human Pain (DRG, spinal cord,
trigeminal and sympathetic ganglia) consortia; think of their data when choosing defaults
(nuclei, high mito/ambient in neurons, many donors, multiome).

- **A. Single-nucleus Atlas Mapping & Condition Comparison:**
  `sc-prep → sc-qc → sc-integrate → sc-annotate → {sc-markers, st-compare} → sc-report`
- **B. Multiome Gene Regulation:** `atac-prep → atac-regulome (+ labels from sc-annotate) → sc-report`

## Read first

1. `~/code/pennsieve/workflow-single-cell/docs/conventions.md`: the file and port contract.
   Follow it exactly. If something is wrong or missing, say so in your report instead of
   silently diverging.
2. Finished apps to copy layout, tone and level of care from: `~/code/pennsieve/st-prep` (prep:
   detection, sample sheets, streaming), `~/code/pennsieve/st-qc` (QC + normalization),
   `~/code/pennsieve/st-celltype` (clustering + marker typing), `~/code/pennsieve/st-compare`
   (Lambda-capable light step), `~/code/pennsieve/st-report` (report: templates, figures,
   citations gathered from provenance), `~/code/pennsieve/cell2location` (GPU/CPU torch app).
   Read their README, app.yml, Dockerfile, Makefile, tests and CITATION.cff.
3. `docs/app-manifest.v1.schema.json` (validate `app.yml` with jsonschema in a venv in your
   scratch dir; do NOT add `application.relations`) and `docs/app-variants.md` (CPU/GPU variants;
   working example `~/code/pennsieve/processor-seizure-dynasd`).

## Every repo has

`app.yml` (`# yaml-language-server: $schema=https://schemas.pennsieve.io/app-manifest/v1.json`,
`schemaVersion: "1.0"`, application name/description/type processor/tags, runtime
cpu/memory/computeTypes/timeoutSeconds, parameters, inputs, outputs; `variants` where a GPU helps).
Tags: `single-cell`, `transcriptomics`, `genomics`, plus `single-nucleus`, `multiome`, `atac-seq`
where relevant, task tags (`quality-control`, `integration`, `clustering`, `cell-typing`,
`differential-expression`, `gene-regulation`, `report`, …) and research fields `neuroscience`,
`pain`, `musculoskeletal` where the defaults target them.

`Dockerfile` (linux/amd64; base pinned by digest; pinned package versions; non-interactive; **no
network at run time**: models, annotations, motif databases baked in at build time with pinned
versions and checksums; runs as a non-root user; as small as reasonable; `python:3.12-slim` +
pinned `requirements.txt`; CPU-only torch from the PyTorch CPU index unless the variant is gpu),
`processor/` code, `tests/` (pytest on a tiny synthetic fixture from `tests/make_fixture.py`, no
Docker), `Makefile` (`test`, `fixture`, `run` = docker compose over a fixture into
`data/output/`, `clean`), `docker-compose.yml`, `dev.env`, `sample-input/README.md`,
`README.md`, `THIRD_PARTY.md` (every bundled component and data file with its licence),
`LICENSE` (Apache-2.0 for our code; if the image bundles GPL code, say so in THIRD_PARTY.md and
the README), `VERSION` (`0.1.0`), `CITATION.cff`, `.gitignore`, `.dockerignore`.

Light steps (sc-markers, sc-report, and anything that fits) declare
`computeTypes: [standard, lambda]` with memory ≤ 3008 MB and a `processor/handler.py` Lambda
entry like st-compare. Heavy ones are `standard` (and `gpu` via variants), sized from your
measurement.

Python 3.12. Pin exact versions, newest on PyPI as of 2026-10-31 (check): scanpy, anndata,
scvi-tools, cellbender, harmonypy, scrublet or scDblFinder-equivalent in Python, celltypist,
snapatac2, pydeseq2, leidenalg/igraph. Use `scanpy.pp.neighbors` +
`scanpy.tl.leiden(flavor="igraph", n_iterations=2)`.

## Simplicity and security (reviewed independently after you finish)

- Smallest code that does the job well; one clear path per input type; no speculative options.
  Prefer library calls over re-implementations. No dead code, no commented-out code.
- Treat every input file as untrusted: never `eval`/`pickle.load`/`torch.load(weights_only=False)`
  on input data; parse `.rds` only through a pinned reader in a subprocess with limits; reject
  paths that escape `$INPUT_DIR` (resolve symlinks, check the prefix); cap sizes you read into
  memory; no shell=True; no secrets in images, logs or provenance.
- Containers run as non-root, read-only except `$OUTPUT_DIR` and `/tmp`.
- Validate parameters up front and exit 2 with a clear message.
- Best practice for the science: follow the single-cell best-practices guide (Heumos et al. 2023,
  doi:10.1038/s41576-023-00586-w) and say in the README where you deviate and why.

## README

What the app does and why, for a biologist and for a computational analyst; port table;
parameter table (defaults and effect); outputs with columns; runtime (cpu/memory chosen and
**measured** peak memory and wall time on the demo data, noting amd64 emulation on an M-series
Mac); limitations (what the method cannot tell you); licences; `## Credits` and `## Citations`
(see conventions.md → Credits: **verify every reference against Crossref/PubMed/DataCite/Discover
before writing it, never from memory**).

## Test on real data

Demo data is described in conventions.md (read-only; do not modify `data/drg-c2`,
`data/drg-atlas`, `data/sympathetic-multiome`). If a download is still running, start with your
fixture and come back. Put your outputs for the next builders under
`~/code/pennsieve/workflow-single-cell/data/work/<demo>/<step>/<port>/`. Local Docker has 8 GB
shared with other builders running in parallel: subsample, use `--memory`, measure peak memory,
remove your containers, build with `--platform linux/amd64`. GPU variants can't be run locally:
build their image and check it imports; say so.

## Git

`git init -b main` if needed; commit with clear messages ending with
`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`, as
`-c user.name="Joost Wagenaar" -c user.email="1605953+jwagenaar@users.noreply.github.com"`; add
the remote `git@github.com:pennsieve-apps/<repo>.git`. **Do not push, tag or create releases**:
the coordinator does.

## Report back

Short: repo path, commits, ports and parameters as built, what you ran it on and the results
(numbers), measured memory/time, image size, chosen cpu/memory, citations with DOI/PMID and how
each was verified, deviations from conventions.md, open problems.
