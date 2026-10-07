"""Guided huddle test: walks the tester through every step of every batch.

    python run_huddle.py              # picks up where the last session stopped
    python run_huddle.py --status     # what has been done
    python run_huddle.py --simulate   # practice run with fake nodes (no hardware)

Steps of one batch (saved in ``<data_dir>/state.json``, so the script can be
stopped and started again at any point):

    connect  -> find the nodes (USB drives), match serials to units, check firmware
    apply    -> back up and replace each node's sct_par.xml with the batch's script
    record   -> field checklist, start time; the nodes record
    stop     -> end time, copy the reference station's MiniSEED
    collect  -> copy every log and data file off the nodes, verified
    analyse  -> measurements, all checks, figures and results/summary.md
    next     -> the next batch starts at "connect"

Safety:
- only folders that look like a SmartSolo node (``device.ini``, or
  ``DigiSolo.LOG`` with ``sct_par.xml``) are touched; on Windows A:-C: never;
- nothing is ever deleted from a node; only sct_par.xml / sct_par_b.xml are
  written, after a backup, and verified by SHA-256;
- scripts are not applied while a node holds data that has not been copied;
- every copy is written as .part, verified by SHA-256 and then renamed;
- the firmware actually running (last boot in the log; device.ini is not
  updated when firmware is changed) must match the plan.
"""

from __future__ import annotations

import datetime as dt
import shutil
import sys
from pathlib import Path

import pandas as pd

from . import analysis, checks, config as hc, nodes as hn, report, xmlgen

STEPS = ["connect", "apply", "record", "stop", "collect", "analyse", "done"]
MIN_FREE_GB = 20


