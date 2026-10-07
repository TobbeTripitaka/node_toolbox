"""Settings, matrix and unit table of the huddle test."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parents[1]          # tests/huddle_test
REPO = HERE.parents[1]                               # node_toolbox
SANDBOX = HERE / "sand_box_huddle_test"              # matrix, units, scripts, settings and (git-ignored) data
sys.path.insert(0, str(REPO / "lib"))

DEFAULTS = {
    # Paths are relative to the folder of huddle.yaml (sand_box_huddle_test/).
    # Raw data, backups, state and results: git-ignored (or outside the repository).
    "data_dir": "data",
    "matrix": "huddle_matrix.csv",
    "units": "units.csv",
    "template": "../templates/sct_par_template.xml",
    "xml_codes": "../xml_codes.yaml",
    "xml_dir": "xml",
    # Where nodes appear when mounted (glob patterns; a node is a folder with device.ini)
    # (Windows drive letters D:-Z: are always searched)
    "mount_globs": ["/Volumes/*", "/media/*/*", "/media/*", "/run/media/*/*", "/mnt/*"],
    # Timing reference: MiniSEED folder of the Pegasus (or any station); empty = consensus of nodes
    "reference": {"path": "", "network": "", "station": "", "channels": "HH?", "stationxml": ""},
    "analysis": {
        "band_hz": [2.0, 15.0],         # common band, below the Nyquist of 50 sps
        "work_rate_sps": 200.0,         # common rate for correlation
        "max_lag_s": 25.0,              # > one block at 50 sps (20 s)
        "event_window_s": [-1.0, 3.0],  # around each impact
        "min_snr": 8.0,                 # impact detection on the reference
        "min_separation_s": 2.0,
        # remove the geophone response from the nodes and the reference's response
        # (needs reference.stationxml) before comparing phases with the reference
        "correct_responses": True,
    },
}


def _merge(a, b):
    out = dict(a)
    for k, v in (b or {}).items():
        out[k] = _merge(a[k], v) if isinstance(v, dict) and isinstance(a.get(k), dict) else v
    return out


def _git_ignored(path: Path) -> bool:
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(REPO), "check-ignore", "-q", str(path / "x")], capture_output=True)
        return r.returncode == 0
    except OSError:                       # no git: trust the .gitignore written with the repository
        return (path.parent == SANDBOX and path.name == "data")


def load_settings(path=None) -> dict:
    """``huddle.yaml`` (in sand_box_huddle_test/ unless given) merged over DEFAULTS.

    Relative paths are taken from the folder of the yaml file.
    """
    import yaml
    path = Path(path) if path else SANDBOX / "huddle.yaml"
    base = path.resolve().parent
    user = yaml.safe_load(path.read_text()) if path.exists() else {}
    cfg = _merge(DEFAULTS, user)
    for k in ("data_dir", "matrix", "units", "template", "xml_codes", "xml_dir"):
        p = Path(str(cfg[k])).expanduser()
        cfg[k] = (p if p.is_absolute() else base / p).resolve()
    d = cfg["data_dir"]
    if (REPO in d.parents or d == REPO) and not _git_ignored(d):
        raise ValueError(f"data_dir {d} is inside the repository and not git-ignored - "
                         "use sand_box_huddle_test/data or a folder outside the repository")
    cfg["settings_file"] = path.resolve()
    return cfg


# Factors beyond rate / gain / filter / rotation, with their default (reference) level.
FACTOR_DEFAULTS = {"gnss_mode": "cycle", "gnss_system": "gps", "low_cut": "off", "adc_mode": "normal",
                   "storage": "dld", "test_mode": "once"}


def load_matrix(cfg) -> pd.DataFrame:
    m = pd.read_csv(cfg["matrix"], dtype={"unit": str, "firmware": str, "row_id": str,
                                          **{k: str for k in FACTOR_DEFAULTS}})
    for c in ("batch", "sample_rate_sps", "gain_db", "orientation_deg", "tilt_deg"):
        m[c] = pd.to_numeric(m[c])
    for k, v in FACTOR_DEFAULTS.items():               # older matrices without these columns
        m[k] = m[k].fillna(v) if k in m else v
    for ch in (1, 2, 3):
        c = f"gain_ch{ch}"
        m[c] = pd.to_numeric(m[c]).fillna(m["gain_db"]) if c in m else m["gain_db"]
    m["per_channel_gain"] = (m.gain_ch1 != m.gain_ch2) | (m.gain_ch1 != m.gain_ch3)
    m["batch"] = m["batch"].astype(int)
    m["block_s"] = 1000.0 / m["sample_rate_sps"]
    return m


def is_default(df, except_=()) -> pd.Series:
    """Rows whose extra factors (GNSS, low cut, ADC mode, storage, test mode,
    per-channel gains) are all at their reference level, apart from ``except_``."""
    ok = pd.Series(True, index=df.index)
    for k, v in FACTOR_DEFAULTS.items():
        if k not in except_ and k in df:
            ok &= df[k].astype(str) == v
    if "per_channel_gain" in df and "per_channel_gain" not in except_:
        ok &= ~df["per_channel_gain"].astype(bool)
    return ok


def load_units(cfg) -> pd.DataFrame:
    u = pd.read_csv(cfg["units"], dtype=str, keep_default_na=False)
    return u


def serial_to_unit(cfg) -> dict:
    u = load_units(cfg)
    return {r.serial: r.unit for r in u.itertuples() if r.serial}


def batch_dir(cfg, batch: int) -> Path:
    return cfg["data_dir"] / "raw" / f"batch_{int(batch):02d}"
