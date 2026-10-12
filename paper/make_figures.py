"""
Make the figures of the Seismica Software Report (paper/figures/).

    cd paper && python make_figures.py          # all figures
    python make_figures.py 3 7                  # selected figures

Needs the sample data (git lfs pull).
"""

import sys
import warnings
from pathlib import Path

import matplotlib as mpl
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import FancyBboxPatch, Rectangle

import style                           # global colours, colour maps and look (paper/style.py)

warnings.filterwarnings("ignore")
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "lib"))
import smartsolo_dld as dld            # noqa: E402
import smartsolo_locate as loc         # noqa: E402
import smartsolo_log as sl             # noqa: E402
import smartsolo_node as sn            # noqa: E402
import smartsolo_orientation as so     # noqa: E402
import smartsolo_waveforms as wf       # noqa: E402

OUT = HERE / "figures"
OUT.mkdir(exist_ok=True)
DML = [ROOT / "data/nodes/453021267", ROOT / "data/nodes/453022522"]
BT = ROOT / "data/break_test"
SERIALS = sorted(p.name for p in BT.iterdir() if p.is_dir() and not p.name.startswith("."))
COL = dict(zip(SERIALS, [style.C[4], style.C[0], style.C[5], style.C[1], style.C[2], style.C[3]]))
DCOL = {"453021267": style.C[0], "453022522": style.C[1]}
W2, W1 = 7.09, 3.39            # page width (18 cm) and column width (8.6 cm), inches

style.apply()


def save(fig, name):
    fig.savefig(OUT / f"{name}.pdf", bbox_inches="tight", metadata={"CreationDate": None})  # reproducible files
    fig.savefig(OUT / f"{name}.png", bbox_inches="tight", dpi=200)
    plt.close(fig)
    print("wrote", name)


label = style.label


def dfile(serial, comp="Z", session=0):
    return next((BT / serial).glob(f"*/seis{session:03d}{comp}.DLD"))


# --------------------------------------------------------------------------- #
def fig1_workflow():
    fig, ax = plt.subplots(figsize=(W2, 3.4))
    ax.set_xlim(0, 100); ax.set_ylim(-1, 50); ax.axis("off")

    def box(x, y, w, h, text, fc, ec="#7a7a7a", fs=6.6, bold=False):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.4,rounding_size=1.2", fc=fc, ec=ec, lw=0.5))
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs,
                fontweight="bold" if bold else "normal", linespacing=1.25)

    def arrow(x0, y0, x1, y1):
        ax.annotate("", (x1, y1), (x0, y0), arrowprops=dict(arrowstyle="-|>", lw=0.6, color=style.GREY,
                                                             shrinkA=0, shrinkB=0, mutation_scale=7))

    fin, fmod, fout = style.tint(style.C[0], 0.85), style.tint(style.C[3], 0.8), style.tint(style.C[2], 0.82)
    ax.text(10, 48.5, "Files on a node", ha="center", fontsize=7.5, fontweight="bold")
    ax.text(50, 48.5, "node_toolbox modules", ha="center", fontsize=7.5, fontweight="bold")
    ax.text(89, 48.5, "Outputs", ha="center", fontsize=7.5, fontweight="bold")
    ins = [(40, "DigiSolo.LOG\nstate of health"), (31, "seisNNN?.DLD\nsamples + GPS tags"),
           (22, "PULSE_{X,Y,Z}.WAV\npulse test"), (13, "sct_par.xml,\ndevice.ini"),
           (4, "SoloLite export\n(optional)")]
    for y, t in ins:
        box(1, y, 18, 6, t, fin)
    mods = [(40, "smartsolo_log", "read_log(s), read_device_info: one wide DataFrame, UTC index"),
            (31, "smartsolo_dld", "header, GPS tags, int24 blocks; GPS-time-of-week timing"),
            (22, "smartsolo_node", "pulse-test fit, geophone QC, script limits, response"),
            (13, "smartsolo_locate", "first stable fix, deployments; radius/polygon/time selection"),
            (4, "smartsolo_waveforms", "index, cut, SEED codes, polarity, gain, export")]
    for y, n, t in mods:
        box(27, y, 46, 6, "", fmod)
        ax.text(50, y + 4.1, n, ha="center", va="center", fontsize=6.9, fontweight="bold", family="monospace")
        ax.text(50, y + 1.7, t, ha="center", va="center", fontsize=5.7)
    ax.text(50, 0.3, "+ smartsolo_orientation: eCompass/tilt statistics, IGRF declination, rotation to N/E",
            ha="center", va="center", fontsize=5.9, style="italic")
    outs = [(40, "pandas DataFrame\n(temp., voltage, GPS)"), (31, "QC tables\n(geophone, pulse, tilt)"),
            (22, "deployment table\n(GeoDataFrame, CSV)"), (13, "ObsPy Stream,\nMiniSEED / SEG-Y"),
            (4, "StationXML with\nresponse, azimuths")]
    for y, t in outs:
        box(79, y, 20, 6, t, fout)
    for (yi, _), (ym, _, _) in zip(ins, mods):
        arrow(19.5, yi + 3, 26.5, ym + 3)
    for (ym, _, _), (yo, _) in zip(mods, outs):
        arrow(73.5, ym + 3, 78.5, yo + 3)
    save(fig, "fig01_workflow")


