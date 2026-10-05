"""
smartsolo_log
=============

Parse SmartSolo node state-of-health logs (``DigiSolo.LOG``) into pandas
DataFrames.

A DigiSolo.LOG file is a plain-text, INI-like file made of numbered sections::

    <00006,06855,01961,00426,00659,00007,00001>      <- record counts header

    [GPS00018]
    GPS Status = GPS Synchronization
    UTC Time = "2024/12/25,15:15:02"
    Latitude   = -71.548245000
    ...

    [Temperature00001]
    UTC Time = "2024/12/25,14:36:24"
    Temperature = -1.3750

Main functions
--------------
read_log(path)            -> one wide DataFrame for a single log file
read_logs(paths)          -> same, for many files / nodes (folder, glob or list)
read_device_info(paths)   -> one row per [DeviceInfoNNNNN] (boot) block
plot_series(df, column)   -> quick plot of one or more columns against time

In the wide DataFrame each log record is one row, every field is a column
(``NaN`` where a field does not apply to that record type), and the index is a
timezone-aware ``DatetimeIndex`` (UTC) taken from the record's ``UTC Time``.
"""

from __future__ import annotations

import glob
import os
import re
from collections import OrderedDict
from pathlib import Path
from typing import Iterable, Union

import numpy as np
import pandas as pd

__all__ = [
    "parse_sections",
    "read_log",
    "read_logs",
    "read_device_info",
    "plot_series",
    "split_list_column",
    "HEADER_FIELDS",
]

PathLike = Union[str, os.PathLike]

# Order of the counters in the first line "<a,b,c,d,e,f,g>" of the file.
# Verified against the sample logs (counts match the number of sections found).
HEADER_FIELDS = ["DeviceInfo", "GPS", "Temperature", "Memory", "Battery", "Error", "Notify"]

_SECTION_RE = re.compile(r"^\[([A-Za-z_]+?)(\d*)\]\s*$")
_HEADER_RE = re.compile(r"^<([\d,\s]+)>\s*$")
_DATETIME_RE = re.compile(r"^\d{4}/\d{2}/\d{2},\d{2}:\d{2}:\d{2}$")
_NUMBER_RE = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$")
_PAIR_RE = re.compile(r"^\s*([-+]?\d+\.?\d*)\s*,\s*([-+]?\d+\.?\d*)\s*$")
_DT_FORMAT = "%Y/%m/%d,%H:%M:%S"

# Fields kept as text even if they look numeric (IDs, codes with leading zeros).
# Values like "a, b" (two numbers) are split into <col>_1 and <col>_2.
# Quoted lists such as "GPS Strength" / "Satellite ID" are kept as strings;
# use split_list_column() to expand them.
STRING_FIELDS = {
    "Serial Number", "BootReason", "Firmware Version", "BootLoader Version",
    "SD Manufacturer ID", "SD OEM_Application ID", "SD Production SN",
    "SD Manufacture Date", "SD Product Name", "Start Date", "Start Time",
    "End Time",
}


# --------------------------------------------------------------------------- #
# Low level parsing
# --------------------------------------------------------------------------- #
def _snake(name: str) -> str:
    """'Latest Fix UTC Time' -> 'latest_fix_utc_time', 'Ch1 RMS Noise' -> 'ch1_rms_noise'."""
    name = _ALIASES.get(name.strip(), name)
    s = re.sub(r"[^0-9A-Za-z]+", "_", name.strip()).strip("_").lower()
    return s


_ALIASES = {"BootReason": "Boot Reason", "UTC Time": "UTC Time"}


def parse_sections(path: PathLike):
    """
    Read a DigiSolo.LOG file and return ``(header_counts, sections)``.

    ``header_counts`` is a dict built from the first ``<...>`` line (or ``{}``).
    ``sections`` is a list of dicts with keys ``record_type``, ``record_no``,
    ``line_no`` and ``fields`` (an ordered dict of raw *string* values).

    Blank lines do not end a section; a section ends at the next ``[Name]``.
    Repeated keys within one section get a numeric suffix (``key_2``...).
    """
    header: dict = {}
    sections = []
    current = None

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line_no, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line:
                continue

            m = _HEADER_RE.match(line)
            if m and not sections and current is None:
                nums = [int(x) for x in m.group(1).split(",") if x.strip()]
                header = {
                    (HEADER_FIELDS[i] if i < len(HEADER_FIELDS) else f"count_{i}"): n
                    for i, n in enumerate(nums)
                }
                continue

            m = _SECTION_RE.match(line)
            if m:
                current = {
                    "record_type": m.group(1),
                    "record_no": int(m.group(2)) if m.group(2) else np.nan,
                    "line_no": line_no,
                    "fields": OrderedDict(),
                }
                sections.append(current)
                continue

            if "=" in line and current is not None:
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip()
                k, n = key, 2
                while k in current["fields"]:
                    k = f"{key}_{n}"
                    n += 1
                current["fields"][k] = value
            # anything else (stray text) is ignored

    return header, sections


