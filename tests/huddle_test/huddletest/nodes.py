"""Mounted nodes: find them, apply a batch's scripts, collect their data, and
keep track of which batch is running (``state.json`` in the data dir).

Nothing on a node is ever deleted, and only sct_par.xml (and sct_par_b.xml)
are ever written to it. Before a script is replaced, the node's
current scripts are backed up to ``<data_dir>/backups/``. Collected files are
verified by size and SHA-256.
"""

from __future__ import annotations

import datetime as dt
import glob
import hashlib
import json
import shutil
from pathlib import Path

import pandas as pd

from . import config as hc

SCRIPT_FILES = ("sct_par.xml", "sct_par_b.xml")
DATA_PATTERNS = ("*.DLD", "*.dld", "*.MiniSeed", "*.miniseed", "*.LOG", "*.log", "*.TXT", "device.ini",
                 "sct_par*.xml", "SCT_INT.XML", "PULSE_*.WAV", "guardfile.db")


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #
def load_state(cfg) -> dict:
    f = cfg["data_dir"] / "state.json"
    if f.exists():
        return json.loads(f.read_text())
    return {"current_batch": None, "batches": {}}


def save_state(cfg, state):
    cfg["data_dir"].mkdir(parents=True, exist_ok=True)
    f = cfg["data_dir"] / "state.json"
    tmp = f.with_suffix(".part")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(f)


def start_batch(cfg, batch: int, note: str = "", force: bool = False) -> dict:
    state = load_state(cfg)
    cur = state.get("current_batch")
    if not force and cur is not None and cur != batch and not state["batches"].get(str(cur), {}).get("collected_complete"):
        raise RuntimeError(f"batch {cur} has not been collected completely - run 'collect' first "
                           "(or 'start --force')")
    state["current_batch"] = batch
    state["batches"].setdefault(str(batch), {"started": _now(), "note": note, "units": {}})
    save_state(cfg, state)
    return state


# --------------------------------------------------------------------------- #
# Nodes
# --------------------------------------------------------------------------- #
def mount_points(cfg) -> list[Path]:
    """Folders where nodes may appear: ``mount_globs`` plus, on Windows, the
    drive letters D: to Z: (A:-C: are never touched)."""
    import os
    pts = []
    for pat in cfg["mount_globs"]:
        pts += [Path(p) for p in glob.glob(str(pat))]
    if os.name == "nt":
        pts += [Path(f"{c}:/") for c in "DEFGHIJKLMNOPQRSTUVWXYZ" if Path(f"{c}:/").exists()]
    return pts


def is_node(p: Path) -> bool:
    """A SmartSolo node seen as a USB drive: ``device.ini`` with the node's
    serial, or ``DigiSolo.LOG`` next to ``sct_par.xml``."""
    try:
        return p.is_dir() and ((p / "device.ini").exists()
                               or ((p / "DigiSolo.LOG").exists() and (p / "sct_par.xml").exists()))
    except OSError:
        return False


def log_firmware(log: Path) -> tuple[str, str]:
    """Firmware and boot RTC of the LAST boot in ``DigiSolo.LOG`` ([DeviceInfo]).

    device.ini is not updated when firmware is changed (seen 7 Oct 2026: three
    nodes downgraded to V1.0.5.6kp, V1.0.7.8bke and V1.0.8.1be still had
    V1.1.4.2be in device.ini), so the log is the record of what is running.
    """
    import re
    try:
        txt = Path(log).read_text(errors="replace")
    except OSError:
        return "", ""
    fws = re.findall(r"Firmware Version\s*=\s*\"?([^\"\r\n]+)", txt)
    rtc = re.findall(r"Boot RTC\s*=\s*\"?([^\"\r\n]+)", txt)
    return (fws[-1].strip() if fws else ""), (rtc[-1].strip() if rtc else "")


def find_nodes(cfg, roots=None) -> pd.DataFrame:
    """Connected nodes, from the mount points or the given ``roots`` (each
    searched itself and one level down). Columns: path, serial, firmware (of
    the last boot in the log, else device.ini), firmware_ini, last_boot,
    firmware_mismatch, unit (from units.csv), n_data (DLD/MiniSEED files),
    error."""
    import smartsolo_node as sn

    cands = []
    for p in ([Path(r) for pat in roots for r in glob.glob(str(pat))] if roots else mount_points(cfg)):
        if is_node(p):
            cands.append(p)
        elif p.is_dir():
            try:
                cands += [q for q in p.iterdir() if is_node(q)]
            except OSError:
                pass
    s2u = hc.serial_to_unit(cfg)
    rows = []
    for p in sorted(set(cands)):
        ini, err = {}, ""
        if (p / "device.ini").exists():
            try:
                ini = sn.read_device_ini(p / "device.ini")
            except Exception as exc:  # noqa: BLE001
                err = f"device.ini: {exc}"
        serial = str(ini.get("sn", "")).strip()
        fw_ini = str(ini.get("firmwareVersion", "")).strip()
        fw_log, boot = log_firmware(p / "DigiSolo.LOG")
        n_data = sum(1 for f in p.rglob("*") if f.suffix.lower() in (".dld", ".miniseed", ".mseed"))
        rows.append(dict(path=str(p), serial=serial, firmware=fw_log or fw_ini, firmware_ini=fw_ini,
                         last_boot=boot, firmware_mismatch=bool(fw_log and fw_ini and fw_log != fw_ini),
                         unit=s2u.get(serial, ""), n_data=n_data, error=err or ("" if serial else "no serial")))
    return pd.DataFrame(rows, columns=["path", "serial", "firmware", "firmware_ini", "last_boot",
                                       "firmware_mismatch", "unit", "n_data", "error"])


