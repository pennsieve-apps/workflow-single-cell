"""Tests for safe_inputs.py: run with `pytest shared/` (pandas, openpyxl, defusedxml, h5py optional)."""
from __future__ import annotations

import gzip
import logging
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import safe_inputs as si  # noqa: E402

SECRET = "TOP-SECRET-CONTENT"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


@pytest.fixture
def tree(tmp_path):
    """outside/ (must never be read), up/ (an upstream app's output), in/ (INPUT_DIR, empty)."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text(SECRET)
    up = tmp_path / "up"
    (up / "run1").mkdir(parents=True)
    (up / "run1" / "summary.json").write_text('{"n": 1}')
    (up / "x.csv").write_text("a,b\n1,2\n")
    inp = tmp_path / "in"
    inp.mkdir()
    return tmp_path, inp, up, outside


def rels(d: si.InputDir, files) -> list[str]:
    return [d.rel(f) for f in files]


# --- layouts -------------------------------------------------------------------------------------

def test_plain_real_files(tree):
    _, inp, _, _ = tree
    (inp / "sub").mkdir()
    (inp / "a.csv").write_text("x")
    (inp / "sub" / "b.csv").write_text("y")
    (inp / ".hidden").write_text("z")
    d = si.InputDir(inp)
    assert rels(d, d.walk()) == ["a.csv", "sub/b.csv"]


def test_flat_links_to_upstream_folders_and_files(tree):
    _, inp, up, _ = tree
    (inp / "run1").symlink_to(up / "run1")
    (inp / "x.csv").symlink_to(up / "x.csv")
    d = si.InputDir(inp)
    assert rels(d, d.walk()) == ["x.csv", "run1/summary.json"]


def test_ports_folder_with_linked_entries_bulk_report_false_negative(tree):
    """bulk-report found nothing in a real INPUT_DIR/de/ whose entries link to upstream outputs."""
    _, inp, up, _ = tree
    (inp / "de").mkdir()
    (inp / "de" / "run1").symlink_to(up / "run1")
    (inp / "de" / "x.csv").symlink_to(up / "x.csv")
    d = si.InputDir(inp)
    assert rels(d, d.walk()) == ["de/x.csv", "de/run1/summary.json"]
    assert d.read_json(inp / "de" / "run1" / "summary.json", 100) == {"n": 1}


def test_relative_symlinks(tree):
    _, inp, up, _ = tree
    (inp / "run1").symlink_to(os.path.relpath(up / "run1", inp))
    (up / "run1" / "alias.json").symlink_to("summary.json")          # stays inside the upstream folder
    d = si.InputDir(inp)
    assert rels(d, d.walk()) == ["run1/alias.json", "run1/summary.json"]


# --- links that leave ----------------------------------------------------------------------------

def test_nested_link_inside_upstream_pointing_outside_is_skipped(tree, caplog):
    _, inp, up, outside = tree
    (up / "run1" / "evil.txt").symlink_to(outside / "secret.txt")
    (up / "run1" / "evil_dir").symlink_to(outside)
    (inp / "run1").symlink_to(up / "run1")
    d = si.InputDir(inp)
    with caplog.at_level(logging.WARNING, "safe_inputs"):
        files = d.walk()
        d.walk()
    assert rels(d, files) == ["run1/summary.json"]
    msgs = [r.getMessage() for r in caplog.records]
    assert msgs.count("skipped run1/evil.txt: resolves outside the input") == 1      # logged once
    assert msgs.count("skipped run1/evil_dir: resolves outside the input") == 1
    assert not any(SECRET in m for m in msgs)


def test_children_of_a_linked_top_folder_are_not_roots(tree):
    """sc-report/sc-markers trusted every child of a top-level dir (is_dir() follows symlinks)."""
    _, inp, up, outside = tree
    (up / "run1" / "evil").symlink_to(outside)
    (inp / "metadata").symlink_to(up / "run1")                       # a LINKED folder, not a real port folder
    d = si.InputDir(inp)
    assert os.path.realpath(outside) not in d.roots
    assert rels(d, d.walk()) == ["metadata/summary.json"]


def test_upstream_entry_that_is_itself_a_link_is_not_trusted(tree):
    """The platform links each entry of an upstream output; an entry the upstream app made a symlink
    to elsewhere must not become a root (one hop only), at the top level or in a port folder."""
    _, inp, up, outside = tree
    (up / "evil").symlink_to(outside)
    (up / "evil.txt").symlink_to(outside / "secret.txt")
    (inp / "evil").symlink_to(up / "evil")
    (inp / "evil.txt").symlink_to(up / "evil.txt")
    (inp / "port").mkdir()
    (inp / "port" / "evil").symlink_to(up / "evil")
    d = si.InputDir(inp)
    assert d.walk() == []
    assert not d.inside(inp / "port" / "evil" / "secret.txt")


def test_app_built_paths_are_rechecked(tree):
    """sc-report: the walk skipped an escaping link, but a helper re-globbed the folder and read it."""
    _, inp, up, outside = tree
    (up / "run1" / "evil").symlink_to(outside)
    (inp / "run1").symlink_to(up / "run1")
    d = si.InputDir(inp)
    built = inp / "run1" / "evil" / "secret.txt"
    assert built.is_file()                       # the OS would happily read it
    assert built not in d.walk()
    assert not d.inside(built)
    assert not d.inside("../outside/secret.txt")
    with pytest.raises(si.UnsafeInput):
        d.open_checked(built)
    for read in (d.read_bytes, d.read_text, d.read_json, d.read_csv, d.inline_png):
        try:
            assert read(built, 1 << 20) is None          # inline_png
        except si.UnsafeInput:
            pass


def test_broken_links_and_loops(tree, caplog):
    _, inp, _, _ = tree
    (inp / "real").mkdir()
    (inp / "real" / "f.txt").write_text("x")
    (inp / "real" / "loop").symlink_to("..")                         # a folder loop
    (inp / "self").symlink_to("self")                                # a link to itself
    (inp / "broken").symlink_to(inp / "missing")
    d = si.InputDir(inp)
    with caplog.at_level(logging.WARNING, "safe_inputs"):
        files = d.walk()
    assert rels(d, files) == ["real/f.txt"]
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "skipped broken: not a regular file" in text
    assert "skipped self: not a regular file" in text
    assert "skipped real/loop: a folder already walked" in text


def test_check_refuses_folders(tree):
    _, inp, _, _ = tree
    (inp / "d").mkdir()
    with pytest.raises(si.UnsafeInput):
        si.InputDir(inp).check(inp / "d")


# --- capped readers ------------------------------------------------------------------------------

def test_read_bytes_text_json_caps(tree):
    _, inp, _, _ = tree
    (inp / "a.json").write_bytes(b'\xef\xbb\xbf{"k": [1, 2]}')
    d = si.InputDir(inp)
    assert d.read_json(inp / "a.json", 100) == {"k": [1, 2]}
    assert d.read_text("a.json", 100) == '{"k": [1, 2]}'
    assert len(d.read_bytes(inp / "a.json", 16)) == 16
    with pytest.raises(si.UnsafeInput, match="larger than 15 bytes"):
        d.read_bytes(inp / "a.json", 15)
    (inp / "deep.json").write_text("[" * 100_000 + "]" * 100_000)
    with pytest.raises(ValueError):
        d.read_json(inp / "deep.json", 1 << 20)


def test_read_csv_and_gzip_bomb(tree):
    pytest.importorskip("pandas")
    _, inp, _, _ = tree
    (inp / "t.csv").write_text("a,b\n1,2\n")
    with gzip.open(inp / "t.csv.gz", "wt") as fh:
        fh.write("a,b\n3,4\n")
    with gzip.open(inp / "bomb.csv.gz", "wb") as fh:
        fh.write(b"a\n" + b"0\n" * (5 << 20))                        # 10 MiB from a few KiB
    d = si.InputDir(inp)
    assert d.read_csv(inp / "t.csv", 100)["b"].tolist() == [2]
    assert d.read_csv(inp / "t.csv.gz", 100)["b"].tolist() == [4]
    assert (inp / "bomb.csv.gz").stat().st_size < 1 << 20
    with pytest.raises(si.UnsafeInput, match="uncompressed"):
        d.read_csv(inp / "bomb.csv.gz", 1 << 20)
    with pytest.raises(si.UnsafeInput):
        d.read_csv(inp / "t.csv", 5)


def test_xlsx_zip_bomb_size(tree):
    _, inp, _, _ = tree
    with zipfile.ZipFile(inp / "bomb.xlsx", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/worksheets/sheet1.xml", b"\0" * (64 << 20))
    (inp / "fake.xlsx").write_text("not a zip")
    d = si.InputDir(inp)
    assert (inp / "bomb.xlsx").stat().st_size < 1 << 20
    assert d.xlsx_uncompressed_size(inp / "bomb.xlsx") == 64 << 20
    with pytest.raises(si.UnsafeInput):
        d.xlsx_uncompressed_size(inp / "fake.xlsx")


def test_real_xlsx_and_defusedxml(tree, monkeypatch):
    openpyxl = pytest.importorskip("openpyxl")
    _, inp, _, _ = tree
    wb = openpyxl.Workbook()
    wb.active.append(["sample_id", "path"])
    wb.save(inp / "samples.xlsx")
    d = si.InputDir(inp)
    assert 0 < d.xlsx_uncompressed_size(inp / "samples.xlsx") < 1 << 20
    if openpyxl.DEFUSEDXML:
        si.assert_defusedxml()
    monkeypatch.setattr(openpyxl, "DEFUSEDXML", False)
    with pytest.raises(RuntimeError, match="defusedxml"):
        si.assert_defusedxml()


def test_inline_png(tree, caplog):
    _, inp, up, outside = tree
    (inp / "fig.png").write_bytes(PNG)
    (inp / "fake.png").write_text("<svg onload=alert(1)>")
    (outside / "o.png").write_bytes(PNG)
    (up / "run1" / "o.png").symlink_to(outside / "o.png")
    (inp / "run1").symlink_to(up / "run1")
    d = si.InputDir(inp)
    assert d.inline_png(inp / "fig.png", 1000).startswith("data:image/png;base64,iVBORw0KGgo")
    with caplog.at_level(logging.WARNING, "safe_inputs"):
        assert d.inline_png(inp / "fake.png", 1000) is None
        assert d.inline_png(inp / "run1" / "o.png", 1000) is None
        assert d.inline_png(inp / "fig.png", 10) is None
    assert "skipped fake.png: not inlined: not a PNG file" in caplog.text
    assert "onload" not in caplog.text


# --- HDF5 ----------------------------------------------------------------------------------------

def test_check_h5(tree):
    h5py = pytest.importorskip("h5py")
    import numpy as np
    _, inp, _, outside = tree
    with h5py.File(outside / "other.h5", "w") as f:
        f["data"] = np.arange(3)
    with h5py.File(inp / "ok.h5", "w") as f:
        f["X/data"] = np.arange(3)
        f["alias"] = h5py.SoftLink("/X/data")
        f["X/back"] = f["X"]                                         # a hard-link cycle
    with h5py.File(inp / "extlink.h5", "w") as f:
        f["g/leak"] = h5py.ExternalLink(str(outside / "other.h5"), "/data")
    (outside / "raw.bin").write_bytes(b"\0" * 24)
    with h5py.File(inp / "extstore.h5", "w") as f:
        f.create_dataset("leak", shape=(3,), dtype="i8", external=[(str(outside / "raw.bin"), 0, 24)])
    with h5py.File(inp / "vds.h5", "w") as f:
        layout = h5py.VirtualLayout(shape=(3,), dtype="i8")
        layout[:] = h5py.VirtualSource(str(outside / "other.h5"), "data", shape=(3,))
        f.create_virtual_dataset("leak", layout)
    (inp / "not.h5").write_text("nope")
    d = si.InputDir(inp)
    d.check_h5(inp / "ok.h5")
    for name, msg in [("extlink.h5", "external link"), ("extstore.h5", "external files"),
                      ("vds.h5", "virtual dataset"), ("not.h5", "not a readable HDF5")]:
        with pytest.raises(si.UnsafeInput, match=msg):
            d.check_h5(inp / name)
    with pytest.raises(si.UnsafeInput, match="outside"):
        d.check_h5(outside / "other.h5")


# --- threads -------------------------------------------------------------------------------------

def affinity() -> int:
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:
        return os.cpu_count() or 1


def test_cpu_threads(tmp_path, monkeypatch):
    monkeypatch.setattr(si, "CGROUP", tmp_path)
    assert si.cpu_threads(3) == 3
    assert si.cpu_threads("5") == 5
    assert si.cpu_threads(0) == affinity()                           # no cgroup: never a fixed 2
    (tmp_path / "cpu.max").write_text("max 100000\n")
    assert si.cpu_threads(None) == affinity()
    (tmp_path / "cpu.max").write_text("150000 100000\n")
    assert si.cpu_threads(0) == min(2, affinity())
    (tmp_path / "cpu.max").unlink()
    (tmp_path / "cpu").mkdir()
    (tmp_path / "cpu" / "cpu.cfs_quota_us").write_text("100000")
    (tmp_path / "cpu" / "cpu.cfs_period_us").write_text("100000")
    assert si.cpu_threads(0) == 1
    (tmp_path / "cpu" / "cpu.cfs_quota_us").write_text("100000000")
    assert si.cpu_threads(0) == affinity()                           # quota above the CPUs we may use


# --- packaging -----------------------------------------------------------------------------------

def test_imports_without_optional_packages(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    code = ("import sys; sys.modules.update(pandas=None, h5py=None, openpyxl=None, numpy=None); "
            f"sys.path.insert(0, {str(Path(__file__).parent)!r}); import safe_inputs as si; "
            f"print(len(si.InputDir({str(tmp_path)!r}).walk()))")
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "1"
