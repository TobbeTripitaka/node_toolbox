"""
smartsolo_batch
===============

Batch conversion of SmartSolo harvests (DLD and/or node MiniSEED + logs)
to an SDS archive with a harvest database, or to windowed files.

>>> import smartsolo_batch as sb
>>> summary = sb.run_batch(["/data/harvest_2026_10"], "project.yaml")

Default output (``output.layout: sds``)::

    <root>/2026/XX/10029/DPZ.D/XX.10029..DPZ.D.2026.279    daily MiniSEED
    <root>/harvest.sqlite      what was converted, from which file, with which settings
    <root>/stations.xml        StationXML of every deployment in the archive
    <root>/stations.csv        station table
    <root>/settings_used.yaml  settings of the last run

Behaviour
---------
* Every source file is recorded in ``harvest.sqlite`` with its size,
  modification time, settings key (timing, polarity, gain, encoding) and
  the time span written. A rerun skips files already harvested with the
  same settings, so new harvests can be added to the same archive. Files
  harvested with other *timing* settings (e.g. the earlier -1 s TOW offset)
  are redone and the affected day files rewritten.
* Day files are merged: new data replace the samples they overlap, other
  data in the day file are kept. Files are written to ``.part`` and moved
  in place atomically; a crash never leaves a half-written day file.
* Conversion runs in parallel processes, one per station id (deployments
  writing the same SDS channel are processed in sequence by one worker).
  Data are read and written one day and one component at a time, so
  memory use does not grow with the deployment length.
* Nothing is ever deleted outside the day files being updated.

``output.layout: files`` writes one file per window chunk instead
(MiniSEED or SEG-Y, see ``smartsolo_waveforms.extract_waveforms``).

Design after the harvest script (SDS writing, harvest database,
parallel workers), re-implemented on the node_toolbox readers.

Authors: Tobias Stål (UTAS)
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
import sys
import warnings
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

__all__ = ["run_batch", "HarvestDB", "sds_path", "write_sds"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT, started TEXT, finished TEXT,
    inputs TEXT, settings TEXT, settings_key TEXT, n_jobs INTEGER, n_errors INTEGER);
CREATE TABLE IF NOT EXISTS files (
    path TEXT, size INTEGER, mtime_ns INTEGER, serial TEXT, component TEXT, format TEXT,
    settings_key TEXT, time_cal TEXT, starttime TEXT, endtime TEXT, sampling_rate REAL,
    deployment_id TEXT, seed_id TEXT, run_id INTEGER, harvested_at TEXT,
    PRIMARY KEY (path, deployment_id));
CREATE TABLE IF NOT EXISTS outputs (
    sds_file TEXT, seed_id TEXT, day TEXT, starttime TEXT, endtime TEXT, npts INTEGER,
    deployment_id TEXT, run_id INTEGER, written_at TEXT);
CREATE TABLE IF NOT EXISTS deployments (
    deployment_id TEXT PRIMARY KEY, serial TEXT, row_json TEXT, run_id INTEGER);
CREATE TABLE IF NOT EXISTS errors (run_id INTEGER, deployment_id TEXT, message TEXT);
"""


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