# --------------------------------------------------------------------------- #
def fig2_dld():
    f = dfile("453004362")
    h = dld.read_dld_header(f)
    tags = dld.read_dld_tags(f)
    st = dld.read_dld(f)
    tr = st[0]

    fig = plt.figure(figsize=(W2, 3.6))
    gs = fig.add_gridspec(2, 2, height_ratios=[1, 1.15], width_ratios=[1.35, 1], hspace=0.55, wspace=0.25)
    ax = fig.add_subplot(gs[0, :]); ax.set_xlim(0, 100); ax.set_ylim(0, 10); ax.axis("off")
    hc, sc_, tc = style.tint(style.C[0], 0.7), style.tint(style.C[3], 0.72), style.tint(style.C[1], 0.7)
    segs = [(0, 9, "header\n512 B", hc), (9, 30, "1000 samples × int24 LE\n3000 B", sc_),
            (39, 6, "tag\n72 B", tc), (45, 30, "1000 samples × int24 LE\n3000 B", sc_),
            (75, 6, "tag\n72 B", tc), (81, 6, "…", "white"), (87, 6, "tag", tc)]
    for x, w, t, c in segs:
        ax.add_patch(Rectangle((x, 5.2), w, 3.2, fc=c, ec="#6b6b6b", lw=0.5))
        ax.text(x + w / 2, 6.8, t, ha="center", va="center", fontsize=6.3)
    ax.text(0, 9.3, f"seis000Z.DLD of node {h['serial']}: {h['file_size']:,} B = 512 + {len(tags)} × (3000 + 72) B",
            fontsize=7, fontweight="bold")
    fields = ["flag\nint32", "tick (ms)\nint64", "UTC text\nHHMMSS.00", "date text\nYYYYMMDD", "latitude\nfloat64",
              "phase\nint32", "counter\nint32", "longitude\nfloat64", "GPS TOW (ms)\ntext"]
    widths = [4.5, 7, 10, 9, 7.5, 5.5, 6, 7.5, 13]
    x = 6
    ax.plot([39, x], [5.2, 3.6], ":", color=style.GREY, lw=0.6); ax.plot([45, x + sum(widths)], [5.2, 3.6], ":", color=style.GREY, lw=0.6)
    offs = [0, 4, 12, 23, 32, 40, 44, 48, 56]
    for w, t, o in zip(widths, fields, offs):
        ax.add_patch(Rectangle((x, 0.4), w, 3.2, fc=style.tint(style.C[1], 0.86), ec="#6b6b6b", lw=0.5))
        ax.text(x + w / 2, 2.0, t, ha="center", va="center", fontsize=5.4, linespacing=1.1)
        ax.text(x + 0.2, 3.75, f"+{o}", fontsize=5, va="bottom")
        x += w
    ax.text(x + 1.5, 2.0, "header: serial, firmware, project,\nstart/end, sample interval,\n"
            "leap seconds (0x104),\nposition at file close", fontsize=5.8, va="center")
    label(ax, "a", x=0.0, y=0.98)

    ax1 = fig.add_subplot(gs[1, 0])
    i = 300 * 1000
    seg = tr.data[i - 120:i + 120].astype(float)
    tt = (np.arange(len(seg)) - 120) / tr.stats.sampling_rate * 1e3
    ax1.plot(tt, seg, ".-", color=style.INK, ms=1.6, lw=0.4)
    ax1.axvline(0, color=style.ACCENT, lw=0.8, ls="--")
    ax1.set_ylim(seg.min() * 1.1, seg.max() + 0.6 * np.ptp(seg))
    ax1.text(0.98, 0.97, "dashed: 72-B tag between samples 299 999 and 300 000", color=style.ACCENT, fontsize=6,
             transform=ax1.transAxes, ha="right", va="top")
    ax1.set(xlabel="time from block boundary (ms)", ylabel="counts (raw Z)")
    d = np.abs(np.diff(tr.data.astype(float)))
    ax1.set_title(f"Samples are continuous across tags: median |Δ| {np.median(d[999::1000]):.0f} at\n"
                  f"boundaries vs {np.median(d):.0f} elsewhere ({len(tags) - 1} boundaries)", fontsize=6.8)
    label(ax1, "b")

    ax2 = fig.add_subplot(gs[1, 1])
    t = tags.set_index("time")
    ax2.plot(t.index, t.counter / 100, "-", color=style.INK, lw=0.7)
    ax2.set(ylabel="tag counter / 100 (s)", title="Counter: time since last GPS sync (black)")
    ax2b = ax2.twinx()
    ax2b.plot(t.index, t.tick_ms.diff() / 1000, "-", color=style.C[0], lw=0.7)
    ax2b.set_ylabel("tick step (s per 1000 samples)", color=style.C[0]); ax2b.set_ylim(0, 3)
    ax2b.tick_params(axis="y", colors=style.C[0]); ax2b.spines["right"].set_visible(True)
    ax2b.spines["right"].set_color(style.C[0])
    loc_ = mdates.AutoDateLocator(maxticks=4)
    ax2.xaxis.set_major_locator(loc_); ax2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax2.set_xlabel("UTC, 31 Mar 2023")
    label(ax2, "c")
    save(fig, "fig02_dld_format")


