#!/usr/bin/env python3
"""
Writes the single-cell workflow definitions (workflow/*.json, the body of
POST /compute/workflows/definitions). Add "organizationId": "N:organization:..." to the body when
posting: without it the workflow belongs to no workspace and the Workflow Library doesn't list it.
Each step's cpu/memory comes from its app's app.yml runtime (the sibling repo ../<app>), rounded up to
a size Fargate offers, and each step pins the newest release tag at or before the checked-out commit.
Steps with cpu/gpu variants run their default (cpu) variant; a run can pick gpu per step.
The helpers are the spatial suite's (../workflow-spatial-transcriptomics/scripts/build-workflows.py).

Usage: scripts/build-workflows.py
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "spatial_builder", ROOT.parent / "workflow-spatial-transcriptomics" / "scripts" / "build-workflows.py")
B = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(B)
step, output, ROW = B.step, B.output, B.ROW

META = ("prep", "metadata", "metadata")


def atlas():
    steps = [
        step("prep", "sc-prep", 1, 2, ["counts"], [("counts", None, "counts")]),
        step("qc", "sc-qc", 2, 2, ["prep"], [("prep", "cells", "cells"), ("prep", "droplets", "droplets")]),
        step("integrate", "sc-integrate", 3, 2, ["qc"], [("qc", "cells", "cells")]),
        step("annotate", "sc-annotate", 4, 2, ["integrate"], [("integrate", "cells", "cells")]),
        step("markers", "sc-markers", 5, 1, ["annotate"],
             [("annotate", "cells", "cells"), ("annotate", "labels", "labels")]),
        step("compare", "st-compare", 5, 3, ["annotate", "prep"],
             [("annotate", "cells", "spatial"), ("annotate", "labels", "labels"), META],
             {"LABEL_COLUMN": "cell_type", "CONDITION_COLUMN": "condition"}),
        step("report", "sc-report", 6, 2, ["prep", "qc", "integrate", "annotate", "markers", "compare"],
             [META, ("qc", "qc", "qc"), ("integrate", "integration", "integration"),
              ("annotate", "annotation", "annotation"), ("markers", "markers", "markers"),
              ("compare", "compare", "compare")],
             {"REPORT_TITLE": "Single-nucleus Atlas Mapping & Condition Comparison"}),
    ]
    return {
        "name": "Single-nucleus Atlas Mapping & Condition Comparison",
        "summary": "snRNA/scRNA-seq: QC, integration, cell types, markers, pseudobulk DE between conditions.",
        "description": (
            "Maps the cell types of single-nucleus or single-cell RNA-seq samples and compares conditions. "
            "Reads Cell Ranger (count, Flex, ARC), .h5ad or Seurat .rds input and joins SPARC "
            "subjects/samples metadata onto the cells (sc-prep), removes ambient RNA (CellBender, gpu "
            "variant), doublets and low-quality nuclei and normalizes (sc-qc), integrates samples by donor "
            "with Harmony (cpu) or scVI (gpu), clusters and embeds them (sc-integrate), labels cell types "
            "from cited marker sets (MARKER_SET: drg-human, knee-human, broad) or by mapping to a reference "
            "(sc-annotate), finds marker genes (sc-markers), tests composition and pseudobulk differential "
            "expression per cell type between conditions (st-compare), and writes an HTML report with "
            "citations (sc-report). Choose the gpu variant of sc-qc, sc-integrate and sc-annotate on a GPU "
            "node for ambient-RNA removal and scVI/scANVI."
            " If you used Pennsieve to store, analyse, share or publish data reported in a publication, please cite Goldblum Z, et al. Scientific Data 2025;12:1834 (doi:10.1038/s41597-025-06075-5), the datasets you used and the methods listed in the report."),
        "inputs": [
            {"id": "counts", "name": "Single-cell data",
             "description": "A folder of Cell Ranger outputs (count/, Flex or ARC), .h5ad or Seurat .rds files, "
                            "with optional samples.csv or SPARC samples.xlsx / subjects.xlsx.",
             "position": {"x": 40, "y": 40 + ROW * 2}},
        ],
        "outputs": [
            output("report_out", "Report", "report", "report", "derivatives/single-cell/report", 7, 1),
            output("tables_out", "Tables", "report", "tables", "derivatives/single-cell/tables", 7, 2),
            output("cells_out", "Annotated cells", "annotate", "cells", "derivatives/single-cell/cells", 7, 3,
                   default=False),
            output("model_out", "Reference model", "annotate", "model", "derivatives/single-cell/model", 7, 4,
                   default=False),
        ],
        "processors": steps,
    }


def main():
    out = ROOT / "workflow"
    out.mkdir(exist_ok=True)
    for name, wf in (("single-nucleus-atlas-mapping", atlas()),):
        assert len(wf["summary"].encode()) <= 100, (name, len(wf["summary"].encode()))
        (out / f"{name}.json").write_text(json.dumps(wf, indent=2) + "\n")
        print(f"wrote workflow/{name}.json ({len(wf['processors'])} steps)")


if __name__ == "__main__":
    main()
