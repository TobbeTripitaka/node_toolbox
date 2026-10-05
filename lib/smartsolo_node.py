"""
smartsolo_node
==============

Read the *other* files SmartSolo nodes leave next to ``DigiSolo.LOG`` and use
them for sensor QC and metadata.

Files in a node folder (IGU-16HR 3C, firmware V1.0.8)
----------------------------------------------------
``device.ini``        serial number and firmware version (``[deviceInfo] sn=...``)
``sct_par.xml``       acquisition script as loaded (``sct_par_b.xml`` = backup copy)
``SCT_INT.XML``       script as interpreted by the node (adds channel 4)
``DigiSolo.LOG``      state-of-health log (see ``smartsolo_log``)
``PULSE_X/Y/Z.WAV``   geophone pulse-test recording of the **latest** power-up
                      (overwritten at every boot), one file per axis
``DigiSolo.TXT``      file-system marker ("This is DigiSolo IGU-16 File System")
``guardfile.db``      binary journal of recent log keys (``memory00336/available
                      memory`` ...); no extra information, ignored

Pulse test (PULSE_*.WAV)
------------------------
24-bit mono WAV. The WAV header says 10 000 Hz, but the recording is the
16-second "Stage 3 geophone/ADC test" (SmartSolo hardware manual) stored as
16 000 samples, i.e. **1000 samples/s**. Only at 1000 Hz does the free
oscillation after each current step give the ~5 Hz natural frequency and
~0.7 damping reported in the log, so :func:`read_pulse` uses 1000 Hz unless
told otherwise. The test consists of

1. ~1.9 s at the start with large (often clipped) signals - not used,
2. four current steps (on/off, two polarities, repeated twice, ~1 s each):
   the DC plateau while current flows is proportional to coil resistance,
   the damped oscillation after each switch gives natural frequency ``f0``
   and damping ``h``,
3. ~6 s of very quiet signal at the end: the electronic noise floor
   (≈ 3.9 counts RMS = 1.16 µV at 0 dB, matching ``ChN RMS Noise`` in the
   log, which is therefore in µV). Presumably the input is switched to a
   resistor/short here; the manual does not say.

Conversion: 3355.4428 counts/mV at 0 dB preamp gain (SmartSolo manual), i.e.
2^23 counts per 2.5 V.
"""

from __future__ import annotations

import configparser
import re
import wave
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd

__all__ = [
    "COUNTS_PER_MV_0DB",
    "NOMINAL_5HZ",
    "read_device_ini",
    "read_script",
    "script_limits",
    "read_pulse",
    "analyse_pulse",
    "analyse_pulse_folder",
    "geophone_qc",
    "read_node_folder",
    "find_node_folders",
    "geophone_response",
    "magnetic_declination",
]

COUNTS_PER_MV_0DB = 3355.4428          # SmartSolo hardware manual, IGU-16HR 3C
PULSE_SAMPLING_RATE = 1000.0           # see module docstring
# DT-SOLO 5 Hz geophone (SmartSolo IGU-16HR 3C data sheet)
NOMINAL_5HZ = dict(f0=5.0, damping=0.70, damping_open=0.60, sensitivity=80.0,
                   coil_resistance=1850.0)


# AusPass / ANSIR published response for IGU-16HR 3C (counts, data already
# multiplied by -1; same for all channels and units, sample-rate independent):
# https://auspass.edu.au/xwiki/bin/view/Instrumentation/SmartSolo%20Nodes/
# Poles correspond to f0 = 5.000 Hz, h = 0.707; sensitivity / 3355.4428 counts/mV
# = 76.6 V/(m/s).
AUSPASS_16HR3C = dict(poles=[complex(-22.211059, 22.217768), complex(-22.211059, -22.217768)],
                      zeros=[0j, 0j], sensitivity=257019225.55108312)


def counts_per_volt(gain_db: float = 0.0) -> float:
    """ADC counts per volt at the given preamp gain (0, 6, ... 36 dB)."""
    return COUNTS_PER_MV_0DB * 1000.0 * 10 ** (float(gain_db) / 20.0)


# --------------------------------------------------------------------------- #
# Small text files
# --------------------------------------------------------------------------- #
def read_device_ini(path) -> dict:
    """``device.ini`` -> ``{'sn': '453022522', 'firmwareVersion': 'V1.0.8.1be'}``."""
    cp = configparser.ConfigParser()
    cp.optionxform = str
    cp.read(path, encoding="utf-8")
    out = {}
    for sec in cp.sections():
        out.update(dict(cp[sec]))
    return out