def _convert_value(value: str, key: str = ""):
    """Convert one raw string to datetime / float / (float, float) / str."""
    if value == "":
        return np.nan
    if key in STRING_FIELDS:
        return value
    if _DATETIME_RE.match(value):
        return pd.Timestamp(pd.to_datetime(value, format=_DT_FORMAT), tz="UTC")
    if _NUMBER_RE.match(value):
        return float(value)
    m = _PAIR_RE.match(value)
    if m:
        return (float(m.group(1)), float(m.group(2)))
    return value


def _section_to_row(sec: dict) -> dict:
    row = {
        "record_type": sec["record_type"],
        "record_no": sec["record_no"],
        "line_no": sec["line_no"],
    }
    for key, raw in sec["fields"].items():
        col = _snake(key)
        val = _convert_value(raw, key)
        if isinstance(val, tuple):
            row[f"{col}_1"], row[f"{col}_2"] = val
        else:
            row[col] = val
    return row


def _serial_from_sections(sections) -> str | None:
    for sec in sections:
        sn = sec["fields"].get("Serial Number")
        if sn:
            return sn
    return None


def _finalise_types(df: pd.DataFrame) -> pd.DataFrame:
    """Make datetime columns proper datetime64[ns, UTC] and numbers numeric."""
    for col in df.columns:
        if df[col].dtype != object:
            continue
        sample = df[col].dropna()
        if sample.empty:
            continue
        if sample.map(lambda v: isinstance(v, pd.Timestamp)).all():
            df[col] = pd.to_datetime(df[col], utc=True)
        elif sample.map(lambda v: isinstance(v, (int, float, np.number))).all():
            df[col] = pd.to_numeric(df[col])
    return df


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def read_log(
    path: PathLike,
    include_device_info: bool = False,
    sort: bool = True,
) -> pd.DataFrame:
    """
    Parse one DigiSolo.LOG into a wide DataFrame.

    Parameters
    ----------
    path : str or Path
        Path to the log file.
    include_device_info : bool, default False
        Also include the ``[DeviceInfoNNNNN]`` boot blocks as rows (their
        time is the ``Boot RTC``). Usually you want ``read_device_info``
        instead.
    sort : bool, default True
        Sort rows by time (stable, so file order is kept for equal times).

    Returns
    -------
    pandas.DataFrame
        Index: ``time`` (``DatetimeIndex``, UTC) from each record's
        ``UTC Time``. Records without their own time (e.g.
        ``[BatteryPowerOnStop]``) inherit the time of the preceding record and
        are flagged with ``time_inferred = True`` (this includes boot
        blocks without a ``Boot RTC``).

        Always-present columns: ``serial``, ``file``, ``record_type``,
        ``record_no``, ``line_no``, ``session``, ``time_inferred``;
        ``session`` is the power-up number (the latest ``[DeviceInfoNNNNN]``
        block before the record, 0 if none); then one column per log
        field in snake_case (``temperature``, ``voltage``, ``latitude``,
        ``gps_status``, ``available_memory`` ...).

        ``df.attrs['header_counts']`` holds the counts from the first line of
        the file and ``df.attrs['parsed_counts']`` the counts actually parsed.
    """
    path = Path(path)
    header, sections = parse_sections(path)
    serial = _serial_from_sections(sections)

    rows = []
    last_time = pd.NaT  # time of the previous section (incl. DeviceInfo boot RTC)
    session = 0          # power-up counter: number of the latest [DeviceInfo] block
    for sec in sections:
        row = _section_to_row(sec)
        if sec["record_type"] == "DeviceInfo":
            session = int(sec["record_no"]) if not pd.isna(sec["record_no"]) else session + 1
        row["session"] = session
        if sec["record_type"] == "DeviceInfo":
            row["utc_time"] = row.get("boot_rtc", pd.NaT)
        own_time = row.get("utc_time", pd.NaT)
        if pd.isna(own_time):
            row["utc_time"] = last_time
            row["time_inferred"] = True
        else:
            row["time_inferred"] = False
            last_time = own_time
        if sec["record_type"] == "DeviceInfo" and not include_device_info:
            continue
        rows.append(row)

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    if "utc_time" not in df.columns:
        df["utc_time"] = pd.NaT
    df = _finalise_types(df)
    df["utc_time"] = pd.to_datetime(df["utc_time"], utc=True)

    df["time_inferred"] = df["time_inferred"].astype(bool)

    df.insert(0, "serial", serial)
    df.insert(1, "file", str(path))

    df = df.rename(columns={"utc_time": "time"}).set_index("time")
    if sort:
        df = df.sort_index(kind="stable")

    parsed = pd.Series([s["record_type"] for s in sections]).value_counts().to_dict()
    df.attrs["header_counts"] = header
    df.attrs["parsed_counts"] = parsed
    df.attrs["serial"] = serial
    return df


