# Conventions shared by the single-cell apps

Every app in the single-cell workflows follows this contract, so any step's output can be linked
to any other step's input without glue. "Single-cell" covers single-cell and single-nucleus
RNA-seq (scRNA-seq, snRNA-seq, 10x Flex) and the RNA + ATAC halves of single-nucleus multiome
(10x Cell Ranger ARC).

Workflows:

- **A. Single-nucleus Atlas Mapping & Condition Comparison** (any scRNA/snRNA data):
  `sc-prep → sc-qc → sc-integrate → sc-annotate → {sc-markers, st-compare} → sc-report`
- **B. Multiome Gene Regulation** (Cell Ranger ARC):
  A's RNA chain gives the cell types; `atac-prep → atac-regulome (+ labels from sc-annotate) → sc-report`

`st-compare` (from the spatial suite, `~/code/pennsieve/st-compare`) is reused unchanged for
composition and pseudobulk differential expression: it reads per-sample `.h5ad` files with counts,
a `labels.csv` and a `samples.csv`, and needs no spatial coordinates. Its input port is called
`spatial`; a workflow links `sc-*`'s `cells` output to it.

## Runtime contract (Pennsieve processors)

- Read from `$INPUT_DIR`, write to `$OUTPUT_DIR`. Parameters arrive as environment variables
  named as in `app.yml`. Never write elsewhere except `/tmp`.
- One output port: it is the whole `$OUTPUT_DIR`. Two or more outputs: each is written to
  `$OUTPUT_DIR/<name>/`.
- Inputs with several ports use `placement: subfolder`: each arrives at `$INPUT_DIR/<name>/`.
  Apps must also work when no links are used (everything side by side at the root of
  `$INPUT_DIR`), by finding files by name anywhere under `$INPUT_DIR` (follow symlinks;
  INPUT_DIR entries are symlinks).
- Several links may feed the same input port; their entries land side by side and two entries
  with the same name fail the run. Apps that can run more than once in a workflow, or whose
  output can be one of several links into the report (sc-markers, atac-regulome), write
  everything under a folder named after their `LABEL` parameter (default: the label column used,
  `_` → `-`).
- Port names: lowercase letters, digits and `-` (no underscores).
- Each output folder gets a `provenance.json`: app name + version (from `VERSION`), tool versions
  (`importlib.metadata.version`), parameters, inputs seen (relative paths), timings, per-sample
  status, peak RSS, and `citations`: the DOIs of the methods the step used (the report gathers
  these).
- A sample that fails is logged and skipped; the step fails only when every sample failed or no
  input was found. Exit 1 = no usable input / every sample failed, 2 = bad parameters.
- Logs go to stdout, one line per sample with counts and timings. Never log file contents or
  metadata values beyond counts (subject sheets may hold clinical fields).
- Memory: per-sample work streams one `.h5ad` at a time. Joint steps (integration, reference
  mapping) hold all cells, CSR sparse, float32; document the cells-per-GiB they need. Never
  densify a full matrix. Target: 300 k nuclei × 30 k genes fits in 30 GiB for joint steps.
- Threads: `THREADS` (default `0` = from the cgroup CPU quota); set `OMP_NUM_THREADS`,
  `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS`, `NUMBA_NUM_THREADS` before importing numpy.
- Reproducible: every random step takes `SEED` (default `42`).
- **No network at run time.** Everything a tool downloads (genome annotations, motif databases,
  models, fonts) is baked into the image at build time, pinned by version and checksum.
- GPU: apps that can use one declare app.yml `variants` (`cpu` default, `gpu`), see
  `docs/app-variants.md` in this repo (from workflow-service), and `~/code/pennsieve/processor-seizure-dynasd` (app.yml, Dockerfile `TORCH_FLAVOUR`) as a working example:
  the gpu variant builds a CUDA image (`build.target`/`build.args`) and declares
  `computeTypes: [gpu]`; the cpu variant uses CPU-only torch. Code picks the device with
  `torch.cuda.is_available()` and logs it.

## Files

### The AnnData contract (`cells` ports)

`<sample_id>.h5ad`, one per sample, written by `sc-prep` and rewritten by later steps (which
only drop observations/genes and add layers, `obs` columns and `obsm` embeddings).
`sample_id` matches `[A-Za-z0-9_-]+`.

