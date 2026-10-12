"""
Instrument responses for SmartSolo nodes, kept as StationXML files.

The folder ``instrument_responses/`` (next to ``lib/``) holds one
sub-folder per response model. Each sub-folder has a ``response.yaml``
(readable description: source, constants, poles and zeros, notes, and the
list of files) and FDSN StationXML files, one per device, preamp gain and,
where it matters, sample rate and anti-alias filter phase:

    auspass/   AusPass/ANSIR published response (default)
    dtcc/      the same structure with DTCC data-sheet constants (harvest script)
    nominal/   DT-SOLO 5 Hz data-sheet geophone (5 Hz, h 0.70, 80 V/(m/s))
    nrl/       EarthScope/IRIS Nominal Response Library, with the FIR decimation
               stages (linear or minimum phase) for each sample rate
    metal/     a joke: a U-shaped "rock EQ" and a preamp that goes to 11

``get_response(model, device_type, gain_db, sampling_rate, filter_phase)``
returns an ObsPy ``Response``. When a file for the exact gain is missing,
the preamp stage of the nearest file is rescaled. When the folder or file
is missing, ``"auspass"`` falls back to the response embedded in
``smartsolo_node.staged_response`` (so the default always works).

``inspect_instrument_response(...)`` prints a summary (stages, poles and
zeros, natural frequency and damping, gains, group delay) and plots
amplitude and phase of one or more responses.

Author: Tobias Stål (UTAS)
"""

from __future__ import annotations

import functools
import os
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

import smartsolo_node as sn

DEFAULT_MODEL = "auspass"
RESPONSE_DIR = Path(os.environ.get("NODE_TOOLBOX_RESPONSES",
                                   Path(__file__).resolve().parents[1] / "instrument_responses"))
GAINS_DB = (0, 6, 12, 18, 24, 30, 36)

__all__ = ["RESPONSE_DIR", "DEFAULT_MODEL", "list_responses", "response_files", "get_response",
           "response_curve", "describe_response", "inspect_instrument_response", "device_key"]


def device_key(device_type) -> str:
    """File name key of a device type: ``IGU-16HR3C`` (also used for the 1C,
    which AusPass finds to have the same response) or ``BD3C-5``."""
    return "BD3C-5" if sn.is_bd3c(device_type) else "IGU-16HR3C"


@functools.lru_cache(maxsize=None)
def _meta(model: str) -> dict:
    import yaml
    f = RESPONSE_DIR / model / "response.yaml"
    if not f.exists():
        raise FileNotFoundError(f"no response model '{model}' ({f} missing)")
    return yaml.safe_load(f.read_text())


def list_responses() -> pd.DataFrame:
    """One row per response model in ``RESPONSE_DIR``."""
    rows = []
    for d in sorted(p for p in RESPONSE_DIR.glob("*/response.yaml")):
        m = _meta(d.parent.name)
        files = m.get("files", [])
        rows.append(dict(model=d.parent.name, title=m.get("title", ""), default=d.parent.name == DEFAULT_MODEL,
                         devices=sorted({f["device"] for f in files}),
                         gains_db=sorted({f["gain_db"] for f in files}),
                         sample_rates=sorted({f.get("sample_rate") for f in files if f.get("sample_rate")}) or "any",
                         filter_phases=sorted({f.get("filter_phase") for f in files if f.get("filter_phase")}) or "-",
                         n_files=len(files), use=m.get("use", "")))
    return pd.DataFrame(rows)


def response_files(model: str) -> pd.DataFrame:
    """The files of one model with their device, gain, rate and phase."""
    df = pd.DataFrame(_meta(model).get("files", []))
    if len(df):
        df["path"] = [str(RESPONSE_DIR / model / f) for f in df.file]
    return df


@functools.lru_cache(maxsize=256)
def _read(path: str):
    from obspy import read_inventory
    return read_inventory(path)[0][0][0].response


def _rescale_preamp(resp, factor):
    for s in resp.response_stages:
        if str(s.input_units).upper() == "V" and str(s.output_units).upper() == "V":
            s.stage_gain *= factor
            break
    else:
        resp.response_stages[0].stage_gain *= factor
    resp.instrument_sensitivity.value *= factor


