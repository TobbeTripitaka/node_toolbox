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

LOGS = ROOT / "data" / "nodes"


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
    for f in sorted(LOGS.rglob("DigiSolo.LOG")):
        df = sl.read_log(f)
        hdr, parsed = df.attrs["header_counts"], df.attrs["parsed_counts"]
        for k in ["GPS", "Temperature", "Memory", "Battery", "DeviceInfo"]:
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
    node = sn.read_node_folder(LOGS / "453022522")
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