# --------------------------------------------------------------------------- #
def fig3_soh():
    df = sl.read_logs(DML)
    deps = loc.build_deployments(DML)
    rec = so.compass_records(df)
    fig, axes = plt.subplots(4, 1, figsize=(W2, 5.0), sharex=True)
    for s, g in df.groupby("serial"):
        c = DCOL[s]
        tmp = g.temperature.dropna(); axes[0].plot(tmp.index, tmp, ".", ms=1.2, color=c, label=s)
        v = g.voltage.dropna(); axes[1].plot(v.index, v, ".", ms=1.5, color=c)
        r = rec[rec.serial == s]
        axes[2].plot(r.index, r.tilt, ".", ms=1.2, color=c)
        axes[3].plot(r.index, r.heading_raw, ".", ms=1.2, color=c)
    for _, d in deps.iterrows():
        for a in axes:
            a.axvspan(d.start, d.end, color=DCOL[d.serial], alpha=0.05, lw=0)
    axes[0].set_ylabel("temperature (°C)"); axes[0].set_ylim(-20, 15); axes[0].legend(markerscale=6, loc="upper right", ncol=2, frameon=True, framealpha=0.85, edgecolor="none")
    axes[1].set_ylabel("battery (V)")
    axes[2].set_ylabel("tilt (°)"); axes[2].axhline(3, color=style.GREY, ls=":", lw=0.7)
    axes[2].text(deps.start.min(), 3.3, "3° spec, horizontal geophones", fontsize=6)
    axes[2].set_ylim(0, 12)
    axes[3].set_ylabel("eCompass North (°)"); axes[3].set_ylim(80, 200)
    for a, l in zip(axes, "abcd"):
        style.faint_grid(a, "y"); a.text(0.005, 0.96, l, transform=a.transAxes, fontsize=9, fontweight="bold", va="top")
    lc = mdates.AutoDateLocator(); axes[-1].xaxis.set_major_locator(lc)
    axes[-1].xaxis.set_major_formatter(mdates.ConciseDateFormatter(lc))
    axes[0].set_title("Dronning Maud Land, Antarctica: state of health from DigiSolo.LOG (shading: deployments)")
    save(fig, "fig03_state_of_health")