def _typed(v: str):
    v = (v or "").strip()
    if re.fullmatch(r"[-+]?\d+", v):
        return int(v)
    if re.fullmatch(r"[-+]?\d*\.\d+", v):
        return float(v)
    return v


def read_script(path) -> pd.Series:
    """Acquisition script XML (``sct_par.xml`` / ``SCT_INT.XML``) as a Series."""
    root = ET.fromstring(Path(path).read_text(encoding="utf-8", errors="replace").strip())
    return pd.Series({el.tag: _typed(el.text) for el in root}, name=Path(path).name)


def script_limits(script: pd.Series) -> dict:
    """
    Geophone test acceptance limits and thresholds from a script, in physical
    units. The script stores integers: resistance in Ω, damping ×1000,
    frequency ×100 Hz, voltages ×100 V.
    """
    g = script.get
    lim = {
        "resistance_ohm": (g("resistance_lower_limit"), g("resistance_upper_limit")),
        "damping": (_div(g("damping_lower_limit"), 1000), _div(g("damping_upper_limit"), 1000)),
        "resonate_freq_hz": (_div(g("resonate_frequency_lower_limit"), 100),
                             _div(g("resonate_frequency_upper_limit"), 100)),
        "sensitivity_v_per_m_s": (g("sensitivity_lower_limit"), g("sensitivity_upper_limit")),
        "battery_threshold_v": _div(g("Battery_Threshold"), 100),
        "battery_critical_v": _div(g("Battery_Critical"), 100),
        "battery_full_v": _div(g("Full_Battery_Indicator_Voltage"), 100),
        "gps_relock_warning_s": g("GPS_Relock_Timeout_Warning"),
        "sample_interval_ms": _div(g("Sample_Rate"), 100),        # stored in 10 µs units
        "sample_rate_hz": (1e5 / g("Sample_Rate")) if g("Sample_Rate") else None,
        "gains_db": [g(f"Channel_{i}_Gain") for i in (1, 2, 3)],
    }
    return lim


def _div(v, d):
    return None if v is None or v == "" else float(v) / d


# --------------------------------------------------------------------------- #
# Pulse test
# --------------------------------------------------------------------------- #
def read_pulse(path, sampling_rate: float | None = PULSE_SAMPLING_RATE):
    """
    Read a ``PULSE_?.WAV`` as an ObsPy Trace (counts, int32).

    ``sampling_rate=None`` uses the WAV header (10 000 Hz, which is not the
    real rate - see module docstring).
    """
    from obspy import Trace

    with wave.open(str(path)) as w:
        sw, n, sr_header = w.getsampwidth(), w.getnframes(), w.getframerate()
        raw = w.readframes(n)
    if sw == 3:
        a = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        x = a[:, 0] | (a[:, 1] << 8) | (a[:, 2] << 16)
        x[x >= 2 ** 23] -= 2 ** 24
    else:
        x = np.frombuffer(raw, {2: "<i2", 4: "<i4"}[sw]).astype(np.int32)
    tr = Trace(x.astype(np.int32))
    tr.stats.sampling_rate = float(sampling_rate or sr_header)
    axis = re.search(r"PULSE_([A-Za-z0-9])", Path(path).name, re.I)
    tr.stats.channel = axis.group(1).upper() if axis else ""
    tr.stats.wav_header_rate = sr_header
    tr.stats.clip_level = 2 ** (8 * sw - 1) - 1
    return tr


def _damped(t, A, f0, h, ph, C):
    w = 2 * np.pi * f0
    wd = w * np.sqrt(max(1 - h * h, 1e-9))
    return A * np.exp(-h * w * t) * np.sin(wd * t + ph) + C


