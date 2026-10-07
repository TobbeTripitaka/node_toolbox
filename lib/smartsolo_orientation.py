"""
smartsolo_orientation
=====================

Tools for the node's built-in eCompass and tilt sensor, written so they are
useful whatever the compass reading turns out to mean.

What the log contains
---------------------
In ``[GPS]`` synchronisation records (every ~10 min) and in the boot block:

``eCompass North``  heading in degrees (0-360). Most likely the magnetic
                    azimuth of the node's N arrow (X axis), but this is not
                    documented and depends on the compass having been
                    calibrated (script setting "ECompass Calibration Mode").
``Tilted Angle``    total tilt from vertical, = sqrt(roll² + pitch²)
``Roll Angle``      rotation about one horizontal axis
``Pitch Angle``     rotation about the other horizontal axis

Because the meaning of the heading is uncertain, everything here keeps the
raw reading and offers *interpretations* separately:

``heading_raw``          as logged (circular mean)
``heading_true_if_mag``  raw + IGRF declination (if the reading is magnetic)
``heading_used``         what you decide to use (see ``orientation_table``)

Circular statistics are used throughout so 359° and 1° average to 0°, not 180°.

Main functions
--------------
compass_records(df)                 tidy time series of heading / tilt / roll / pitch
circular_mean(deg), circular_std(deg)
orientation_table(df_or_logs, deps) one row per deployment: heading stats, rotation
                                    rate, tilt, boot values, declination, field
                                    strength, QC flags
plot_orientation(df, serial)        heading and tilt against time
set_channel_azimuths(inv, table)    write chosen azimuths into a StationXML Inventory
rotate_to_ne(st, north_azimuth)     rotate horizontal data to geographic N/E

Tobias Stål 2024-2026
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

__all__ = [
    "circular_mean",
    "circular_std",
    "circular_diff",
    "compass_records",
    "orientation_table",
    "plot_orientation",
    "igrf_field",
    "set_channel_azimuths",
    "rotate_to_ne",
    "TILT_SPEC_HORIZONTAL",
    "TILT_SPEC_VERTICAL",
]

# DT-SOLO distortion specs are given for 0-3° (horizontal) and 0-10° (vertical) tilt
TILT_SPEC_HORIZONTAL = 3.0
TILT_SPEC_VERTICAL = 10.0


# --------------------------------------------------------------------------- #
# Circular statistics
# --------------------------------------------------------------------------- #
def circular_mean(deg) -> float:
    """Mean direction in degrees [0, 360)."""
    a = np.radians(np.asarray(deg, float))
    a = a[~np.isnan(a)]
    if a.size == 0:
        return np.nan
    m = float(np.degrees(np.arctan2(np.sin(a).mean(), np.cos(a).mean())) % 360)
    return 0.0 if m > 360 - 1e-9 else m


def circular_std(deg) -> float:
    """Circular standard deviation in degrees (≈ ordinary std for small spread)."""
    a = np.radians(np.asarray(deg, float))
    a = a[~np.isnan(a)]
    if a.size == 0:
        return np.nan
    r = np.hypot(np.sin(a).mean(), np.cos(a).mean())
    return float(np.degrees(np.sqrt(-2 * np.log(max(r, 1e-12)))))


def circular_diff(a, b):
    """Signed smallest difference a - b in degrees, in (-180, 180]."""
    return (np.asarray(a, float) - np.asarray(b, float) + 180) % 360 - 180


def _unwrap_deg(deg):
    return np.degrees(np.unwrap(np.radians(np.asarray(deg, float))))


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #
def compass_records(df: pd.DataFrame) -> pd.DataFrame:
    """
    Tidy compass/tilt time series from a ``smartsolo_log`` DataFrame:
    columns ``serial, session, heading_raw, heading_unwrapped, tilt, roll,
    pitch, tilt_check`` (``tilt_check`` = sqrt(roll²+pitch²) - tilt, should be
    ~0), indexed by time.
    """
    cols = {"ecompass_north": "heading_raw", "tilted_angle": "tilt",
            "roll_angle": "roll", "pitch_angle": "pitch"}
    have = [c for c in cols if c in df]
    if not have:
        return pd.DataFrame()
    g = df[df["ecompass_north"].notna()] if "ecompass_north" in df else df.iloc[0:0]
    out = g[["serial", "session"] + have].rename(columns=cols).copy()
    out["heading_raw"] = out["heading_raw"] % 360      # V1.0.5 firmware logs -180..180
    parts = []
    for _, sub in out.groupby(["serial", "session"], sort=False):
        sub = sub.sort_index().copy()
        sub["heading_unwrapped"] = _unwrap_deg(sub["heading_raw"])
        parts.append(sub)
    out = pd.concat(parts) if parts else out
    if {"roll", "pitch", "tilt"} <= set(out):
        out["tilt_check"] = np.hypot(out["roll"], out["pitch"]) - out["tilt"]
    return out


# --------------------------------------------------------------------------- #
# Magnetic field
# --------------------------------------------------------------------------- #
def igrf_field(lat, lon, time, elevation_m: float = 0.0) -> dict:
    """
    IGRF main field at a point: ``declination`` (deg, east +), ``inclination``
    (deg, down +), ``horizontal_nT``, ``total_nT``. Needs ``ppigrf``.

    A weak horizontal field (small ``horizontal_nT``, e.g. near the magnetic
    poles) makes compass headings less reliable.
    """
    import ppigrf

    t = pd.Timestamp(time)
    t = t.tz_convert("UTC").tz_localize(None) if t.tzinfo else t
    be, bn, bu = (float(np.ravel(v)[0]) for v in
                  ppigrf.igrf(lon, lat, elevation_m / 1000.0, t.to_pydatetime()))
    h = float(np.hypot(be, bn))
    return dict(declination=float(np.degrees(np.arctan2(be, bn))),
                inclination=float(np.degrees(np.arctan2(-bu, h))),
                horizontal_nT=h, total_nT=float(np.sqrt(h * h + bu * bu)))


# --------------------------------------------------------------------------- #
# Orientation table
# --------------------------------------------------------------------------- #
def orientation_table(df: pd.DataFrame, deps: pd.DataFrame | None = None,
                      settle: str = "6h", unstable_std_deg: float = 5.0,
                      with_igrf: bool = True, device_info: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    One row per deployment (serial + session) summarising the compass and
    tilt readings.

    Parameters
    ----------
    df : DataFrame from ``smartsolo_log.read_log(s)``
    deps : optional deployment table (``smartsolo_locate.build_deployments``)
        for coordinates and deployment ids (needed for IGRF).
    settle : str
        Ignore readings this long after the first reading when computing the
        "settled" heading (installation disturbance, snow settling).
    unstable_std_deg : float
        Flag ``heading_unstable`` when the circular std exceeds this.
    with_igrf : bool
        Add declination / field strength (requires ``ppigrf``; skipped with a
        warning if not installed).
    device_info : optional, from ``smartsolo_log.read_device_info``
        Adds the boot-time readings (``Booting eCompass North`` ...).

    Columns
    -------
    heading_raw (circular mean of all), heading_settled (after ``settle``),
    heading_first / heading_last (median of first/last day), heading_std,
    heading_range, rotation_deg (last - first), rotation_deg_per_day (linear
    fit), tilt / roll / pitch (median), tilt_change, n_readings, plus flags:
    heading_unstable, tilt_over_horizontal_spec (>3°),
    tilt_over_vertical_spec (>10°), and if IGRF is available:
    declination, inclination, horizontal_nT, heading_true_if_mag.
    """
    rec = compass_records(df)
    if rec.empty:
        return pd.DataFrame()
    rows = []
    for (serial, session), g in rec.groupby(["serial", "session"]):
        g = g.sort_index()
        t0, t1 = g.index[0], g.index[-1]
        first = g[g.index <= t0 + pd.Timedelta("1D")]
        last = g[g.index >= t1 - pd.Timedelta("1D")]
        settled = g[g.index >= t0 + pd.Timedelta(settle)]
        days = (g.index - t0) / pd.Timedelta("1D")
        slope = (np.polyfit(days, g["heading_unwrapped"], 1)[0]
                 if len(g) > 2 and days.max() > 0.5 else np.nan)
        row = dict(serial=serial, session=int(session), start=t0, end=t1, n_readings=len(g),
                   heading_raw=circular_mean(g["heading_raw"]),
                   heading_settled=circular_mean(settled["heading_raw"]) if len(settled) else np.nan,
                   heading_first=circular_mean(first["heading_raw"]),
                   heading_last=circular_mean(last["heading_raw"]),
                   heading_std=circular_std(g["heading_raw"]),
                   heading_range=float(np.ptp(g["heading_unwrapped"])),
                   rotation_deg=float(circular_diff(circular_mean(last["heading_raw"]),
                                                    circular_mean(first["heading_raw"]))),
                   rotation_deg_per_day=float(slope),
                   tilt=float(g["tilt"].median()) if "tilt" in g else np.nan,
                   roll=float(g["roll"].median()) if "roll" in g else np.nan,
                   pitch=float(g["pitch"].median()) if "pitch" in g else np.nan,
                   tilt_change=float(last["tilt"].median() - first["tilt"].median()) if "tilt" in g else np.nan)
        row["heading_unstable"] = row["heading_std"] > unstable_std_deg
        row["tilt_over_horizontal_spec"] = row["tilt"] > TILT_SPEC_HORIZONTAL
        row["tilt_over_vertical_spec"] = row["tilt"] > TILT_SPEC_VERTICAL
        rows.append(row)
    out = pd.DataFrame(rows)

    if device_info is not None and len(device_info):
        di = device_info.reset_index()
        keep = {"booting_ecompass_north": "boot_heading", "booting_tilted_angle": "boot_tilt",
                "booting_roll_angle": "boot_roll", "booting_pitch_angle": "boot_pitch"}
        di = di[["serial_number", "boot_no"] + [c for c in keep if c in di]].rename(
            columns={"serial_number": "serial", "boot_no": "session", **keep})
        di["serial"] = di["serial"].astype(str)
        di = di.drop_duplicates(["serial", "session"])
        out = out.merge(di, on=["serial", "session"], how="left")
        if "boot_heading" in out:
            out["boot_vs_settled_deg"] = circular_diff(out["boot_heading"], out["heading_settled"])

    if deps is not None and len(deps):
        d = deps[["serial", "session", "deployment_id", "latitude", "longitude", "elevation"]].copy()
        d["serial"] = d["serial"].astype(str)
        out = d.merge(out, on=["serial", "session"], how="inner")
        if with_igrf:
            try:
                f = [igrf_field(r.latitude, r.longitude, r.start, r.elevation or 0.0)
                     for r in out.itertuples()]
                out = pd.concat([out, pd.DataFrame(f, index=out.index)], axis=1)
                out["heading_true_if_mag"] = (out["heading_settled"] + out["declination"]) % 360
            except ImportError:
                warnings.warn("ppigrf not installed - declination columns are NaN (pip install ppigrf)")
                for c in ("declination", "inclination", "horizontal_nT", "total_nT", "heading_true_if_mag"):
                    out[c] = np.nan
    return out