class Wizard:
    def __init__(self, cfg, auto=False, simulate=False, out=print):
        self.cfg, self.auto, self.simulate, self.out = cfg, auto, simulate, out
        self.sim_root = cfg["data_dir"] / "simulation" if simulate else None

    # ------------------------------------------------------------------ io
    def ask(self, question, default="", choices=None):
        """Ask the tester; ``auto`` mode takes the default."""
        hint = f" [{default}]" if default != "" else ""
        if choices:
            hint = f" ({'/'.join(choices)})" + hint
        if self.auto:
            self.out(f"? {question}{hint} -> {default}")
            return default
        while True:
            a = input(f"? {question}{hint}: ").strip() or default
            if not choices or a in choices:
                return a
            self.out(f"  please answer one of {', '.join(choices)}")

    def confirm(self, question, word="yes"):
        """Dangerous steps need the word typed out."""
        return self.ask(f"{question} Type '{word}' to go ahead", word if self.auto else "") == word

    def say(self, *lines):
        for ln in lines:
            self.out(ln)

    def head(self, text):
        self.out("")
        self.out("=" * 78)
        self.out(text)
        self.out("=" * 78)

    def table(self, df, cols=None):
        if df is None or not len(df):
            self.out("  (nothing)")
            return
        with pd.option_context("display.width", 200, "display.max_rows", 200, "display.max_colwidth", 60):
            self.out((df[cols] if cols else df).to_string(index=False))

    # --------------------------------------------------------------- state
    def state(self):
        return hn.load_state(self.cfg)

    def batch_state(self, b):
        return self.state()["batches"].get(str(b), {})

    def set_step(self, b, step, **extra):
        st = self.state()
        bs = st["batches"].setdefault(str(b), {"started": hn._now(), "units": {}})
        bs["step"] = step
        bs.setdefault("field", {}).update(extra)
        st["current_batch"] = b
        hn.save_state(self.cfg, st)

    def batches(self):
        return sorted(hc.load_matrix(self.cfg).batch.unique())

    def current(self):
        st = self.state()
        for b in self.batches():
            if self.batch_state(b).get("step", "connect") != "done":
                return int(b)
        return None

    # ------------------------------------------------------------- overview
    def status(self):
        rows = []
        for b in self.batches():
            bs = self.batch_state(b)
            rows.append(dict(batch=b, step=bs.get("step", "not started"),
                             start=bs.get("field", {}).get("start", ""), end=bs.get("field", {}).get("end", ""),
                             collected=sum(1 for u in bs.get("units", {}).values() if u.get("dld_files")),
                             missing=", ".join(bs.get("missing_units", []))))
        self.table(pd.DataFrame(rows))

    def preflight(self):
        d = self.cfg["data_dir"]
        d.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(d).free / 1e9
        self.say(f"data folder: {d}  ({free:.0f} GB free)")
        if free < MIN_FREE_GB:
            self.say(f"WARNING: less than {MIN_FREE_GB} GB free - 24 nodes x several hours need tens of GB")
        m = hc.load_matrix(self.cfg)
        missing = [r.row_id for r in m.itertuples() if not (self.cfg["xml_dir"] / f"{r.row_id}.xml").exists()]
        if missing:
            self.say(f"{len(missing)} scripts missing - writing them now")
            xmlgen.write_all(self.cfg, m)

    # ------------------------------------------------------------- steps
    def find(self, b):
        if self.simulate and not any((self.sim_root / "mounts").glob("NODE_*")):
            from . import synthetic
            synthetic.make_nodes(self.cfg, self.sim_root / "mounts")
        return hn.find_nodes(self.cfg)

    def assign_serials(self, found):
        """Unknown serials -> ask which unit (suggesting units with the same firmware)."""
        units = hc.load_units(self.cfg)
        changed = False
        for n in found[found.unit == ""].itertuples():
            if not n.serial:
                continue
            free = units[(units.serial == "") & (units.firmware == n.firmware)].unit.tolist()
            self.say(f"  node {n.serial} (firmware {n.firmware or '?'}) is not in units.csv; "
                     f"free units with this firmware: {', '.join(free) or 'none'}")
            u = self.ask(f"  unit for node {n.serial} (blank = skip)", free[0] if free else "")
            if u and u in set(units.unit) and units.loc[units.unit == u, "serial"].iloc[0] == "":
                units.loc[units.unit == u, "serial"] = n.serial
                changed = True
            elif u:
                self.say(f"  {u} is not a free unit - skipped")
        if changed:
            shutil.copy2(self.cfg["units"], str(self.cfg["units"]) + ".bak")
            units.to_csv(self.cfg["units"], index=False)
            self.say(f"  units.csv updated (backup units.csv.bak)")
        return hn.find_nodes(self.cfg) if changed else found

    def wait_for_nodes(self, b, purpose):
        m = hc.load_matrix(self.cfg)
        want = set(m[m.batch == b].unit)
        while True:
            found = self.find(b)
            self.say(f"\n{len(found)} node(s) connected:")
            self.table(found, ["serial", "unit", "firmware", "firmware_ini", "n_data", "error"])
            if found.firmware_mismatch.any():
                self.say("NOTE: device.ini firmware differs from the last boot in the log for "
                         f"{', '.join(found[found.firmware_mismatch].serial)} - the log is used "
                         "(device.ini is not updated when firmware is changed).")
            found = self.assign_serials(found)
            have = set(found.unit) & want
            missing = sorted(want - have)
            self.say(f"batch {b}: {len(have)} of {len(want)} units connected"
                     + (f"; missing {', '.join(missing)}" if missing else ""))
            if not missing:
                return found
            a = self.ask(f"connect more nodes and [r]escan, [c]ontinue {purpose} with these, or [q]uit",
                         "c" if self.auto else "r", ["r", "c", "q"])
            if a == "c":
                return found
            if a == "q":
                sys.exit(0)

    def step_connect(self, b):
        self.head(f"Batch {b} - connect the nodes")
        m = hc.load_matrix(self.cfg)
        rows = m[m.batch == b]
        self.say("Planned settings:")
        self.table(rows, ["row_id", "firmware", "sample_rate_sps", "gain_db", "filter_phase", "orientation_deg",
                          "gnss_mode", "low_cut", "adc_mode", "storage", "test_mode", "role"])
        man = self.cfg["xml_dir"] / "manifest.csv"
        if man.exists():
            mf = pd.read_csv(man, dtype=str, keep_default_na=False)
            unc = mf[mf.row_id.isin(rows.row_id) & (mf.unconfirmed != "")]
            if len(unc):
                self.say("\nThese scripts use codes not yet confirmed by a SoloLite export - "
                         "check them in SoloLite before deploying:")
                self.table(unc, ["row_id", "unconfirmed"])
        self.say("", "Connect the nodes to the reader/rack. They appear as USB drives.")
        self.ask("press Enter when they are connected", "")
        found = self.wait_for_nodes(b, "applying scripts")
        left = [(n, hn.uncollected_data(self.cfg, n.path, n.serial)) for n in found.itertuples()]
        left = [(n, f) for n, f in left if f]
        if left:
            self.say(f"\n{len(left)} node(s) hold data that has not been copied yet "
                     "(earlier recordings). It is copied to raw/batch_00/ before anything is changed.")
            if self.ask("copy it now", "y", ["y", "n"]) == "y":
                res = hn.collect_existing(self.cfg, found[found.serial.isin([n.serial for n, _ in left])])
                self.table(res)
        self.set_step(b, "apply")

    def step_apply(self, b):
        self.head(f"Batch {b} - write the scripts to the nodes")
        found = self.wait_for_nodes(b, "applying scripts")
        hn.start_batch(self.cfg, b, force=True)
        dry = hn.apply_batch(self.cfg, b, found, dry_run=True)
        self.table(dry, ["serial", "unit", "row_id", "action", "message"])
        bad = dry[dry.action == "error"]
        if len(bad):
            self.say("\nThese nodes will NOT get a script:",
                     "- firmware differs: flash the planned firmware in SoloLite, or swap the unit;",
                     "- data not collected: answer 'y' to copying earlier data in the connect step.")
        n_ok = int((dry.action == "would apply").sum())
        if not n_ok:
            self.say("nothing to apply")
            return
        if not self.confirm(f"Write the batch {b} script to {n_ok} node(s)? The current scripts are backed up."):
            self.say("not applied")
            return
        res = hn.apply_batch(self.cfg, b, found, dry_run=False)
        self.table(res, ["serial", "unit", "row_id", "action", "message"])
        self.say("", "Scripts written and verified. Eject the drives safely (Eject / Safely Remove) "
                     "before unplugging the nodes.")
        self.set_step(b, "record")

    def step_record(self, b):
        self.head(f"Batch {b} - record")
        m = hc.load_matrix(self.cfg)
        long_ = (m[m.batch == b].gnss_mode == "always").any()
        hours = 3 if long_ else 1
        self.say("Checklist:",
                 "  [ ] all nodes on the pad, arrows to north, as in the layout (rotated units as planned)",
                 "  [ ] reference station (Pegasus + Compact) recording, its clock locked",
                 "  [ ] nodes switched on outdoors with sky view; wait for GPS lock (LEDs)",
                 f"  [ ] record at least {hours} h" + (" (GNSS always-on vs cycle needs >= 3 h)" if long_ else ""),
                 "  [ ] impacts: 3 series of ~15 hammer blows at irregular 3-12 s intervals,",
                 "      one series near the start, one in the middle, one near the end",
                 "  [ ] note anything unusual (moved node, rain, traffic)")
        now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M")
        start = self.ask("recording start (UTC, YYYY-MM-DDTHH:MM)", now)
        site = self.ask("site / pad", "UTAS huddle pad")
        who = self.ask("tester", "")
        notes = self.ask("notes", "")
        self.set_step(b, "stop", start=start, site=site, tester=who, notes_start=notes)
        if self.simulate:
            from . import synthetic
            synthetic.make_batch(self.cfg, b, self.sim_root / "mounts", self.sim_root / "reference_source",
                                 duration_s=140.0, seed=int(b))
            self.say("(simulation: the nodes have recorded)")
        else:
            self.say("", "The nodes are recording. Stop here; run this script again when the recording is done.")
            if self.ask("is the recording already finished", "n", ["y", "n"]) != "y":
                sys.exit(0)

    def step_stop(self, b):
        self.head(f"Batch {b} - end of the recording")
        now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M")
        end = self.ask("recording end (UTC, YYYY-MM-DDTHH:MM)", now)
        notes = self.ask("notes", "")
        default_ref = str(self.sim_root / "reference_source") if self.simulate else str(self.cfg["reference"].get("path", ""))
        ref = self.ask("folder with the reference station's MiniSEED for this batch (blank = none)", default_ref)
        if ref:
            n = self.copy_reference(b, Path(ref).expanduser())
            self.say(f"copied {n} reference file(s) to reference/batch_{b:02d}/")
        self.set_step(b, "collect", end=end, notes_end=notes, reference_source=ref)

    def copy_reference(self, b, src):
        dst = self.cfg["data_dir"] / "reference" / f"batch_{b:02d}"
        dst.mkdir(parents=True, exist_ok=True)
        n = 0
        for f in sorted(src.rglob("*")):
            if f.is_file() and f.suffix.lower() in (".mseed", ".miniseed", ".msd", ".seed", ".xml", ""):
                t = dst / f.relative_to(src)
                if t.exists() and hn._sha(t) == hn._sha(f):
                    continue
                t.parent.mkdir(parents=True, exist_ok=True)
                tmp = t.with_name(t.name + ".part")
                shutil.copy2(f, tmp)
                if hn._sha(tmp) != hn._sha(f):
                    raise IOError(f"copy of {f} failed verification")
                tmp.replace(t)
                n += 1
        return n

    def step_collect(self, b):
        self.head(f"Batch {b} - copy the data off the nodes")
        self.say("Switch the nodes off and connect them to the reader/rack.")
        self.ask("press Enter when they are connected", "")
        while True:
            found = self.wait_for_nodes(b, "collecting")
            res = hn.collect_batch(self.cfg, b, found)
            self.table(res, ["serial", "unit", "files", "copied", "dld_files", "bytes", "message"])
            bs = self.batch_state(b)
            if bs.get("collected_complete"):
                break
            self.say(f"not collected yet: {', '.join(bs.get('missing_units', []))}")
            a = self.ask("[r]escan for more nodes or [c]ontinue without them", "c" if self.auto else "r", ["r", "c"])
            if a == "c":
                break
        self.say(f"data in {hc.batch_dir(self.cfg, b)} (nothing was deleted from the nodes; "
                 "format them in SoloLite only after checking the copy).")
        self.set_step(b, "analyse")

    def step_analyse(self, b):
        self.head(f"Batch {b} - analysis")
        meas = analysis.measure_batch(self.cfg, b, verbose=True)
        self.say(f"{len(meas)} measurements")
        res = checks.run_checks(self.cfg)
        report.all_figures(res, self.cfg)
        path = report.write_summary(res, self.cfg)
        s = res.get("settings")
        if s is not None and "ok" in s:
            bad = s[(s.batch == b) & ~s.ok.astype(bool)]
            self.say(f"settings: {len(s[s.batch == b]) - len(bad)} of {len(s[s.batch == b])} units as planned")
            if len(bad):
                self.table(bad, ["row_id", "problem"])
        for name in ("time_labels", "firmware", "block_convention"):
            if name in res and len(res[name]) and "error" not in res[name]:
                self.say(f"\n{name}: {checks.REGISTRY[name]['question']}")
                self.table(res[name].head(12))
        self.say(f"\nfull summary: {path}", f"figures: {self.cfg['data_dir'] / 'results' / 'figures'}")
        self.set_step(b, "done")

    # ------------------------------------------------------------- run
    def run(self, batch=None, once=False):
        self.head("SmartSolo huddle test" + (" - SIMULATION (fake nodes)" if self.simulate else ""))
        self.preflight()
        self.status()
        while True:
            b = batch if batch is not None else self.current()
            if b is None:
                self.say("\nAll batches are done. Summary: " + str(self.cfg["data_dir"] / "results" / "summary.md"))
                return
            step = self.batch_state(b).get("step", "connect")
            if step == "done":
                if batch is not None:
                    return
                continue
            getattr(self, f"step_{step}")(b)
            if self.batch_state(b).get("step") == step:      # step did not finish (e.g. not confirmed)
                return
            if self.batch_state(b).get("step") == "done":
                if once or batch is not None:
                    return
                nxt = self.current()
                if nxt is None or self.ask(f"start batch {nxt} now", "y", ["y", "n"]) != "y":
                    return


