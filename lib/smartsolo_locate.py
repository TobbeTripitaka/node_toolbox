"""
smartsolo_locate
================

Work out *where* and *when* each SmartSolo node was recording, from its
``DigiSolo.LOG`` files, and select nodes by location and time.

Concepts
--------
Deployment
    One power-up (``session``) of one node: from the ``[DeviceInfoNNNNN]``
    boot block to the next boot. A node that was moved between sites gets a
    new deployment each time it is powered up again.

Deployment location
    The **first stable GPS position after power-up**. The first fixes after a
    cold start can be tens of metres off, so we take the first run of
    ``n_stable`` consecutive GPS fixes that all lie within ``stable_tol_m`` of
    their median, and use that median. On moving ice the node then drifts;
    the drift is reported (``drift_m``, ``drift_m_per_day``) but the position
    stays the one at power-up.

    Power-ups without any GPS fix (e.g. bench tests) have no location and are
    excluded from spatial selection.

Main functions
--------------
find_logs(root)                         -> list of log files below a folder
build_deployments(logs)                 -> GeoDataFrame, one row per deployment
select_deployments(deps, point=..., radius_km=..., polygon=..., start=..., end=...)
export_stations(deps, "stations.gpkg")  -> CSV / GeoPackage / GeoJSON / Shapefile
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

import smartsolo_log as sl

__all__ = [
    "find_logs",
    "first_stable_fix",
    "build_deployments",
    "load_polygon",
    "select_deployments",
    "export_stations",
    "distance_m",
]

WGS84 = "EPSG:4326"


# --------------------------------------------------------------------------- #
# Geometry helpers
# --------------------------------------------------------------------------- #
def _geod():
    from pyproj import Geod

    return Geod(ellps="WGS84")


def distance_m(lat1, lon1, lat2, lon2):
    """Geodesic distance(s) in metres on the WGS84 ellipsoid (array friendly)."""
    lat1, lon1, lat2, lon2 = np.broadcast_arrays(
        np.asarray(lat1, float), np.asarray(lon1, float),
        np.asarray(lat2, float), np.asarray(lon2, float))
    _, _, d = _geod().inv(lon1, lat1, lon2, lat2)
    return d


def _local_xy(lat, lon, lat0, lon0):
    """Small-area east/north offsets in metres from (lat0, lon0)."""
    r = 6371008.8
    x = np.radians(np.asarray(lon) - lon0) * r * np.cos(np.radians(lat0))
    y = np.radians(np.asarray(lat) - lat0) * r
    return x, y


# --------------------------------------------------------------------------- #
# Finding logs
# --------------------------------------------------------------------------- #
def _looks_like_digisolo_log(path: Path, nbytes: int = 4096) -> bool:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(nbytes)
    except OSError:
        return False
    return "[DeviceInfo" in head or ("UTC Time" in head and head.lstrip().startswith("<"))


def find_logs(root, pattern: str = "*", check_content: bool = True) -> list[Path]:
    """
    Recursively find SmartSolo log files below ``root`` (a folder or list).

    By default every ``*.LOG`` / ``*.log`` file is opened and kept only if it
    looks like a DigiSolo log (contains ``[DeviceInfo`` / ``UTC Time``), so
    the files can have any name.
    """
    roots = [root] if isinstance(root, (str, os.PathLike)) else list(root)
    found = []
    for r in roots:
        r = Path(r)
        if r.is_file():
            found.append(r)
            continue
        for p in r.rglob(pattern):
            if p.is_file() and p.suffix.lower() == ".log":
                found.append(p)
    found = sorted(set(found))
    if check_content:
        found = [p for p in found if _looks_like_digisolo_log(p)]
    return found


# --------------------------------------------------------------------------- #
# Stable fix and deployments
# --------------------------------------------------------------------------- #
def _nanmed(a):
    a = np.asarray(a, float)
    return float(np.nanmedian(a)) if np.isfinite(a).any() else np.nan


def first_stable_fix(fixes: pd.DataFrame, n_stable: int = 5, stable_tol_m: float = 10.0):
    """
    First run of ``n_stable`` consecutive fixes all within ``stable_tol_m`` of
    their median position. ``fixes`` must have a time index and ``latitude``,
    ``longitude`` (and optionally ``altitude``) columns, sorted by time.

    Returns ``(lat, lon, alt, time_of_first_fix_in_run, index_of_first_fix)``
    or ``None`` if no stable run exists (fewer fixes than ``n_stable`` -> the
    median of all fixes is used, if any).
    """
    fx = fixes.dropna(subset=["latitude", "longitude"])
    if fx.empty:
        return None
    lat = pd.to_numeric(fx["latitude"], errors="coerce").to_numpy(float)
    lon = pd.to_numeric(fx["longitude"], errors="coerce").to_numpy(float)
    alt = (pd.to_numeric(fx["altitude"], errors="coerce").to_numpy(float) if "altitude" in fx
           else np.full(len(fx), np.nan))

    if len(fx) < n_stable:
        return (float(np.median(lat)), float(np.median(lon)), _nanmed(alt),
                fx.index[0], 0)

    for i in range(len(fx) - n_stable + 1):
        la, lo = lat[i:i + n_stable], lon[i:i + n_stable]
        mla, mlo = np.median(la), np.median(lo)
        x, y = _local_xy(la, lo, mla, mlo)
        if np.all(np.hypot(x, y) <= stable_tol_m):
            return (float(mla), float(mlo), _nanmed(alt[i:i + n_stable]),
                    fx.index[i], i)
    return None


def _deployments_from_log(df: pd.DataFrame, info: pd.DataFrame | None,
                          n_stable: int, stable_tol_m: float) -> list[dict]:
    rows = []
    for session, g in df.groupby("session", sort=True):
        g = g.sort_index(kind="stable")
        real = g[~g["time_inferred"]] if "time_inferred" in g else g
        if real.empty:
            continue
        fixes = g[g["latitude"].notna()] if "latitude" in g else g.iloc[0:0]
        stable = first_stable_fix(fixes, n_stable, stable_tol_m) if len(fixes) else None

        row = {
            "serial": g["serial"].iloc[0],
            "session": int(session),
            "log_file": g["file"].iloc[0],
            "start": real.index.min(),
            "end": real.index.max(),
            "n_records": len(g),
            "n_fixes": len(fixes),
            "latitude": np.nan, "longitude": np.nan, "elevation": np.nan,
            "fix_time": pd.NaT, "unstable_fixes_skipped": np.nan,
            "median_offset_m": np.nan, "max_offset_m": np.nan,
            "drift_m": np.nan, "drift_m_per_day": np.nan,
        }
        if stable is not None:
            lat0, lon0, alt0, t0, i0 = stable
            after = fixes.iloc[i0:]  # ignore the cold-start fixes before the stable run
            x, y = _local_xy(after["latitude"], after["longitude"], lat0, lon0)
            r = np.hypot(x, y)
            row.update(latitude=lat0, longitude=lon0, elevation=alt0, fix_time=t0,
                       unstable_fixes_skipped=i0,
                       median_offset_m=float(np.median(r)),
                       max_offset_m=float(np.max(r)))
            # drift: median of last day of fixes vs the stable start position
            tlast = fixes.index.max()
            last = fixes[fixes.index >= tlast - pd.Timedelta("1D")]
            lx, ly = _local_xy(last["latitude"].median(), last["longitude"].median(), lat0, lon0)
            drift = float(np.hypot(lx, ly))
            days = (last.index.mean() - t0) / pd.Timedelta("1D")
            row.update(drift_m=drift, drift_m_per_day=drift / days if days > 0.5 else np.nan)
            # orientation from the eCompass / tilt sensor (median over the deployment)
            for col in ["tilted_angle", "roll_angle", "pitch_angle"]:
                if col in after:
                    row[f"{col}_median"] = float(after[col].median())
            if "ecompass_north" in after:
                # circular statistics; some firmware logs -180..180, others 0..360
                import smartsolo_orientation as so
                row["ecompass_north_median"] = so.circular_mean(after["ecompass_north"] % 360)
                row["ecompass_north_std"] = so.circular_std(after["ecompass_north"] % 360)

        if info is not None and not info.empty:
            m = info[(info["serial_number"].astype(str) == str(row["serial"]))
                     & (info["boot_no"] == session)
                     & (info["file"] == row["log_file"])]
            if len(m):
                m = m.iloc[0]
                for col in ["boot_reason", "device_type", "firmware_version", "sample_rate",
                            "sample_rate_hz", "channel_1_gain", "channel_2_gain", "channel_3_gain"]:
                    if col in m:
                        row[col] = m[col]
                # boot-time geophone test of this power-up
                import smartsolo_node as sn
                qc = sn.geophone_qc(pd.DataFrame([m]))
                if len(qc):
                    row["geophone_ok"] = bool(qc["ok_all"].all())
                    row["geophone_test_noisy"] = bool(qc["noisy"].any())
                    for col in ["resonate_freq_hz", "damping", "sensitivity"]:
                        row[f"test_{col}_mean"] = float(qc[col].mean())
                    row["test_resistance_ohm_mean"] = float(qc["resistance_1_ohm"].mean())
        rows.append(row)
    return rows


def build_deployments(logs, n_stable: int = 5, stable_tol_m: float = 10.0,
                      min_duration: str | pd.Timedelta = "0s",
                      keep_unlocated: bool = False, cache: str | os.PathLike | None = None,
                      verbose: bool = False):
    """
    Build a deployment table from log files.

    Parameters
    ----------
    logs : folder, glob, file, or list of these
        Passed to :func:`find_logs` (folders) / used directly (files).
    n_stable, stable_tol_m : int, float
        Definition of the first stable fix (see module docstring).
    min_duration : str or Timedelta
        Drop deployments shorter than this (e.g. ``"1h"`` to drop reboots).
    keep_unlocated : bool
        Keep power-ups that have no GPS position (geometry is empty).
    cache : path, optional
        If given and the file exists, load the table from it instead of
        parsing again; otherwise parse and save it there (``.gpkg``,
        ``.parquet`` or ``.csv``). Useful for a full hard drive.

    Returns
    -------
    geopandas.GeoDataFrame (EPSG:4326)
        One row per deployment with ``serial``, ``session``, ``start``,
        ``end`` (UTC), ``latitude``, ``longitude``, ``elevation`` (first
        stable fix), ``fix_time``, quality columns (``n_fixes``,
        ``unstable_fixes_skipped``, ``median_offset_m``, ``max_offset_m``,
        ``drift_m``, ``drift_m_per_day``), median orientation
        (``ecompass_north_median``, ``tilted_angle_median``, ``roll_angle_median``,
        ``pitch_angle_median``), the boot-time geophone test of that power-up
        (``geophone_ok``, ``geophone_test_noisy``, ``test_*_mean``) and
        ``log_file``.
    """
    import geopandas as gpd

    if cache is not None and Path(cache).exists():
        return _read_table(cache)

    if isinstance(logs, (str, os.PathLike)) and Path(logs).is_dir():
        files = find_logs(logs)
    elif isinstance(logs, (list, tuple)) and all(Path(p).is_dir() for p in logs):
        files = find_logs(logs)
    else:
        files = sl._expand_paths(logs)
    if not files:
        raise FileNotFoundError(f"No log files found in {logs!r}")

    rows = []
    for f in files:
        if verbose:
            print("reading", f)
        df = sl.read_log(f, sort=False)
        if df.empty:
            continue
        info = sl.read_device_info(f).reset_index()
        rows += _deployments_from_log(df, info, n_stable, stable_tol_m)

    deps = pd.DataFrame(rows)
    if deps.empty:
        return gpd.GeoDataFrame(deps, geometry=[], crs=WGS84)

    # the same log copied twice to the drive -> same serial/session/start
    deps = deps.drop_duplicates(subset=["serial", "session", "start"]).copy()
    deps["duration"] = deps["end"] - deps["start"]
    deps = deps[deps["duration"] >= pd.Timedelta(min_duration)]
    if not keep_unlocated:
        deps = deps[deps["latitude"].notna()]
    deps = deps.sort_values(["serial", "start"]).reset_index(drop=True)
    deps.insert(0, "deployment_id", deps["serial"].astype(str) + "_" + deps["session"].astype(str).str.zfill(3))

    gdf = gpd.GeoDataFrame(
        deps, geometry=gpd.points_from_xy(deps["longitude"], deps["latitude"]), crs=WGS84)
    if cache is not None:
        _write_table(gdf, cache)
    return gdf


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #
def load_polygon(polygon, crs=WGS84):
    """
    Return a GeoDataFrame of polygon(s) from: a shapely geometry, a list of
    ``(lon, lat)`` vertices, a GeoSeries / GeoDataFrame, or a file path
    (GeoPackage, Shapefile, GeoJSON, KML ... anything ``geopandas.read_file``
    reads). Plain geometries / vertex lists are assumed to be in ``crs``.
    """
    import geopandas as gpd
    from shapely.geometry import Polygon
    from shapely.geometry.base import BaseGeometry

    if isinstance(polygon, (str, os.PathLike)):
        g = gpd.read_file(polygon)
    elif isinstance(polygon, gpd.GeoDataFrame):
        g = polygon
    elif isinstance(polygon, gpd.GeoSeries):
        g = gpd.GeoDataFrame(geometry=polygon)
    elif isinstance(polygon, BaseGeometry):
        g = gpd.GeoDataFrame(geometry=[polygon], crs=crs)
    else:  # sequence of (lon, lat)
        g = gpd.GeoDataFrame(geometry=[Polygon(polygon)], crs=crs)
    if g.crs is None:
        g = g.set_crs(crs)
    return g


def _to_utc(t):
    if t is None:
        return None
    t = pd.Timestamp(str(t)) if not isinstance(t, pd.Timestamp) else t
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def select_deployments(deps, point=None, radius_km: float | None = None, polygon=None,
                       start=None, end=None, serials: Iterable | None = None,
                       predicate: str = "within"):
    """
    Select deployments by location and (optionally) time.

    Parameters
    ----------
    deps : GeoDataFrame from :func:`build_deployments`
    point : (lat, lon), optional
        Centre for a radius search (note: **lat, lon** order).
    radius_km : float, optional
        Keep deployments whose stable position is within this geodesic
        distance of ``point``. Adds a ``distance_km`` column.
    polygon : shapely geometry, list of (lon, lat), GeoDataFrame or file, optional
        Keep deployments inside the polygon(s), using ``geopandas.sjoin``
        (``predicate`` 'within' by default; use 'intersects' to include points
        on the boundary). Attributes of the polygon layer are joined in.
    start, end : str / datetime / Timestamp, optional (UTC if naive)
        Keep deployments that overlap ``[start, end]``.
    serials : iterable, optional
        Only these node serial numbers.

    Point/radius and polygon can be combined (both must match).

    Returns
    -------
    GeoDataFrame with the extra columns ``sel_start`` / ``sel_end``: the part
    of the deployment inside the requested time window. Use these to cut the
    waveforms.
    """
    import geopandas as gpd

    out = deps[deps.geometry.notna() & ~deps.geometry.is_empty].copy()

    if serials is not None:
        serials = {str(s) for s in serials}
        out = out[out["serial"].astype(str).isin(serials)]

    if (point is None) != (radius_km is None):
        raise ValueError("Give both point=(lat, lon) and radius_km, or neither.")
    if point is not None:
        lat0, lon0 = point
        d = distance_m(out["latitude"].to_numpy(), out["longitude"].to_numpy(), lat0, lon0)
        out["distance_km"] = d / 1000.0
        out = out[out["distance_km"] <= radius_km]

    if polygon is not None:
        poly = load_polygon(polygon).to_crs(out.crs)
        poly = poly.rename(columns={c: f"poly_{c}" for c in poly.columns
                                    if c != poly.geometry.name and c in out.columns})
        joined = gpd.sjoin(out, poly, how="inner", predicate=predicate)
        joined = joined[~joined.index.duplicated(keep="first")]
        out = joined.drop(columns=[c for c in ["index_right"] if c in joined])

    t0, t1 = _to_utc(start), _to_utc(end)
    out["sel_start"] = out["start"] if t0 is None else out["start"].where(out["start"] > t0, t0)
    out["sel_end"] = out["end"] if t1 is None else out["end"].where(out["end"] < t1, t1)
    out = out[out["sel_end"] > out["sel_start"]]
    return out.sort_values(["serial", "start"])


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
def _write_table(gdf, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ext = path.suffix.lower()
    g = gdf.copy()
    for c in g.columns:
        if pd.api.types.is_timedelta64_dtype(g[c]):
            g[c] = (g[c].dt.total_seconds() / 3600.0).round(6)  # hours
            g = g.rename(columns={c: f"{c}_h"})
    if ext == ".csv":
        pd.DataFrame(g.drop(columns=g.geometry.name)).to_csv(path, index=False)
    elif ext == ".parquet":
        g.to_parquet(path)
    else:  # .gpkg .geojson .shp ...
        for c in g.columns:
            if pd.api.types.is_datetime64_any_dtype(g[c]):
                g[c] = g[c].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        g.to_file(path)
    return path


def _read_table(path):
    import geopandas as gpd

    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".csv":
        df = pd.read_csv(path, dtype={"serial": str})
        g = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df["longitude"], df["latitude"]), crs=WGS84)
    elif ext == ".parquet":
        g = gpd.read_parquet(path)
    else:
        g = gpd.read_file(path)
    for c in ["start", "end", "fix_time", "sel_start", "sel_end"]:
        if c in g:
            g[c] = pd.to_datetime(g[c], utc=True)
    if "serial" in g:
        g["serial"] = g["serial"].astype(str)
    if "duration_h" in g:
        g["duration"] = pd.to_timedelta(g["duration_h"], unit="h").dt.round("1s")
    return g


def export_stations(deps, path):
    """
    Write a deployment/selection table. The format follows the extension:
    ``.csv`` (no geometry), ``.gpkg`` / ``.geojson`` / ``.shp`` (for QGIS),
    ``.parquet``. Durations are written in hours.
    """
    return _write_table(deps, path)


# --------------------------------------------------------------------------- #
# Deployments straight from DLD data files (no log needed)
# --------------------------------------------------------------------------- #
def _device_from_serial(serial: str) -> str:
    """Best guess of the node type from the serial (4530... IGU-16HR, 5900... BD3C-5)."""
    s = str(serial)
    if s.startswith("4530"):
        return "IGU-16HR 3C (from serial)"
    if s.startswith("5900"):
        return "BD3C-5 (from serial)"
    return ""


def build_deployments_from_dld(dld, n_stable: int = 5, stable_tol_m: float = 10.0,
                               tag_marks: str | None = None):
    """
    Deployments from raw DLD files alone - useful when the logs are missing.

    Every DLD file has the node serial in its header and a GPS position in
    each per-1000-sample time tag. Files are grouped by serial and file
    number (``seis000X/Y/Z`` = one recording, one per power-up); the location
    is the first stable position among the tags, as for logs.

    ``dld`` may be a folder, glob, file or list. Returns the same columns as
    :func:`build_deployments` (``session`` = DLD file number, ``source`` =
    "dld").
    """
    import geopandas as gpd

    import smartsolo_dld as dl

    if isinstance(dld, (str, os.PathLike)) and Path(dld).is_dir():
        files = sorted(p for p in Path(dld).rglob("*") if p.suffix.lower() == ".dld")
    else:
        files = [Path(p) for p in sl._expand_paths(dld)]
    groups: dict = {}
    for f in files:
        if not dl.is_dld(f):
            continue
        h = dl.read_dld_header(f)
        groups.setdefault((h["serial"], h["file_index"], h["start"]), []).append((f, h))
    rows = []
    for (serial, idx, _), items in sorted(groups.items(), key=lambda kv: (kv[0][0], str(kv[0][2]))):
        f, h = sorted(items, key=lambda x: x[1]["component"] != "Z")[0]
        tags = dl.read_dld_tags(f).set_index("time")
        segs = dl.scan_dld(f, tag_marks=tag_marks)
        stable = first_stable_fix(tags[["latitude", "longitude"]], n_stable, stable_tol_m)
        row = dict(serial=serial, session=idx if idx is not None else -1, log_file="",
                   start=min(s["starttime"] for s in segs), end=max(s["endtime"] for s in segs),
                   n_records=len(tags), n_fixes=len(tags), latitude=np.nan, longitude=np.nan,
                   elevation=float(h["altitude"]), fix_time=pd.NaT,
                   unstable_fixes_skipped=np.nan, median_offset_m=np.nan, max_offset_m=np.nan,
                   drift_m=np.nan, drift_m_per_day=np.nan, firmware_version=h["firmware"],
                   device_type=_device_from_serial(serial), sample_rate_hz=segs[0]["sampling_rate"],
                   source="dld", dld_files=";".join(sorted(str(p) for p, _ in items)))
        if stable is not None:
            lat0, lon0, _, t0, i0 = stable
            after = tags.iloc[i0:]
            x, y = _local_xy(after["latitude"], after["longitude"], lat0, lon0)
            r = np.hypot(x, y)
            row.update(latitude=lat0, longitude=lon0, fix_time=t0, unstable_fixes_skipped=i0,
                       median_offset_m=float(np.median(r)), max_offset_m=float(np.max(r)))
        rows.append(row)
    deps = pd.DataFrame(rows)
    if deps.empty:
        return gpd.GeoDataFrame(deps, geometry=[], crs=WGS84)
    deps["duration"] = deps["end"] - deps["start"]
    deps.insert(0, "deployment_id", deps["serial"].astype(str) + "_dld" + deps["session"].astype(int).astype(str).str.zfill(3))
    return gpd.GeoDataFrame(deps, geometry=gpd.points_from_xy(deps["longitude"], deps["latitude"]), crs=WGS84)


def combine_deployments(log_deps, dld_deps, extend: bool = True):
    """
    Merge log-based and DLD-based deployment tables. A DLD deployment that
    overlaps a log deployment of the same serial is dropped (the log one has
    the better metadata); with ``extend=True`` the log deployment's start/end
    are widened to cover the data (the log's first record is often ~1 min
    after recording started). DLD deployments of nodes without logs are kept.
    """
    import geopandas as gpd

    log_deps = log_deps.copy()
    if "source" not in log_deps:
        log_deps["source"] = "log"
    keep = []
    for _, d in dld_deps.iterrows():
        m = log_deps[(log_deps["serial"].astype(str) == str(d["serial"]))
                     & (log_deps["start"] <= d["end"]) & (log_deps["end"] >= d["start"])]
        if len(m):
            if extend:
                i = m.index[0]
                log_deps.loc[i, "start"] = min(log_deps.loc[i, "start"], d["start"])
                log_deps.loc[i, "end"] = max(log_deps.loc[i, "end"], d["end"])
                log_deps.loc[i, "duration"] = log_deps.loc[i, "end"] - log_deps.loc[i, "start"]
                if "dld_files" in d:
                    log_deps.loc[i, "dld_files"] = d["dld_files"]
        else:
            keep.append(d)
    out = pd.concat([log_deps, pd.DataFrame(keep)], ignore_index=True) if keep else log_deps
    return gpd.GeoDataFrame(out, geometry="geometry", crs=WGS84).sort_values(["serial", "start"]).reset_index(drop=True)
