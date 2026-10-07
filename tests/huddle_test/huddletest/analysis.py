"""Load a batch and measure every node against the reference.

``measure_batch`` is the expensive step: for every unit and component it finds
the lag, correlation, polarity and amplitude ratio at every impact, and a
horizontal rotation. The checks in ``checks.py`` only summarise its table, so
new checks are cheap to add.

Sign convention: ``lag_s`` > 0 means the node's time stamps are late
(the node's trace must be shifted earlier to match the reference).
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from . import config as hc

COMP_MAP = {"X": "N", "Y": "E", "Z": "Z", "N": "N", "E": "E"}


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def unit_folder(cfg, batch, unit) -> Path | None:
    hits = sorted(hc.batch_dir(cfg, batch).glob(f"{unit}_*"))
    return hits[0] if hits else None


def load_unit(cfg, row, folder=None):
    """Stream of one unit (Z/N/E) from its DLD files, or its node MiniSEED
    files when ``storage == "miniseed"`` (DLD preferred if both exist).
    Polarity x -1 (node_toolbox's IGU-16HR convention) and the planned gain
    removed, so all nodes are comparable - except for units with different
    gains per channel (``per_channel_gain``), whose counts stay raw: the
    mapping of logged channels 1-3 to X/Y/Z is what the ``channel_mapping``
    check measures. ``stats.huddle`` holds the matrix row."""
    import smartsolo_dld as dld
    import smartsolo_waveforms as sw
    from obspy import Stream

    folder = folder or unit_folder(cfg, row.batch, row.unit)
    if folder is None:
        return Stream()
    st = Stream()
    files = sorted(Path(folder).rglob("*"))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        dlds = [f for f in files if f.suffix.lower() == ".dld" and dld.is_dld(f)]
        for f in dlds:
            st += dld.read_dld(f)
        if not dlds or str(getattr(row, "storage", "dld")) == "miniseed":
            ms = [f for f in files if f.suffix.lower() in (".miniseed", ".mseed", ".msd")]
            if ms:
                st = Stream()
                for f in ms:
                    part = sw.read_mseed_robust(f)
                    for tr in part:
                        tr.stats.channel = sw.component_from(f, tr)
                    st += part
    st.merge(method=1)
    raw = bool(getattr(row, "per_channel_gain", False))
    g = 1.0 if raw else 10 ** (float(row.gain_db) / 20)
    for tr in st:
        tr.data = -tr.data.astype(np.float64) / g
        tr.stats.channel = COMP_MAP.get(tr.stats.channel[-1:], tr.stats.channel)
        tr.stats.network, tr.stats.station = "HT", row.unit
        tr.stats.huddle = row._asdict() if hasattr(row, "_asdict") else dict(row)
        tr.stats.gain_removed = not raw
    return st


def reference_folder(cfg, batch=None) -> Path | None:
    """``<data_dir>/reference/batch_NN/`` if the sandbox copied the reference
    there for this batch, else ``reference.path`` from ``huddle.yaml``."""
    if batch is not None:
        d = cfg["data_dir"] / "reference" / f"batch_{int(batch):02d}"
        if d.is_dir() and any(d.iterdir()):
            return d
    p = cfg["reference"].get("path")
    return Path(p).expanduser() if p else None


def load_reference(cfg, t0=None, t1=None, batch=None):
    """Reference stream (Z/N/E) from the batch's reference folder (see
    ``reference_folder``), or None."""
    from obspy import Stream, read

    ref = cfg["reference"]
    folder = reference_folder(cfg, batch)
    if folder is None:
        return None
    st = Stream()
    for f in sorted(folder.rglob("*")):
        if f.is_file():
            try:
                st += read(str(f), starttime=t0, endtime=t1)
            except Exception:  # noqa: BLE001 - not a waveform file
                continue
    if ref.get("station"):
        st = st.select(station=ref["station"])
    if ref.get("channels"):
        st = st.select(channel=ref["channels"])
    st.merge(method=1, fill_value=0)
    for tr in st:
        tr.data = tr.data.astype(np.float64)
        tr.stats.channel = tr.stats.channel[-1].replace("1", "N").replace("2", "E")
    return st


def consensus_reference(streams: dict, matrix_rows: pd.DataFrame):
    """Without a reference station: the median of the units at 1000 sps, 0 dB,
    linear phase, not rotated (lags are then relative to these nodes)."""
    from obspy import Stream
    sel = matrix_rows[(matrix_rows.sample_rate_sps == 1000) & (matrix_rows.gain_db == 0)
                      & (matrix_rows.filter_phase == "linear") & (matrix_rows.orientation_deg == 0)
                      & hc.is_default(matrix_rows)]
    out = Stream()
    for comp in "ZNE":
        trs = [streams[u].select(channel=comp)[0] for u in sel.unit if u in streams and streams[u].select(channel=comp)]
        if not trs:
            continue
        t0 = max(tr.stats.starttime for tr in trs); t1 = min(tr.stats.endtime for tr in trs)
        cut = [tr.slice(t0, t1) for tr in trs]
        n = min(tr.stats.npts for tr in cut)
        tr = cut[0].copy()
        tr.data = np.median(np.vstack([c.data[:n] for c in cut]), axis=0)
        tr.stats.station, tr.stats.channel = "CONS", comp
        out.append(tr)
    return out


# --------------------------------------------------------------------------- #
# Signal processing
# --------------------------------------------------------------------------- #
def prep(tr, band, rate):
    """Bandpass (zero phase) in the common band, then to the common rate."""
    tr = tr.copy()
    tr.detrend("demean"); tr.taper(0.02)
    tr.filter("bandpass", freqmin=band[0], freqmax=min(band[1], 0.45 * tr.stats.sampling_rate),
              corners=4, zerophase=True)
    if tr.stats.sampling_rate != rate:
        if tr.stats.sampling_rate > rate:
            tr.filter("lowpass", freq=0.45 * rate, corners=8, zerophase=True)
        tr.interpolate(rate, method="lanczos", a=8)
    return tr


def envelope(x, rate, smooth_s=0.05):
    from scipy.signal import hilbert
    e = np.abs(hilbert(x))
    k = max(1, int(smooth_s * rate))
    return np.convolve(e, np.ones(k) / k, mode="same")


def detect_events(tr, min_snr=8.0, min_sep_s=2.0) -> list:
    """Impact times: envelope peaks above ``min_snr`` x the median envelope."""
    rate = tr.stats.sampling_rate
    e = envelope(tr.data, rate)
    thr = min_snr * np.median(e)
    from scipy.signal import find_peaks
    pk, _ = find_peaks(e, height=thr, distance=int(min_sep_s * rate))
    return [tr.stats.starttime + p / rate for p in pk]


def _xcorr_lag(a, b, max_lag_n):
    """Lag (samples) maximising the normalised correlation of b against a; b is
    longer by 2*max_lag_n. Returns (lag, cc) with parabolic refinement; lag > 0
    means b is late."""
    from obspy.signal.cross_correlation import correlate_template
    cc = correlate_template(b, a, mode="valid", normalize="full")
    i = int(np.argmax(np.abs(cc)))
    frac = 0.0
    if 0 < i < len(cc) - 1:
        y0, y1, y2 = np.abs(cc[i - 1]), np.abs(cc[i]), np.abs(cc[i + 1])
        d = y0 - 2 * y1 + y2
        frac = 0.5 * (y0 - y2) / d if d else 0.0
    return i + frac - max_lag_n, float(cc[i])


def coarse_lag(ref_tr, node_tr, max_lag_s, rate):
    """Lag over the whole overlap from envelopes (resolves block-size errors)."""
    t0 = max(ref_tr.stats.starttime, node_tr.stats.starttime) + max_lag_s
    t1 = min(ref_tr.stats.endtime, node_tr.stats.endtime) - max_lag_s
    if t1 - t0 < 10:
        return np.nan, np.nan
    a = envelope(ref_tr.slice(t0, t1).data, rate)
    b = envelope(node_tr.slice(t0 - max_lag_s, t1 + max_lag_s).data, rate)
    n = int(round(max_lag_s * rate))
    a = a - a.mean(); b = b - b.mean()
    m = len(a) + 2 * n
    lag, cc = _xcorr_lag(a, b[:m], n)
    return lag / rate, cc


def _rotate(n, e, deg):
    t = np.deg2rad(deg)
    return n * np.cos(t) + e * np.sin(t), -n * np.sin(t) + e * np.cos(t)


def measure_unit(ref, node, events, row, an) -> list[dict]:
    """Per component and event: lag, cc, polarity, amplitude ratio; plus the
    horizontal rotation that best maps the reference N/E onto the node's."""
    rate, band = an["work_rate_sps"], an["band_hz"]
    w0, w1 = an["event_window_s"]
    rz, nz = ref.select(channel="Z"), node.select(channel="Z")
    if not rz or not nz:
        return []
    R = {c: prep(ref.select(channel=c)[0], band, rate) for c in "ZNE" if ref.select(channel=c)}
    N = {c: prep(node.select(channel=c)[0], band, rate) for c in "ZNE" if node.select(channel=c)}
    lag0, cc0 = coarse_lag(R["Z"], N["Z"], an["max_lag_s"], rate)
    if not np.isfinite(lag0):
        return []
    fine_n = int(0.5 * rate)
    out = []
    noise = {}
    for c in N:                                    # RMS away from the impacts (quiet windows)
        tr = N[c]
        mask = np.ones(tr.stats.npts, bool)
        for t in events:
            i0 = int((t + lag0 - 3 - tr.stats.starttime) * rate); i1 = int((t + lag0 + 6 - tr.stats.starttime) * rate)
            mask[max(i0, 0):max(i1, 0)] = False
        noise[c] = float(np.sqrt(np.mean(tr.data[mask] ** 2))) if mask.sum() > rate else np.nan
    for t in events:
        for c in "ZNE":
            if c not in R or c not in N:
                continue
            a = R[c].slice(t + w0, t + w1).data
            b = N[c].slice(t + w0 + lag0 - 0.5, t + w1 + lag0 + 0.5).data
            if len(a) < 10 or len(b) < len(a) + 2 * fine_n:
                continue
            b = b[:len(a) + 2 * fine_n]
            lag, cc = _xcorr_lag(a, b, fine_n)
            seg = b[fine_n + int(round(lag)): fine_n + int(round(lag)) + len(a)]
            amp = float(np.sqrt(np.mean(seg ** 2)) / np.sqrt(np.mean(a ** 2))) if np.any(a) else np.nan
            out.append(dict(component=c, event_time=str(t), lag_s=lag0 + lag / rate, cc=abs(cc),
                            polarity=int(np.sign(cc)), amp_ratio=amp, coarse_lag_s=lag0, coarse_cc=cc0,
                            noise_rms=noise.get(c, np.nan)))
    # rotation: angle that maximises correlation of rotated reference horizontals with the node's
    if all(c in R and c in N for c in "NE"):
        best = (np.nan, -np.inf)
        for deg in range(0, 360, 2):
            sc = 0.0
            for t in events:
                rn = R["N"].slice(t + w0, t + w1).data; re_ = R["E"].slice(t + w0, t + w1).data
                k = len(rn)
                nn = N["N"].slice(t + w0 + lag0, t + w1 + lag0 + 1).data[:k]
                ne = N["E"].slice(t + w0 + lag0, t + w1 + lag0 + 1).data[:k]
                if len(nn) < k or len(ne) < k or k < 10:
                    continue
                pn, pe = _rotate(rn, re_, deg)
                sc += np.dot(pn, nn) + np.dot(pe, ne)
            if sc > best[1]:
                best = (deg, sc)
        out.append(dict(component="H", event_time="", lag_s=np.nan, cc=np.nan, polarity=0,
                        amp_ratio=np.nan, coarse_lag_s=lag0, coarse_cc=cc0, rotation_deg=best[0]))
    return out


