"""Figures from the check results (``results/figures/*.png``)."""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from pathlib import Path  # noqa: E402


def _save(fig, cfg, name):
    d = cfg["data_dir"] / "results" / "figures"
    d.mkdir(parents=True, exist_ok=True)
    fig.savefig(d / f"{name}.png", dpi=150, bbox_inches="tight")
    return fig


def lag_vs_block(res, cfg):
    """Lag against block length per firmware, with the fitted line."""
    t, b = res["timing"], res["block_convention"]
    t = t[t.filter_phase == "linear"]
    fig, ax = plt.subplots(figsize=(7, 4.5))
    for k, (fw, g) in enumerate(t.groupby("firmware")):
        col = plt.cm.tab10(k % 10)
        ax.plot(g.block_s, 1000 * g.lag_s, "o", color=col, label=fw)
        row = b[b.firmware == fw]
        if len(row) and np.isfinite(row.slope_b.iloc[0]):
            x = np.geomspace(0.2, 21, 100)
            ax.plot(x, 1000 * (row.offset_a_s.iloc[0] + row.slope_b.iloc[0] * x), "-", color=col, lw=0.8)
    ax.set_xscale("log"); ax.set_yscale("symlog", linthresh=1.0); ax.set_xlabel("block length (s) = 1000 / sample rate")
    ax.set_ylabel("lag against reference (ms, symlog)"); ax.axhline(0, color="k", lw=0.5)
    ax.legend(fontsize=7, ncol=2); ax.set_title("Timing by sample rate and firmware")
    return _save(fig, cfg, "lag_vs_block")


def polarity_map(res, cfg):
    p = res["polarity"].set_index("row_id")[[c for c in "ZNE" if c in res["polarity"]]]
    fig, ax = plt.subplots(figsize=(4, max(3, 0.18 * len(p))))
    ax.imshow(p.values, cmap="RdBu", vmin=-1, vmax=1, aspect="auto")
    ax.set_xticks(range(p.shape[1]), p.columns); ax.set_yticks(range(len(p)), p.index, fontsize=6)
    ax.set_title("Polarity (+1 = FDSN)")
    return _save(fig, cfg, "polarity")


def gain_plot(res, cfg):
    g = res["gain"]
    fig, ax = plt.subplots(figsize=(6, 3.5))
    ax.plot(g.gain_db, g.error_pct, "o")
    ax.axhline(0, color="k", lw=0.5); ax.set_xlabel("gain (dB)"); ax.set_ylabel("amplitude error (%)")
    ax.set_title("Gain: amplitude against 10^(gain/20)")
    return _save(fig, cfg, "gain")


def scatter_plot(res, cfg):
    t = res["timing"]
    fig, ax = plt.subplots(figsize=(7, 3.5))
    for k, (fw, g) in enumerate(t.groupby("firmware")):
        ax.plot([k] * len(g), 1000 * g.lag_s, "o", alpha=0.7)
    ax.set_xticks(range(t.firmware.nunique()), sorted(t.firmware.unique()), rotation=45, fontsize=7)
    ax.set_ylabel("lag (ms)"); ax.set_title("Lag of every node by firmware")
    return _save(fig, cfg, "lag_by_firmware")


def all_figures(res, cfg):
    out = []
    for f in (lag_vs_block, polarity_map, gain_plot, scatter_plot):
        try:
            out.append(f(res, cfg))
        except Exception as exc:  # noqa: BLE001
            print(f"{f.__name__}: {exc}")
    return out


def write_summary(res, cfg, path=None) -> Path:
    """``results/summary.md``: every check with its question and result table,
    the open questions of the test and where the files are."""
    from . import checks
    from . import nodes as hn
    path = Path(path) if path else cfg["data_dir"] / "results" / "summary.md"
    st = hn.load_state(cfg)
    lines = ["# Huddle test summary", "",
             f"Generated {pd.Timestamp.now(tz='UTC'):%Y-%m-%d %H:%M} UTC from `{cfg['data_dir']}`.", "",
             "## Batches", ""]
    for b, v in sorted(st.get("batches", {}).items(), key=lambda kv: int(kv[0])):
        notes = v.get("field", {})
        lines.append(f"- Batch {b}: step `{v.get('step', '?')}`, started {notes.get('start', v.get('started', ''))}, "
                     f"ended {notes.get('end', '')}, units collected {sum(1 for u in v.get('units', {}).values() if u.get('dld_files'))}"
                     + (f", missing {', '.join(v['missing_units'])}" if v.get("missing_units") else ""))
    for name, df in res.items():
        q = checks.REGISTRY.get(name, {}).get("question", "")
        lines += ["", f"## {name}", "", q, "", "```",
                  df.to_string(index=False, max_rows=60, max_colwidth=60) if len(df) else "(no rows)", "```"]
    lines += ["", "## Files", "",
              "- `raw/batch_NN/<unit>_<serial>/`: everything copied off each node, with `collect_manifest.csv`",
              "- `reference/batch_NN/`: the reference station's MiniSEED",
              "- `results/batch_NN/measurements.csv`: one row per unit, component and impact",
              "- `results/<check>.csv` and `results/figures/`", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))
    return path