def analyse_pulse(tr, gain_db: float = 0.0, skip: float = 1.7, min_step_counts: float = 5e5,
                  fit_window: float = 0.9, nominal_f0: float = 5.0) -> dict:
    """
    Analyse one pulse-test trace.

    Finds the current steps (jumps > ``min_step_counts``), fits a damped
    oscillation ``A·exp(-h·ω0·t)·sin(ωd·t+φ)+C`` to ``fit_window`` seconds
    after each step and measures the noise floor after the last step.

    Returns ``{"steps": DataFrame, "noise_rms_counts", "noise_rms_uV",
    "f0_hz", "damping", "clipped_fraction"}``. ``f0_hz``/``damping`` are
    medians over the *switch-off* steps after a negative current (best fits,
    closest to the logged values); every step is listed in ``steps``.
    """
    from scipy.optimize import curve_fit

    x = tr.data.astype(float)
    sr = tr.stats.sampling_rate
    cpv = counts_per_volt(gain_db)
    i0 = int(skip * sr)
    d = np.diff(x)
    jumps = np.where(np.abs(d) > min_step_counts)[0]
    jumps = jumps[jumps >= i0]
    edges = []
    for j in jumps:
        if not edges or j - edges[-1] > 0.5 * sr:
            edges.append(j)

    rows = []
    for k, e in enumerate(edges):
        pre = slice(max(e - int(0.3 * sr), 0), max(e - int(0.02 * sr), 1))
        nxt = edges[k + 1] if k + 1 < len(edges) else len(x)
        post_end = min(e + int(fit_window * sr), nxt - 2)
        lvl_before = float(np.median(x[pre]))
        lvl_after = float(np.median(x[max(post_end - int(0.3 * sr), e + 1):post_end]))
        seg = x[e + 3:post_end]
        t = np.arange(len(seg)) / sr
        row = dict(time_s=e / sr, level_before=lvl_before, level_after=lvl_after,
                   level_before_mV=lvl_before / cpv * 1e3, level_after_mV=lvl_after / cpv * 1e3,
                   kind=("off" if abs(lvl_after) < abs(lvl_before) else "on"),
                   polarity=np.sign(lvl_before if abs(lvl_after) < abs(lvl_before) else lvl_after))
        try:
            A0 = seg[np.argmax(np.abs(seg - lvl_after))] - lvl_after
            p, _ = curve_fit(_damped, t, seg, p0=[A0, nominal_f0, 0.7, 0.0, lvl_after],
                             bounds=([-np.inf, 0.5 * nominal_f0, 0.05, -2 * np.pi, -np.inf],
                                     [np.inf, 3 * nominal_f0, 0.999, 2 * np.pi, np.inf]),
                             maxfev=20000)
            resid = float(np.std(seg - _damped(t, *p)) / np.ptp(seg))
            row.update(amplitude=p[0], f0_hz=p[1], damping=p[2], rel_residual=resid)
        except Exception:  # noqa: BLE001
            row.update(amplitude=np.nan, f0_hz=np.nan, damping=np.nan, rel_residual=np.nan)
        rows.append(row)
    steps = pd.DataFrame(rows)

    # noise floor: from 1.2 s after the last step (ringing has died out) to the end
    start = (edges[-1] + int(1.2 * sr)) if edges else int(0.65 * len(x))
    tail = x[start:]
    noise = float(np.std(tail)) if len(tail) > 10 else np.nan

    best = steps[(steps.get("kind") == "off") & (steps.get("polarity") < 0)] if len(steps) else steps
    if best.empty and len(steps):
        best = steps[steps["kind"] == "off"]
    return {
        "axis": tr.stats.channel,
        "steps": steps,
        "f0_hz": float(best["f0_hz"].median()) if len(best) else np.nan,
        "damping": float(best["damping"].median()) if len(best) else np.nan,
        "noise_rms_counts": noise,
        "noise_rms_uV": noise / cpv * 1e6,
        "clipped_fraction": float(np.mean(np.abs(tr.data) >= tr.stats.clip_level)),
        "sampling_rate": sr,
    }


