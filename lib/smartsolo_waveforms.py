"""
smartsolo_waveforms
===================

Index SmartSolo MiniSEED / SEG-Y files on disk, cut out the time windows of
selected deployments with ObsPy, set proper SEED codes from a mapping table,
and write MiniSEED + StationXML.

Typical workflow
----------------
>>> import smartsolo_locate as loc, smartsolo_waveforms as wf
>>> deps  = loc.build_deployments("data/nodes")
>>> sel   = loc.select_deployments(deps, point=(-71.549, 11.117), radius_km=1,
...                                start="2024-12-26T00:00", end="2024-12-26T01:00")
>>> index = wf.index_waveforms("data/seismic_traces")
>>> st    = wf.extract_waveforms(sel, index, mapping="data/station_mapping.csv",
...                              out_dir="output/mseed")
>>> inv   = wf.build_inventory(sel, mapping="data/station_mapping.csv")
>>> inv.write("output/stations.xml", format="STATIONXML")

or all in one go with :func:`extract_region`.

Identifying the node of a file
------------------------------
SmartSolo exports usually keep the 9-digit serial in the file name and/or in a
folder named after the serial, and the MiniSEED station code is often empty or
a truncated serial. :func:`serial_from_path` looks for a 9-digit number in the
file name, then in the parent folders, then in the header station code (and
the mapping table, if the header already has the mapped station code). Pass
your own ``serial_from=callable(path, trace) -> str`` if your layout differs.

Mapping table (CSV)
-------------------
Columns: ``serial, network, station`` (required) and optionally ``location``,
``channel_prefix`` (band+instrument code, e.g. ``"DP"``), ``start``, ``end``
(UTC, so one serial can map to different stations over time)::

    serial,network,station,location,channel_prefix,start,end
    453021267,XX,GL01,,,
    453022522,XX,GL02,,,

If ``channel_prefix`` is empty it is derived from the sample rate following
SEED conventions for a geophone (instrument code ``P``): 1000-5000 Hz -> ``G``,
250-1000 Hz -> ``D``, 80-250 Hz -> ``E``, 10-80 Hz -> ``S`` (e.g. ``EPZ`` at
100 Hz, ``DPZ`` at 250/500 Hz).
"""

from __future__ import annotations

import os
import re
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

__all__ = [
    "WAVEFORM_EXTENSIONS",
    "serial_from_path",
    "component_from",
    "index_waveforms",
    "load_mapping",
    "seed_codes",
    "extract_waveforms",
    "build_inventory",
    "extract_region",
]

WAVEFORM_EXTENSIONS = {
    ".miniseed": "MSEED", ".mseed": "MSEED", ".msd": "MSEED", ".seed": "MSEED",
    ".sgy": "SEGY", ".segy": "SEGY",
    ".dld": "DLD",          # raw SmartSolo files, read with smartsolo_dld
}

