"""
smartsolo_dld
=============

Read SmartSolo raw ``.DLD`` data files (``seis000X.DLD``, ``seis000Y.DLD``,
``seis000Z.DLD`` ...) directly into ObsPy, without exporting with SoloLite.

The format is undocumented. This reader is an independent implementation,
written from scratch from the description in docs/DLD_FORMAT.md. That
description comes only from inspecting data files recorded by our own
IGU-16HR 3C nodes (firmware V1.0.5, V1.0.8, V1.1.2; 250, 500 and 1000
samples/s) and from public information. No vendor software was decompiled
or disassembled and no vendor code is included (docs/CLEAN_ROOM.md).
Files are only ever opened read-only. What is known:

File layout
-----------
One file per component (X, Y, Z) and recording segment::

    0x000  512-byte file header (see read_dld_header)
    0x200  block 0: 1000 samples, 24-bit signed little-endian (3000 bytes)
           72-byte time tag 0
           block 1: 1000 samples
           72-byte time tag 1
           ...
           block n-1, tag n-1  (= end of file)

Time tag (72 bytes, repeated after every 1000 samples)::

    +0   int32   flag (always 0 so far)
    +4   int64   tick, ms; +1000 per tag at 1000 sps, +2000 at 500, +4000 at 250
    +12  char[11] "HHMMSS.00"  UTC time as text (whole seconds)
    +23  char[9]  "YYYYMMDD"   UTC date
    +32  float64 latitude  (deg)
    +40  int32   small signed value, mostly 0/±1 (probably clock phase error)
    +44  int32   time since the last GPS synchronisation, 10 ms units
    +48  float64 longitude (deg)
    +56  char[16] GPS time of week of the next GPS pulse, ms, as text

* The sample rate is 1000 samples / (tick difference between tags).
* **Tag time** (``time_source="tow"``, default): GPS week + TOW -
  (GPS-UTC) (+ ``DEFAULTS["tow_offset_s"]``, 0). The text time (``"label"``)
  is 1 s late until the receiver has learned the leap seconds, then jumps
  back 2 s and is 1 s early; TOW is continuous and consistent between nodes
  (see docs/FINDINGS.md).
* **Block convention (to verify!)**: by default a tag is taken as the time
  of the *first sample of the block before it* (``tag_marks="block_start"``).
  The alternative ``"block_end"`` shifts every sample one block (1-4 s)
  earlier. Compare one file with a SoloLite MiniSEED export
  (``compare_with_export``) to settle this.
* The header position/altitude are written when the file is closed (after
  any move); use the tag positions for the installed position.
* Data are raw ADC counts **including the preamp gain** (0-36 dB, from the
  log ``Channel N Gain``) and with the raw SmartSolo polarity (see
  ``smartsolo_waveforms.extract_waveforms(invert_polarity=...)``).
* Component letters: X = north-south, Y = east-west, Z = vertical (SmartSolo
  manual). They are kept as the channel code here and mapped to N/E/Z in
  ``smartsolo_waveforms``.

Main functions
--------------
read_dld_header(path)        dict with serial, firmware, start/end, position ...
read_dld_tags(path)          DataFrame, one row per time tag
read_dld(path, ...)          obspy Stream (one trace per continuous segment)
scan_dld(path)               index rows without reading samples (for index_waveforms)
read_dld_node(files)         X, Y, Z of one recording into one Stream
compare_with_export(dld_st, exported_st)   timing/amplitude check vs SoloLite export

Tobias Stål (UTAS) 2023-2026
"""

from __future__ import annotations

import re
import struct
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

__all__ = [
    "DLD_MAGIC",
    "read_dld_header",
    "read_dld_tags",
    "read_dld",
    "scan_dld",
    "read_dld_node",
    "is_dld",
    "compare_with_export",
    "time_calibration",
    "gps_utc_offset",
]

DLD_MAGIC = b"DTCCSZ-TEC-FTS"
LFS_POINTER = b"version https://git-lfs"

