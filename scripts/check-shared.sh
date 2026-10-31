#!/usr/bin/env bash
# Checks that each app repo's processor/safe_inputs.py is byte-identical to the canonical
# shared/safe_inputs.py of this repo. Exit 1 (with a diff) when any copy is missing or differs.
#
#   scripts/check-shared.sh ~/code/pennsieve/sc-prep ~/code/pennsieve/sc-qc ...
set -euo pipefail

canonical="$(cd "$(dirname "$0")/.." && pwd)/shared/safe_inputs.py"
if [ "$#" -eq 0 ]; then
  echo "usage: $0 <app repo> [<app repo> ...]" >&2
  exit 2
fi

status=0
for repo in "$@"; do
  copy="$repo/processor/safe_inputs.py"
  if [ ! -f "$copy" ]; then
    echo "MISSING  $copy"
    status=1
  elif cmp -s "$canonical" "$copy"; then
    echo "OK       $copy"
  else
    echo "DIFFERS  $copy"
    diff -u "$canonical" "$copy" || true
    status=1
  fi
done
exit "$status"