def get_response(model: str = DEFAULT_MODEL, device_type="IGU-16HR 3C 5Hz", gain_db: float = 0.0,
                 sampling_rate: float | None = None, filter_phase: str = "linear",
                 nearest_rate: bool = True, fallback: bool = True):
    """
    ObsPy ``Response`` (velocity M/S -> counts) of a response model.

    ``gain_db`` is the preamp gain *contained in the data* (the log value for
    raw DLD counts, 0 when the gain was removed). ``sampling_rate`` and
    ``filter_phase`` (``"linear"`` / ``"minimum"``) choose the file for
    models with rate-specific FIR stages (``nrl``); otherwise the rate is
    written into the ADC stage. With ``nearest_rate`` the closest available
    rate is used (with a warning) when there is no file for the exact one.
    ``fallback``: if the ``auspass`` files are missing, use the embedded
    ``smartsolo_node.staged_response``.
    """
    import copy

    dev = device_key(device_type)
    try:
        files = response_files(model)
    except FileNotFoundError:
        if model == "auspass" and fallback:
            warnings.warn(f"{RESPONSE_DIR / model} not found - using the embedded AusPass response")
            return sn.staged_response(device_type, float(sampling_rate or 1000.0), gain_db, model="auspass")
        if model == "dtcc" and fallback:
            return sn.staged_response(device_type, float(sampling_rate or 1000.0), gain_db, model="dtcc")
        raise
    f = files[files.device == dev]
    if not len(f):
        raise ValueError(f"model '{model}' has no response for {dev} (devices: {sorted(set(files.device))})")
    if "filter_phase" in f and f.filter_phase.notna().any():
        ph = "MP" if str(filter_phase).lower().startswith("min") else "LP"
        f = f[f.filter_phase == ph] if (f.filter_phase == ph).any() else f
    if "sample_rate" in f and f.sample_rate.notna().any():
        if sampling_rate is None:
            sampling_rate = 1000.0 if (f.sample_rate == 1000).any() else float(f.sample_rate.max())
        rates = np.array(sorted(f.sample_rate.dropna().unique()), float)
        r = rates[np.argmin(np.abs(np.log(rates / float(sampling_rate))))]
        if abs(r - float(sampling_rate)) > 1e-6:
            if not nearest_rate:
                raise ValueError(f"model '{model}' has no {dev} file at {sampling_rate:g} sps (has {list(rates)})")
            warnings.warn(f"model '{model}': no {dev} file at {sampling_rate:g} sps, using {r:g} sps "
                          "(its FIR stages and delay belong to that rate)")
        f = f[f.sample_rate == r]
    g = np.array(f.gain_db, float)
    row = f.iloc[int(np.argmin(np.abs(g - float(gain_db))))]
    resp = copy.deepcopy(_read(row.path))
    if abs(float(row.gain_db) - float(gain_db)) > 1e-9:
        _rescale_preamp(resp, 10 ** ((float(gain_db) - float(row.gain_db)) / 20))
    if sampling_rate is not None and not (pd.notna(row.get("sample_rate", np.nan))):
        from obspy.core.inventory.util import Frequency
        for s in resp.response_stages:
            if getattr(s, "decimation_input_sample_rate", None) is not None:
                s.decimation_input_sample_rate = Frequency(float(sampling_rate))
    return resp


# --------------------------------------------------------------------------- #
# Inspection
# --------------------------------------------------------------------------- #
def response_curve(resp, freqs=None, sampling_rate=None, output="VEL"):
    """Complex response at ``freqs`` (Hz; default 0.05 Hz .. 0.45 x rate)."""
    rate = sampling_rate or _rate(resp) or 1000.0
    if freqs is None:
        freqs = np.logspace(np.log10(0.05), np.log10(0.49 * rate), 600)
    freqs = np.asarray(freqs, float)
    return freqs, resp.get_evalresp_response_for_frequencies(freqs, output=output)


def _rate(resp):
    rs = [getattr(s, "decimation_input_sample_rate", None) for s in resp.response_stages]
    fs = [getattr(s, "decimation_factor", None) for s in resp.response_stages]
    last = None
    for r, f in zip(rs, fs):
        if r:
            last = r / (f or 1)
    return last