# Package-wide defaults (can be changed: smartsolo_dld.DEFAULTS["time_source"] = "label")
# Package-wide defaults. Change them in code (smartsolo_dld.DEFAULTS["tow_offset_s"] = 0.0) or,
# better, in a settings file read with smartsolo_config.load_config() / apply_config().
#   time_source      "tow": GPS week + time of week stored in each tag (binary tick, default);
#                    "label": the HHMMSS/YYYYMMDD text (1 s late / early, jumps 2 s - diagnostics only)
#   tow_offset_s     added to the GPS time of week. 0 since Oct 2026: a comparison with co-located
#                    permanent stations (R. Pickle, ANU) showed data 1 s early with the earlier -1 s
#   tag_marks        "block_start": tag k = time of the first sample of the block before it
#   include_trailing read samples after the last tag (file cut short at power-off)
#   leap_seconds     None = GPS-UTC from the built-in table; an int overrides it (future leap seconds)
DEFAULTS = {"time_source": "tow", "tag_marks": "block_start", "tow_offset_s": 0.0,
            "include_trailing": True, "leap_seconds": None}

GPS_EPOCH = pd.Timestamp("1980-01-06", tz="UTC")
# GPS - UTC leap seconds (date from which the value applies)
_LEAPS = [("1999-01-01", 13), ("2006-01-01", 14), ("2009-01-01", 15), ("2012-07-01", 16),
          ("2015-07-01", 17), ("2017-01-01", 18)]
_LEAP_T = np.array([(pd.Timestamp(d, tz="UTC") - GPS_EPOCH).total_seconds() for d, _ in _LEAPS])
_LEAP_V = np.array([v for _, v in _LEAPS])


def gps_utc_offset(t) -> int:
    """GPS - UTC in seconds at time ``t`` (18 since 2017-01-01; ``DEFAULTS["leap_seconds"]``
    overrides the table for times after its last entry)."""
    t = pd.Timestamp(t)
    t = t.tz_localize("UTC") if t.tzinfo is None else t
    off = 13
    for d, v in _LEAPS:
        if t >= pd.Timestamp(d, tz="UTC"):
            off = v
    if DEFAULTS.get("leap_seconds") is not None and t >= pd.Timestamp(_LEAPS[-1][0], tz="UTC"):
        off = int(DEFAULTS["leap_seconds"])
    return off


def _leap_from_gps_seconds(gps_s: np.ndarray) -> np.ndarray:
    """Vectorised GPS - UTC for times given as seconds since the GPS epoch."""
    i = np.searchsorted(_LEAP_T + _LEAP_V, gps_s, side="right") - 1
    out = np.where(i >= 0, _LEAP_V[np.clip(i, 0, None)], 13)
    if DEFAULTS.get("leap_seconds") is not None:
        out = np.where(i == len(_LEAPS) - 1, int(DEFAULTS["leap_seconds"]), out)
    return out


def time_calibration(time_source=None, tag_marks=None) -> str:
    """Short string identifying the timing settings, stored with every output
    (e.g. ``"tow+0/block_start"``) so data converted with other settings can be found."""
    src = _opt("time_source", time_source)
    off = DEFAULTS["tow_offset_s"] if src == "tow" else 0
    return f"{src}{off:+g}/{_opt('tag_marks', tag_marks)}"


def _opt(name, value):
    return DEFAULTS[name] if value is None else value
HEADER_SIZE = 512
TAG_SIZE = 72
_TAG_RE = re.compile(rb"\d{6}\.\d\d\x00\x00\d{8}\x00")
_NAME_RE = re.compile(r"seis(\d+)([A-Za-z])", re.I)


# --------------------------------------------------------------------------- #
# Header and tags
# --------------------------------------------------------------------------- #
def _cstr(b: bytes) -> str:
    return b.split(b"\x00")[0].decode("ascii", errors="replace").strip()


def _utc(date: str, time: str):
    try:
        return pd.Timestamp(f"{date[:4]}-{date[4:6]}-{date[6:8]}T{time[:2]}:{time[2:4]}:{time[4:6]}",
                            tz="UTC") + pd.Timedelta(float("0" + time[6:]) if len(time) > 6 else 0, "s")
    except (ValueError, IndexError):
        return pd.NaT


def is_dld(path) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(len(DLD_MAGIC)) == DLD_MAGIC
    except OSError:
        return False