class HarvestDB:
    """SQLite record of what has been converted (one writer: the main process)."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.path)
        self.con.executescript(_SCHEMA)

    def close(self):
        self.con.commit()
        self.con.close()

    def start_run(self, inputs, cfg, key) -> int:
        cur = self.con.execute("INSERT INTO runs (started, inputs, settings, settings_key) VALUES (?,?,?,?)",
                               (_now(), json.dumps([str(i) for i in inputs]), json.dumps(cfg, default=str), key))
        self.con.commit()
        return cur.lastrowid

    def finish_run(self, run_id, n_jobs, n_errors):
        self.con.execute("UPDATE runs SET finished=?, n_jobs=?, n_errors=? WHERE run_id=?",
                         (_now(), n_jobs, n_errors, run_id))
        self.con.commit()

    def file_status(self, path, size, mtime_ns, deployment_id, key, time_cal) -> str:
        """'new', 'done' (same file and settings), 'stale_timing' or 'changed'."""
        r = self.con.execute("SELECT size, mtime_ns, settings_key, time_cal FROM files "
                             "WHERE path=? AND deployment_id=?", (str(path), deployment_id)).fetchone()
        if r is None:
            return "new"
        if r[0] != size or r[1] != mtime_ns:
            return "changed"
        if r[3] != time_cal:
            return "stale_timing"
        return "done" if r[2] == key else "changed"

    def record(self, run_id, files, outputs, dep_row, errors):
        c = self.con
        c.executemany("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      [(f["path"], f["size"], f["mtime_ns"], f["serial"], f["component"], f["format"],
                        f["settings_key"], f["time_cal"], f["starttime"], f["endtime"],
                        f["sampling_rate"], f["deployment_id"], f["seed_id"], run_id, _now())
                       for f in files])
        c.executemany("INSERT INTO outputs VALUES (?,?,?,?,?,?,?,?,?)",
                      [(o["sds_file"], o["seed_id"], o["day"], o["starttime"], o["endtime"], o["npts"],
                        o["deployment_id"], run_id, _now()) for o in outputs])
        if dep_row is not None:
            c.execute("INSERT OR REPLACE INTO deployments VALUES (?,?,?,?)",
                      (dep_row["deployment_id"], dep_row["serial"], json.dumps(dep_row, default=str), run_id))
        c.executemany("INSERT INTO errors VALUES (?,?,?)", [(run_id, e[0], e[1]) for e in errors])
        c.commit()

    def deployments(self) -> pd.DataFrame:
        rows = [json.loads(r[0]) for r in self.con.execute("SELECT row_json FROM deployments")]
        return pd.DataFrame(rows)

    def table(self, name) -> pd.DataFrame:
        return pd.read_sql_query(f"SELECT * FROM {name}", self.con)


# --------------------------------------------------------------------------- #
# SDS
# --------------------------------------------------------------------------- #
def sds_path(root, seed_id: str, day) -> Path:
    """``root/YEAR/NET/STA/CHA.D/NET.STA.LOC.CHA.D.YEAR.DOY``."""
    net, sta, loc, cha = seed_id.split(".")
    d = pd.Timestamp(day)
    return (Path(root) / f"{d.year}" / net / sta / f"{cha}.D"
            / f"{net}.{sta}.{loc}.{cha}.D.{d.year}.{d.dayofyear:03d}")


def write_sds(st, root, encoding="STEIM2", reclen=4096, clear=None) -> list[dict]:
    """
    Write a Stream (one channel or several) into day files under ``root``,
    merging with existing files: existing samples inside the time span of the
    new data (or ``clear=(t0, t1)`` +- 5 s, a whole deployment redone with new
    timing settings) are replaced. Atomic (``.part`` + ``os.replace``). Returns one dict per day file.
    """
    from obspy import Stream, UTCDateTime, read

    out = []
    for seed_id in sorted({tr.id for tr in st}):
        sub = st.select(id=seed_id)
        t_first = min(tr.stats.starttime for tr in sub)
        t_last = max(tr.stats.endtime for tr in sub)
        day = pd.Timestamp(t_first.datetime).normalize()
        while day <= pd.Timestamp(t_last.datetime):
            d0 = UTCDateTime(day.to_pydatetime())
            d1 = d0 + 86400
            new = sub.slice(d0, d1 - 1e-6, nearest_sample=False)
            day_next = day + pd.Timedelta(days=1)
            if not new or not sum(tr.stats.npts for tr in new):
                day = day_next
                continue
            path = sds_path(root, seed_id, day)
            path.parent.mkdir(parents=True, exist_ok=True)
            c0 = min(tr.stats.starttime for tr in new)
            c1 = max(tr.stats.endtime for tr in new)
            if clear is not None:
                k0 = UTCDateTime(pd.Timestamp(clear[0]).to_pydatetime()) - 5.0
                k1 = UTCDateTime(pd.Timestamp(clear[1]).to_pydatetime()) + 5.0
                c0, c1 = min(c0, max(k0, d0)), max(c1, min(k1, d1 - 1e-6))
            merged = Stream()
            if path.exists():
                old = read(str(path), format="MSEED")
                for tr in old:
                    for a, b in ((tr.stats.starttime, c0 - 1e-6), (c1 + 1e-6, tr.stats.endtime)):
                        if b >= a:
                            piece = tr.slice(a, b, nearest_sample=False)
                            if piece.stats.npts:
                                merged.append(piece)
            merged += new.copy()
            enc = encoding
            for tr in merged:
                if enc.upper() in ("STEIM1", "STEIM2", "INT32") and tr.data.dtype.kind == "f":
                    enc = "FLOAT32"
            for tr in merged:
                tr.data = tr.data.astype(np.float32 if enc == "FLOAT32" else np.int32)
                tr.stats.pop("mseed", None)
            merged.sort()
            merged.merge(method=1)
            merged = merged.split()
            tmp = path.with_name(path.name + ".part")
            merged.write(str(tmp), format="MSEED", encoding=enc, reclen=reclen)
            os.replace(tmp, path)
            out.append(dict(sds_file=str(path), seed_id=seed_id, day=str(day.date()),
                            starttime=str(min(tr.stats.starttime for tr in new)),
                            endtime=str(max(tr.stats.endtime for tr in new)),
                            npts=int(sum(tr.stats.npts for tr in new))))
            day = day_next
    return out


# --------------------------------------------------------------------------- #
# Worker
# --------------------------------------------------------------------------- #
def _station_worker(args):
    """Process all jobs of one station id (in order). Runs in a worker process."""
    import smartsolo_config as sc
    import smartsolo_waveforms as sw

    cfg, jobs = args
    sc.apply_config(cfg)
    d, o = cfg["data"], cfg["output"]
    results = []
    for job in jobs:
        dep = pd.DataFrame([job["dep"]])
        for c in ("start", "end", "sel_start", "sel_end"):
            if c in dep:
                dep[c] = pd.to_datetime(dep[c], utc=True)
        index = pd.DataFrame(job["index"])
        index["starttime"] = pd.to_datetime(index["starttime"], utc=True)
        index["endtime"] = pd.to_datetime(index["endtime"], utc=True)
        index["serial"] = index["serial"].astype(str)
        outputs, errors = [], []
        t0, t1 = pd.Timestamp(job["t0"]), pd.Timestamp(job["t1"])
        clear = (job["t0"], job["t1"]) if job["redo"] else None
        try:
            with warnings.catch_warnings(record=True) as wl:
                warnings.simplefilter("always")
                days = pd.date_range(t0.normalize(), t1, freq="1D")
                for day in days:
                    a, b = max(t0, day), min(t1, day + pd.Timedelta(days=1))
                    if b <= a:
                        continue
                    kw = dict(mapping=job["mapping"], start=a, end=b, default_network=cfg["metadata"]["network"],
                              default_location=cfg["metadata"]["location"],
                              invert_polarity=d["invert_polarity"], remove_gain=d["remove_gain"],
                              encoding=d["encoding"], components=d["components"])
                    if o["layout"] == "sds":
                        st = sw.extract_waveforms(dep, index, **kw)
                        if len(st):
                            outputs += write_sds(st, o["root"], d["encoding"], d["record_length"],
                                                 clear=clear)
                    else:
                        st = sw.extract_waveforms(dep, index, out_dir=o["root"], chunk=o["chunk"],
                                                  out_format=o["format"], return_stream=False, **kw)
                        outputs += [dict(sds_file=str(p), seed_id="", day=str(day.date()),
                                         starttime=str(a), endtime=str(b), npts=0)
                                    for p in st.written_files]
                msgs = sorted({str(w.message) for w in wl})
        except Exception as exc:  # noqa: BLE001 - one bad deployment must not stop the batch
            errors.append((job["dep"]["deployment_id"], f"{type(exc).__name__}: {exc}"))
            msgs = []
        for x in outputs:
            x["deployment_id"] = job["dep"]["deployment_id"]
        results.append(dict(job=job, outputs=outputs, errors=errors, warnings=msgs))
    return results


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
def _deployments(inputs, cfg):
    import smartsolo_locate as loc

    dcfg = cfg["deployments"]
    logs = [p for r in inputs for p in (loc.find_logs(r) if Path(r).is_dir() else [])]
    deps = None
    if logs:
        deps = loc.build_deployments(logs, n_stable=dcfg["n_stable"], stable_tol_m=dcfg["stable_tol_m"])
    if dcfg["use_dld_positions"]:
        dlds = [p for r in inputs for p in (Path(r).rglob("*") if Path(r).is_dir() else [Path(r)])
                if p.suffix.lower() == ".dld"]
        if dlds:
            dd = loc.build_deployments_from_dld(dlds, n_stable=dcfg["n_stable"],
                                                stable_tol_m=dcfg["stable_tol_m"])
            deps = dd if deps is None else loc.combine_deployments(deps, dd)
    if deps is None or deps.empty:
        raise ValueError(f"no deployments found in {inputs} (no logs and no DLD files)")
    s = cfg["selection"]
    poly = loc.load_polygon(s["polygon"]) if s["polygon"] else None
    return loc.select_deployments(deps, point=tuple(s["point"]) if s["point"] else None,
                                  radius_km=s["radius_km"], polygon=poly, start=s["start"], end=s["end"])


def run_batch(inputs, config=None, workers=None, dry_run: bool = False, verbose: bool = True,
              **overrides) -> pd.DataFrame:
    """
    Convert everything under ``inputs`` (folder(s) with harvests: DLD files,
    node MiniSEED and DigiSolo logs) according to ``config`` (YAML/JSON
    path, dict or None = defaults; ``section__key=value`` overrides).

    Returns a summary DataFrame, one row per deployment, with ``status``
    (``converted``, ``skipped`` (already harvested), ``redone`` (timing
    settings changed), ``error``, ``planned`` for ``dry_run=True``),
    the window and the number of day files written.
    """
    import smartsolo_config as sc
    import smartsolo_locate as loc
    import smartsolo_waveforms as sw

    cfg = sc.apply_config(sc.load_config(config, **overrides))
    inputs = [inputs] if isinstance(inputs, (str, os.PathLike)) else list(inputs)
    inputs = [Path(i).resolve() for i in inputs]       # absolute paths in the database
    key = sc.settings_key(cfg)
    import smartsolo_dld as dld
    tcal = dld.time_calibration()
    root = Path(cfg["output"]["root"])
    root.mkdir(parents=True, exist_ok=True)
    db = HarvestDB(root / "harvest.sqlite")
    workers = workers or cfg["batch"]["workers"]
    mapping = sw.load_mapping(cfg["metadata"]["mapping"]) if cfg["metadata"]["mapping"] else None

    sel = _deployments(inputs, cfg)
    index = sw.index_waveforms(inputs, mapping=mapping)
    rows, by_station = [], {}
    for _, dep in sel.iterrows():
        t0, t1 = dep.get("sel_start", dep["start"]), dep.get("sel_end", dep["end"])
        files = index[(index["serial"] == str(dep["serial"])) & (index["endtime"] >= t0)
                      & (index["starttime"] <= t1)]
        if cfg["data"]["components"]:
            files = files[files["component"].map(lambda c: sw.DEFAULT_COMPONENT_MAP.get(c, c))
                          .isin([c.upper() for c in cfg["data"]["components"]])]
        if len(files) and t1 >= files["endtime"].max():     # end is exclusive: keep the last sample
            t1 = files["endtime"].max() + pd.Timedelta(seconds=1 / float(files["sampling_rate"].max()))
        row = dict(deployment_id=dep["deployment_id"], serial=str(dep["serial"]), start=t0, end=t1,
                   n_files=len(files), status="", day_files=0, message="")
        if files.empty:
            row["status"] = "no data"
            rows.append(row)
            continue
        gains = {float(dep[c]) for c in ("channel_1_gain", "channel_2_gain", "channel_3_gain")
                 if c in dep and pd.notna(dep[c])}
        if len(gains) > 1:      # Ch1-3 -> X/Y/Z mapping undocumented: don't guess (as the harvest script)
            row["status"], row["message"] = "error", f"channel gains differ {sorted(gains)}"
            rows.append(row)
            continue
        status = {db.file_status(f["path"], int(f["size"]), Path(f["path"]).stat().st_mtime_ns,
                                 dep["deployment_id"], key, tcal) for _, f in files.iterrows()}
        if status == {"done"} and cfg["batch"]["skip_harvested"]:
            row["status"] = "skipped"
            rows.append(row)
            continue
        redo = "stale_timing" in status
        if redo and not cfg["batch"]["redo_stale_timing"]:
            row["status"], row["message"] = "stale (not redone)", "harvested with other timing settings"
            rows.append(row)
            continue
        net, sta, locc, _ = sw.seed_codes(dep["serial"], t0, mapping, 250, "Z", None,
                                          cfg["metadata"]["network"], dep.get("device_type"))
        dep_row = {k: (str(v) if isinstance(v, pd.Timestamp) else v) for k, v in dep.items()
                   if k != "geometry"}
        dep_row = {k: (None if (isinstance(v, float) and np.isnan(v)) else v) for k, v in dep_row.items()}
        dep_row["sel_start"], dep_row["sel_end"] = str(t0), str(t1)
        job = dict(dep=dep_row, index=files.astype({"starttime": str, "endtime": str}).to_dict("records"),
                   t0=str(t0), t1=str(t1), redo=redo, mapping=mapping)
        by_station.setdefault(f"{net}.{sta}.{locc}", []).append(job)
        row["status"] = "planned" if dry_run else ("redone" if redo else "converted")
        rows.append(row)
    summary = pd.DataFrame(rows)
    if dry_run or not by_station:
        db.close()
        return summary

    run_id = db.start_run(inputs, cfg, key)
    tasks = [(cfg, jobs) for jobs in by_station.values()]
    n_err = 0
    if workers and workers > 1 and len(tasks) > 1:
        try:
            with ProcessPoolExecutor(max_workers=min(workers, len(tasks))) as ex:
                results = [r for rs in ex.map(_station_worker, tasks) for r in rs]
        except (OSError, RuntimeError) as exc:     # e.g. no process spawning possible
            warnings.warn(f"parallel processing failed ({exc}); running serially")
            results = [r for t in tasks for r in _station_worker(t)]
    else:
        results = [r for t in tasks for r in _station_worker(t)]
    for res in results:
        job = res["job"]
        dep_id = job["dep"]["deployment_id"]
        i = summary.index[summary["deployment_id"] == dep_id][0]
        if res["errors"]:
            n_err += 1
            summary.loc[i, "status"] = "error"
            summary.loc[i, "message"] = "; ".join(e[1] for e in res["errors"])
            db.record(run_id, [], [], None, res["errors"])
            continue
        summary.loc[i, "day_files"] = len(res["outputs"])
        if res["warnings"]:
            summary.loc[i, "message"] = "; ".join(res["warnings"])[:500]
        files = [dict(path=f["path"], size=int(f["size"]), mtime_ns=Path(f["path"]).stat().st_mtime_ns,
                      serial=str(f["serial"]), component=f["component"], format=f["format"],
                      settings_key=key, time_cal=tcal, starttime=f["starttime"], endtime=f["endtime"],
                      sampling_rate=float(f["sampling_rate"]), deployment_id=dep_id,
                      seed_id="") for f in job["index"]]
        db.record(run_id, files, res["outputs"], job["dep"], [])
        if verbose:
            print(f"{dep_id}: {summary.loc[i, 'status']}, {len(res['outputs'])} files")
    db.finish_run(run_id, len(results), n_err)

    # metadata for everything in the archive
    alld = db.deployments()
    if len(alld) and (cfg["output"]["stationxml"] or cfg["output"]["station_table"]):
        for c in ("start", "end", "sel_start", "sel_end", "fix_time"):
            if c in alld:
                alld[c] = pd.to_datetime(alld[c], utc=True, errors="coerce")
        import geopandas as gpd
        alld = gpd.GeoDataFrame(alld, geometry=gpd.points_from_xy(alld["longitude"], alld["latitude"]),
                                crs="EPSG:4326")
        if cfg["output"]["stationxml"]:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                inv = sw.build_inventory(alld, mapping=mapping, response=cfg["metadata"]["response"],
                                         polarity_inverted=cfg["data"]["invert_polarity"],
                                         gain_db=0.0 if cfg["data"]["remove_gain"] else "auto",
                                         default_network=cfg["metadata"]["network"])
            inv.write(str(root / "stations.xml"), format="STATIONXML")
        if cfg["output"]["station_table"]:
            loc.export_stations(alld, root / "stations.csv")
    try:
        import yaml
        (root / "settings_used.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    except ImportError:
        (root / "settings_used.json").write_text(json.dumps(cfg, indent=2, default=str))
    db.close()
    return summary


def main(argv=None):
    """Command line: ``python lib/smartsolo_batch.py settings.yaml HARVEST_DIR [...] [--dry-run]``."""
    import argparse

    ap = argparse.ArgumentParser(description="Convert SmartSolo harvests to an SDS archive.")
    ap.add_argument("settings", help="YAML/JSON settings file ('-' for defaults)")
    ap.add_argument("inputs", nargs="+", help="harvest folders (DLD/MiniSEED + DigiSolo.LOG)")
    ap.add_argument("--dry-run", action="store_true", help="only show what would be converted")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--template", action="store_true",
                    help="write a settings template to SETTINGS and exit")
    a = ap.parse_args(argv)
    import smartsolo_config as sc
    if a.template:
        print("wrote", sc.write_template(a.settings))
        return
    summary = run_batch(a.inputs, None if a.settings == "-" else a.settings,
                        workers=a.workers, dry_run=a.dry_run)
    with pd.option_context("display.width", 200, "display.max_rows", 500):
        print(summary.drop(columns=["start", "end"]).to_string(index=False))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()