def collected_hashes(cfg, serial: str) -> dict:
    """sha256 -> batch of every file already collected from this node."""
    out = {}
    for f in (cfg["data_dir"] / "raw").glob(f"batch_*/*_{serial}/collect_manifest.csv"):
        try:
            d = pd.read_csv(f, dtype=str)
        except Exception:  # noqa: BLE001
            continue
        b = f.parents[1].name
        out.update({h: b for h in d.get("sha256", [])})
    return out


def uncollected_data(cfg, node_path, serial) -> list[str]:
    """DLD/MiniSEED files on a node that are in no collect manifest (by name and size).
    Applying a new script is refused while these exist, unless overridden."""
    seen = set()
    for f in (cfg["data_dir"] / "raw").glob(f"batch_*/*_{serial}/collect_manifest.csv"):
        try:
            d = pd.read_csv(f, dtype=str)
            seen |= set(zip(d["file"], d["bytes"]))
        except Exception:  # noqa: BLE001
            pass
    out = []
    root = Path(node_path)
    for f in root.rglob("*"):
        if f.suffix.lower() in (".dld", ".miniseed", ".mseed"):
            if (f.relative_to(root).as_posix(), str(f.stat().st_size)) not in seen:
                out.append(f.relative_to(root).as_posix())
    return sorted(out)


