#!/usr/bin/env bash
# Creates the public GitHub repositories of the single-cell and bulk RNA-seq workflows in the
# pennsieve-apps organization (empty; each app is pushed from its own checkout). Idempotent:
# a repo that already exists is skipped.
set -euo pipefail
ORG=pennsieve-apps
while IFS='|' read -r name desc; do
  if gh repo view "$ORG/$name" >/dev/null 2>&1; then echo "exists: $ORG/$name"; continue; fi
  gh repo create "$ORG/$name" --public -d "$desc"
done <<'EOF'
workflow-single-cell|Pennsieve workflows: single-nucleus / single-cell RNA-seq atlas mapping and condition comparison, and multiome (RNA + ATAC) gene regulation
sc-prep|Pennsieve app: reads Cell Ranger (count, ARC), Seurat .rds or .h5ad into one AnnData per sample, with SPARC subjects/samples metadata on every cell
sc-qc|Pennsieve app: ambient RNA removal (CellBender), doublet detection, QC filtering and normalization for single-nucleus / single-cell RNA-seq
sc-integrate|Pennsieve app: batch integration (scVI or Harmony), Leiden clustering and UMAP across single-cell samples
sc-annotate|Pennsieve app: cell-type annotation by reference mapping (scANVI, CellTypist) or marker sets, with a reusable reference model
sc-markers|Pennsieve app: marker genes per cluster or cell type, with dot plots, for single-cell RNA-seq
sc-report|Pennsieve app: HTML report and tables for the single-cell RNA-seq and multiome workflows
atac-prep|Pennsieve app: reads Cell Ranger ARC / ATAC fragments into per-sample AnnData with ATAC QC (TSS enrichment, fragment sizes) using SnapATAC2
atac-regulome|Pennsieve app: peaks per cell type, motif activity (chromVAR-style) and peak-to-gene links for single-nucleus multiome data
workflow-bulk-rnaseq|Pennsieve workflows: bulk RNA-seq from FASTQ to differential expression and cross-tissue comparison
rnaseq-index|Pennsieve app: builds a salmon transcriptome index and transcript-to-gene map from FASTA and GTF files, for use as a workspace reference
rnaseq-quant|Pennsieve app: FASTQ trimming and QC (fastp) and transcript quantification (salmon) to gene counts (tximport-style)
bulk-de|Pennsieve app: differential expression (PyDESeq2) and gene-set enrichment for bulk RNA-seq, from counts and a sample sheet
bulk-report|Pennsieve app: HTML report and tables for the bulk RNA-seq workflows
EOF
