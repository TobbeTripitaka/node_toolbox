"""Batch designs. Batches 1-4 are a Latin square (``huddle_matrix.csv``).
``propose_batch`` adds further batches with random or optimised settings and
appends them to the matrix, so every later step (XML, apply, collect,
analyse) works on them unchanged.

Each unit keeps its firmware. A new batch always contains at least one
reference setting (1000 sps, 0 dB, linear, not rotated) so the batch can be
tied to the others.
"""

from __future__ import annotations

import itertools

import numpy as np
import pandas as pd

from . import config as hc

SPACE = dict(
    sample_rate_sps=[4000, 2000, 1000, 500, 250, 125, 100, 50],
    gain_db=[0, 6, 12, 18, 24, 30, 36],
    filter_phase=["linear", "minimum"],
    orientation_deg=[0, 90],
    gnss_mode=["cycle", "always"],
    gnss_system=["gps", "gps+glonass", "gps+beidou"],
    low_cut=["off", "0.05", "0.2", "0.8", "1.25", "7.5", "dc", "unknown_2", "unknown_10"],  # unknown_*: frequency not known
    adc_mode=["normal", "low_power"],
    storage=["dld", "miniseed"],
    test_mode=["once", "none"],
)
REFERENCE_SETTING = dict(sample_rate_sps=1000, gain_db=0, filter_phase="linear", orientation_deg=0,
                         **hc.FACTOR_DEFAULTS)


def _settings(space, full=False):
    """Candidate settings. Default: vary ONE factor from the reference setting
    (rate sweep at 0 dB/linear, gains at 1000 sps, minimum phase at any rate,
    rotation at the reference), so each result can be interpreted on its own.
    ``full=True``: the full factorial (all factors at once)."""
    if full:
        keys = list(space)
        return [dict(zip(keys, v)) for v in itertools.product(*space.values())]
    ref = REFERENCE_SETTING
    out = [dict(ref, sample_rate_sps=r) for r in space["sample_rate_sps"]]
    out += [dict(ref, gain_db=g) for g in space["gain_db"] if g != ref["gain_db"]]
    out += [dict(ref, sample_rate_sps=r, filter_phase=p) for r in space["sample_rate_sps"]
            for p in space["filter_phase"] if p != ref["filter_phase"]]
    out += [dict(ref, orientation_deg=o) for o in space["orientation_deg"] if o != ref["orientation_deg"]]
    for k in hc.FACTOR_DEFAULTS:
        out += [dict(ref, **{k: v}) for v in space.get(k, []) if v != ref[k]]
    return out


def _coverage(matrix, keys):
    return matrix.groupby(keys).size().to_dict()


def _uncertainty(cfg) -> dict:
    """(firmware, rate) -> spread of measured lags (larger = measure again)."""
    f = cfg["data_dir"] / "results" / "timing.csv"
    if not f.exists():
        return {}
    t = pd.read_csv(f)
    if "lag_mad_s" not in t:
        return {}
    return t.groupby(["firmware", "sample_rate_sps"]).lag_mad_s.max().to_dict()


def propose_batch(cfg, method="optimised", seed=0, space=None, n_reference=2, weights=None,
                  write=True, full_factorial=False) -> pd.DataFrame:
    """Rows of a new batch for every unit in ``units.csv``.

    ``method="random"``: settings drawn at random from ``space``.
    ``method="optimised"``: for each unit, the setting that most improves
    coverage of (firmware x rate), (firmware x gain) and (firmware x filter
    phase), preferring combinations whose measured lag spread is largest.
    ``n_reference`` units (spread over firmware versions) get the reference
    setting. Candidates vary one factor at a time unless ``full_factorial``. With ``write=True`` the rows are appended to the matrix CSV.
    """
    space = space or SPACE
    weights = weights or dict(rate=1.0, gain=0.6, phase=0.6, orientation=0.2, factors=0.3, uncertainty=50.0)
    m = hc.load_matrix(cfg)
    units = hc.load_units(cfg)
    batch = int(m.batch.max()) + 1
    rng = np.random.default_rng(seed)
    opts = _settings(space, full_factorial)
    cov = {k: _coverage(m.assign(**{c: m[c].astype(str) for c in hc.FACTOR_DEFAULTS}), ["firmware", k])
           for k in ("sample_rate_sps", "gain_db", "filter_phase", "orientation_deg", *hc.FACTOR_DEFAULTS)}
    unc = _uncertainty(cfg)
    fws = units.firmware.unique()
    ref_fw = set(rng.choice(fws, size=min(n_reference, len(fws)), replace=False))
    rows, used_ref = [], set()
    for u in units.itertuples():
        if u.firmware in ref_fw and u.firmware not in used_ref:
            s, role = dict(REFERENCE_SETTING), "reference setting"
            used_ref.add(u.firmware)
        elif method == "random":
            s, role = dict(opts[rng.integers(len(opts))]), "random"
        else:
            def score(o):
                c = lambda k: cov[k].get((u.firmware, o[k]), 0)
                return (weights["rate"] / (1 + c("sample_rate_sps")) + weights["gain"] / (1 + c("gain_db"))
                        + weights["phase"] / (1 + c("filter_phase")) + weights["orientation"] / (1 + c("orientation_deg"))
                        + sum(weights["factors"] / (1 + c(k)) for k in hc.FACTOR_DEFAULTS)
                        + weights["uncertainty"] * unc.get((u.firmware, o["sample_rate_sps"]), 0.0)
                        + 1e-6 * rng.random())
            s, role = dict(max(opts, key=score)), "optimised"
        for k in cov:
            cov[k][(u.firmware, s[k])] = cov[k].get((u.firmware, s[k]), 0) + 1
        rows.append(dict(row_id=f"B{batch}-{u.unit}", batch=batch, unit=u.unit, firmware=u.firmware,
                         **s, tilt_deg=0, role=role, design=method,
                         gain_ch1=s["gain_db"], gain_ch2=s["gain_db"], gain_ch3=s["gain_db"]))
    new = pd.DataFrame(rows)
    if write:
        cols = pd.read_csv(cfg["matrix"], nrows=0).columns
        pd.concat([pd.read_csv(cfg["matrix"]), new[cols]]).to_csv(cfg["matrix"], index=False)
    return new