def read_dld_header(path) -> dict:
    """
    Decode the 512-byte DLD file header.

    Keys: ``magic, version, serial, hardware_id, project_name, bootloader,
    firmware, start, end`` (UTC Timestamps from the text fields),
    ``start_tick_ms, end_tick_ms, duration_s, latitude, longitude,
    altitude, script_name, component, file_index`` and ``unknown_*`` for
    fields whose meaning is not known yet.
    """
    path = Path(path)
    with open(path, "rb") as fh:
        h = fh.read(HEADER_SIZE)
    if not h.startswith(DLD_MAGIC):
        if h.startswith(LFS_POINTER):
            raise ValueError(f"{path} is a Git LFS pointer, not the DLD data: install Git LFS and run "
                             "'git lfs install && git lfs pull' in the repository")
        raise ValueError(f"{path} is not a SmartSolo DLD file")
    t0, t1 = struct.unpack_from("<qq", h, 0xD0)
    lon, lat = struct.unpack_from("<dd", h, 0x190)
    m = _NAME_RE.search(path.stem)
    out = {
        "magic": _cstr(h[0x00:0x10]),
        "version": struct.unpack_from("<i", h, 0x10)[0],
        "serial": _cstr(h[0x20:0x30]),
        "hardware_id": _cstr(h[0x30:0x40]),
        "project_name": _cstr(h[0x40:0x60]),
        "bootloader": _cstr(h[0x70:0x80]),
        "firmware": _cstr(h[0x80:0x90]),
        "start": _utc(_cstr(h[0xA0:0xB0]), _cstr(h[0x90:0xA0])),
        "end": _utc(_cstr(h[0xC0:0xD0]), _cstr(h[0xB0:0xC0])),
        "start_tick_ms": t0,
        "end_tick_ms": t1,
        "duration_s": (t1 - t0) / 1000.0,
        "latitude": lat,
        "longitude": lon,
        "altitude": struct.unpack_from("<f", h, 0x11C)[0],
        "script_name": _cstr(h[0x140:0x190]),
        "component": m.group(2).upper() if m else "",
        "file_index": int(m.group(1)) if m else None,
        "unknown_0x060": struct.unpack_from("<2i", h, 0x60),
        "leap_seconds": struct.unpack_from("<i", h, 0x104)[0],   # 0 = receiver did not know them yet
        "unknown_0x100": struct.unpack_from("<7i", h, 0x100),
        "unknown_0x120": struct.unpack_from("<4i", h, 0x120),
        "file_size": path.stat().st_size,
    }
    return out


def _layout(buf: bytes):
    """Return (block_bytes, period, n_tags) by locating the tags."""
    m = _TAG_RE.search(buf, HEADER_SIZE)
    if m is None:
        raise ValueError("no time tag found")
    first = m.start() - 12
    block = first - HEADER_SIZE
    period = block + TAG_SIZE
    n = (len(buf) - HEADER_SIZE) // period
    # verify / trim to the tags that are really there
    while n > 0 and not _TAG_RE.match(buf, HEADER_SIZE + n * period - TAG_SIZE + 12):
        n -= 1
    return block, period, n


def _open(path):
    """Read-only memory map of a file (no copy of the whole file into memory)."""
    import mmap
    with open(path, "rb") as fh:
        if Path(path).stat().st_size == 0:
            return b""
        return mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)


def _close(buf):
    try:
        buf.close()
    except (AttributeError, BufferError):
        pass