| Slot | Content |
|---|---|
| `X` | From sc-prep: raw UMI counts, CSR, integer-valued. From sc-qc on: log-normalized (`normalize_total` to `TARGET_SUM`, `log1p`) float32 CSR; raw counts in `layers["counts"]` (after ambient removal when it ran; the uncorrected counts in `layers["counts_raw"]`). Apps that need counts read `layers["counts"]` when present, else `X`. |
| `obs_names` | `<sample_id>:<barcode>`, unique across samples. The barcode is as in the source (for ARC, the same barcode in RNA and ATAC, so `atac-prep` produces the same `obs_names`). |
| `obs["sample_id"]` | categorical. |
| `obs["total_counts"]`, `obs["n_genes_by_counts"]`, `obs["pct_counts_mt"]`, `obs["pct_counts_ribo"]` | Written by sc-prep (`scanpy.pp.calculate_qc_metrics`; mito = `MT-`/`mt-`, ribo = `RPS`/`RPL` prefixes, case-insensitive). |
| From sc-qc | `obs["doublet_score"]`, `obs["predicted_doublet"]`, `obs["qc_pass"]` is not kept (failing cells are dropped); `obs["ambient_fraction"]` when ambient removal ran. |
| From sc-integrate | `obsm["X_pca"]`, `obsm["X_integrated"]` (scVI latent or Harmony-corrected PCs, method in `uns["pennsieve"]["integration"]`), `obsm["X_umap"]`, `obs["cluster"]` (`C01`, …, shared across samples). |
| From sc-annotate | `obs["cell_type"]`, `obs["cell_type_score"]` (confidence 0–1), `obs["cell_type_source"]` (`reference`, `markers`, `model`). |
| Sample metadata | Every column of the sample's row in `samples.csv` (below) except `path`, as categorical `obs` columns (`condition`, `sex`, `donor`, `tissue`, …). |
| `var_names` | Gene symbols, made unique. `var["gene_ids"]` (Ensembl ids), `var["feature_types"]`; only `Gene Expression` features are kept (ARC `Peaks` go to atac-prep). `var["mt"]`, `var["ribo"]` booleans. |
| `uns["pennsieve"]` | `source` (input path), `chemistry` when known (`3p`, `5p`, `flex`, `multiome`), `organism` (`human`, `mouse`, `rat`; from metadata, else from gene symbol case/Ensembl prefix), `prep_version`, then each step adds its own key (`qc`, `integration`, `annotation`) with its parameters. |

Observations keep their order; apps never reorder `obs` of a file they rewrite.

Gene sets may differ between samples after sc-qc. Apps that combine samples use the genes present
in every sample and say how many in their log and `summary.json`.

### Droplets (`droplets` port of sc-prep)

When a sample has a raw (unfiltered) matrix (`raw_feature_bc_matrix.h5`,
`sample_raw_feature_bc_matrix.h5` for Flex, or the `raw_feature_bc_matrix/` MTX folder), sc-prep
copies it as `<sample_id>.raw.h5` (10x HDF5; MTX is converted). sc-qc uses it for ambient RNA
removal; without it, ambient removal is skipped for that sample with a warning.

### `samples.csv` (in the `metadata` port, written by `sc-prep`)

One row per sample. Required columns: `sample_id`, `path` (relative to the input), `source`
(`cellranger`, `cellranger-arc`, `h5ad`, `rds`), `n_obs`, `n_genes`, `organism`. Then metadata
columns, from (in order of precedence):