def _sha(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while b := fh.read(chunk):
            h.update(b)
    return h.hexdigest()


def apply_batch(cfg, batch: int, nodes: pd.DataFrame | None = None, dry_run: bool = True,
                check_firmware: bool = True, allow_uncollected: bool = False) -> pd.DataFrame:
    """Copy ``xml/<row_id>.xml`` of ``batch`` to each mounted node as
    ``sct_par.xml`` (and ``sct_par_b.xml`` if the node has one). Nodes are
    matched by serial through ``units.csv``. ``dry_run=True`` (default) only
    reports what would happen."""
    m = hc.load_matrix(cfg)
    rows = m[m["batch"] == batch].set_index("unit")
    nodes = find_nodes(cfg) if nodes is None else nodes
    state = load_state(cfg)
    out = []
    for n in nodes.itertuples():
        rec = dict(path=n.path, serial=n.serial, unit=n.unit, row_id="", action="", message="")
        if not n.unit:
            rec.update(action="skip", message="serial not in units.csv")
        elif n.unit not in rows.index:
            rec.update(action="skip", message=f"unit {n.unit} not in batch {batch}")
        else:
            r = rows.loc[n.unit]
            rec["row_id"] = r.row_id
            xml = Path(cfg["xml_dir"]) / f"{r.row_id}.xml"
            if not xml.exists():
                rec.update(action="error", message=f"{xml.name} missing - run 'make-xml'")
            elif check_firmware and n.firmware and n.firmware != r.firmware:
                rec.update(action="error", message=f"firmware {n.firmware} on node, {r.firmware} planned")
            elif not allow_uncollected and (left := uncollected_data(cfg, n.path, n.serial)):
                rec.update(action="error", message=f"{len(left)} data file(s) on the node not collected yet "
                                                   f"(e.g. {left[0]}) - collect them first")
            else:
                rec["action"] = "would apply" if dry_run else "applied"
                if not dry_run:
                    node = Path(n.path)
                    bdir = cfg["data_dir"] / "backups" / f"{n.serial}" / dt.datetime.now().strftime("%Y%m%dT%H%M%S")
                    bdir.mkdir(parents=True, exist_ok=True)
                    for s in SCRIPT_FILES:
                        if (node / s).exists():
                            shutil.copy2(node / s, bdir / s)
                    targets = [s for s in SCRIPT_FILES if s == "sct_par.xml" or (node / s).exists()]
                    for s in targets:
                        tmp = node / (s + ".part")
                        shutil.copyfile(xml, tmp)
                        tmp.replace(node / s)
                        if _sha(node / s) != _sha(xml):
                            raise IOError(f"verification failed for {node / s}")
                    b = state["batches"].setdefault(str(batch), {"started": _now(), "units": {}})
                    b["units"].setdefault(n.unit, {}).update(serial=n.serial, row_id=r.row_id,
                                                             applied=_now(), backup=str(bdir))
        out.append(rec)
    if not dry_run:
        save_state(cfg, state)
    return pd.DataFrame(out)


def _copy_node(cfg, src: Path, dst: Path, serial: str, batch: int, dry_run: bool):
    earlier = {h: bb for h, bb in collected_hashes(cfg, serial).items() if bb != f"batch_{batch:02d}"}
    files = sorted({f for pat in DATA_PATTERNS for f in src.rglob(pat) if f.is_file()})
    nbytes, copied, n_dld, man = 0, 0, 0, []
    for f in files:
        rel = f.relative_to(src).as_posix()
        size = f.stat().st_size
        is_data = f.suffix.lower() in (".dld", ".miniseed", ".mseed")
        h = _sha(f)
        if is_data and h in earlier:
            man.append(dict(file=rel, bytes=size, sha256=h, status=f"in {earlier[h]}"))
            continue
        nbytes += size
        n_dld += is_data
        t = dst / rel
        status_ = "copied"
        if dry_run:
            status_ = "would copy"
        elif t.exists() and t.stat().st_size == size and _sha(t) == h:
            status_ = "already here"
        else:
            t.parent.mkdir(parents=True, exist_ok=True)
            tmp = t.with_name(t.name + ".part")
            shutil.copy2(f, tmp)
            if _sha(tmp) != h:
                tmp.unlink(missing_ok=True)
                raise IOError(f"copy of {f} failed verification")
            tmp.replace(t)
            copied += 1
        man.append(dict(file=rel, bytes=size, sha256=h, status=status_))
    if not dry_run:
        dst.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(man, columns=["file", "bytes", "sha256", "status"]).to_csv(dst / "collect_manifest.csv", index=False)
    return files, copied, n_dld, nbytes, man


def collect_existing(cfg, nodes: pd.DataFrame) -> pd.DataFrame:
    """Copy data already on the nodes BEFORE the test (factory or earlier
    recordings) to ``<data_dir>/raw/batch_00/pre_<serial>/``, so that applying
    batch scripts is safe. Verified like ``collect_batch``; nothing deleted."""
    out = []
    for n in nodes.itertuples():
        dst = hc.batch_dir(cfg, 0) / f"pre_{n.serial}"
        files, copied, n_dld, nbytes, _ = _copy_node(cfg, Path(n.path), dst, n.serial, 0, False)
        out.append(dict(serial=n.serial, unit=n.unit, files=len(files), copied=copied, dld_files=n_dld, bytes=nbytes))
    return pd.DataFrame(out)


def collect_batch(cfg, batch: int, nodes: pd.DataFrame | None = None, dry_run: bool = False) -> pd.DataFrame:
    """Copy every data and log file from each connected node of ``batch`` to
    ``<data_dir>/raw/batch_NN/<unit>_<serial>/`` keeping the node's folder
    layout. Each copy is written as ``.part``, verified with SHA-256 and then
    renamed; identical files already there are skipped. DLD/MiniSEED files
    already collected in an EARLIER batch (same SHA-256) are not copied again
    but listed in the manifest. ``collect_manifest.csv`` lists every file with
    size, hash and status. Nothing is deleted from the nodes."""
    nodes = find_nodes(cfg) if nodes is None else nodes
    m = hc.load_matrix(cfg)
    units = set(m[m["batch"] == batch]["unit"])
    state = load_state(cfg)
    b = state["batches"].setdefault(str(batch), {"started": _now(), "units": {}})
    out = []
    for n in nodes.itertuples():
        if not n.unit or n.unit not in units:
            out.append(dict(serial=n.serial, unit=n.unit, files=0, copied=0, dld_files=0, bytes=0,
                            message="serial not in units.csv" if not n.unit else "not in this batch"))
            continue
        src = Path(n.path)
        dst = hc.batch_dir(cfg, batch) / f"{n.unit}_{n.serial}"
        files, copied, n_dld, nbytes, man = _copy_node(cfg, src, dst, n.serial, batch, dry_run)
        if not dry_run:
            b["units"].setdefault(n.unit, {}).update(serial=n.serial, collected=_now(), files=len(files),
                                                     dld_files=n_dld, path=str(dst), firmware_log=n.firmware)
        out.append(dict(serial=n.serial, unit=n.unit, files=len(files), copied=copied, dld_files=n_dld,
                        bytes=nbytes, message="" if n_dld else "no new DLD/MiniSEED files"))
    if not dry_run:
        got = {u for u, v in b["units"].items() if v.get("dld_files")}
        b["collected_complete"] = units <= got
        b["missing_units"] = sorted(units - got)
        save_state(cfg, state)
    return pd.DataFrame(out)


def status(cfg) -> pd.DataFrame:
    """One row per matrix row: applied / collected times from the state file."""
    m = hc.load_matrix(cfg)
    st = load_state(cfg)
    rows = []
    for r in m.itertuples():
        u = st["batches"].get(str(r.batch), {}).get("units", {}).get(r.unit, {})
        rows.append(dict(row_id=r.row_id, batch=r.batch, unit=r.unit, serial=u.get("serial", ""),
                         applied=u.get("applied", ""), collected=u.get("collected", ""),
                         dld_files=u.get("dld_files", 0),
                         current=r.batch == st.get("current_batch")))
    return pd.DataFrame(rows)
