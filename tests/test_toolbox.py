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

LOGS = ROOT / "data" / "logfiles"


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
    for f in sorted(LOGS.glob("*.LOG")):
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
