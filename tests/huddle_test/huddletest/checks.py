"""The huddle-test checks. Each check is a function registered with
``@check(name)`` that takes the measurement table (all batches analysed so
far) plus a context, and returns a result table. To add a test, write a new
function here (or in any module imported before ``run_checks``):

    @check("my_test", "what it answers")
    def my_test(meas, ctx):
        return meas.groupby("unit").lag_s.median().reset_index()

Results go to ``<data_dir>/results/<name>.csv``.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from . import config as hc

REGISTRY: dict = {}


def check(name: str, question: str = ""):
    def deco(f):
        REGISTRY[name] = dict(func=f, question=question or (f.__doc__ or "").strip().split("\n")[0])
        return f
    return deco


def _z(meas, default=True):
    """Z rows; with ``default`` only rows whose extra factors are at the reference level."""
    z = meas[meas.component == "Z"]
    return z[hc.is_default(z)] if default else z


def _norm(v) -> str:
    """'100.0' == '100', 'Linear' == 'linear'."""
    try:
        return f"{float(v):g}"
    except (TypeError, ValueError):
        return str(v).strip().lower()


def _mad(x):
    x = np.asarray(x, float)
    return float(np.nanmedian(np.abs(x - np.nanmedian(x)))) if len(x) else np.nan


# --------------------------------------------------------------------------- #
@check("settings", "Did every node record with the planned settings (rate, firmware, gain, leap seconds, GPS)?")
def settings(meas, ctx):
    import smartsolo_dld as dld
    import smartsolo_log as sl
    cfg, m = ctx["cfg"], ctx["matrix"]
    rows = []
    for r in m[m.batch.isin(ctx["batches"])].itertuples():
        d = dict(row_id=r.row_id, batch=r.batch, unit=r.unit, planned_firmware=r.firmware,
                 planned_rate=r.sample_rate_sps, planned_gain=r.gain_db)
        from .analysis import unit_folder
        f = unit_folder(cfg, r.batch, r.unit)
        dlds = sorted(p for p in Path(f).rglob("*") if p.suffix.lower() == ".dld") if f else []
        mseed = sorted(p for p in Path(f).rglob("*") if p.suffix.lower() in (".miniseed", ".mseed")) if f else []
        if not dlds and mseed:
            import smartsolo_waveforms as sw
            tr = sw.read_mseed_robust(mseed[0], headonly=True)[0]
            prob = [] if str(r.storage) == "miniseed" else ["MiniSEED written, DLD planned"]
            if abs(tr.stats.sampling_rate - r.sample_rate_sps) > 1e-6:
                prob.append(f"rate {tr.stats.sampling_rate:g}")
            rows.append({**d, "rate": tr.stats.sampling_rate, "storage": "miniseed", "ok": not prob,
                         "problem": "; ".join(prob)})
            continue
        if not dlds:
            rows.append({**d, "ok": False, "problem": "no DLD or MiniSEED files"})
            continue
        if str(r.storage) == "miniseed" and not mseed:
            d["problem_storage"] = "DLD written, MiniSEED planned"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            h = dld.read_dld_header(dlds[0]); seg = dld.scan_dld(dlds[0])
            tags = dld.read_dld_tags(dlds[0])
        lmt = tags.label_minus_tow_s.dropna()
        d.update(serial=h["serial"], firmware=h["firmware"], rate=seg[0]["sampling_rate"],
                 leap_seconds=h["leap_seconds"], max_sync_age_s=max(s["max_sync_age_s"] for s in seg),
                 label_minus_tow_first_s=float(lmt.iloc[0]) if len(lmt) else np.nan,
                 label_minus_tow_last_s=float(lmt.iloc[-1]) if len(lmt) else np.nan,
                 label_jumps=int((lmt.diff().abs() > 1.5).sum()),   # the 2-s jump
                 segments=len(seg))
        logs = [p for p in Path(f).rglob("*.LOG")]
        logged = {}
        if logs:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                info = sl.read_device_info(logs)
            # the boot that recorded this batch = the last block of the newest log (file order,
            # not boot time: boots without an RTC time have NaT)
            if len(info):
                info = info.sort_values(["file", "boot_no"]) if "boot_no" in info else info
            last = info.iloc[-1] if len(info) else {}
            if "firmware_version" in info:
                d["firmware_log"] = last["firmware_version"]
            for c in ("script_name", "sample_rate", "channel_1_gain", "channel_2_gain", "channel_3_gain",
                      "anti_alias_filter_type", "low_cutter_filter", "gps_power_mode", "adc_lp_mode",
                      "fifo_storage_mode"):
                if c in info:
                    logged[c] = last[c]
                    d["log_" + c] = last[c]
        prob = []
        expect = _expected_log(r, ctx)
        for c, v in expect.items():
            if c in logged and pd.notna(logged[c]) and _norm(logged[c]) != _norm(v):
                prob.append(f"log {c}={logged[c]} (planned {v})")
        if d.get("problem_storage"):
            prob.append(d.pop("problem_storage"))
        if d["firmware"] != r.firmware:
            prob.append(f"firmware {d['firmware']} in DLD header")
        if d.get("firmware_log") and d["firmware_log"] != r.firmware:
            prob.append(f"firmware {d['firmware_log']} in log")
        if abs(d["rate"] - r.sample_rate_sps) > 1e-6:
            prob.append(f"rate {d['rate']:g}")
        if d["segments"] > 1:
            prob.append(f"{d['segments']} segments (gaps)")
        rows.append({**d, "ok": not prob, "problem": "; ".join(prob)})
    return pd.DataFrame(rows)


@check("time_labels", "How do the text time labels relate to GPS time of week on each firmware (offset, 2-s jumps, leap field)?")
def time_labels(meas, ctx):
    s = ctx.get("result_settings")
    if s is None or "label_minus_tow_first_s" not in s:
        s = settings(meas, ctx)
    if "label_minus_tow_first_s" not in s:
        return pd.DataFrame()
    s = s.dropna(subset=["label_minus_tow_first_s"])
    g = s.groupby("planned_firmware")
    return pd.DataFrame(dict(
        nodes=g.size(), leap_field=g.leap_seconds.agg(lambda x: sorted(set(x))),
        label_minus_tow_first_s=g.label_minus_tow_first_s.agg(lambda x: sorted(set(np.round(x, 3)))),
        label_minus_tow_last_s=g.label_minus_tow_last_s.agg(lambda x: sorted(set(np.round(x, 3)))),
        files_with_jump=g.label_jumps.agg(lambda x: int((x > 0).sum())),
        max_sync_age_s=g.max_sync_age_s.max())).reset_index().rename(columns={"planned_firmware": "firmware"})


def _expected_log(r, ctx):
    """What the DeviceInfo block of the log should say for matrix row ``r``."""
    import yaml
    codes = yaml.safe_load(Path(ctx["cfg"]["xml_codes"]).read_text())
    f = codes.get("factors", {})
    e = {"sample_rate": int(100000 / r.sample_rate_sps),
         "anti_alias_filter_type": "Linear" if r.filter_phase == "linear" else "Minimum"}
    for ch in (1, 2, 3):
        e[f"channel_{ch}_gain"] = int(getattr(r, f"gain_ch{ch}"))
    if "low_cut" in f:
        e["low_cutter_filter"] = f["low_cut"]["levels"][str(r.low_cut)]["code"]
    if "adc_mode" in f:
        e["adc_lp_mode"] = f["adc_mode"]["levels"][str(r.adc_mode)]["code"]
    e["gps_power_mode"] = {"cycle": "CycleOff", "always": "AlwaysOn"}.get(str(r.gnss_mode), "")
    if not e["gps_power_mode"] or str(r.gnss_mode) == "always":
        e.pop("gps_power_mode")              # wording for "always on" not seen in a log yet
    return e


@check("timing", "Absolute timing of each node against the reference (median lag over all impacts).")
def timing(meas, ctx, default=False):
    z = _z(meas, default)
    g = z.groupby(["row_id", "batch", "unit", "firmware", "sample_rate_sps", "block_s", "gain_db",
                   "filter_phase", "orientation_deg"])
    return g.agg(lag_s=("lag_s", "median"), lag_mad_s=("lag_s", _mad), cc=("cc", "median"),
                 n_events=("lag_s", "size"), coarse_lag_s=("coarse_lag_s", "first")).reset_index()


@check("block_convention", "Does a tag mark the start of the block before it? Fit lag = a + b * block length per firmware.")
def block_convention(meas, ctx):
    t = timing(meas, ctx, default=True)
    t = t[(t.filter_phase == "linear")]
    rows = []
    for fw, g in t.groupby("firmware"):
        if g.block_s.nunique() < 2:
            rows.append(dict(firmware=fw, n_rates=g.block_s.nunique(), offset_a_s=g.lag_s.median(),
                             slope_b=np.nan, verdict="need >= 2 rates"))
            continue
        b, a = np.polyfit(g.block_s, g.lag_s, 1)
        res = g.lag_s - (a + b * g.block_s)
        verdict = ("tags mark the block start (default correct)" if abs(b) < 0.1 else
                   "tags mark the block END: data one block late with the default" if abs(b - 1) < 0.1 else
                   "data one block early" if abs(b + 1) < 0.1 else "unclear")
        rows.append(dict(firmware=fw, n_rates=g.block_s.nunique(), rates=",".join(str(int(x)) for x in sorted(g.sample_rate_sps.unique())),
                         offset_a_s=a, slope_b=b, residual_rms_s=float(np.sqrt(np.mean(res ** 2))), verdict=verdict))
    return pd.DataFrame(rows)


@check("firmware", "Do firmware versions differ in timing, amplitude or polarity at the same setting?")
def firmware(meas, ctx):
    z = _z(meas)
    base = z[(z.gain_db == 0) & (z.filter_phase == "linear") & (z.orientation_deg == 0)]
    return base.groupby(["firmware", "sample_rate_sps"]).agg(
        lag_s=("lag_s", "median"), lag_mad_s=("lag_s", _mad), amp_ratio=("amp_ratio", "median"),
        polarity=("polarity", "mean"), units=("unit", "nunique")).reset_index()


@check("polarity", "Is every channel positive (FDSN convention) after the x -1 of node_toolbox?")
def polarity(meas, ctx):
    p = meas[meas.component.isin(list("ZNE")) & (meas.orientation_deg == 0)]
    p = p[hc.is_default(p, except_=("per_channel_gain", "gnss_mode", "gnss_system", "adc_mode", "storage", "test_mode"))]
    t = p.groupby(["row_id", "unit", "firmware", "component"]).polarity.mean().unstack("component").reset_index()
    t["all_positive"] = (t[[c for c in "ZNE" if c in t]] > 0.8).all(axis=1)
    return t


@check("gain", "Is the amplitude exactly 10^(gain/20)? Ratio to the 0 dB nodes of the same batch.")
def gain(meas, ctx):
    z = _z(meas)
    rows = []
    for b, g in z.groupby("batch"):
        ref = g[(g.gain_db == 0) & (g.filter_phase == "linear") & (g.sample_rate_sps == 1000)].amp_ratio.median()
        sel = g[(g.sample_rate_sps == 1000) & (g.filter_phase == "linear")]
        for (u, fw, gd), h in sel.groupby(["unit", "firmware", "gain_db"]):
            rows.append(dict(batch=b, unit=u, firmware=fw, gain_db=gd, amp_ratio=h.amp_ratio.median() / ref,
                             error_pct=100 * (h.amp_ratio.median() / ref - 1)))
    return pd.DataFrame(rows)


@check("filter_phase", "Delay and waveform change of the minimum-phase filter against linear phase.")
def filter_phase(meas, ctx):
    t = timing(meas, ctx, default=True)
    lin = t[t.filter_phase == "linear"].groupby("sample_rate_sps").lag_s.median()
    mn = t[t.filter_phase == "minimum"].copy()
    mn["linear_lag_s"] = mn.sample_rate_sps.map(lin)
    mn["delay_s"] = mn.lag_s - mn.linear_lag_s
    return mn[["row_id", "unit", "firmware", "sample_rate_sps", "lag_s", "linear_lag_s", "delay_s", "cc"]]


@check("orientation", "Horizontal rotation of each node from the data, against the planned rotation.")
def orientation(meas, ctx):
    h = meas[meas.component == "H"][["row_id", "unit", "firmware", "orientation_deg", "rotation_deg"]].copy()
    h["error_deg"] = ((h.rotation_deg - h.orientation_deg + 180) % 360) - 180
    return h


@check("unit_scatter", "Scatter between nodes with identical settings in the same batch.")
def unit_scatter(meas, ctx):
    t = timing(meas, ctx, default=True)
    amp = _z(meas).groupby("row_id").amp_ratio.median()
    t["amp_ratio"] = t.row_id.map(amp)
    keys = ["batch", "sample_rate_sps", "gain_db", "filter_phase", "orientation_deg"]
    g = t.groupby(keys)
    out = g.agg(units=("unit", "nunique"), firmwares=("firmware", "nunique"), lag_std_s=("lag_s", "std"),
                lag_range_s=("lag_s", lambda x: x.max() - x.min()), amp_std=("amp_ratio", "std")).reset_index()
    return out[out.units > 1]


@check("factors", "Effect of each extra setting (GNSS, low cut, ADC mode, storage, constellation, test mode) "
                  "against reference-setting nodes of the same batch and firmware generation.")
def factors(meas, ctx):
    z = meas[meas.component == "Z"]
    per_unit = z.groupby("row_id").agg(
        batch=("batch", "first"), unit=("unit", "first"), firmware=("firmware", "first"),
        sample_rate_sps=("sample_rate_sps", "first"), lag_s=("lag_s", "median"), lag_mad_s=("lag_s", _mad),
        cc=("cc", "median"), amp_ratio=("amp_ratio", "median"), noise_rms=("noise_rms", "median"),
        per_channel_gain=("per_channel_gain", "first"), gain_db=("gain_db", "first"),
        **{k: (k, "first") for k in hc.FACTOR_DEFAULTS}).reset_index()
    ref = per_unit[hc.is_default(per_unit) & (per_unit.gain_db == 0)]
    rows = []
    for k, dflt in hc.FACTOR_DEFAULTS.items():
        for lev, g in per_unit[per_unit[k].astype(str) != dflt].groupby(k):
            for r in g.itertuples():
                # baseline: reference nodes of the same firmware in the same batch, else the same
                # firmware in other batches (firmware offsets cancel), else the batch's reference nodes
                same_rate = ref[ref.sample_rate_sps == r.sample_rate_sps]
                cands = [(same_rate[(same_rate.batch == r.batch) & (same_rate.firmware == r.firmware)],
                          "same firmware, same batch"),
                         (same_rate[same_rate.firmware == r.firmware], "same firmware, other batches"),
                         (same_rate[same_rate.batch == r.batch], "other firmware, same batch")]
                b, how = next(((c, h) for c, h in cands if len(c)), (None, ""))
                if b is None:
                    continue
                rows.append(dict(factor=k, level=lev, row_id=r.row_id, unit=r.unit, firmware=r.firmware,
                                 compared_with=how,
                                 lag_diff_s=r.lag_s - b.lag_s.median(), lag_mad_s=r.lag_mad_s,
                                 ref_lag_mad_s=b.lag_mad_s.median(), cc=r.cc, ref_cc=b.cc.median(),
                                 amp_ratio=r.amp_ratio / b.amp_ratio.median(),
                                 noise_ratio=r.noise_rms / b.noise_rms.median()))
    return pd.DataFrame(rows)


@check("channel_mapping", "Which logged channel (1-3) is X, Y and Z? Units with gains 0/12/24 dB on "
                          "Ch1/Ch2/Ch3: the amplitude of each component shows its gain.")
def channel_mapping(meas, ctx):
    m = meas[meas.component.isin(list("ZNE"))]
    pcg = m[m.per_channel_gain.astype(str).str.lower().isin(["true", "1"])]
    rows = []
    for rid, g in pcg.groupby("row_id"):
        b = g.batch.iloc[0]
        ref = m[(m.batch == b) & hc.is_default(m) & (m.gain_db == 0) & (m.sample_rate_sps == g.sample_rate_sps.iloc[0])]
        gains = {ch: float(g[f"gain_ch{ch}"].iloc[0]) for ch in (1, 2, 3)}
        rec = dict(row_id=rid, unit=g.unit.iloc[0], firmware=g.firmware.iloc[0])
        for comp, h in g.groupby("component"):
            r0 = ref[ref.component == comp].amp_ratio.median()
            if not np.isfinite(r0) or r0 <= 0:
                continue
            gain_db = 20 * np.log10(h.amp_ratio.median() / r0)
            ch = min(gains, key=lambda c: abs(gains[c] - gain_db))
            rec[f"{comp}_gain_db"] = round(float(gain_db), 2)
            rec[f"{comp}_is_channel"] = ch
        comps = [rec.get(f"{c}_is_channel") for c in "ZNE"]
        rec["mapping"] = ", ".join(f"Ch{rec[f'{c}_is_channel']}={'XYZ'['NEZ'.index(c)]}" for c in "NEZ"
                                   if f"{c}_is_channel" in rec)
        rec["unique"] = len(set(comps)) == 3 and None not in comps
        rows.append(rec)
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
def run_checks(cfg, batches=None, names=None, matrix=None) -> dict:
    """Run the registered checks on the measurements of ``batches`` (default:
    all measured) and write ``results/<name>.csv``. Returns {name: DataFrame}."""
    res_dir = cfg["data_dir"] / "results"
    files = sorted(res_dir.glob("batch_*/measurements.csv"))
    meas = pd.concat([pd.read_csv(f) for f in files], ignore_index=True) if files else pd.DataFrame()
    if batches is not None and len(meas):
        meas = meas[meas.batch.isin(batches)]
    if meas.empty:
        raise FileNotFoundError("no measurements - run 'analyse' first")
    if "per_channel_gain" in meas:
        meas["per_channel_gain"] = meas["per_channel_gain"].astype(str).str.lower().isin(["true", "1"])
    for k, v in hc.FACTOR_DEFAULTS.items():
        meas[k] = meas[k].fillna(v).astype(str) if k in meas else v
    ctx = dict(cfg=cfg, matrix=hc.load_matrix(cfg) if matrix is None else matrix,
               batches=sorted(meas.batch.unique()))
    out = {}
    for name, c in REGISTRY.items():
        if names and name not in names:
            continue
        try:
            out[name] = c["func"](meas, ctx)
            ctx[f"result_{name}"] = out[name]
        except Exception as exc:  # noqa: BLE001 - report and continue with the other checks
            out[name] = pd.DataFrame([dict(error=f"{type(exc).__name__}: {exc}")])
        out[name].to_csv(res_dir / f"{name}.csv", index=False)
    return out