# --------------------------------------------------------------------------- #
# Plot
# --------------------------------------------------------------------------- #
def plot_orientation(df: pd.DataFrame, serial=None, axes=None):
    """Heading (unwrapped), tilt, roll and pitch against time for one or all nodes."""
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    rec = compass_records(df)
    if serial is not None:
        rec = rec[rec["serial"] == str(serial)]
    if axes is None:
        _, axes = plt.subplots(3, 1, figsize=(11, 7), sharex=True)
    for (ser, ses), g in rec.groupby(["serial", "session"]):
        lab = f"{ser} s{ses}"
        axes[0].plot(g.index, g["heading_unwrapped"], ".", ms=2, label=lab)
        axes[1].plot(g.index, g["tilt"], ".", ms=2, label=lab)
        axes[2].plot(g.index, g["roll"], ".", ms=2, label=f"{lab} roll")
        axes[2].plot(g.index, g["pitch"], "x", ms=2, label=f"{lab} pitch")
    axes[0].set_ylabel("eCompass North (°)")
    axes[1].set_ylabel("tilt (°)")
    axes[1].axhline(TILT_SPEC_HORIZONTAL, color="r", ls=":", lw=1, label="3° horizontal spec")
    axes[2].set_ylabel("roll / pitch (°)")
    for a in axes:
        a.grid(alpha=.3)
        a.legend(fontsize=7, markerscale=4, loc="best")
    loc = mdates.AutoDateLocator()
    axes[-1].xaxis.set_major_locator(loc)
    axes[-1].xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc))
    return axes