# Z/N/E kept; raw DLD axes X (north-south) -> N, Y (east-west) -> E (SmartSolo manual)
DEFAULT_COMPONENT_MAP = {"Z": "Z", "N": "N", "E": "E", "X": "N", "Y": "E"}
_SERIAL_RE = re.compile(r"(?<!\d)(\d{9})(?!\d)")
_COMP_RE = re.compile(r"(?:^|[._\-])([ZNEXY123])(?=[._\-]|$)", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _utc(t):
    from obspy import UTCDateTime

    if t is None or (isinstance(t, float) and np.isnan(t)) or t is pd.NaT:
        return None
    if isinstance(t, UTCDateTime):
        return t
    t = pd.Timestamp(t)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    return UTCDateTime(t.tz_convert("UTC").to_pydatetime())


def _pd(t):
    return pd.Timestamp(t.datetime, tz="UTC") if t is not None else pd.NaT


def serial_from_path(path, trace=None, mapping: pd.DataFrame | None = None) -> str | None:
    """Best guess of the node serial for a waveform file (see module doc)."""
    p = Path(path)
    m = _SERIAL_RE.search(p.stem)
    if m:
        return m.group(1)
    for parent in p.parents:
        m = _SERIAL_RE.search(parent.name)
        if m:
            return m.group(1)
    if trace is not None:
        sta = (trace.stats.station or "").strip()
        if _SERIAL_RE.fullmatch(sta):
            return sta
        if mapping is not None and sta:
            hit = mapping[(mapping["station"] == sta)
                          & ((mapping["network"] == trace.stats.network) | (trace.stats.network == ""))]
            if len(hit["serial"].unique()) == 1:
                return str(hit["serial"].iloc[0])
    return None


def component_from(path, trace) -> str:
    """Component letter from the channel code, else from the file name, else '?'."""
    cha = (trace.stats.channel or "").strip()
    if cha:
        return cha[-1].upper()
    m = None
    for m in _COMP_RE.finditer(Path(path).name):
        pass  # take the last match (closest to the extension)
    return m.group(1).upper() if m else "?"


# --------------------------------------------------------------------------- #
# Index
# --------------------------------------------------------------------------- #
def index_waveforms(root, serial_from=None, component_func=None, mapping=None, extensions=None,
                    cache: str | os.PathLike | None = None, refresh: bool = False,
                    tag_marks: str | None = None, verbose: bool = False) -> pd.DataFrame:
    """
    Scan a folder tree (or list of folders/files) for MiniSEED, SEG-Y and raw
    SmartSolo DLD files and return one row per trace id per file:

    ``path, format, serial, component, network, station, location, channel,
    starttime, endtime, sampling_rate, npts``

    Only headers are read. With ``cache="index.csv"`` the index is saved and
    re-used next time (only new/changed files are scanned) - recommended for
    a full hard drive.

    ``serial_from(path, trace) -> str`` and ``component_func(path, trace) -> str``
    override how the node serial and component letter are found. DLD files
    carry the serial and component (X/Y/Z) in their header; ``tag_marks`` is
    the DLD timing convention (see ``smartsolo_dld``).
    """
    from obspy import read

    comp_of = component_func or component_from

    exts = {k.lower(): v for k, v in (extensions or WAVEFORM_EXTENSIONS).items()}
    mapping = load_mapping(mapping) if mapping is not None else None
    roots = [root] if isinstance(root, (str, os.PathLike)) else list(root)

    files = []
    for r in roots:
        r = Path(r)
        if r.is_file():
            files.append(r)
        else:
            files += [p for p in r.rglob("*") if p.is_file() and p.suffix.lower() in exts]
    files = sorted(set(files))

    old = None
    if cache is not None and Path(cache).exists() and not refresh:
        old = pd.read_csv(cache, dtype={"serial": str, "network": str, "station": str,
                                        "location": str, "channel": str},
                          keep_default_na=False, na_values={"sampling_rate": [""]})
        old["starttime"] = pd.to_datetime(old["starttime"], utc=True)
        old["endtime"] = pd.to_datetime(old["endtime"], utc=True)

    rows = []
    known = {}
    if old is not None:
        for path, g in old.groupby("path"):
            known[path] = g
    for f in files:
        key = str(f)
        stat = f.stat()
        if key in known:
            g = known[key]
            if (g["size"].iloc[0] == stat.st_size) and (g["mtime"].iloc[0] == int(stat.st_mtime)):
                rows += g.to_dict("records")
                continue
        fmt = exts[f.suffix.lower()]
        if fmt == "DLD":
            import smartsolo_dld as dld
            try:
                segs = dld.scan_dld(f, tag_marks=tag_marks)
            except Exception as exc:  # noqa: BLE001
                warnings.warn(f"could not read {f}: {exc}")
                continue
            for g in segs:
                rows.append(dict(path=key, format="DLD", serial=g["serial"],
                                 component=g["component"], network="", station="",
                                 location="", channel=g["component"],
                                 starttime=g["starttime"], endtime=g["endtime"],
                                 sampling_rate=g["sampling_rate"], npts=g["npts"],
                                 size=stat.st_size, mtime=int(stat.st_mtime)))
            if verbose:
                print("indexed", f)
            continue
        try:
            st = read(str(f), format=fmt, headonly=True)
        except Exception as exc:  # noqa: BLE001 - keep going on a bad file
            warnings.warn(f"could not read {f}: {exc}")
            continue
        if verbose:
            print("indexed", f)
        groups = {}
        for tr in st:
            comp = comp_of(f, tr)
            k = (tr.stats.network, tr.stats.station, tr.stats.location, tr.stats.channel, comp)
            t0, t1 = tr.stats.starttime, tr.stats.endtime
            if k in groups:
                g = groups[k]
                g["starttime"], g["endtime"] = min(g["starttime"], t0), max(g["endtime"], t1)
                g["npts"] += tr.stats.npts
            else:
                ser = serial_from(f, tr) if serial_from else serial_from_path(f, tr, mapping)
                groups[k] = dict(path=key, format=fmt, serial=ser, component=comp,
                                 network=tr.stats.network, station=tr.stats.station,
                                 location=tr.stats.location, channel=tr.stats.channel,
                                 starttime=t0, endtime=t1,
                                 sampling_rate=tr.stats.sampling_rate, npts=tr.stats.npts,
                                 size=stat.st_size, mtime=int(stat.st_mtime))
        for g in groups.values():
            g["starttime"], g["endtime"] = _pd(g["starttime"]), _pd(g["endtime"])
            rows.append(g)

    idx = pd.DataFrame(rows, columns=["path", "format", "serial", "component", "network",
                                      "station", "location", "channel", "starttime",
                                      "endtime", "sampling_rate", "npts", "size", "mtime"])
    idx["starttime"] = pd.to_datetime(idx["starttime"], utc=True)
    idx["endtime"] = pd.to_datetime(idx["endtime"], utc=True)
    idx["serial"] = idx["serial"].astype("string")
    missing = idx["serial"].isna().sum()
    if missing:
        warnings.warn(f"{missing} index rows without a serial number "
                      "(give serial_from=... or a mapping table)")
    idx = idx.sort_values(["serial", "component", "starttime"]).reset_index(drop=True)
    if cache is not None:
        Path(cache).parent.mkdir(parents=True, exist_ok=True)
        idx.to_csv(cache, index=False)
    return idx


# --------------------------------------------------------------------------- #
# Codes
# --------------------------------------------------------------------------- #
def load_mapping(mapping) -> pd.DataFrame:
    """Read / normalise the serial -> network/station/location mapping table."""
    if isinstance(mapping, pd.DataFrame):
        m = mapping.copy()
    else:
        m = pd.read_csv(mapping, dtype=str, keep_default_na=False, comment="#")
    m.columns = [c.strip().lower() for c in m.columns]
    for c in ["serial", "network", "station"]:
        if c not in m:
            raise ValueError(f"mapping table needs a '{c}' column")
    for c in ["location", "channel_prefix", "start", "end"]:
        if c not in m:
            m[c] = ""
    for c in ["serial", "network", "station", "location", "channel_prefix"]:
        m[c] = m[c].fillna("").astype(str).str.strip()
    m["start"] = pd.to_datetime(m["start"].replace("", None), utc=True)
    m["end"] = pd.to_datetime(m["end"].replace("", None), utc=True)
    return m


def band_code(sampling_rate: float) -> str:
    """SEED band code for a short-period sensor (corner > 10 s)."""
    sr = float(sampling_rate)
    if sr >= 1000:
        return "G"
    if sr >= 250:
        return "D"
    if sr >= 80:
        return "E"
    if sr >= 10:
        return "S"
    return "M"


def seed_codes(serial, time=None, mapping=None, sampling_rate=100.0, component="Z",
               component_map=None, default_network="XX"):
    """
    ``(network, station, location, channel)`` for a node serial at ``time``.

    Without a mapping row the station is the last 5 digits of the serial and
    the network ``default_network`` (a warning is issued).
    """
    component_map = component_map or DEFAULT_COMPONENT_MAP
    comp = component_map.get(component, component)
    serial = str(serial)
    row = None
    if mapping is not None:
        m = mapping[mapping["serial"] == serial]
        if time is not None and len(m) > 1:
            t = pd.Timestamp(time)
            ok = (m["start"].isna() | (m["start"] <= t)) & (m["end"].isna() | (m["end"] >= t))
            m = m[ok]
        if len(m):
            row = m.iloc[0]
    if row is None:
        warnings.warn(f"serial {serial} not in mapping table - using {default_network}.{serial[-5:]}")
        net, sta, loc, prefix = default_network, serial[-5:], "", ""
    else:
        net, sta, loc, prefix = row["network"], row["station"], row["location"], row["channel_prefix"]
    if not prefix:
        prefix = band_code(sampling_rate) + "P"
    return net, sta, loc, f"{prefix}{comp}"


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #
def _read_window(path, fmt, t0, t1, tag_marks=None):
    from obspy import read

    if fmt == "DLD":
        import smartsolo_dld as dld
        st = dld.read_dld(path, starttime=t0, endtime=t1, tag_marks=tag_marks)
        for tr in st:
            tr.stats.pop("dld", None)
            tr.stats.pop("coordinates", None)
            tr.stats.gain_removed = False
    elif fmt == "MSEED":
        st = read(path, format="MSEED", starttime=t0, endtime=t1)
    else:
        st = read(path, format=fmt)
        for tr in st:  # drop SEG-Y specific headers -> plain ObsPy traces
            tr.stats.pop("segy", None)
            tr.stats.pop("su", None)
            tr.stats.pop("_format", None)
    st.trim(t0, t1, nearest_sample=False)
    return st


def extract_waveforms(selection, index: pd.DataFrame, mapping=None, start=None, end=None,
                      components=None, component_map=None, out_dir=None, chunk=None,
                      merge_method: int = 1, fill_value=None, return_stream: bool = True,
                      default_network: str = "XX", encoding=None, component_func=None,
                      invert_polarity="auto", out_format: str = "MSEED",
                      tag_marks: str | None = None, verbose: bool = False):
    """
    Cut waveforms for the selected deployments.

    Parameters
    ----------
    selection : GeoDataFrame from ``smartsolo_locate.select_deployments``
        Uses ``serial``, ``sel_start``/``sel_end`` (or ``start``/``end``),
        ``latitude``, ``longitude``, ``elevation``, ``deployment_id``.
    index : DataFrame from :func:`index_waveforms`
    mapping : CSV path or DataFrame, optional
        Serial -> SEED codes (see module doc).
    start, end : optional
        Further restrict the window (UTC). The window used for each deployment
        is the overlap of [start, end) with [sel_start, sel_end) - the end
        time is exclusive, so consecutive windows/chunks don't share samples.
    components : iterable, optional
        e.g. ``["Z"]`` for vertical only.
    component_map : dict, optional
        Map component letters found in the files to Z/N/E, e.g.
        ``{"X": "E", "Y": "N"}``. Default keeps Z/N/E.
    out_dir : path, optional
        Write files to ``out_dir/NET/STA/NET.STA.LOC.CHA__start__end.mseed``
        (or ``.sgy``).
    out_format : "MSEED" (default) or "SEGY"
        SEG-Y traces are limited to 32767 samples, so long windows are split
        into several traces (whole seconds) inside each file.
    tag_marks : DLD timing convention (see ``smartsolo_dld``).
    chunk : str, optional
        With ``out_dir``: split output files, e.g. ``"1D"`` or ``"1h"``.
    merge_method, fill_value :
        Passed to ``Stream.merge`` (gaps stay masked if ``fill_value`` is None;
        masked traces are split before writing).
    invert_polarity : "auto" (default), True or False
        SmartSolo IGU-16HR data have **negative polarity on all channels**
        relative to the FDSN convention (positive = up / north / east):
        the manual defines positive = case moving down / south / west, and
        AusPass confirmed by controlled tests that freshly exported data need
        ``x -1`` on every channel. ``"auto"`` inverts unless the deployment
        is a BD3C-5 broadband node (``device_type`` contains "BD3C", or the
        serial starts with 5900 when the type is unknown). The inversion is
        recorded in ``stats.processing`` and ``stats.polarity_inverted``.
        Use ``build_inventory(..., polarity_inverted=...)`` with the same choice.
    component_func : callable(path, trace) -> str, optional
        Same function as given to :func:`index_waveforms`, if any.
    return_stream : bool
        Set False for very large extractions written to disk, to save memory.

    Returns
    -------
    obspy.Stream (empty if ``return_stream=False``). Each trace has
    ``stats.coordinates`` (latitude, longitude, elevation),
    ``stats.serial`` and ``stats.deployment_id``.
    """
    from obspy import Stream
    from obspy.core.util import AttribDict

    mapping = load_mapping(mapping) if mapping is not None else None
    component_map = {**DEFAULT_COMPONENT_MAP, **(component_map or {})}
    comp_of = component_func or component_from
    t_start, t_end = _utc(start), _utc(end)
    out = Stream()
    written = []

    for _, dep in selection.iterrows():
        t0 = _utc(dep.get("sel_start", dep["start"]))
        t1 = _utc(dep.get("sel_end", dep["end"]))
        if t_start is not None:
            t0 = max(t0, t_start)
        if t_end is not None:
            t1 = min(t1, t_end)
        if t1 <= t0:
            continue
        rows = index[(index["serial"] == str(dep["serial"]))
                     & (index["endtime"] >= _pd(t0)) & (index["starttime"] <= _pd(t1))]
        if components is not None:
            want = {c.upper() for c in components}
            rows = rows[rows["component"].map(lambda c: component_map.get(c, c)).isin(want)]
        if rows.empty:
            if verbose:
                print(f"no waveform files for {dep['serial']} {t0} - {t1}")
            continue

        st = Stream()
        for (path, fmt), grp in rows.groupby(["path", "format"], sort=False):
            part = _read_window(path, fmt, t0, t1, tag_marks)
            comps = set(grp["component"])
            for tr in part:
                comp = comp_of(path, tr)
                if comp not in comps:
                    continue
                net, sta, loc, cha = seed_codes(dep["serial"], _pd(t0), mapping,
                                                tr.stats.sampling_rate, comp,
                                                component_map, default_network)
                tr.stats.network, tr.stats.station = net, sta
                tr.stats.location, tr.stats.channel = loc, cha
                st.append(tr)
        if not st:
            continue
        st.merge(method=merge_method, fill_value=fill_value)
        st.trim(t0, t1 - _EPS, nearest_sample=False)   # end is exclusive
        flip = invert_polarity
        if flip == "auto":
            dtype = dep.get("device_type", np.nan)
            if pd.isna(dtype) or not str(dtype):
                flip = not str(dep["serial"]).startswith("5900")    # BD3C-5 needs no flip
            else:
                flip = "BD3C" not in str(dtype)
        for tr in st:
            tr.stats.polarity_inverted = bool(flip)
            if flip:
                tr.data = -tr.data
                tr.stats.processing = list(getattr(tr.stats, "processing", [])) + [
                    "node_toolbox: polarity inverted (x -1), SmartSolo IGU-16HR -> FDSN convention"]
        for tr in st:
            tr.stats.coordinates = AttribDict(latitude=float(dep["latitude"]),
                                              longitude=float(dep["longitude"]),
                                              elevation=float(dep["elevation"]))
            tr.stats.serial = str(dep["serial"])
            tr.stats.deployment_id = dep.get("deployment_id", "")
        if verbose:
            print(f"{dep.get('deployment_id', dep['serial'])}: {len(st)} traces")

        if out_dir is not None:
            written += _write_stream(st, out_dir, t0, t1, chunk, encoding, out_format)
        if return_stream:
            out += st

    out.written_files = written  # list of paths, for convenience
    return out


def _segy_pieces(sub):
    """Split traces into <= 32767 samples, starting on whole seconds when possible."""
    from obspy import Stream

    out = Stream()
    for tr in sub:
        sr = tr.stats.sampling_rate
        n = int(32767 // sr * sr) if sr <= 32767 else 32767
        n = max(n, 1)
        for i in range(0, tr.stats.npts, n):
            piece = tr.copy()
            piece.data = tr.data[i:i + n]
            piece.stats.starttime = tr.stats.starttime + i / sr
            for k in ("coordinates", "processing", "serial", "deployment_id", "polarity_inverted",
                      "gain_removed", "dld", "mseed"):
                piece.stats.pop(k, None)
            if piece.data.dtype.kind == "i":
                piece.data = piece.data.astype(np.int32)
            else:
                piece.data = piece.data.astype(np.float32)
            out.append(piece)
    return out


def _write_stream(st, out_dir, t0, t1, chunk, encoding, out_format="MSEED"):
    import warnings as _w

    from obspy import UTCDateTime

    out_dir = Path(out_dir)
    paths = []
    edges = [t0]
    if chunk:
        step = pd.Timedelta(chunk).total_seconds()
        first = UTCDateTime(int(t0.timestamp // step) * step)
        e = first + step
        while e < t1:
            edges.append(e)
            e += step
    edges.append(t1)
    for a, b in zip(edges[:-1], edges[1:]):
        piece = st.slice(a, b - _EPS, nearest_sample=False).split()   # [a, b)
        if not piece:
            continue
        for tr_id in sorted({tr.id for tr in piece}):
            sub = piece.select(id=tr_id)
            net, sta, loc, cha = tr_id.split(".")
            d = out_dir / (net or "_") / (sta or "_")
            d.mkdir(parents=True, exist_ok=True)
            ext = "sgy" if out_format.upper() == "SEGY" else "mseed"
            fn = d / f"{tr_id}__{a.strftime('%Y%m%dT%H%M%SZ')}__{b.strftime('%Y%m%dT%H%M%SZ')}.{ext}"
            for tr in sub:
                if tr.data.dtype == np.float64:
                    tr.data = tr.data.astype(np.float32)
            if ext == "sgy":
                pieces = _segy_pieces(sub)
                enc = 2 if pieces[0].data.dtype == np.int32 else 5
                with _w.catch_warnings():
                    _w.simplefilter("ignore")
                    pieces.write(str(fn), format="SEGY", data_encoding=enc)
            else:
                kw = {"encoding": encoding} if encoding else {}
                sub.write(str(fn), format="MSEED", **kw)
            paths.append(fn)
    return paths


# --------------------------------------------------------------------------- #
# StationXML
# --------------------------------------------------------------------------- #
_EPS = 1e-6  # seconds; used to make window ends exclusive

_ORIENT = {"Z": (0.0, -90.0), "N": (0.0, 0.0), "E": (90.0, 0.0)}          # FDSN, after x -1
_ORIENT_RAW = {"Z": (0.0, 90.0), "N": (180.0, 0.0), "E": (270.0, 0.0)}    # raw SmartSolo polarity


def build_inventory(selection, mapping=None, components=("Z", "N", "E"), sampling_rate=None,
                    stream=None, default_network: str = "XX", source: str = "node_toolbox",
                    response: str | None = None, gain_db="auto",
                    polarity_inverted: bool = True):
    """
    Build an ObsPy Inventory (StationXML) for the selected deployments:
    one Station per deployment (start/end dates = deployment time span,
    coordinates = first stable fix) and one Channel per component.

    The channel codes follow :func:`seed_codes`. If ``stream`` is given, the
    channels and sample rates actually present in it are used.

    ``polarity_inverted`` must match what was done to the waveforms
    (``extract_waveforms(invert_polarity=...)``):

      * ``True`` (default): data were multiplied by -1, so the channels follow
        the FDSN convention: Z dip -90 (up positive), N azimuth 0, E azimuth 90;
      * ``False``: raw SmartSolo polarity, described in the metadata instead:
        Z dip +90 (down positive), N azimuth 180, E azimuth 270.

    ``response``:
      * ``None`` (default) - no instrument response (data stay in counts);
      * ``"auspass"`` - the AusPass/ANSIR published IGU-16HR 3C response
        (5 Hz, h 0.707, 257 019 226 counts/(m/s)); assumes the preamp gain was
        removed at export ("Remove Gain" in SoloLite);
      * ``"nominal"`` - DT-SOLO 5 Hz data-sheet values (5 Hz, h 0.70,
        80 V/m/s) + 3355.4428 counts/mV × gain;
      * ``"test"`` - the mean f0 / damping / sensitivity of the boot-time
        geophone test of that deployment (``test_*_mean`` columns), falling
        back to nominal when the test failed or was noisy.
    The test values cannot yet be assigned to Z/N/E individually because the
    log's Ch1/Ch2/Ch3 -> axis mapping is not documented.

    ``gain_db``: preamp gain to include in the response. ``"auto"`` uses the
    deployment's ``channel_1_gain`` (from the log) unless the stream says the
    gain was removed (``stats.gain_removed``); raw DLD data contain the gain,
    SoloLite exports with "Remove Gain" do not (use ``gain_db=0`` for those).
    """
    import smartsolo_node as sn
    from obspy import UTCDateTime
    from obspy.core.inventory import Channel, Equipment, Inventory, Network, Site, Station

    mapping = load_mapping(mapping) if mapping is not None else None
    nets: dict[str, Network] = {}

    for _, dep in selection.iterrows():
        t0, t1 = _utc(dep["start"]), _utc(dep["end"])
        sr = sampling_rate or dep.get("sample_rate_hz")
        if sr is None or pd.isna(sr):
            sr = 1e5 / dep["sample_rate"] if pd.notna(dep.get("sample_rate", np.nan)) else 100.0
        if stream is not None:
            trs = [tr for tr in stream if getattr(tr.stats, "serial", None) == str(dep["serial"])
                   and getattr(tr.stats, "deployment_id", None) == dep.get("deployment_id")]
            chans = sorted({(tr.stats.network, tr.stats.station, tr.stats.location,
                             tr.stats.channel, tr.stats.sampling_rate) for tr in trs})
        else:
            chans = []
            for comp in components:
                n, s, l, c = seed_codes(dep["serial"], _pd(t0), mapping, sr, comp,
                                        None, default_network)
                chans.append((n, s, l, c, float(sr)))
        if not chans:
            continue
        net_code, sta_code = chans[0][0], chans[0][1]
        sensor = Equipment(type="Geophone", description=str(dep.get("device_type", "SmartSolo")),
                           manufacturer="DTCC SmartSolo", serial_number=str(dep["serial"]))
        elev = float(dep["elevation"]) if pd.notna(dep.get("elevation", np.nan)) else 0.0
        sta = Station(code=sta_code, latitude=float(dep["latitude"]),
                      longitude=float(dep["longitude"]), elevation=elev,
                      start_date=t0, end_date=t1, creation_date=t0,
                      site=Site(name=f"SmartSolo {dep['serial']} ({dep.get('deployment_id', '')})"),
                      description=(f"first stable GPS fix {dep.get('fix_time')}; "
                                   f"drift {dep.get('drift_m', np.nan):.1f} m"))
        resp = None
        g = gain_db
        if g == "auto":
            removed = False
            if stream is not None:
                trs_ = [tr for tr in stream if getattr(tr.stats, "serial", None) == str(dep["serial"])]
                removed = any(getattr(tr.stats, "gain_removed", False) for tr in trs_)
            g = 0.0 if removed else float(dep.get("channel_1_gain", 0) or 0)
            if pd.isna(g):
                g = 0.0
        if response == "auspass":
            resp = sn.auspass_response(gain_db=g)
        elif response is not None:
            pars = dict(f0=sn.NOMINAL_5HZ["f0"], damping=sn.NOMINAL_5HZ["damping"],
                        sensitivity=sn.NOMINAL_5HZ["sensitivity"])
            if response == "test" and bool(dep.get("geophone_ok", False)) \
                    and not bool(dep.get("geophone_test_noisy", True)):
                pars = dict(f0=dep["test_resonate_freq_hz_mean"], damping=dep["test_damping_mean"],
                            sensitivity=dep["test_sensitivity_mean"])
            resp = sn.geophone_response(pars["f0"], pars["damping"], pars["sensitivity"], g)
        orient = _ORIENT if polarity_inverted else _ORIENT_RAW
        for n, s, l, c, rate in chans:
            az, dip = orient.get(c[-1], (0.0, 0.0))
            sta.channels.append(Channel(
                code=c, location_code=l, latitude=float(dep["latitude"]),
                longitude=float(dep["longitude"]), elevation=elev,
                depth=0.0, azimuth=az, dip=dip, sample_rate=float(rate),
                start_date=t0, end_date=t1, sensor=sensor, response=resp))
        nets.setdefault(net_code, Network(code=net_code, stations=[])).stations.append(sta)

    return Inventory(networks=list(nets.values()), source=source,
                     created=UTCDateTime())


# --------------------------------------------------------------------------- #
# One-call pipeline
# --------------------------------------------------------------------------- #
def extract_region(log_root, waveform_root, mapping=None, point=None, radius_km=None,
                   polygon=None, start=None, end=None, out_dir="output", components=None,
                   chunk=None, deployments_cache=None, index_cache=None,
                   return_stream=True, invert_polarity="auto", response="auspass",
                   out_format="MSEED", use_dld_positions=True, **select_kw):
    """
    Logs (and/or raw DLD headers) -> deployments -> selection -> waveforms,
    all in one call. ``waveform_root`` may hold MiniSEED, SEG-Y and/or raw
    DLD files; with ``use_dld_positions`` (default) nodes without a log are
    located from the GPS positions in their DLD time tags, and log
    deployments are widened to cover the recorded data. ``log_root=None``
    works from DLD files alone.

    Writes to ``out_dir``: ``stations.csv``, ``stations.gpkg``,
    ``stations.xml`` (StationXML) and MiniSEED files under ``out_dir/mseed``.

    Returns ``(selection, stream, inventory)``.
    """
    import smartsolo_locate as loc

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    deps = loc.build_deployments(log_root, cache=deployments_cache) if log_root is not None else None
    if use_dld_positions:
        dld_deps = loc.build_deployments_from_dld(waveform_root)
        if len(dld_deps):
            deps = dld_deps if deps is None or deps.empty else loc.combine_deployments(deps, dld_deps)
    if deps is None:
        raise ValueError("no deployments: give log_root and/or DLD files in waveform_root")
    sel = loc.select_deployments(deps, point=point, radius_km=radius_km, polygon=polygon,
                                 start=start, end=end, **select_kw)
    if sel.empty:
        warnings.warn("no deployments match the selection")
    loc.export_stations(sel, out_dir / "stations.csv")
    loc.export_stations(sel, out_dir / "stations.gpkg")
    index = index_waveforms(waveform_root, mapping=mapping, cache=index_cache)
    st = extract_waveforms(sel, index, mapping=mapping, components=components,
                           out_dir=out_dir / ("segy" if out_format.upper() == "SEGY" else "mseed"),
                           chunk=chunk, return_stream=return_stream,
                           invert_polarity=invert_polarity, out_format=out_format)
    flipped = bool(st[0].stats.polarity_inverted) if len(st) else invert_polarity is not False
    inv = build_inventory(sel, mapping=mapping, stream=st if return_stream and len(st) else None,
                          response=response, polarity_inverted=flipped)
    inv.write(str(out_dir / "stations.xml"), format="STATIONXML")
    return sel, st, inv


def convert_dld(dld_root, out_dir="output", mapping=None, out_format="MSEED", chunk="1D",
                log_root=None, start=None, end=None, invert_polarity="auto", components=None,
                response="auspass", tag_marks=None):
    """
    Convert raw SmartSolo DLD files straight to MiniSEED or SEG-Y (+ station
    table and StationXML), without SoloLite.

    Every recording found under ``dld_root`` is converted (optionally only
    ``[start, end)``); positions come from the logs in ``log_root`` if given,
    else from the GPS positions in the DLD time tags. Returns
    ``(deployments, inventory, written_files)``; data are not kept in memory.
    """
    import smartsolo_locate as loc

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    deps = loc.build_deployments_from_dld(dld_root, tag_marks=tag_marks)
    if log_root is not None:
        deps = loc.combine_deployments(loc.build_deployments(log_root), deps)
    sel = loc.select_deployments(deps, start=start, end=end)
    index = index_waveforms(dld_root, mapping=mapping, tag_marks=tag_marks)
    has_data = [bool(((index["serial"] == str(d["serial"])) & (index["endtime"] >= d["sel_start"])
                      & (index["starttime"] <= d["sel_end"])).any()) for _, d in sel.iterrows()]
    sel = sel[has_data]          # only deployments with DLD data
    st = extract_waveforms(sel, index, mapping=mapping, components=components,
                           out_dir=out_dir / out_format.lower(), chunk=chunk,
                           return_stream=False, invert_polarity=invert_polarity,
                           out_format=out_format, tag_marks=tag_marks)
    flipped = invert_polarity is not False
    inv = build_inventory(sel, mapping=mapping, response=response, polarity_inverted=flipped)
    inv.write(str(out_dir / "stations.xml"), format="STATIONXML")
    loc.export_stations(sel, out_dir / "stations.csv")
    return sel, inv, st.written_files