def describe_response(resp, sampling_rate=None) -> dict:
    """Numbers of a response: overall sensitivity, poles/zeros, f0 and damping
    of the geophone pole pair, flat gain, gain at selected frequencies,
    group delay at 10 Hz and the -3 dB corners."""
    st = resp.response_stages
    paz = [s for s in st if hasattr(s, "poles") and s.poles]
    out = dict(sensitivity=resp.instrument_sensitivity.value,
               sensitivity_freq_hz=resp.instrument_sensitivity.frequency,
               input_units=resp.instrument_sensitivity.input_units,
               output_units=resp.instrument_sensitivity.output_units,
               n_stages=len(st), n_fir_stages=sum(1 for s in st if type(s).__name__ in ("FIRResponseStage",
                                                                                         "CoefficientsTypeResponseStage")
                                                    and len(getattr(s, "numerator", []) or []) > 1))
    if paz:
        p = np.array(paz[0].poles, complex)
        cp = p[np.abs(p.imag) > 0]
        if len(cp):
            w0 = float(np.abs(cp[0])); out.update(f0_hz=w0 / (2 * np.pi), damping=float(-cp[0].real / w0))
        out.update(poles=[complex(x) for x in paz[0].poles], zeros=[complex(x) for x in paz[0].zeros])
    rate = sampling_rate or _rate(resp) or 1000.0
    f, h = response_curve(resp, sampling_rate=rate)
    a = np.abs(h)
    flat = float(np.median(a[(f > 3 * out.get("f0_hz", 5)) & (f < 0.2 * rate)])) if ((f > 3 * out.get("f0_hz", 5)) & (f < 0.2 * rate)).any() else float(a.max())
    out["flat_gain"] = flat
    for fx in (1, 2, 5, 10, 20, 50):
        if fx < 0.45 * rate:
            out[f"gain_{fx}Hz_rel_flat_db"] = float(20 * np.log10(np.interp(fx, f, a) / flat))
    ph = np.unwrap(np.angle(h))
    gd = -np.gradient(ph, 2 * np.pi * f)
    out["group_delay_10Hz_ms"] = float(1000 * np.interp(10, f, gd)) if 10 < 0.45 * rate else np.nan
    below = f[(a < flat / np.sqrt(2)) & (f < 50)]
    above = f[(a < flat / np.sqrt(2)) & (f > 0.1 * rate)]
    out["low_corner_hz"] = float(below.max()) if len(below) else np.nan
    out["high_corner_hz"] = float(above.min()) if len(above) else np.nan
    return out


def inspect_instrument_response(models=DEFAULT_MODEL, device_type="IGU-16HR 3C 5Hz", gain_db: float = 0.0,
                                sampling_rate: float = 1000.0, filter_phase: str = "linear", plot: bool = True,
                                normalise: bool = False, ax=None, verbose: bool = True):
    """
    Inspect one or more responses. ``models`` is a model name, an ObsPy
    ``Response``, or a list / dict of these (dict keys become labels).
    Prints a summary table, plots amplitude and phase (``plot=True``), and
    returns ``(table, figure)``.

        import smartsolo_response as sr
        sr.inspect_instrument_response()                              # the default (auspass)
        sr.inspect_instrument_response(["auspass", "dtcc", "nominal", "nrl"], normalise=True)
        sr.inspect_instrument_response("nrl", sampling_rate=250, filter_phase="minimum")
    """
    if isinstance(models, dict):
        items = list(models.items())
    elif isinstance(models, (list, tuple)):
        items = [(m if isinstance(m, str) else f"response {i}", m) for i, m in enumerate(models)]
    else:
        items = [(models if isinstance(models, str) else "response", models)]
    resps, rows = {}, []
    for label, m in items:
        r = get_response(m, device_type, gain_db, sampling_rate, filter_phase) if isinstance(m, str) else m
        resps[label] = r
        d = describe_response(r, sampling_rate)
        rows.append(dict(model=label, **{k: v for k, v in d.items() if k not in ("poles", "zeros")}))
    table = pd.DataFrame(rows).set_index("model")
    if verbose:
        with pd.option_context("display.width", 160, "display.max_columns", 30, "display.float_format",
                               lambda x: f"{x:.6g}"):
            print(f"{device_type}, {gain_db:g} dB preamp gain in the data, {sampling_rate:g} sps, "
                  f"{filter_phase} phase")
            print(table.T.to_string())
    fig = None
    if plot:
        import matplotlib.pyplot as plt
        if ax is None:
            fig, ax = plt.subplots(2, 1, figsize=(7.5, 5.2), sharex=True,
                                   gridspec_kw=dict(height_ratios=[1.4, 1]))
        else:
            fig = ax[0].figure
        for label, r in resps.items():
            f, h = response_curve(r, sampling_rate=sampling_rate)
            a = np.abs(h)
            if normalise:
                a = a / describe_response(r, sampling_rate)["flat_gain"]
            ax[0].loglog(f, a, lw=1.2, label=label)
            ax[1].semilogx(f, np.degrees(np.unwrap(np.angle(h))), lw=1.0)
        ax[0].set_ylabel("|H| (relative)" if normalise else "|H| (counts per m/s)")
        ax[1].set_ylabel("phase (°)"); ax[1].set_xlabel("frequency (Hz)")
        ax[0].legend(fontsize=8, frameon=False)
        for a_ in ax:
            a_.grid(True, which="both", alpha=0.2)
        ax[0].set_title(f"{device_type}, {gain_db:g} dB, {sampling_rate:g} sps", fontsize=9)
    return table, fig