# --------------------------------------------------------------------------- #
# Using an orientation
# --------------------------------------------------------------------------- #
def set_channel_azimuths(inv, table: pd.DataFrame, heading_col: str = "heading_used",
                         station_col: str = "station"):
    """
    Write horizontal channel azimuths into an ObsPy Inventory in place:
    N channel = heading, E channel = heading + 90 (keeping a 180° offset if
    the channel was already set to the opposite polarity, e.g. 180/270).

    ``table`` needs a station code column and the heading to use, e.g.
    ``orientation_table(...)`` plus your own ``heading_used`` /
    ``station`` columns. Returns the inventory.
    """
    lookup = dict(zip(table[station_col], table[heading_col]))
    for net in inv:
        for sta in net:
            h = lookup.get(sta.code)
            if h is None or pd.isna(h):
                continue
            for cha in sta.channels:
                comp = cha.code[-1]
                if comp not in "NE12":
                    continue
                base = h if comp in "N1" else h + 90
                flip = 180.0 if abs(circular_diff(cha.azimuth or 0, 0 if comp in "N1" else 90)) > 90 else 0.0
                cha.azimuth = float((base + flip) % 360)
    return inv


def rotate_to_ne(st, north_azimuth: float, n_comp: str = "N", e_comp: str = "E"):
    """
    Rotate the horizontal traces of a Stream in place so that N/E point to
    geographic north/east, given the azimuth of the node's N channel
    (degrees clockwise from north). Z is untouched. Returns the Stream.
    """
    from obspy.signal.rotate import rotate2zne

    for sta in sorted({(tr.stats.network, tr.stats.station, tr.stats.location) for tr in st}):
        sel = st.select(network=sta[0], station=sta[1], location=sta[2])
        n = sel.select(component=n_comp)
        e = sel.select(component=e_comp)
        z = sel.select(component="Z")
        if not (len(n) == len(e) == 1 and len(z) == 1):
            warnings.warn(f"{'.'.join(sta)}: need exactly one Z, N and E trace - skipped")
            continue
        n, e, z = n[0], e[0], z[0]
        npts = min(n.stats.npts, e.stats.npts, z.stats.npts)
        zz, nn, ee = rotate2zne(z.data[:npts].astype(float), 0, -90,
                                n.data[:npts].astype(float), north_azimuth, 0,
                                e.data[:npts].astype(float), (north_azimuth + 90) % 360, 0)
        n.data, e.data = nn, ee
        for tr in (n, e):
            tr.stats.processing = list(getattr(tr.stats, "processing", [])) + [
                f"rotate_to_ne(north_azimuth={north_azimuth:.2f})"]
    return st