# --------------------------------------------------------------------------- # FIX
def fig4_location():
    df = sl.read_logs(DML)
    deps = loc.build_deployments(DML).set_index("serial")
    fig = plt.figure(figsize=(W2, 2.55))
    h = 1.6                                    # same plot height for a, b (square) and c
    axes = style.row_axes(fig, [h, h, W2 - 0.55 - 0.6 - 0.8 - 2 * h - 0.12], h, left_in=0.55,
                          gaps_in=[0.6, 0.8], bottom_in=0.42)

    def en(g, d):
        e = np.asarray(loc.distance_m(d.latitude, g.longitude, d.latitude, d.longitude)) * np.sign(g.longitude - d.longitude)
        n = np.asarray(loc.distance_m(g.latitude, d.longitude, d.latitude, d.longitude)) * np.sign(g.latitude - d.latitude)
        return np.asarray(e), np.asarray(n)

    for k, (s, lim) in enumerate([("453021267", 12), ("453022522", 80)]):
        d = deps.loc[s]
        g = df[(df.serial == s) & (df.session == d.session) & df.latitude.notna()]
        g = g[g.index >= d.start]
        e, n = en(g, d)
        days = (g.index - d.start).total_seconds() / 86400
        sc = axes[k].scatter(e, n, c=days, s=1.2, cmap=style.CMAP, lw=0)
        nsk = int(d.unstable_fixes_skipped or 0)
        if nsk:
            axes[k].plot(e[:nsk], n[:nsk], "x", color=style.ACCENT, ms=4, mew=1, label=f"{nsk} cold-start fixes,\nskipped")
            axes[k].legend(loc="lower right")
        axes[k].plot(0, 0, "+", color=style.INK, ms=8, mew=1)
        axes[k].set(xlim=(-lim, lim), ylim=(-lim, lim), xlabel="east (m)", ylabel="north (m)")
        axes[k].set_title(f"{s}: fixes relative to\nfirst stable fix (+)", fontsize=7)
        label(axes[k], "ab"[k], x=-0.18)
        r = pd.Series(np.asarray(loc.distance_m(g.latitude, g.longitude, d.latitude, d.longitude)), index=g.index)
        rr = r[r.index >= d.fix_time].rolling("1D").median()
        axes[2].plot(rr.index, rr, color=DCOL[s], label=f"{s}: drift {d.drift_m:.1f} m")
    cax = axes[0].inset_axes([0.07, 0.08, 0.04, 0.5])
    cb = fig.colorbar(sc, cax=cax); cb.set_label("days", fontsize=5.5, labelpad=1)
    cb.ax.tick_params(labelsize=5)
    axes[2].set(ylabel="distance from first stable fix (m)\n1-day running median", ylim=(0, 6), title="Position drift on ice")
    axes[2].legend(loc="upper left"); style.faint_grid(axes[2], "y"); label(axes[2], "c", x=-0.2)
    lc = mdates.AutoDateLocator(maxticks=5); axes[2].xaxis.set_major_locator(lc)
    axes[2].xaxis.set_major_formatter(mdates.ConciseDateFormatter(lc))
    save(fig, "fig04_location")