1. A user sample sheet (`SAMPLE_SHEET`, default `samples.csv`, needs `sample_id` and `path`;
   `path` matches when it equals or ends the sample's path). With a sheet, only its rows are
   samples; anything else detected is listed in `prep.json → ignored`.
2. **SPARC dataset metadata**: `samples.xlsx` / `subjects.xlsx` (SPARC Dataset Structure; also
   `.csv`/`.tsv`) found anywhere in the input. A sample folder named `sam-…` (or below a `sam-…`
   folder) matches the `sample id` row; its `subject id` (or a `sub-…` folder above it) joins the
   subject row. Column names become snake_case (`subject experimental group` →
   `subject_experimental_group`). Subject columns get no prefix unless they clash with a sample
   column (then `subject_<name>`). Empty columns are dropped. **Only columns named in
   `METADATA_COLUMNS` (comma-separated, default: `subject_experimental_group,sample_experimental_group,sex,age,species,sample_anatomical_location,laterality`) are copied onto cells**; all
   columns go to `samples_full.csv` in the metadata port, so clinical fields never spread into
   every derived file unless asked for.
3. `CONDITION_COLUMN` (default `auto`: `condition` if present, else
   `subject_experimental_group`, else `sample_experimental_group`) is copied to a `condition`
   column, which st-compare uses by default.

Without any of these, `sample_id` = sanitised folder name above `outs/`/`count/`, or the file
stem.

### `prep.json` (metadata port)

Per sample: source detected, files read, chemistry, organism, droplets present, features kept/
dropped, warnings; plus `ignored` and `skipped`.

### Labels (`labels` ports)

Every app that assigns labels writes `labels.csv`: `sample_id`, `obs_id` (the `obs_names`
value), then label columns (`cluster` from sc-integrate; `cell_type`, `cell_type_score` from
sc-annotate). This is the same format as the spatial suite, so st-compare, st-niches and the
reports read it.

### Reference models (`model` port of sc-annotate)

`reference.h5ad` (counts + `obs["cell_type"]`, subsampled to ≤ `MAX_CELLS_PER_TYPE`) plus, when
scANVI ran, the scvi-tools model folder `scanvi/` and a cell2location-ready signature
`signatures.csv` (genes × cell types, mean normalized expression per type). A later run of
sc-annotate takes it on its `reference` input, and the spatial cell2location app can use it as
its reference.

### ATAC (`atac` ports)

`atac-prep` writes `<sample_id>.h5ad` per sample: SnapATAC2 AnnData with the fragment file
imported (`obsm["fragment_paired"]` or SnapATAC2's own layout), a tile matrix (500 bp bins,
`X`), `obs` QC (`n_fragment`, `frac_dup`, `frac_mito`, `tsse`), `obs_names` matching the RNA
side (`<sample_id>:<barcode>`). Cells failing ATAC QC are dropped; `atac_qc.csv` lists them.
Genome annotation (GENCODE GTF for TSS, chromosome sizes) is baked into the image for GRCh38 and
GRCm39; `GENOME` selects (`auto` from the fragment chromosome names and `organism`).

### Tables and figures

Tables are CSV (UTF-8, header row, `.` decimal), long format where natural. Figures are PNG,
150 dpi; per-sample figures are `<sample_id>_<what>.png` with the sample id in the title. A
`summary.json` per output port holds the headline numbers the report shows.

## Credits (every app)

Users of these apps must be able to see and cite whose work they rely on. Each app:

- `CITATION.cff` (CFF 1.2.0), like `~/code/pennsieve/st-qc/CITATION.cff`: the app itself
  (authors: Pennsieve (University of Pennsylvania)), and under `references:` **every method,
  library and dataset the app depends on** with full author lists as published, journal, year,
  volume, issue, pages, DOI and PMID. Demo data entries are `type: data` with the Discover DOI,
  the contributors (with ORCID) and the licence, plus the paper that describes it.
- README `## Citations` (the same works, short form with DOI) and `## Credits`: one sentence per
  tool/dataset naming its authors/maintainers and what the app uses it for.
- **Every reference is verified against its source** before it is written: papers against
  Crossref (`https://api.crossref.org/works/<doi>`) and PubMed (`https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&id=<pmid>&retmode=json`),
  datasets against DataCite (`https://api.datacite.org/dois/<doi>`) and the Discover API
  (`https://api.pennsieve.io/discover/datasets/<id>`), software licences against the project's
  own LICENSE file at the pinned version. Title, authors (count and order), year, journal,
  volume, pages, DOI and PMID must match the source exactly. Never write a reference from memory.
  Record how each was checked in the report.

## Demo data

Under `~/code/pennsieve/workflow-single-cell/data/` (gitignored; `scripts/fetch-sample-data.sh`):

- `drg-c2/`: Discover 475 v1, *Single nucleus sequencing of human C2 dorsal root ganglia tissue
  from fusion surgeries* (Arendt-Tranholm A, Sankaranarayanan I, Price TJ; doi:10.26275/cxuf-ftav;
  CC BY 4.0; described by doi:10.1093/brain/awaf370). 12 patients, 10x Flex; per sample
  `primary/sub-*/sam-*/count/sample_filtered_feature_bc_matrix.h5` and
  `sample_raw_feature_bc_matrix.h5`; `subjects.xlsx` has neck-pain scores and duration
  (`pain_location`, `left_average_pain`, …) and `subject experimental group`.
- `drg-atlas/`: Discover 480 v1, *Single nucleus sequencing atlas of human dorsal root ganglion*
  (Sankaranarayanan I, Price TJ; doi:10.26275/9qsp-i8dh; CC BY 4.0). 42 donors, 83 samples, same
  layout; organ donors and surgical patients (`Cohort`, `age category`).
- `sympathetic-multiome/`: Discover 465 v1, *A single-cell multi-omic atlas of human sympathetic
  ganglia* (Yang L, Gereau RW; doi:10.26275/oqxx-si9h; CC BY 4.0). One aggregated
  `filtered_feature_bc_matrix/` (MTX), `atac_fragments.tsv.gz` (17.7 GB, all samples, barcodes
  with a `-N` sample suffix), `meta.data.csv` (the authors' per-cell metadata). For tests, cut
  the fragments to a few thousand barcodes of two samples (`scripts/` in your repo or `tests/`).

Local Docker has 8 GB shared with other builders: subsample (`MAX_CELLS_PER_SAMPLE` in sc-prep,
e.g. 3000) and use 3–4 samples for runs; give `docker run` a `--memory` limit, measure peak
memory, remove your containers, and build with `--platform linux/amd64`.

Upstream outputs, once produced, go under `data/work/<demo>/<step>/<port>/` (e.g.
`data/work/drg-c2/prep/cells`). Until they exist, use `tests/make_fixture.py` (a tiny synthetic
dataset following this contract) and say so in your report.