def analyse_pulse_folder(folder, gain_db: float = 0.0, **kw) -> pd.DataFrame:
    """One summary row per ``PULSE_?.WAV`` in a folder."""
    rows = []
    for p in sorted(Path(folder).glob("PULSE_*.[Ww][Aa][Vv]")):
        r = analyse_pulse(read_pulse(p), gain_db=gain_db, **kw)
        s = r["steps"]
        rows.append({
            "axis": r["axis"], "file": str(p), "f0_hz": r["f0_hz"], "damping": r["damping"],
            "noise_rms_uV": r["noise_rms_uV"], "noise_rms_counts": r["noise_rms_counts"],
            "plateau_pos_mV": s.loc[(s.kind == "on") & (s.polarity > 0), "level_after_mV"].median() if len(s) else np.nan,
            "plateau_neg_mV": s.loc[(s.kind == "on") & (s.polarity < 0), "level_after_mV"].median() if len(s) else np.nan,
            "n_steps": len(s), "clipped_fraction": r["clipped_fraction"],
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# QC of the boot-time geophone tests in the log
# --------------------------------------------------------------------------- #
DEFAULT_LIMITS = {
    "resistance_ohm": (1640, 1905),
    "damping": (0.647, 0.752),
    "resonate_freq_hz": (4.63, 5.37),
    "sensitivity_v_per_m_s": (71, 82),
}


COPPER_ALPHA = 0.00393   # 1/°C, temperature coefficient of copper


def geophone_qc(device_info: pd.DataFrame, limits: dict | None = None,
                spread_noise_max: float = 5000.0, r_ref_ohm: float = 1850.0,
                t_ref_c: float = 25.0) -> pd.DataFrame:
    """
    Long table: one row per boot and geophone channel with the logged test
    values and pass/fail flags against the script limits.

    ``device_info`` comes from ``smartsolo_log.read_device_info``. ``limits``
    from :func:`script_limits` (defaults = the DML script). Resistance in the
    log is in kΩ and is compared with the temperature-corrected limits when
    the boot block has them (``Lower/Upper Resistance Limit`` second values).

    ``spread_noise_max``: tests with higher ``ChN Spread Noise`` were probably
    taken while the node was moving (handling, installation, wind) - flagged
    ``noisy``; treat their f0/damping/sensitivity with caution.

    ``coil_temp_est_c`` is a rough coil temperature from the copper coil
    resistance, assuming the nominal 1850 Ω is at ``t_ref_c`` (not stated in
    the data sheet). Differences between boots are more reliable than the
    absolute value.
    """
    lim = {**DEFAULT_LIMITS, **{k: v for k, v in (limits or {}).items() if k in DEFAULT_LIMITS}}
    di = device_info.reset_index()
    rows = []
    for _, b in di.iterrows():
        rlo, rhi = lim["resistance_ohm"]
        if pd.notna(b.get("lower_resistance_limit_2", np.nan)):
            rlo, rhi = b["lower_resistance_limit_2"], b["upper_resistance_limit_2"]
        for ch in (1, 2, 3):
            p = f"ch{ch}_"
            if f"{p}resonate_freq" not in b:
                continue
            r1 = b.get(f"{p}resistance_1", np.nan) * 1000
            r2 = b.get(f"{p}resistance_2", np.nan) * 1000
            f0, h, s = b[f"{p}resonate_freq"], b[f"{p}damping"], b[f"{p}sensitivity"]
            row = dict(serial=str(b.get("serial_number")), boot_no=b.get("boot_no"),
                       boot_time=b.get("boot_time"), channel=ch,
                       resistance_1_ohm=r1, resistance_2_ohm=r2, resonate_freq_hz=f0,
                       damping=h, sensitivity=s, rms_noise_uV=b.get(f"{p}rms_noise"),
                       spread_noise=b.get(f"{p}spread_noise"),
                       booting_temperature=b.get("booting_temperature"),
                       logged_test_passed=b.get("geophone_test_passed"))
            row["coil_temp_est_c"] = t_ref_c + (r1 / r_ref_ohm - 1) / COPPER_ALPHA
            row["ok_resistance"] = rlo <= r1 <= rhi
            row["ok_f0"] = lim["resonate_freq_hz"][0] <= f0 <= lim["resonate_freq_hz"][1]
            row["ok_damping"] = lim["damping"][0] <= h <= lim["damping"][1]
            row["ok_sensitivity"] = lim["sensitivity_v_per_m_s"][0] <= s <= lim["sensitivity_v_per_m_s"][1]
            row["ok_all"] = all(row[k] for k in ["ok_resistance", "ok_f0", "ok_damping", "ok_sensitivity"])
            row["noisy"] = bool(row["spread_noise"] > spread_noise_max)
            rows.append(row)
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Node folders
# --------------------------------------------------------------------------- #
def find_node_folders(root) -> list[Path]:
    """Folders below ``root`` that contain a ``DigiSolo.LOG`` (any case)."""
    root = Path(root)
    return sorted({p.parent for p in root.rglob("*") if p.is_file()
                   and p.name.lower() == "digisolo.log"})


def read_node_folder(folder, analyse: bool = True) -> dict:
    """
    Everything in one node folder: ``serial``, ``firmware``, ``script``
    (Series), ``limits``, ``log`` (path), ``device_info``, ``qc`` and the pulse
    analysis (``pulse``) of the latest power-up.
    """
    import smartsolo_log as sl

    folder = Path(folder)
    files = {p.name.lower(): p for p in folder.iterdir() if p.is_file()}
    out: dict = {"folder": folder}
    if "device.ini" in files:
        ini = read_device_ini(files["device.ini"])
        out["serial"], out["firmware"] = ini.get("sn"), ini.get("firmwareVersion")
    for key in ["sct_par.xml", "sct_int.xml"]:
        if key in files:
            out["script" if key == "sct_par.xml" else "script_int"] = read_script(files[key])
    out["limits"] = script_limits(out["script"]) if "script" in out else dict(DEFAULT_LIMITS)
    if "sct_par.xml" in files and "sct_par_b.xml" in files:
        out["script_backup_identical"] = (files["sct_par.xml"].read_bytes()
                                          == files["sct_par_b.xml"].read_bytes())
    if "digisolo.log" in files:
        out["log"] = files["digisolo.log"]
        out["device_info"] = sl.read_device_info(files["digisolo.log"])
        out["qc"] = geophone_qc(out["device_info"], out["limits"])
        if "serial" not in out and len(out["device_info"]):
            out["serial"] = str(out["device_info"]["serial_number"].iloc[0])
    if analyse and any(k.startswith("pulse_") for k in files):
        gains = out["limits"].get("gains_db", [0]) if isinstance(out["limits"], dict) else [0]
        out["pulse"] = analyse_pulse_folder(folder, gain_db=(gains[0] or 0))
    return out


# --------------------------------------------------------------------------- #
# Response and orientation helpers
# --------------------------------------------------------------------------- #
def geophone_response(f0: float = 5.0, damping: float = 0.70, sensitivity: float = 80.0,
                      gain_db: float = 0.0, sampling_rate: float | None = None,
                      normalization_frequency: float = 15.0):
    """
    ObsPy ``Response`` for a SmartSolo geophone channel: velocity (M/S) ->
    counts. Two zeros at 0 and the pole pair of a damped oscillator,
    ``-h·ω0 ± i·ω0·√(1-h²)``; sensor gain ``sensitivity`` V/(m/s) and
    digitiser gain :func:`counts_per_volt` (``gain_db``). ``sensitivity`` is
    the flat, above-resonance value; the overall sensitivity reported at
    ``normalization_frequency`` is slightly lower (≈0.6 % at 15 Hz).
    Anti-alias filter not included.
    """
    w0 = 2 * np.pi * f0
    wd = w0 * np.sqrt(1 - damping ** 2)
    poles = [complex(-damping * w0, wd), complex(-damping * w0, -wd)]
    zeros = [0j, 0j]
    return _paz_response(zeros, poles, sensitivity * counts_per_volt(gain_db),
                         normalization_frequency)


def _shape_at(f, zeros, poles):
    """|H(f)| of the zeros/poles alone; -> 1 at high frequency for a geophone."""
    s = 2j * np.pi * f
    return float(abs(np.prod([s - z for z in zeros]) / np.prod([s - p for p in poles])))


def _paz_response(zeros, poles, sensitivity_hf, fn):
    """Response whose high-frequency (flat) gain is ``sensitivity_hf``."""
    from obspy.core.inventory.response import Response

    shape = _shape_at(fn, zeros, poles)
    resp = Response.from_paz(zeros=zeros, poles=poles, stage_gain=sensitivity_hf * shape,
                             stage_gain_frequency=fn, input_units="M/S", output_units="COUNTS",
                             normalization_frequency=fn, normalization_factor=1.0 / shape)
    resp.recalculate_overall_sensitivity(fn)
    return resp


def auspass_response(normalization_frequency: float = 15.0, gain_db: float = 0.0):
    """
    The AusPass/ANSIR IGU-16HR 3C response (velocity -> counts), for data
    exported in counts with the preamp gain removed and the polarity
    inverted (x -1) - see ``AUSPASS_16HR3C``. ``gain_db`` adds the preamp
    gain for data where it was *not* removed (raw DLD).
    """
    p = AUSPASS_16HR3C
    return _paz_response(p["zeros"], p["poles"], p["sensitivity"] * 10 ** (gain_db / 20.0),
                         normalization_frequency)


def magnetic_declination(lat, lon, time, elevation_m: float = 0.0) -> float:
    """
    Magnetic declination (degrees, east positive) from IGRF via ``ppigrf``
    (``pip install ppigrf``). Useful to turn the logged ``eCompass North``
    (magnetic) into a true azimuth: ``true = magnetic + declination``.
    """
    import datetime

    import ppigrf

    t = pd.Timestamp(time)
    t = t.tz_convert("UTC").tz_localize(None) if t.tzinfo else t
    be, bn, _ = ppigrf.igrf(lon, lat, elevation_m / 1000.0, t.to_pydatetime())
    return float(np.degrees(np.arctan2(np.ravel(be)[0], np.ravel(bn)[0])))