# --------------------------------------------------------------------------- #
def fig5_pulse():
    folder = ROOT / "data/nodes/453022522"
    tr = sn.read_pulse(folder / "PULSE_Z.WAV")
    res = sn.analyse_pulse(tr)
    sr = tr.stats.sampling_rate
    x = tr.data.astype(float)
    fig = plt.figure(figsize=(W2, 2.55))
    h = 1.65                                   # same plot height for a, b and the square c
    ax, axz, ax3 = style.row_axes(fig, [1.95, W2 - 0.6 - 0.7 - 0.75 - 1.95 - h - 0.1, h], h, left_in=0.6,
                                  gaps_in=[0.7, 0.75], bottom_in=0.42)
    cpv = sn.counts_per_volt(0)
    ax.plot(np.arange(len(x)) / sr, x / cpv * 1e3, color=style.INK, lw=0.4)
    best = res["steps"]
    for _, r in best.iterrows():
        ax.axvline(r.time_s, color=style.ACCENT if r.kind == "off" else style.C[0], lw=0.6, ls=":")
    ax.set(xlabel="s (1000 sps)", ylabel="mV at ADC", title="PULSE_Z.WAV (453022522), 16-s test")
    label(ax, "a")
    # zoom on a switch-off fit
    r = best[(best.kind == "off") & (best.polarity < 0)].iloc[0]
    e = int(r.time_s * sr)
    seg = x[e + 3:e + int(0.9 * sr)]
    t = np.arange(len(seg)) / sr
    axz.plot(t, seg / cpv * 1e3, ".", color=style.INK, ms=1.2, label="data")
    from scipy.optimize import curve_fit
    p, _ = curve_fit(sn._damped, t, seg, p0=[r.amplitude, r.f0_hz, r.damping, 0, np.median(seg[-100:])], maxfev=20000)
    axz.plot(t, sn._damped(t, *p) / cpv * 1e3, color=style.ACCENT, lw=1, label=f"fit f0={p[1]:.2f} Hz\nh={p[2]:.2f}")
    axz.set(xlabel="s after switch-off", ylabel="mV", title="damped-oscillator fit"); axz.legend(loc="center right")
    label(axz, "b")
    # pulse vs logged boot test, all nodes
    rows = []
    folders = [folder] + [sn.find_node_folders(BT / s)[0] for s in SERIALS]
    for fo in folders:
        node = sn.read_node_folder(fo)
        info = sl.read_device_info(fo / "DigiSolo.LOG")
        qc = sn.geophone_qc(info)
        last = qc[qc.boot_no == qc.boot_no.max()]
        for _, pr in node["pulse"].iterrows():
            ch = {"X": 1, "Y": 2, "Z": 3}[pr.axis]
            q = last[last.channel == ch]
            if len(q):
                rows.append(dict(serial=fo.name if fo.parent.name != "break_test" else fo.parent.name, axis=pr.axis,
                                 f0_pulse=pr.f0_hz, h_pulse=pr.damping, f0_log=q.resonate_freq_hz.iloc[0],
                                 h_log=q.damping.iloc[0], noise=pr.noise_rms_uV))
    R = pd.DataFrame(rows)
    R = R[(R.f0_log.between(3, 8)) & (R.h_log.between(0.3, 1))]
    ax3.plot(R.f0_log, R.f0_pulse, "o", ms=3, mfc="none", color=style.C[0], label="f0 (Hz)")
    ax3.plot([4.4, 5.6], [4.4, 5.6], ":", color=style.GREY, lw=0.7)
    ax3.set(xlabel="logged boot test f0 (Hz)", ylabel="pulse-file fit f0 (Hz)", xlim=(4.4, 5.6), ylim=(4.4, 5.6),
            title=f"{len(R)} channels, 7 nodes")
    label(ax3, "c")
    ax3.text(5.58, 4.42, f"median |Δf0| = {np.median(np.abs(R.f0_pulse - R.f0_log)):.2f} Hz\n"
             f"median |Δh| = {np.median(np.abs(R.h_pulse - R.h_log)):.3f}\nnoise {R.noise.min():.2f}–{R.noise.max():.2f} µV",
             fontsize=5.8, va="bottom", ha="right")
    save(fig, "fig05_pulse_test")
    R.to_csv(OUT / "fig05_pulse_vs_log.csv", index=False)



