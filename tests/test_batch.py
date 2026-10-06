"""Tests for the improvements merged from the harvest script: binary GPS week/TOW timing, trailing
samples, staged responses, settings files and the SDS batch converter."""

import struct
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import smartsolo_config as sc  # noqa: E402
import smartsolo_dld as dl  # noqa: E402
import smartsolo_node as sn  # noqa: E402

EXCERPT = ROOT / "data" / "timing_example" / "453010047_seis001Z_excerpt.DLD"
BREAK = ROOT / "data" / "break_test"


def _need(path):
    if not path.exists() or path.stat().st_size < 1000:
        pytest.skip("Git LFS files not pulled")


def _layout(buf):
    return dl._layout(buf)


def test_binary_tick_is_week_and_tow():
    _need(EXCERPT)
    t = dl.read_dld_tags(EXCERPT)
    assert (t["tow_ms"] == t["gps_tow_ms"]).all()          # binary = text TOW
    assert t["gps_week"].nunique() == 1 and 2200 < t["gps_week"].iloc[0] < 2300
    assert (np.diff(t["gps_ms"]) == 2000).all()             # 1000 samples at 500 sps


def test_trailing_samples(tmp_path):
    _need(EXCERPT)
    raw = EXCERPT.read_bytes()
    block, period, n = _layout(raw)
    cut = tmp_path / "seis001Z.DLD"
    cut.write_bytes(raw[:dl.HEADER_SIZE + (n - 1) * period + 1500])   # last tag lost
    full, part = dl.read_dld(EXCERPT)[0], dl.read_dld(cut)[0]
    assert part.stats.npts == (n - 1) * (block // 3) + 500
    assert part.stats.starttime == full.stats.starttime
    assert np.array_equal(part.data, full.data[:part.stats.npts])
    assert dl.scan_dld(cut)[0]["npts"] == part.stats.npts
    dl.DEFAULTS["include_trailing"] = False
    try:
        assert dl.read_dld(cut)[0].stats.npts == (n - 1) * (block // 3)
    finally:
        dl.DEFAULTS["include_trailing"] = True


def test_gps_week_rollover_is_continuous(tmp_path):
    _need(EXCERPT)
    raw = bytearray(EXCERPT.read_bytes())
    block, period, n = _layout(bytes(raw))
    week = 2300
    tow0 = 604_800_000 - 90_000                 # 2 s blocks: rollover after 45 tags
    for k in range(n):
        p = dl.HEADER_SIZE + k * period + block
        ms = tow0 + 2000 * k
        w, tow = week + ms // 604_800_000, ms % 604_800_000
        struct.pack_into("<q", raw, p + 4, (w << 32) | tow)
        raw[p + 56:p + 72] = str(tow).encode().ljust(16, b"\x00")
    f = tmp_path / "seis001Z.DLD"
    f.write_bytes(bytes(raw))
    st = dl.read_dld(f)
    assert len(st) == 1                          # no split at the rollover
    t = dl.read_dld_tags(f)["time_tow"]
    assert (t.diff().dropna().dt.total_seconds() == 2.0).all()
    assert st[0].stats.sampling_rate == 500.0
    # GPS week 2301 starts 2024-02-11 00:00:00 GPS = 2024-02-10 23:59:42 UTC
    assert str(t.iloc[45]) == "2024-02-10 23:59:42+00:00"


def test_leap_override():
    dl.DEFAULTS["leap_seconds"] = 19
    try:
        assert dl.gps_utc_offset("2030-01-01") == 19
        assert dl.gps_utc_offset("2016-01-01") == 17
    finally:
        dl.DEFAULTS["leap_seconds"] = None
    assert dl.gps_utc_offset("2030-01-01") == 18


def test_staged_response_constants():
    a = sn.staged_response(model="auspass", gain_db=0)
    b = sn.staged_response(model="dtcc", gain_db=0)
    hf = lambda r: abs(r.get_evalresp_response_for_frequencies([200.0], output="VEL")[0])
    assert hf(a) == pytest.approx(257019225.55, rel=1e-5)          # AusPass published value
    assert hf(b) == pytest.approx(76.6 * 3355500, rel=1e-5)        # DTCC data-sheet values
    assert hf(sn.staged_response(gain_db=24)) / hf(a) == pytest.approx(10 ** 1.2)
    old = sn.auspass_response()
    assert a.instrument_sensitivity.value == pytest.approx(old.instrument_sensitivity.value)
    assert sn.instrument_code("IGU-16 BD3C 5s") == "H" and sn.instrument_code("IGU-16HR 3C 5Hz") == "P"


def test_config_roundtrip(tmp_path):
    p = sc.write_template(tmp_path / "s.yaml")
    c = sc.load_config(p)
    assert Path(c["output"]["root"]) == (tmp_path / "output" / "sds").resolve()   # relative to file
    c["output"]["root"] = sc.DEFAULT_CONFIG["output"]["root"]
    assert c == sc.DEFAULT_CONFIG
    j = tmp_path / "s.json"
    j.write_text('{"timing": {"tow_offset_s": -1}}')
    assert sc.load_config(j)["timing"]["tow_offset_s"] == -1
    with pytest.raises(KeyError):
        sc.load_config({"timing": {"tow_ofset_s": 1}})


def test_mseed_with_blank_records(tmp_path):
    from obspy import Trace

    import smartsolo_waveforms as sw
    f = tmp_path / "seis000Z.MiniSeed"
    Trace(np.arange(5000, dtype=np.int32)).write(str(f), format="MSEED", reclen=512, encoding="STEIM2")
    f.write_bytes(f.read_bytes() + b"\x00" * 2048)      # unwritten flash
    assert sw.read_mseed_robust(f)[0].stats.npts == 5000


def test_batch_sds_idempotent_and_redo(tmp_path):
    import smartsolo_batch as sb
    from obspy import read
    src = BREAK / "453010029"
    dld = next(src.glob("*/seis000Z.DLD"), None)
    if dld is None:
        pytest.skip("break_test data missing")
    _need(dld)
    cfg = {"output": {"root": str(tmp_path / "sds")}, "batch": {"workers": 1}}
    s1 = sb.run_batch([src], cfg, verbose=False)
    assert "converted" in set(s1["status"])
    day = next((tmp_path / "sds").rglob("*DPZ.D.2023.090"))
    st = read(str(day))
    raw = dl.read_dld(dld)[0]
    assert len(st) == 1 and st[0].stats.starttime == raw.stats.starttime
    assert np.array_equal(st[0].data, -raw.data)          # x -1, no gain change, no lost sample
    s2 = sb.run_batch([src], cfg, verbose=False)
    assert "converted" not in set(s2["status"]) and "skipped" in set(s2["status"])
    cfg["timing"] = {"tow_offset_s": -1.0}
    s3 = sb.run_batch([src], cfg, verbose=False)
    assert "redone" in set(s3["status"])
    st3 = read(str(day))
    assert len(st3) == 1 and st3[0].stats.starttime == raw.stats.starttime - 1   # old data replaced
    db = sb.HarvestDB(tmp_path / "sds" / "harvest.sqlite")
    assert len(db.table("runs")) == 2
    db.close()
    assert (tmp_path / "sds" / "stations.xml").exists()
    dl.DEFAULTS["tow_offset_s"] = 0.0
    dl._CACHE.clear()