def simulation_settings(cfg):
    """Settings for a practice run: a copy of matrix and units under
    ``<data_dir>/simulation`` and fake node drives there."""
    import yaml
    root = cfg["data_dir"] / "simulation"
    (root / "config").mkdir(parents=True, exist_ok=True)
    for k in ("matrix", "units"):
        t = root / "config" / Path(cfg[k]).name
        if not t.exists():
            shutil.copy2(cfg[k], t)
            if k == "units":                                     # simulated serials, not the real ones
                u = pd.read_csv(t, dtype=str, keep_default_na=False)
                u["serial"] = ""
                u.to_csv(t, index=False)
    s = root / "config" / "huddle.yaml"
    s.write_text(yaml.safe_dump({
        "data_dir": str(root / "data"), "matrix": str(root / "config" / "huddle_matrix.csv"),
        "units": str(root / "config" / "units.csv"), "xml_codes": str(cfg["xml_codes"]),
        "template": str(cfg["template"]), "xml_dir": str(cfg["xml_dir"]),
        "mount_globs": [str(root / "mounts" / "*")], "analysis": cfg["analysis"],
        "reference": {"path": "", "station": "PEG", "channels": "HH?"}}))
    sim = hc.load_settings(s)
    sim["data_dir"] = root / "data"
    return sim, root


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--settings", default=None, help="settings file (default sand_box_huddle_test/huddle.yaml)")
    ap.add_argument("--batch", type=int, default=None, help="work on this batch only")
    ap.add_argument("--status", action="store_true", help="show progress and exit")
    ap.add_argument("--simulate", action="store_true", help="practice run with fake nodes in data/simulation/")
    ap.add_argument("--auto", action="store_true", help="accept every default (for tests; implies no prompts)")
    a = ap.parse_args(argv)
    cfg = hc.load_settings(a.settings)
    if a.simulate:
        cfg, root = simulation_settings(cfg)
        w = Wizard(cfg, auto=a.auto, simulate=True)
        w.sim_root = root
    else:
        w = Wizard(cfg, auto=a.auto)
    if a.status:
        w.status()
        return w
    w.run(a.batch)
    return w