def _expand_paths(paths) -> list[Path]:
    if isinstance(paths, (str, os.PathLike)):
        paths = [paths]
    out: list[Path] = []
    for p in paths:
        p = str(p)
        if os.path.isdir(p):
            out += sorted(Path(p).rglob("*.LOG")) + sorted(Path(p).rglob("*.log"))
        elif any(ch in p for ch in "*?["):
            out += [Path(x) for x in sorted(glob.glob(p, recursive=True))]
        else:
            out.append(Path(p))
    # de-duplicate, keep order (case-insensitive filesystems may double-match)
    seen, uniq = set(), []
    for p in out:
        key = os.path.realpath(p)
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def read_logs(paths, sort: bool = True, **kwargs) -> pd.DataFrame:
    """
    Parse many log files (several nodes) into one wide DataFrame.

    ``paths`` may be a folder (searched recursively for ``*.LOG``), a glob
    pattern (``"data/**/DigiSolo*.LOG"``), a single file, or a list of any of
    these. Rows are tagged with ``serial`` and ``file`` so nodes can be
    compared, e.g. ``df.groupby('serial')['temperature']``.
    """
    files = _expand_paths(paths)
    if not files:
        raise FileNotFoundError(f"No log files found for {paths!r}")
    frames = [read_log(f, sort=False, **kwargs) for f in files]
    df = pd.concat(frames, sort=False)
    if sort:
        df = df.sort_index(kind="stable")
    df.attrs = {}
    return df


def read_device_info(paths) -> pd.DataFrame:
    """
    One row per ``[DeviceInfoNNNNN]`` block (written each time the node
    boots): firmware, serial, sample rate, gains, geophone test results
    (resistance, natural frequency, damping, sensitivity, noise per channel),
    SD-card info, boot temperature/tilt/voltage, GPS lock time, etc.

    Index: ``boot_time`` (``Boot RTC``, UTC). Accepts the same ``paths`` as
    :func:`read_logs`.
    """
    rows = []
    for f in _expand_paths(paths):
        _, sections = parse_sections(f)
        for sec in sections:
            if sec["record_type"] != "DeviceInfo":
                continue
            row = _section_to_row(sec)
            row["file"] = str(f)
            rows.append(row)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = _finalise_types(df)
    df = df.drop(columns=["record_type"]).rename(columns={"record_no": "boot_no"})
    df["boot_time"] = pd.to_datetime(df.get("boot_rtc"), utc=True)
    front = [c for c in ["serial_number", "file", "boot_no", "boot_reason"] if c in df]
    df = df[front + [c for c in df.columns if c not in front]]
    return df.set_index("boot_time").sort_index(kind="stable")


def split_list_column(df: pd.DataFrame, column: str, prefix: str | None = None) -> pd.DataFrame:
    """
    Expand a comma separated string column (e.g. ``gps_strength`` or
    ``satellite_id``) into numeric columns ``<prefix>_0, <prefix>_1, ...``.
    """
    prefix = prefix or column
    s = df[column].dropna().astype(str)
    wide = s.str.split(",", expand=True).apply(lambda c: pd.to_numeric(c.str.strip(), errors="coerce"))
    wide.columns = [f"{prefix}_{i}" for i in wide.columns]
    return wide.reindex(df.index) if not df.index.has_duplicates else wide


def plot_series(df: pd.DataFrame, columns, by: str | None = "serial", ax=None, **plot_kw):
    """
    Quick time-series plot of one or more columns.

    >>> plot_series(df, "temperature")
    >>> plot_series(df, ["voltage", "temperature"])   # one subplot each
    >>> plot_series(df, "temperature", by=None)        # ignore node grouping

    If ``by`` is a column (default ``'serial'``) with several values, one line
    per group is drawn.
    """
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    if isinstance(columns, str):
        columns = [columns]
    if ax is None:
        fig, axes = plt.subplots(len(columns), 1, sharex=True, squeeze=False,
                                 figsize=(10, 3 * len(columns)))
        axes = axes[:, 0]
    else:
        axes = np.atleast_1d(ax)

    plot_kw.setdefault("marker", ".")
    plot_kw.setdefault("markersize", 2)
    plot_kw.setdefault("linewidth", 0.8)

    for col, a in zip(columns, axes):
        data = df[[col] + ([by] if by and by in df else [])].dropna(subset=[col])
        if by and by in data and data[by].nunique() > 1:
            for name, g in data.groupby(by):
                a.plot(g.index, g[col], label=str(name), **plot_kw)
            a.legend(title=by, fontsize="small")
        else:
            a.plot(data.index, data[col], **plot_kw)
        a.set_ylabel(col)
        a.grid(True, alpha=0.3)
    loc = mdates.AutoDateLocator()
    axes[-1].xaxis.set_major_locator(loc)
    axes[-1].xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc))
    axes[-1].set_xlabel("time (UTC)")
    return axes
