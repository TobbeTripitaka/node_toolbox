#!/usr/bin/env python
"""Run the huddle test, batch by batch.

    python tests/huddle_test/huddle.py make-xml                 # xml/<row_id>.xml for all rows
    python tests/huddle_test/huddle.py nodes                    # mounted nodes and their units
    python tests/huddle_test/huddle.py start 1                  # batch 1 is now running
    python tests/huddle_test/huddle.py apply 1 [--yes]          # scripts onto the nodes (dry run without --yes)
    python tests/huddle_test/huddle.py collect 1                # data off the nodes into data_dir
    python tests/huddle_test/huddle.py status                   # what has been applied / collected
    python tests/huddle_test/huddle.py analyse 1                # measurements of batch 1
    python tests/huddle_test/huddle.py checks                   # all checks on all analysed batches
    python tests/huddle_test/huddle.py propose --method optimised   # append the next batch to the matrix
    python tests/huddle_test/huddle.py demo                     # synthetic batch 1 end to end

The guided way is sand_box_huddle_test/run_huddle.py; this tool runs single steps.
Settings: sand_box_huddle_test/huddle.yaml (data folder, mount points, reference).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd  # noqa: E402

from huddletest import analysis, checks, config, design, nodes, report, xmlgen  # noqa: E402


def _print(df):
    with pd.option_context("display.width", 220, "display.max_rows", 500, "display.max_columns", 30):
        print(df.to_string(index=False) if len(df) else "(nothing)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--settings", default=None, help="settings file (default sand_box_huddle_test/huddle.yaml)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("make-xml"); p.add_argument("--batch", type=int, nargs="*")
    p = sub.add_parser("nodes"); p.add_argument("--root", nargs="*")
    p = sub.add_parser("start"); p.add_argument("batch", type=int); p.add_argument("--note", default="")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("apply"); p.add_argument("batch", type=int); p.add_argument("--yes", action="store_true")
    p.add_argument("--root", nargs="*"); p.add_argument("--ignore-firmware", action="store_true")
    p = sub.add_parser("collect"); p.add_argument("batch", type=int); p.add_argument("--root", nargs="*")
    p.add_argument("--dry-run", action="store_true")
    sub.add_parser("status")
    p = sub.add_parser("analyse"); p.add_argument("batch", type=int, nargs="+")
    p = sub.add_parser("checks"); p.add_argument("--batch", type=int, nargs="*"); p.add_argument("--only", nargs="*")
    p = sub.add_parser("propose"); p.add_argument("--method", choices=["optimised", "random"], default="optimised")
    p.add_argument("--seed", type=int, default=0); p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("demo"); p.add_argument("--data-dir", default="/tmp/huddle_demo")
    a = ap.parse_args(argv)

    if a.cmd == "demo":
        return demo(a.data_dir)
    cfg = config.load_settings(a.settings)
    if a.cmd == "make-xml":
        man = xmlgen.write_all(cfg, config.load_matrix(cfg), a.batch)
        print(f"wrote {len(man)} XML files to {cfg['xml_dir']}")
        unc = man[man.unconfirmed.fillna("") != ""]
        print(f"{len(unc)} files use unconfirmed codes (see xml/manifest.csv)")
    elif a.cmd == "nodes":
        _print(nodes.find_nodes(cfg, a.root))
    elif a.cmd == "start":
        if a.force:
            st = nodes.load_state(cfg); st["current_batch"] = None; nodes.save_state(cfg, st)
        nodes.start_batch(cfg, a.batch, a.note)
        print(f"current batch: {a.batch}")
    elif a.cmd == "apply":
        st = nodes.load_state(cfg)
        if st.get("current_batch") != a.batch:
            sys.exit(f"current batch is {st.get('current_batch')}; run 'start {a.batch}' first")
        _print(nodes.apply_batch(cfg, a.batch, nodes.find_nodes(cfg, a.root), dry_run=not a.yes,
                                 check_firmware=not a.ignore_firmware))
        if not a.yes:
            print("dry run - add --yes to write the scripts")
    elif a.cmd == "collect":
        _print(nodes.collect_batch(cfg, a.batch, nodes.find_nodes(cfg, a.root), dry_run=a.dry_run))
    elif a.cmd == "status":
        s = nodes.status(cfg)
        _print(s.groupby("batch").agg(rows=("row_id", "size"), applied=("applied", lambda x: (x != "").sum()),
                                      collected=("collected", lambda x: (x != "").sum()),
                                      current=("current", "any")).reset_index())
    elif a.cmd == "analyse":
        for b in a.batch:
            meas = analysis.measure_batch(cfg, b)
            print(f"batch {b}: {len(meas)} measurements")
    elif a.cmd == "checks":
        res = checks.run_checks(cfg, a.batch, a.only)
        for name, df in res.items():
            print(f"\n== {name}: {checks.REGISTRY[name]['question']}")
            _print(df.head(30))
        report.all_figures(res, cfg)
        print(f"\nresults and figures in {cfg['data_dir'] / 'results'}")
    elif a.cmd == "propose":
        new = design.propose_batch(cfg, a.method, a.seed, write=not a.dry_run)
        _print(new)
        if not a.dry_run:
            print(f"appended batch {new.batch.iloc[0]} to {cfg['matrix']}; run 'make-xml --batch {new.batch.iloc[0]}'")


def demo(data_dir="/tmp/huddle_demo", batch=1, faults=None, duration_s=200.0, verbose=True, batches=None):
    """Synthetic batch through the whole chain, in a copy of this folder's
    matrix and units (the repository files are not changed)."""
    import shutil

    from huddletest import synthetic

    d = Path(data_dir)
    if d.exists():
        shutil.rmtree(d)
    (d / "config").mkdir(parents=True)
    here = Path(__file__).resolve().parent
    for f in ("sand_box_huddle_test/huddle_matrix.csv", "sand_box_huddle_test/units.csv", "xml_codes.yaml"):
        shutil.copy(here / f, d / "config" / Path(f).name)
    shutil.copytree(here / "templates", d / "config" / "templates")
    import yaml
    settings = d / "config" / "huddle.yaml"
    settings.write_text(yaml.safe_dump({
        "data_dir": str(d / "data"), "matrix": str(d / "config" / "huddle_matrix.csv"),
        "units": str(d / "config" / "units.csv"), "xml_codes": str(d / "config" / "xml_codes.yaml"),
        "template": str(d / "config" / "templates" / "sct_par_template.xml"),
        "xml_dir": str(d / "xml"), "mount_globs": [str(d / "mounts" / "*")],
        "reference": {"path": str(d / "reference"), "station": "PEG", "channels": "HH?"}}))
    cfg = config.load_settings(settings)
    faults = faults if faults is not None else {"V1.0.7.5ke": {"block_end": True},
                                                "V1.0.7.9be": {"offset_s": 0.020},
                                                "F2B": {"flip": ["Z"]}}
    xmlgen.write_all(cfg, config.load_matrix(cfg))
    for b in (batches or [batch]):
        cfg["mount_globs"] = [str(d / f"mounts_b{b}" / "*")]
        synthetic.make_nodes(cfg, d / f"mounts_b{b}")                  # nodes as they arrive
        nodes.collect_existing(cfg, nodes.find_nodes(cfg))               # earlier data off first
        nodes.start_batch(cfg, b, force=True)
        applied = nodes.apply_batch(cfg, b, dry_run=False)               # scripts on
        synthetic.make_batch(cfg, b, d / f"mounts_b{b}", d / "reference", faults=faults if b == batch else {},
                             duration_s=duration_s, seed=b)              # the nodes record
        collected = nodes.collect_batch(cfg, b)
        analysis.measure_batch(cfg, b, verbose=verbose)
    res = checks.run_checks(cfg)
    report.all_figures(res, cfg)
    if verbose:
        print(applied.action.value_counts().to_dict(), f"{collected.dld_files.sum()} DLD files collected")
        for k in ("block_convention", "firmware", "polarity", "settings"):
            print(f"\n== {k}"); _print(res[k].head(26))
    return cfg, res


if __name__ == "__main__":
    main()
