"""Run with:  pytest -q tests"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import smartsolo_log as sl          # noqa: E402
import smartsolo_locate as loc      # noqa: E402

NODES = ROOT / "data" / "nodes"
LOGS = [NODES / "453021267", NODES / "453022522"]      # the two DML nodes with long logs


def _gps(t, lat, lon, alt=100.0):
    return (f"[GPS0000{t[-1]}]\nGPS Status = GPS Synchronization\nUTC Time = \"{t}\"\n"
            f"Longitude = {lon:.9f}\nLatitude   = {lat:.9f}\nAltitude   = {alt}\n\n")


def _boot(n, rtc):
    return (f"[DeviceInfo{n:05d}]\nBootReason = 0300\nSerial Number = 999000111\n"
            f"Boot RTC =\"{rtc}\"\nDevice Type = IGU-16HR 3C 5Hz\nSample Rate = 250\n\n")


@pytest.fixture
def moved_log(tmp_path):
    """Node powered up at site A (with a bad first fix), then moved to site B."""
    txt = "<00002,00010,00000,00000,00000,00000,00000>\n\n"
    txt += _boot(1, "2025/01/01,00:00:00")
    txt += _gps("2025/01/01,00:01:00", -71.0010, 11.0010)          # cold-start outlier (~120 m)
    for i in range(6):
        txt += _gps(f"2025/01/01,0{i + 1}:00:00", -71.0000 + i * 1e-6, 11.0000)
    txt += "[BatteryPowerOnStop]\n\n"
    txt += _boot(2, "2025/02/01,00:00:00")
    for i in range(6):
        txt += _gps(f"2025/02/01,0{i + 1}:00:00", -72.0000, 12.0000)
    p = tmp_path / "moved" / "DigiSolo.LOG"
    p.parent.mkdir()
    p.write_text(txt)
    return p


def test_sample_counts_match_header():
    for f in sorted(NODES.rglob("DigiSolo.LOG")):
        df = sl.read_log(f)
        hdr, parsed = df.attrs["header_counts"], df.attrs["parsed_counts"]
        for k in ["GPS", "Temperature", "Memory", "Battery", "DeviceInfo"]:   # GNSS counted as GPS
            assert hdr[k] == parsed.get(k, 0)
        assert isinstance(df.index, pd.DatetimeIndex) and str(df.index.tz) == "UTC"


def test_sample_deployments():
    deps = loc.build_deployments(LOGS)
    assert set(deps["serial"]) == {"453021267", "453022522"}
    d = deps.set_index("serial").loc["453022522"]
    assert d["unstable_fixes_skipped"] == 2          # cold-start fixes ~100 m off
    assert abs(d["latitude"] + 71.550) < 0.001


def test_moved_node_split_into_deployments(moved_log):
    deps = loc.build_deployments(moved_log)
    assert list(deps["session"]) == [1, 2]
    a, b = deps.iloc[0], deps.iloc[1]
    assert a["unstable_fixes_skipped"] == 1
    assert abs(a["latitude"] + 71.0) < 1e-4 and abs(b["latitude"] + 72.0) < 1e-4

    near_a = loc.select_deployments(deps, point=(-71.0, 11.0), radius_km=1)
    assert list(near_a["session"]) == [1]           # site B power-up excluded

    poly_b = [(11.9, -72.1), (12.1, -72.1), (12.1, -71.9), (11.9, -71.9)]
    assert list(loc.select_deployments(deps, polygon=poly_b)["session"]) == [2]


def test_time_window_clipping(moved_log):
    deps = loc.build_deployments(moved_log)
    sel = loc.select_deployments(deps, start="2025-01-01T02:30", end="2025-01-01T03:00")
    assert len(sel) == 1
    assert sel["sel_start"].iloc[0] == pd.Timestamp("2025-01-01T02:30", tz="UTC")
    assert sel["sel_end"].iloc[0] == pd.Timestamp("2025-01-01T03:00", tz="UTC")
    assert loc.select_deployments(deps, start="2025-01-15", end="2025-01-16").empty


def test_extraction_with_synthetic_data(tmp_path):
    pytest.importorskip("obspy")
    import smartsolo_waveforms as wf
    sys.path.insert(0, str(ROOT / "scripts"))
    import make_synthetic_waveforms as mk

    traces = tmp_path / "traces"
    mk.main(str(traces), hours_mseed=1, minutes_segy=10)
    idx = wf.index_waveforms(traces)
    assert set(idx["serial"]) == {"453021267", "453022522"}

    deps = loc.build_deployments(LOGS)
    sel = loc.select_deployments(deps, start="2024-12-26T00:02", end="2024-12-26T00:04")
    st = wf.extract_waveforms(sel, idx, mapping=ROOT / "data" / "station_mapping.csv",
                              out_dir=tmp_path / "out")
    assert sorted({tr.stats.station for tr in st}) == ["GL01", "GL02"]
    assert all(tr.stats.npts == 12000 for tr in st)  # 2 min @ 100 Hz, end exclusive
    assert all(tr.stats.channel.startswith("EP") for tr in st)
    inv = wf.build_inventory(sel, mapping=ROOT / "data" / "station_mapping.csv", stream=st)
    assert len(inv.get_contents()["channels"]) == 6


def test_node_folder_and_pulse():
    pytest.importorskip("scipy")
    import smartsolo_node as sn
    node = sn.read_node_folder(NODES / "453022522")
    assert node["serial"] == "453022522"
    assert node["limits"]["damping"] == (0.647, 0.752)
    p = node["pulse"].set_index("axis")
    assert list(p.index) == ["X", "Y", "Z"]
    assert ((p["f0_hz"] > 4.8) & (p["f0_hz"] < 5.1)).all()        # ~5 Hz geophone at 1000 sps
    assert ((p["damping"] > 0.68) & (p["damping"] < 0.75)).all()
    assert ((p["noise_rms_uV"] > 1.0) & (p["noise_rms_uV"] < 1.3)).all()   # matches log RMS Noise
    qc = node["qc"]
    assert qc[qc.boot_no == 4]["ok_all"].all()
    assert qc[qc.boot_no == 3]["noisy"].all()


def test_circular_and_orientation():
    import smartsolo_orientation as so
    assert so.circular_mean([359, 1]) == pytest.approx(0, abs=1e-9)
    assert so.circular_diff(1, 359) == pytest.approx(2)
    df = sl.read_logs(LOGS)
    deps = loc.build_deployments(LOGS)
    ot = so.orientation_table(df, deps, with_igrf=False).set_index("serial")
    assert ot.loc["453022522", "tilt_over_horizontal_spec"]
    assert abs(ot.loc["453021267", "heading_settled"] - 156.7) < 1


def test_polarity_and_auspass_response(tmp_path):
    pytest.importorskip("obspy")
    import numpy as np
    import smartsolo_node as sn
    import smartsolo_waveforms as wf
    sys.path.insert(0, str(ROOT / "scripts"))
    import make_synthetic_waveforms as mk

    mk.main(str(tmp_path / "tr"), hours_mseed=1, minutes_segy=5)
    idx = wf.index_waveforms(tmp_path / "tr")
    sel = loc.select_deployments(loc.build_deployments(LOGS), start="2024-12-26T00:01", end="2024-12-26T00:02")
    raw = wf.extract_waveforms(sel, idx, invert_polarity=False)
    fl = wf.extract_waveforms(sel, idx)                       # auto: IGU-16HR -> x -1
    assert np.array_equal(raw[0].data, -fl[0].data) and fl[0].stats.polarity_inverted
    inv = wf.build_inventory(sel, response="auspass")
    z = [c for c in inv[0][0].channels if c.code.endswith("Z")][0]
    assert z.dip == -90
    inv_raw = wf.build_inventory(sel, polarity_inverted=False)
    assert [c for c in inv_raw[0][0].channels if c.code.endswith("Z")][0].dip == 90
    r = sn.auspass_response()
    hf = abs(r.get_evalresp_response_for_frequencies(np.array([200.0]), output="VEL")[0])
    assert hf == pytest.approx(257019225.55, rel=1e-4)


# --------------------------------------------------------------------------- #
# Raw DLD files (break test, 4 complete nodes, Git LFS)
# --------------------------------------------------------------------------- #
BT = ROOT / "data" / "break_test"


def _dld(serial, name):
    f = next((BT / serial).glob(f"*/{name}.DLD"), None)
    if f is None or f.stat().st_size < 1000:      # LFS pointer only
        pytest.skip("DLD files not present (git lfs pull)")
    return f


def test_dld_header_tags_and_samples():
    import numpy as np
    import smartsolo_dld as dl
    f = _dld("453009194", "seis000Z")
    h = dl.read_dld_header(f)
    assert h["serial"] == "453009194" and h["component"] == "Z" and h["leap_seconds"] == 0
    tags = dl.read_dld_tags(f)
    assert (tags["tick_ms"].diff().dropna() == 2000).all()               # 1000 samples / 2 s -> 500 sps
    assert (tags["label_minus_tow_s"] == 2).all()                         # leap seconds not yet known
    st = dl.read_dld(f)
    tr = st[0]
    assert len(st) == 1 and tr.stats.sampling_rate == 500 and tr.stats.npts == len(tags) * 1000
    d = np.abs(np.diff(tr.data.astype(float)))
    assert np.median(d[999::1000]) < 2 * np.median(d)                    # contiguous across tags
    w = dl.read_dld(f, starttime="2023-03-31T01:40:00", endtime="2023-03-31T01:40:10")[0]
    assert w.stats.npts == 5001 and np.array_equal(w.data, tr.slice(w.stats.starttime, w.stats.endtime).data)


def test_dld_labels_late_while_leap_seconds_unknown():
    """Header leap_seconds = 0 -> text labels are 2 s ahead of TOW-derived UTC in every tag."""
    import smartsolo_dld as dl
    for s in ("453004362", "453009194", "453010029", "453010047", "453010077", "453010167"):
        f = _dld(s, "seis000Z")
        assert dl.read_dld_header(f)["leap_seconds"] == 0
        assert (dl.read_dld_tags(f)["label_minus_tow_s"] == 2).all()
        assert len(dl.scan_dld(f, time_source="tow")) == 1


def test_dld_reader_never_modifies_files():
    """Reading headers, tags and samples leaves the DLD files byte-for-byte unchanged."""
    import hashlib, os
    import smartsolo_dld as dl
    f = _dld("453009194", "seis000Z")
    before = (hashlib.sha256(f.read_bytes()).hexdigest(), os.stat(f).st_mtime_ns)
    dl.read_dld_header(f); dl.read_dld_tags(f); dl.scan_dld(f); dl.read_dld(f)
    assert (hashlib.sha256(f.read_bytes()).hexdigest(), os.stat(f).st_mtime_ns) == before


def test_dld_neighbouring_nodes_align_with_tow_time():
    """453004362 stands 5 m from 453009194: ambient noise lines up at ~0 lag."""
    from obspy import UTCDateTime
    from obspy.signal.cross_correlation import correlate, xcorr_max
    import smartsolo_dld as dl
    t0, t1 = UTCDateTime("2023-03-31T01:35:00"), UTCDateTime("2023-03-31T01:35:30")
    x, y = (dl.read_dld(_dld(s, "seis000Z"), starttime=t0, endtime=t1)[0] for s in ("453009194", "453004362"))
    for tr in (x, y):
        tr.detrend("demean"); tr.filter("bandpass", freqmin=2, freqmax=40)
    n = min(x.stats.npts, y.stats.npts)
    lag, cc = xcorr_max(correlate(x.data[:n], y.data[:n], 1500), abs_max=False)
    assert abs(lag / 500) < 0.1 and cc > 0.3


def test_dld_pipeline_mseed_and_segy(tmp_path):
    import numpy as np
    from obspy import read
    import smartsolo_dld as dl
    import smartsolo_waveforms as wf
    _dld("453010077", "seis000Z")
    deps = loc.combine_deployments(loc.build_deployments(BT), loc.build_deployments_from_dld(BT))
    idx = wf.index_waveforms([p for p in BT.rglob("seis000?.DLD")])
    sel = loc.select_deployments(deps, point=(-42.9017, 147.3301), radius_km=0.03,
                                 start="2023-03-31T01:40:00", end="2023-03-31T01:40:20")
    assert sorted(sel["serial"]) == ["453010077", "453010167"]
    for fmt in ("MSEED", "SEGY"):
        st = wf.extract_waveforms(sel, idx, out_dir=tmp_path / fmt, out_format=fmt)
        assert sorted({tr.stats.channel for tr in st}) == ["DPE", "DPN", "DPZ"]   # 500 sps
    raw = dl.read_dld(_dld("453010077", "seis000Z"), starttime="2023-03-31T01:40:00",
                      endtime="2023-03-31T01:40:19.998")[0]
    m = read(str(next((tmp_path / "MSEED").rglob("*10077..DPZ*.mseed"))))[0]
    s = read(str(next((tmp_path / "SEGY").rglob("*10077..DPZ*.sgy"))), format="SEGY")[0]
    assert m.stats.starttime == s.stats.starttime == raw.stats.starttime
    assert np.array_equal(m.data, -raw.data) and np.array_equal(s.data, m.data)
    inv = wf.build_inventory(sel, stream=st, response="auspass")                    # 0 dB
    assert inv[0][0][0].response.instrument_sensitivity.value == pytest.approx(255455695.9, rel=1e-3)


def test_pulse_rate_is_fixed_1000():
    """PULSE_*.WAV is 1000 sps even when the node records at 500 sps."""
    pytest.importorskip("scipy")
    import smartsolo_node as sn
    p = sn.analyse_pulse_folder(next((BT / "453009194").glob("*")))
    assert ((p["f0_hz"] > 4.7) & (p["f0_hz"] < 5.3)).all()