def _digits(a: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Integer value and validity of fixed-width ASCII digit columns."""
    d = a.astype(np.int64) - 48
    ok = ((d >= 0) & (d <= 9)).all(axis=1)
    val = np.zeros(len(a), dtype=np.int64)
    for j in range(a.shape[1]):
        val = val * 10 + np.clip(d[:, j], 0, 9)
    return val, ok


def _tags_from_buffer(buf, block, period, n, time_source=None) -> pd.DataFrame:
    """Decode all tags at once (vectorised). Columns:

    ``block, offset, flag, tick_ms, gps_week, tow_ms`` (binary GPS week and
    time of week from the tick: ``tick = week << 32 | tow_ms``),
    ``time_label`` (text), ``gps_tow_ms`` (text TOW, = ``tow_ms`` in all files
    seen), ``latitude, longitude, phase_error, counter, sync_age_s``
    (time since the last GPS synchronisation), ``time_tow``, ``time``
    (= ``time_tow`` or ``time_label``) and ``label_minus_tow_s``.
    """
    if n == 0:
        raise ValueError("no time tags")
    raw = np.frombuffer(buf, np.uint8, count=n * period, offset=HEADER_SIZE).reshape(n, period)
    a = np.array(raw[:, block:block + TAG_SIZE])                 # n x 72 copy (small)
    del raw
    col = lambda o, dt_: a[:, o:o + np.dtype(dt_).itemsize].copy().view(dt_)[:, 0]
    flag, tick = col(0, "<i4"), col(4, "<i8")
    lat, lon = col(32, "<f8"), col(48, "<f8")
    i1, i2 = col(40, "<i4"), col(44, "<i4")
    # text label HHMMSS.ss + YYYYMMDD
    hms, ok_t = _digits(a[:, 12:18])
    frac, ok_f = _digits(a[:, 19:21])
    ymd, ok_d = _digits(a[:, 23:31])
    lab = pd.to_datetime(pd.DataFrame({"year": ymd // 10000, "month": ymd // 100 % 100, "day": ymd % 100,
                                       "hour": hms // 10000, "minute": hms // 100 % 100,
                                       "second": hms % 100}), errors="coerce", utc=True)
    lab = lab + pd.to_timedelta(np.where(ok_f, frac, 0) * 10, unit="ms")
    lab[~(ok_t & ok_d)] = pd.NaT
    # text TOW: digits up to the first NUL
    t16 = a[:, 56:72].astype(np.int64) - 48
    isd = (t16 >= 0) & (t16 <= 9)
    ndig = np.where(isd.all(axis=1), 16, np.argmin(isd, axis=1))
    tow_txt = np.zeros(n, dtype=np.int64)
    for j in range(16):
        use = j < ndig
        tow_txt = np.where(use, tow_txt * 10 + np.clip(t16[:, j], 0, 9), tow_txt)
    tow_txt = np.where(ndig > 0, tow_txt, -1)
    # binary GPS week / TOW
    week = tick >> 32
    tow_bin = tick & 0xFFFFFFFF
    ok_bin = (week > 500) & (week < 10000) & (tow_bin < 604_800_000)
    tow_ms = np.where(ok_bin, tow_bin, tow_txt)
    # week from the label where the tick does not hold one (not seen so far)
    lab_s = (lab - GPS_EPOCH).dt.total_seconds().to_numpy()
    week_lab = np.floor((lab_s + 18) / 604800)
    wk = np.where(ok_bin, week, week_lab)
    gps_s = wk * 604800.0 + tow_ms / 1000.0
    # label-derived week can be off by one at a week boundary: put the time next to the label
    fix = ~ok_bin & np.isfinite(lab_s)
    gps_s = np.where(fix & (gps_s - lab_s > 302400), gps_s - 604800, gps_s)
    gps_s = np.where(fix & (lab_s - gps_s > 302400), gps_s + 604800, gps_s)
    utc_s = gps_s + DEFAULTS["tow_offset_s"] - _leap_from_gps_seconds(gps_s)
    valid = (tow_ms >= 0) & np.isfinite(gps_s)
    utc_ms = np.where(valid, np.round(np.nan_to_num(utc_s) * 1000), 0).astype(np.int64)
    t_tow = pd.Series(pd.to_datetime(GPS_EPOCH.value // 1_000_000 + utc_ms, unit="ms", utc=True))
    t_tow[~valid] = pd.NaT
    df = pd.DataFrame({
        "block": np.arange(n), "offset": HEADER_SIZE + np.arange(n) * period + block,
        "flag": flag, "tick_ms": tick, "gps_week": week, "tow_ms": tow_ms,
        # continuous GPS milliseconds (no jump at the weekly TOW rollover)
        "gps_ms": np.where(np.isfinite(wk), wk, 0).astype(np.int64) * 604_800_000 + tow_ms,
        "time_label": lab.to_numpy(), "latitude": lat, "longitude": lon,
        "phase_error": i1, "counter": i2, "sync_age_s": i2 / 100.0,
        "gps_tow_ms": np.where(tow_txt >= 0, tow_txt, np.nan),
    })
    df["time_label"] = pd.to_datetime(df["time_label"], utc=True)
    df["time_tow"] = t_tow
    src = _opt("time_source", time_source)
    df["time"] = df["time_tow"] if (src == "tow" and df["time_tow"].notna().all()) else df["time_label"]
    df["label_minus_tow_s"] = (df["time_label"] - df["time_tow"]).dt.total_seconds()
    return df


_CACHE: dict = {}


def _parsed(path, time_source=None):
    """(header, block_bytes, period, n_tags, tags, n_trailing_samples) of a file,
    cached by path, size, modification time and timing settings."""
    path = Path(path)
    stt = path.stat()
    key = (str(path.resolve()), stt.st_size, stt.st_mtime_ns, _opt("time_source", time_source),
           DEFAULTS["tow_offset_s"], DEFAULTS.get("leap_seconds"))
    if key not in _CACHE:
        hdr = read_dld_header(path)
        buf = _open(path)
        try:
            block, period, n = _layout(buf)
            tags = _tags_from_buffer(buf, block, period, n, time_source)
            rem = len(buf) - HEADER_SIZE - n * period
        finally:
            _close(buf)
        trailing = max(0, min(rem, block)) // 3
        leap = hdr["leap_seconds"]
        if leap and len(tags) and leap != gps_utc_offset(tags["time"].iloc[0]):
            warnings.warn(f"{path.name}: header leap seconds {leap} != table "
                          f"{gps_utc_offset(tags['time'].iloc[0])} - check DEFAULTS['leap_seconds']")
        if len(_CACHE) > 64:
            _CACHE.clear()
        _CACHE[key] = (hdr, block, period, n, tags, trailing)
    return _CACHE[key]


def read_dld_tags(path, time_source=None) -> pd.DataFrame:
    """
    All time tags of a DLD file, one row per 1000 samples (see
    ``_tags_from_buffer`` for the columns). The UTC time ``time`` comes from
    the binary GPS week and time of week (``time_source="tow"``, default).
    """
    return _parsed(path, time_source)[4].copy()


# --------------------------------------------------------------------------- #
# Samples
# --------------------------------------------------------------------------- #
def _decode24(raw: np.ndarray) -> np.ndarray:
    a = raw.reshape(-1, 3).astype(np.int32)
    x = a[:, 0] | (a[:, 1] << 8) | (a[:, 2] << 16)
    x[x >= 1 << 23] -= 1 << 24
    return x


def _segments(tags: pd.DataFrame, block_samples: int, sr: float, tag_marks: str):
    """Split blocks into continuous runs: both the tag time and the tick must advance by
    exactly one block. Returns a list of (first_block, last_block, t0)."""
    dt = block_samples / sr
    shift = pd.Timedelta(0) if tag_marks == "block_start" else -pd.Timedelta(seconds=dt)
    ticks = tags["gps_ms"].to_numpy()
    tsec = (tags["time"] - tags["time"].iloc[0]).dt.total_seconds().to_numpy()
    # tick to 1 ms; time to 1.5 ms (TOW) - the 0.5 s for "label" only tolerates its 10 ms text
    tol = 0.0015 if _opt("time_source", None) == "tow" and tags["time"].equals(tags["time_tow"]) else 0.5
    brk = (np.abs(np.diff(ticks) - dt * 1000) > 1) | (np.abs(np.diff(tsec) - dt) > tol)
    starts = np.r_[0, np.where(brk)[0] + 1]
    ends = np.r_[starts[1:] - 1, len(tags) - 1]
    times = tags["time"]
    return [(int(a), int(b), times.iloc[a] + shift) for a, b in zip(starts, ends)]


def _sampling_rate(tags: pd.DataFrame, block_samples: int) -> float:
    d = np.diff(tags["gps_ms"].to_numpy())
    if len(d) == 0:
        raise ValueError("need at least two time tags to determine the sample rate")
    step = float(np.median(d))
    return block_samples / (step / 1000.0)


def scan_dld(path, tag_marks: str | None = None, time_source: str | None = None) -> list[dict]:
    """
    Cheap description of a DLD file for indexing: one dict per continuous
    segment with ``serial, component, starttime, endtime, sampling_rate,
    npts`` (Timestamps, UTC), ``max_sync_age_s`` (longest time without GPS
    synchronisation in the segment) and ``time_cal``. Reads header and tags only.
    """
    hdr, block, period, n, tags, trailing = _parsed(path, time_source)
    tag_marks = _opt("tag_marks", tag_marks)
    ns = block // 3
    sr = _sampling_rate(tags, ns)
    segs = _segments(tags, ns, sr, tag_marks)
    out = []
    for k, (b0, b1, t0) in enumerate(segs):
        npts = (b1 - b0 + 1) * ns
        if k == len(segs) - 1 and DEFAULTS["include_trailing"]:
            npts += trailing
        out.append(dict(serial=hdr["serial"], component=hdr["component"], starttime=t0,
                        endtime=t0 + pd.Timedelta(seconds=(npts - 1) / sr),
                        sampling_rate=sr, npts=npts, project_name=hdr["project_name"],
                        firmware=hdr["firmware"], leap_seconds=hdr["leap_seconds"],
                        max_sync_age_s=float(tags["sync_age_s"].iloc[b0:b1 + 1].max()),
                        time_cal=time_calibration(time_source, tag_marks)))
    return out


def read_dld(path, starttime=None, endtime=None, tag_marks: str | None = None,
             headonly: bool = False, station: str | None = None, network: str = "",
             time_source: str | None = None):
    """
    Read one DLD file into an ObsPy Stream (one Trace per gap-free segment).

    Parameters
    ----------
    starttime, endtime : optional (UTCDateTime / str / Timestamp)
        Only decode the blocks needed for this window (then trimmed). The
        file is memory-mapped and its tags cached, so reading many windows of
        a long file (e.g. day by day) is cheap.
    tag_marks : "block_start" (default) or "block_end"
        Timing convention, see module docstring.
    time_source : "tow" (default) or "label"
        Tag times from the GPS week and time of week (continuous, consistent
        between nodes) or from the HHMMSS/YYYYMMDD text (diagnostics only).
    headonly : bool
        Traces without data (stats only).
    station, network : str
        Codes to put in the header. Default station = last 5 digits of the
        serial (SEED allows 5 characters); ``stats.serial`` has the full one.

    Each trace has ``stats.channel`` = component letter (X/Y/Z),
    ``stats.serial``, ``stats.dld`` (header dict, file path, timing
    settings ``time_cal``, longest GPS-sync gap), ``stats.coordinates``
    (median tag position) and data as int32 counts. Samples after the last
    tag (file cut short) are included (``DEFAULTS["include_trailing"]``).
    """
    from obspy import Stream, Trace, UTCDateTime
    from obspy.core.util import AttribDict

    path = Path(path)
    hdr, block, period, n, tags, trailing = _parsed(path, time_source)
    tag_marks = _opt("tag_marks", tag_marks)
    ns = block // 3
    sr = _sampling_rate(tags, ns)
    if not DEFAULTS["include_trailing"]:
        trailing = 0
    tcal = time_calibration(time_source, tag_marks)

    def _u(t):
        if t is None:
            return None
        return t if isinstance(t, UTCDateTime) else UTCDateTime(pd.Timestamp(t).isoformat())

    t_lo, t_hi = _u(starttime), _u(endtime)
    st = Stream()
    buf = None if headonly else _open(path)
    try:
        for s0, s1, t0 in _segments(tags, ns, sr, tag_marks):
            seg_start = UTCDateTime(t0.isoformat())
            block_dt = ns / sr
            last = s1 == n - 1
            b0, b1 = s0, s1
            if t_lo is not None:
                b0 = max(s0, s0 + int(np.floor((t_lo - seg_start) / block_dt)))
            if t_hi is not None:
                b1 = min(s1, s0 + int(np.floor((t_hi - seg_start) / block_dt)))
            extra = trailing if (last and b1 == s1 and (t_hi is None or
                                 t_hi >= seg_start + (s1 - s0 + 1) * block_dt)) else 0
            if b1 < b0 and not (extra and b0 == s1 + 1):
                continue
            first_block_start = seg_start + (b0 - s0) * block_dt
            npts = max(0, b1 - b0 + 1) * ns + extra
            if headonly:
                data = np.array([], dtype=np.int32)
            else:
                parts = []
                if b1 >= b0:
                    raw = np.frombuffer(buf, np.uint8, count=(b1 - b0 + 1) * period,
                                        offset=HEADER_SIZE + b0 * period).reshape(-1, period)[:, :block]
                    parts.append(_decode24(np.ascontiguousarray(raw)))
                    del raw
                if extra:
                    tail = np.frombuffer(buf, np.uint8, count=extra * 3, offset=HEADER_SIZE + n * period)
                    parts.append(_decode24(np.array(tail)))
                    del tail
                data = np.concatenate(parts) if len(parts) > 1 else parts[0]
            tr = Trace(data=data)
            tr.stats.sampling_rate = sr
            tr.stats.starttime = first_block_start
            tr.stats.network = network
            tr.stats.station = station if station is not None else hdr["serial"][-5:]
            tr.stats.channel = hdr["component"]
            tr.stats.serial = hdr["serial"]
            sub = tags.iloc[b0:max(b0, b1) + 1]
            tr.stats.coordinates = AttribDict(latitude=float(sub["latitude"].median()),
                                              longitude=float(sub["longitude"].median()),
                                              elevation=float(hdr["altitude"]))
            tr.stats.dld = AttribDict(path=str(path), tag_marks=tag_marks, time_cal=tcal,
                                      first_block=int(b0), last_block=int(b1),
                                      trailing_samples=int(extra),
                                      max_sync_age_s=float(sub["sync_age_s"].max()),
                                      header={k: v for k, v in hdr.items()
                                              if not isinstance(v, pd.Timestamp)})
            if headonly:
                tr.stats.npts = npts
            st.append(tr)
    finally:
        if buf is not None:
            _close(buf)
    if not headonly and (t_lo is not None or t_hi is not None):
        st.trim(t_lo, t_hi, nearest_sample=False)
    return st


def read_dld_node(files, **kw):
    """
    Read several DLD files (e.g. seis000X/Y/Z of one node, or consecutive
    files seis000..seis00N) into one Stream, merged per channel.
    """
    from obspy import Stream

    st = Stream()
    for f in files:
        st += read_dld(f, **kw)
    st.merge(method=1)
    return st


# --------------------------------------------------------------------------- #
# Verification against a SoloLite export
# --------------------------------------------------------------------------- #
def compare_with_export(dld_tr, exported_tr, max_lag_s: float = 5.0) -> dict:
    """
    Compare a trace read from DLD with the same channel exported by SoloLite
    (MiniSEED/SEG-Y). Returns the time shift that best aligns them
    (``lag_s``: positive = DLD is late), the correlation and the amplitude
    ratio. ``lag_s`` ≈ 0 confirms ``tag_marks="block_start"``; ≈ -block
    duration means use ``"block_end"``; a ratio of -1 means the export
    already inverted polarity, 10^(gain/20) that it removed the gain.
    """
    from obspy.signal.cross_correlation import correlate, xcorr_max

    a, b = dld_tr.copy(), exported_tr.copy()
    if a.stats.sampling_rate != b.stats.sampling_rate:
        b.resample(a.stats.sampling_rate)
    t0, t1 = max(a.stats.starttime, b.stats.starttime), min(a.stats.endtime, b.stats.endtime)
    if t1 - t0 < 2 * max_lag_s:
        raise ValueError("traces overlap too little")
    a.trim(t0, t1); b.trim(t0, t1)
    n = min(a.stats.npts, b.stats.npts)
    x, y = a.data[:n].astype(float), b.data[:n].astype(float)
    shift = int(max_lag_s * a.stats.sampling_rate)
    cc = correlate(x - x.mean(), y - y.mean(), shift)
    lag, val = xcorr_max(cc, abs_max=True)
    ratio = float(np.dot(x, y) / np.dot(y, y)) if np.dot(y, y) else np.nan
    return {"lag_s": -lag / a.stats.sampling_rate, "correlation": float(val), "amplitude_ratio": ratio,
            "start_difference_s": float(a.stats.starttime - b.stats.starttime)}
