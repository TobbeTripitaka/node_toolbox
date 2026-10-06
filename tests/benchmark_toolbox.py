#!/usr/bin/env python
"""
Time the main node_toolbox operations on this machine and write a LaTeX
table (Seismica style) plus a short summary paragraph.

    python benchmark_toolbox.py                       # run from the node_toolbox folder
    python benchmark_toolbox.py --repo ~/proj/node_toolbox --out tab_perf.tex --reps 5

Needs the example data, including the raw DLD files (git lfs pull).

Tobias Stål 2026
"""
import argparse
import os
import platform
import statistics
import sys
import tempfile
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")


def timeit(fn, reps):
    """Median wall-clock time (s) of ``reps`` calls, plus the last result."""
    times, result = [], None
    for _ in range(reps):
        t = time.perf_counter()
        result = fn()
        times.append(time.perf_counter() - t)
    return statistics.median(times), result


def fmt(seconds):
    if seconds < 1e-3:
        return f"{seconds * 1e3:.2f} ms"
    if seconds < 1:
        return f"{seconds * 1e3:.0f} ms"
    return f"{seconds:.2g} s" if seconds < 10 else f"{seconds:.0f} s"


def cpu_name():
    try:
        if platform.system() == "Darwin":
            import subprocess
            return subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], text=True).strip()
        if platform.system() == "Linux":
            for line in open("/proc/cpuinfo"):
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except Exception:  # noqa: BLE001
        pass
    return platform.processor() or platform.machine()


def tex(s):
    return s.replace("_", r"\_").replace("%", r"\%").replace("&", r"\&").replace("#", r"\#")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=".", help="node_toolbox folder (default: current folder)")
    ap.add_argument("--out", default="tests/tab_perf.tex", help="LaTeX output file")
    ap.add_argument("--reps", type=int, default=5, help="repetitions for the fast DLD operations")
    ap.add_argument("--reps-slow", type=int, default=3, help="repetitions for logs, deployments and index")
    args = ap.parse_args()

    repo = Path(args.repo).expanduser().resolve()
    sys.path.insert(0, str(repo / "lib"))
    import numpy as np
    import obspy
    import pandas as pd
    import smartsolo_dld as dld
    import smartsolo_locate as loc
    import smartsolo_log as sl
    import smartsolo_waveforms as wf

    bt = repo / "data" / "break_test"
    nodes = repo / "data" / "nodes"
    dld_file = next(bt.glob("453004362/*/seis000Z.DLD"))
    log_file = nodes / "453021267" / "DigiSolo.LOG"
    if dld_file.read_bytes()[:14] != dld.DLD_MAGIC:
        sys.exit(f"{dld_file} is not DLD data (Git LFS pointer?). Run 'git lfs install && git lfs pull'.")

    hdr = dld.read_dld_header(dld_file)
    tags = dld.read_dld_tags(dld_file)
    sr = 1000.0 / (tags.tick_ms.diff().median() / 1000.0)
    n_blocks = len(tags)
    n_dld = len(list(bt.rglob("seis*.DLD")))
    print(f"DLD test file: {dld_file.relative_to(repo)}  ({dld_file.stat().st_size / 1e6:.1f} MB, "
          f"{n_blocks} blocks, {sr:.0f} sps)")

    rows = []
    r, R = args.reps, args.reps_slow

    t, _ = timeit(lambda: dld.read_dld_header(dld_file), r)
    rows.append(("DLD header", t))
    t, _ = timeit(lambda: dld.read_dld_tags(dld_file), r)
    rows.append((f"DLD time tags ({n_blocks} blocks)", t))
    t_full, st = timeit(lambda: dld.read_dld(dld_file), r)
    npts = st[0].stats.npts
    rows.append((f"DLD component ({npts:,} samples)".replace(",", r"\,"), t_full))
    t0 = st[0].stats.starttime + 600
    t, _ = timeit(lambda: dld.read_dld(dld_file, starttime=t0, endtime=t0 + 60), r)
    rows.append(("DLD 60-s window", t))

    t_log, df = timeit(lambda: sl.read_log(log_file), R)
    rows.append((f"Log, {log_file.stat().st_size / 1e6:.1f}~MB, {len(df)} records", t_log))
    t, _ = timeit(lambda: loc.build_deployments(nodes), R)
    rows.append((f"Deployments from {len(list(nodes.rglob('DigiSolo.LOG')))} logs", t))
    t, _ = timeit(lambda: loc.build_deployments_from_dld(bt), R)
    rows.append((f"Deployments from {n_dld} DLD files", t))
    t, _ = timeit(lambda: wf.index_waveforms(bt), R)
    rows.append((f"Waveform index, {n_dld} DLD files", t))

    # export throughput (not in the table, printed for reference)
    with tempfile.TemporaryDirectory() as tmp:
        s2 = st.copy()
        for tr in s2:
            tr.stats.channel = "DPZ"
        t_ms, _ = timeit(lambda: s2.write(os.path.join(tmp, "x.mseed"), format="MSEED"), r)

    machine = f"{cpu_name()}, {os.cpu_count()} cores"
    versions = (f"Python {platform.python_version()}, ObsPy {obspy.__version__}, "
                f"pandas {pd.__version__}, NumPy {np.__version__}")
    duration_min = npts / sr / 60
    mb = dld_file.stat().st_size / 1e6
    speedup = npts / sr / t_full
    day_mb = 86400 * 1000 * (3 + 72 / 1000) / 1e6          # 1000 sps, 3 B/sample + 72-B tag per 1000 samples
    day_s = day_mb / (mb / t_full)

    lines = [r"\begin{table}[ht!]", r"\seismicatablestyle", r"\begin{tabular}{m{4.9cm} m{2.4cm}}",
             r"Operation & Time \\", r"\hline"]
    lines += [f"{name} & {fmt(t)} \\\\" for name, t in rows]
    lines += [r"\end{tabular}",
              rf"\caption{{Median execution times ({r} repetitions; {R} for the last four rows) on "
              rf"{tex(machine)} ({tex(versions)}).}}",
              r"\label{tab:perf}", r"\end{table}"]
    table = "\n".join(lines)

    sp = f"{float(f'{speedup:.2g}'):,.0f}".replace(",", r"\,")        # 2 significant digits
    text = (rf"Table~\ref{{tab:perf}} lists execution times on {tex(machine)} ({tex(versions)}). "
            rf"Reading a {duration_min:.0f}-minute, {sr:.0f}-sps DLD component ({mb:.1f}~MB) takes "
            rf"{fmt(t_full).replace(' ', '~')}, about {sp} times faster than real time. "
            rf"One day of 1000-sps data (about {day_mb:.0f}~MB per component) takes about "
            rf"{day_s:.0f}~s. Log parsing is the slowest step, at about {t_log:.1f}~s for a two-week log "
            rf"with {len(df)} records, but it runs once per node and the deployment and waveform index "
            r"tables can be cached as CSV.")

    Path(args.out).write_text(text + "\n\n" + table + "\n")
    print("\n" + text + "\n\n" + table)
    print(f"\n(for reference: writing the component as MiniSEED took {fmt(t_ms)})")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