# --------------------------------------------------------------------------- #
def fig6_timing():
    from obspy import UTCDateTime
    from obspy.signal.cross_correlation import correlate, xcorr_max
    ex = ROOT / "data/timing_example"
    files = {"453009194": ex / "453009194_seis001Z_excerpt.DLD", "453010047": ex / "453010047_seis001Z_excerpt.DLD"}
    fig = plt.figure(figsize=(W2, 2.45))
    axes = style.row_axes(fig, [2.85, 2.85], 1.6, left_in=0.6, gaps_in=0.8, bottom_in=0.42)
    ax = axes[0]
    for (s, f), c, ls in zip(files.items(), [style.C[0], style.C[3]], ["-", "--"]):
        t = dld.read_dld_tags(f)
        ax.step(t.time_tow, t.label_minus_tow_s, where="post", color=c, ls=ls, lw=1.2, label=s)
    ax.set(ylim=(-1.6, 1.8), ylabel="text label − UTC(TOW) (s)", title="Time tags of two neighbouring nodes")
    ax.annotate("label 00:58:37 repeated:\nreceiver learns leap seconds", xy=(pd.Timestamp("2023-04-07 00:58:37", tz="UTC"), 0.0),
                xytext=(pd.Timestamp("2023-04-07 00:58:50", tz="UTC"), 0.9), fontsize=6, arrowprops=dict(arrowstyle="->", lw=0.6))
    ax.legend(loc="center left"); style.faint_grid(ax, "y"); label(ax, "a")
    ax = axes[1]
    for src, mk, c in [("label", "o", style.ACCENT), ("tow", "x", style.C[0])]:
        rows, t = [], UTCDateTime("2023-04-07T00:57:10")
        while t + 20 <= UTCDateTime("2023-04-07T01:00:05"):
            x, y = (dld.read_dld(f, starttime=t, endtime=t + 20, time_source=src).merge(fill_value=0)[0] for f in files.values())
            for z in (x, y):
                z.detrend("demean"); z.filter("bandpass", freqmin=2, freqmax=40)
            n = min(x.stats.npts, y.stats.npts)
            lag, cc = xcorr_max(correlate(x.data[:n], y.data[:n], 1500), abs_max=False)
            rows.append((pd.Timestamp((t + 10).datetime, tz="UTC"), lag / 500)); t += 10
        r = pd.DataFrame(rows, columns=["t", "lag"])
        ax.plot(r.t, r.lag, mk, ms=4, color=c, mfc="none", label=f"time_source='{src}'")
    ax.axvline(pd.Timestamp("2023-04-07 00:58:37", tz="UTC"), color=style.GREY, ls=":", lw=0.7)
    ax.set(ylim=(-0.5, 2.6), ylabel="lag of max. correlation (s)", title="453009194 vs 453010047 (21 m), 20-s windows")
    ax.legend(loc="center right"); style.faint_grid(ax, "y"); label(ax, "b")
    for a in axes:
        a.xaxis.set_major_locator(mdates.MinuteLocator()); a.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        a.set_xlabel("UTC, 7 Apr 2023")
    save(fig, "fig06_timing")


FIGS = {1: fig1_workflow, 2: fig2_dld, 3: fig3_soh, 4: fig4_location, 5: fig5_pulse, 6: fig6_timing}

NEEDS_DLD = {2, 6}          # figures that read raw DLD files (stored with Git LFS)


def dld_available():
    files = list(BT.rglob("*.DLD")) + list((ROOT / "data/timing_example").glob("*.DLD"))
    return bool(files) and all(f.read_bytes()[:len(dld.DLD_MAGIC)] == dld.DLD_MAGIC for f in files)


if __name__ == "__main__":
    which = [int(a) for a in sys.argv[1:]] or list(FIGS)
    have_dld = dld_available()
    if not have_dld and NEEDS_DLD & set(which):
        print("The raw DLD files are Git LFS pointers (not downloaded). Run\n"
              "    git lfs install && git lfs pull\n"
              "in the repository (install Git LFS first, e.g. 'brew install git-lfs').\n"
              f"Skipping figures {sorted(NEEDS_DLD & set(which))}.")
    for k in which:
        if k in NEEDS_DLD and not have_dld:
            continue
        FIGS[k]()