def correct_responses(streams, ref, stationxml):
    """Remove the geophone (IGU-16HR poles/zeros, unit gain) from the nodes and
    the full response from the reference, so their phases can be compared
    (the 5 Hz geophone shifts the phase by tens of degrees in the 2-15 Hz band)."""
    import smartsolo_node as sn
    from obspy import read_inventory
    paz = dict(poles=sn.GEOPHONE_PAZ["poles"], zeros=sn.GEOPHONE_PAZ["zeros"], gain=1.0, sensitivity=1.0)
    for st in streams.values():
        for tr in st:
            tr.simulate(paz_remove=paz, remove_sensitivity=False)
    ref.remove_response(read_inventory(stationxml), output="VEL", water_level=60)


def measure_batch(cfg, batch: int, matrix=None, reference=None, verbose=True) -> pd.DataFrame:
    """Measurement table of one batch (also written to
    ``results/batch_NN/measurements.csv``)."""
    m = hc.load_matrix(cfg) if matrix is None else matrix
    rows = m[m["batch"] == batch]
    an = cfg["analysis"]
    streams = {}
    for r in rows.itertuples():
        st = load_unit(cfg, r)
        if len(st):
            streams[r.unit] = st
        elif verbose:
            print(f"{r.row_id}: no data")
    if not streams:
        raise FileNotFoundError(f"no data for batch {batch} in {hc.batch_dir(cfg, batch)}")
    t0 = min(tr.stats.starttime for st in streams.values() for tr in st)
    t1 = max(tr.stats.endtime for st in streams.values() for tr in st)
    ref = reference if reference is not None else load_reference(cfg, t0, t1, batch)
    ref_kind = "reference station"
    if ref is None or not len(ref):
        ref, ref_kind = consensus_reference(streams, rows), "consensus of 1000 sps / 0 dB / linear nodes"
    if an.get("correct_responses") and cfg["reference"].get("stationxml") and ref_kind == "reference station":
        correct_responses(streams, ref, cfg["reference"]["stationxml"])
        ref_kind += " (responses removed)"
    rz = prep(ref.select(channel="Z")[0], an["band_hz"], an["work_rate_sps"])
    events = detect_events(rz, an["min_snr"], an["min_separation_s"])
    events = [t for t in events if t + an["event_window_s"][0] - an["max_lag_s"] > t0
              and t + an["event_window_s"][1] + an["max_lag_s"] < t1]
    if verbose:
        print(f"batch {batch}: {len(streams)} units, {len(events)} impacts, reference: {ref_kind}")
    recs = []
    for r in rows.itertuples():
        if r.unit not in streams:
            continue
        for d in measure_unit(ref, streams[r.unit], events, r, an):
            recs.append({**{k: getattr(r, k) for k in ("row_id", "batch", "unit", "firmware",
                         "sample_rate_sps", "block_s", "gain_db", "filter_phase", "orientation_deg",
                         "role", "design", "gain_ch1", "gain_ch2", "gain_ch3", "per_channel_gain",
                         *hc.FACTOR_DEFAULTS)}, **d, "reference": ref_kind})
    meas = pd.DataFrame(recs)
    out = cfg["data_dir"] / "results" / f"batch_{batch:02d}"
    out.mkdir(parents=True, exist_ok=True)
    meas.to_csv(out / "measurements.csv", index=False)
    return meas
