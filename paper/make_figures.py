"""
Make the figures of the Seismica Software Report (paper/figures/).

    cd paper && python make_figures.py          # all figures
    python make_figures.py 3 7                  # selected figures

Needs the sample data (git lfs pull) and, for Fig. 7, internet access for
the Esri World Imagery basemap (contextily).
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
SERIALS = sorted(p.name for p in BT.iterdir() if p.is_dir())
COL = dict(zip(SERIALS, ["C4", "C0", "C5", "C1", "C2", "C3"]))
DCOL = {"453021267": "C0", "453022522": "C3"}
W2, W1 = 7.09, 3.39            # page width (18 cm) and column width (8.6 cm), inches

mpl.rcParams.update({"font.size": 7.5, "axes.titlesize": 8, "axes.labelsize": 7.5, "legend.fontsize": 6.5,
                     "xtick.labelsize": 6.5, "ytick.labelsize": 6.5, "axes.linewidth": 0.6,
                     "lines.linewidth": 0.8, "savefig.dpi": 300, "figure.dpi": 150,
                     "font.family": "DejaVu Sans", "axes.grid": False})


def save(fig, name):
    fig.savefig(OUT / f"{name}.pdf", bbox_inches="tight")
    fig.savefig(OUT / f"{name}.png", bbox_inches="tight", dpi=200)
    plt.close(fig)
    print("wrote", name)


def label(ax, s, x=-0.12, y=1.04):
    ax.text(x, y, s, transform=ax.transAxes, fontsize=9, fontweight="bold", va="bottom", ha="right")


def dfile(serial, comp="Z", session=0):
    return next((BT / serial).glob(f"*/seis{session:03d}{comp}.DLD"))


# --------------------------------------------------------------------------- #
def fig1_workflow():
    fig, ax = plt.subplots(figsize=(W2, 3.4))
    ax.set_xlim(0, 100); ax.set_ylim(-1, 50); ax.axis("off")

    def box(x, y, w, h, text, fc, ec="k", fs=6.6, bold=False):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.4,rounding_size=1.2", fc=fc, ec=ec, lw=0.7))
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs,
                fontweight="bold" if bold else "normal", linespacing=1.25)

    def arrow(x0, y0, x1, y1):
        ax.annotate("", (x1, y1), (x0, y0), arrowprops=dict(arrowstyle="-|>", lw=0.7, color="0.25",
                                                             shrinkA=0, shrinkB=0, mutation_scale=7))

    fin, fmod, fout = "#e8eef7", "#fdf1d8", "#e4f2e4"
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
    segs = [(0, 9, "header\n512 B", "#c9d6ea"), (9, 30, "1000 samples × int24 LE\n3000 B", "#fbe3b4"),
            (39, 6, "tag\n72 B", "#e7b8b8"), (45, 30, "1000 samples × int24 LE\n3000 B", "#fbe3b4"),
            (75, 6, "tag\n72 B", "#e7b8b8"), (81, 6, "…", "white"), (87, 6, "tag", "#e7b8b8")]
    for x, w, t, c in segs:
        ax.add_patch(Rectangle((x, 5.2), w, 3.2, fc=c, ec="k", lw=0.6))
        ax.text(x + w / 2, 6.8, t, ha="center", va="center", fontsize=6.3)
    ax.text(0, 9.3, f"seis000Z.DLD of node {h['serial']}: {h['file_size']:,} B = 512 + {len(tags)} × (3000 + 72) B",
            fontsize=7, fontweight="bold")
    fields = ["flag\nint32", "tick (ms)\nint64", "UTC text\nHHMMSS.00", "date text\nYYYYMMDD", "latitude\nfloat64",
              "phase\nint32", "counter\nint32", "longitude\nfloat64", "GPS TOW (ms)\ntext"]
    widths = [4.5, 7, 10, 9, 7.5, 5.5, 6, 7.5, 13]
    x = 6
    ax.plot([39, x], [5.2, 3.6], "k:", lw=0.6); ax.plot([45, x + sum(widths)], [5.2, 3.6], "k:", lw=0.6)
    offs = [0, 4, 12, 23, 32, 40, 44, 48, 56]
    for w, t, o in zip(widths, fields, offs):
        ax.add_patch(Rectangle((x, 0.4), w, 3.2, fc="#f6dede", ec="k", lw=0.5))
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
    ax1.plot(tt, seg, "k.-", ms=1.6, lw=0.5)
    ax1.axvline(0, color="C3", lw=0.8, ls="--")
    ax1.set_ylim(seg.min() * 1.1, seg.max() + 0.6 * np.ptp(seg))
    ax1.text(0.98, 0.97, "dashed: 72-B tag between samples 299 999 and 300 000", color="C3", fontsize=6,
             transform=ax1.transAxes, ha="right", va="top")
    ax1.set(xlabel="time from block boundary (ms)", ylabel="counts (raw Z)")
    d = np.abs(np.diff(tr.data.astype(float)))
    ax1.set_title(f"Samples are continuous across tags: median |Δ| {np.median(d[999::1000]):.0f} at\n"
                  f"boundaries vs {np.median(d):.0f} elsewhere ({len(tags) - 1} boundaries)", fontsize=6.8)
    label(ax1, "b")

    ax2 = fig.add_subplot(gs[1, 1])
    t = tags.set_index("time")
    ax2.plot(t.index, t.counter / 100, "k-", lw=0.7)
    ax2.set(ylabel="tag counter / 100 (s)", title="Counter: time since last GPS sync (black)")
    ax2b = ax2.twinx()
    ax2b.plot(t.index, t.tick_ms.diff() / 1000, "C0-", lw=0.7)
    ax2b.set_ylabel("tick step (s per 1000 samples)", color="C0"); ax2b.set_ylim(0, 3)
    ax2b.tick_params(axis="y", colors="C0")
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
            a.axvspan(d.start, d.end, color=DCOL[d.serial], alpha=0.06, lw=0)
    axes[0].set_ylabel("temperature (°C)"); axes[0].set_ylim(-20, 15); axes[0].legend(markerscale=6, loc="upper right", ncol=2)
    axes[1].set_ylabel("battery (V)")
    axes[2].set_ylabel("tilt (°)"); axes[2].axhline(3, color="k", ls=":", lw=0.7)
    axes[2].text(deps.start.min(), 3.3, "3° spec, horizontal geophones", fontsize=6)
    axes[2].set_ylim(0, 12)
    axes[3].set_ylabel("eCompass North (°)"); axes[3].set_ylim(80, 200)
    for a, l in zip(axes, "abcd"):
        a.grid(alpha=0.25); a.text(0.005, 0.96, l, transform=a.transAxes, fontsize=9, fontweight="bold", va="top")
    lc = mdates.AutoDateLocator(); axes[-1].xaxis.set_major_locator(lc)
    axes[-1].xaxis.set_major_formatter(mdates.ConciseDateFormatter(lc))
    axes[0].set_title("Dronning Maud Land, Antarctica: state of health from DigiSolo.LOG (shading: deployments)")
    save(fig, "fig03_state_of_health")


# --------------------------------------------------------------------------- #
def fig4_location():
    df = sl.read_logs(DML)
    deps = loc.build_deployments(DML).set_index("serial")
    fig, axes = plt.subplots(1, 3, figsize=(W2, 2.55), gridspec_kw=dict(width_ratios=[1, 1, 1.45], wspace=0.42))

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
        sc = axes[k].scatter(e, n, c=days, s=1.2, cmap="viridis", lw=0)
        nsk = int(d.unstable_fixes_skipped or 0)
        if nsk:
            axes[k].plot(e[:nsk], n[:nsk], "rx", ms=4, mew=1, label=f"{nsk} cold-start fixes,\nskipped")
            axes[k].legend(loc="lower right")
        axes[k].plot(0, 0, "k+", ms=8, mew=1)
        axes[k].set(xlim=(-lim, lim), ylim=(-lim, lim), aspect="equal", xlabel="east (m)", ylabel="north (m)")
        axes[k].set_title(f"{s}: fixes relative to\nfirst stable fix (+)", fontsize=7)
        label(axes[k], "ab"[k], x=-0.18)
        r = pd.Series(np.asarray(loc.distance_m(g.latitude, g.longitude, d.latitude, d.longitude)), index=g.index)
        rr = r[r.index >= d.fix_time].rolling("1D").median()
        axes[2].plot(rr.index, rr, color=DCOL[s], label=f"{s}: drift {d.drift_m:.1f} m")
    cax = axes[0].inset_axes([0.07, 0.08, 0.04, 0.5])
    cb = fig.colorbar(sc, cax=cax); cb.set_label("days", fontsize=5.5, labelpad=1)
    cb.ax.tick_params(labelsize=5)
    axes[2].set(ylabel="distance from first stable fix (m)\n1-day running median", ylim=(0, 6), title="Position drift on ice")
    axes[2].legend(loc="upper left"); axes[2].grid(alpha=.25); label(axes[2], "c", x=-0.15)
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
    fig = plt.figure(figsize=(W2, 2.7))
    gs = fig.add_gridspec(1, 3, width_ratios=[1.5, 1, 1.15], wspace=0.45)
    ax = fig.add_subplot(gs[0])
    cpv = sn.counts_per_volt(0)
    ax.plot(np.arange(len(x)) / sr, x / cpv * 1e3, "k", lw=0.5)
    best = res["steps"]
    for _, r in best.iterrows():
        ax.axvline(r.time_s, color="C3" if r.kind == "off" else "C0", lw=0.6, ls=":")
    ax.set(xlabel="s (1000 sps)", ylabel="mV at ADC", title="PULSE_Z.WAV (453022522), 16-s test")
    label(ax, "a")
    # zoom on a switch-off fit
    r = best[(best.kind == "off") & (best.polarity < 0)].iloc[0]
    e = int(r.time_s * sr)
    seg = x[e + 3:e + int(0.9 * sr)]
    t = np.arange(len(seg)) / sr
    axz = fig.add_subplot(gs[1])
    axz.plot(t, seg / cpv * 1e3, "k.", ms=1.2, label="data")
    from scipy.optimize import curve_fit
    p, _ = curve_fit(sn._damped, t, seg, p0=[r.amplitude, r.f0_hz, r.damping, 0, np.median(seg[-100:])], maxfev=20000)
    axz.plot(t, sn._damped(t, *p) / cpv * 1e3, "C3", lw=1, label=f"fit f0={p[1]:.2f} Hz, h={p[2]:.2f}")
    axz.set(xlabel="s after switch-off", ylabel="mV", title="damped-oscillator fit"); axz.legend(loc="upper right")
    label(axz, "b")
    # pulse vs logged boot test, all nodes
    rows = []
    folders = [folder] + [next((BT / s).iterdir()) for s in SERIALS]
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
    ax3 = fig.add_subplot(gs[2])
    ax3.plot(R.f0_log, R.f0_pulse, "o", ms=3, mfc="none", color="C0", label="f0 (Hz)")
    ax3.plot([4.4, 5.6], [4.4, 5.6], "k:", lw=0.7)
    ax3.set(xlabel="logged boot test f0 (Hz)", ylabel="pulse-file fit f0 (Hz)", xlim=(4.4, 5.6), ylim=(4.4, 5.6),
            aspect="equal", title=f"{len(R)} channels, 7 nodes")
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
    rows = []
    for s in SERIALS:
        tg = dld.read_dld_tags(dfile(s))
        rows.append((s, dld.read_dld_header(dfile(s))["leap_seconds"], tg.label_minus_tow_s.median()))

    def lags(a, b, src, t0="2023-03-31T01:35", t1="2023-03-31T01:50", win=30, step=30):
        out, t = [], UTCDateTime(t0)
        while t < UTCDateTime(t1):
            x = dld.read_dld(dfile(a), starttime=t, endtime=t + win, time_source=src)[0]
            y = dld.read_dld(dfile(b), starttime=t, endtime=t + win, time_source=src)[0]
            for z in (x, y):
                z.detrend("demean"); z.filter("bandpass", freqmin=2, freqmax=40)
            n = min(x.stats.npts, y.stats.npts)
            lag, cc = xcorr_max(correlate(x.data[:n], y.data[:n], 1500), abs_max=False)
            out.append((t.datetime, lag / 500, cc)); t += step
        return pd.DataFrame(out, columns=["time", "lag", "cc"]).set_index("time")

    fig, axes = plt.subplots(1, 3, figsize=(W2, 2.5), gridspec_kw=dict(width_ratios=[1.1, 1.3, 0.8], wspace=0.35))
    ax = axes[0]
    tt = np.array([0, 1, 2, 3, 4, 5, 6])
    ax.step(tt, np.where(tt < 3, 2, 0), where="post", color="C3", lw=1.2, label="text label − UTC(TOW)")
    ax.step(tt, np.zeros_like(tt) + 0.03, where="post", color="C0", lw=1.2, ls="--", label="UTC(TOW) − UTC")
    ax.axvline(3, color="0.5", lw=0.6, ls=":")
    ax.text(3.15, 1.0, "receiver learns\nleap seconds\n(header 0x104:\n0 → 18)", fontsize=5.8)
    ax.set(xlabel="time (schematic)", ylabel="offset (s)", ylim=(-0.5, 2.8), xticks=[], xlim=(0, 6),
           title="Text label vs GPS TOW")
    ax.legend(loc="upper right", fontsize=5.6)
    label(ax, "a")
    ax = axes[1]
    for (a, b, dist), c, mk in zip([("453009194", "453004362", 5.4), ("453010077", "453010167", 11.9)], ["C0", "C2"], ["x", "+"]):
        s_ = lags(a, b, "tow"); s_ = s_[s_.cc > 0.1]
        ax.plot(s_.index, s_.lag * 1e3, mk, ms=3.5, color=c,
                label=f"{a[-5:]}–{b[-5:]} ({dist:.0f} m): median {s_.lag.median() * 1e3:+.0f} ms")
    ax.set(ylim=(-300, 300), ylabel="lag of max. correlation (ms)", title="Neighbour lags (TOW, 30-s windows)")
    ax.legend(loc="lower left", fontsize=5.4); ax.grid(alpha=.25)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M")); ax.set_xlabel("UTC, 31 Mar 2023")
    label(ax, "b")
    ax = axes[2]
    R = pd.DataFrame(rows, columns=["serial", "leap", "off"])
    ax.barh(range(len(R)), R.off, color="C3", height=0.6)
    for i, r in enumerate(R.itertuples()):
        ax.text(0.08, i, f"{r.serial}  leap={r.leap}", color="w", va="center", fontsize=5.6)
    ax.set_yticks([])
    ax.set(xlabel="label − UTC(TOW) (s)", xlim=(0, 2.4), title="All six 31 Mar files")
    label(ax, "c", x=-0.05)
    save(fig, "fig06_timing")
    pd.DataFrame(rows, columns=["serial", "leap_seconds", "label_minus_tow_s"]).to_csv(OUT / "fig06_label_offsets.csv", index=False)


# --------------------------------------------------------------------------- #
def fig7_selection():
    import contextily as cx
    import geopandas as gpd
    from shapely.geometry import Polygon
    deps = loc.build_deployments_from_dld(BT)
    deps = deps[deps.session == 0].reset_index(drop=True)
    g = gpd.GeoDataFrame(deps, geometry=gpd.points_from_xy(deps.longitude, deps.latitude), crs=4326).to_crs(3857)
    centre = (-42.9017, 147.3301)
    sel_r = loc.select_deployments(deps, point=centre, radius_km=0.03, start="2023-03-31T01:40", end="2023-03-31T01:41")
    poly = Polygon([(147.3290, -42.9029), (147.3297, -42.9029), (147.3297, -42.9022), (147.3290, -42.9022)])
    sel_p = loc.select_deployments(deps, polygon=poly, start="2023-03-31T01:40", end="2023-03-31T01:41")
    fig, ax = plt.subplots(figsize=(W1, 3.6))
    for s in SERIALS:
        tg = dld.read_dld_tags(dfile(s))
        tg = tg[tg.latitude.abs() > 1]
        tp = gpd.GeoSeries(gpd.points_from_xy(tg.longitude, tg.latitude), crs=4326).to_crs(3857)
        ax.plot(tp.x, tp.y, "-", color=COL[s], lw=0.7, alpha=0.9)
    c = gpd.GeoSeries(gpd.points_from_xy([centre[1]], [centre[0]]), crs=4326).to_crs(3857)
    circ = gpd.GeoSeries(gpd.points_from_xy([centre[1]], [centre[0]]), crs=4326).to_crs(32755).buffer(30).to_crs(3857)
    circ.boundary.plot(ax=ax, color="yellow", lw=1.0, ls="--")
    gpd.GeoSeries([poly], crs=4326).to_crs(3857).boundary.plot(ax=ax, color="cyan", lw=1.0, ls="--")
    for _, r in g.iterrows():
        chosen = r.serial in set(sel_r.serial) | set(sel_p.serial)
        ax.plot(r.geometry.x, r.geometry.y, "^", ms=7, color=COL[r.serial], mec="w" if chosen else "k", mew=1.0)
        off = {"453004362": (6, -10), "453010047": (-30, -12), "453010077": (-34, 3)}.get(r.serial, (5, 4))
        ax.annotate(r.serial[-5:], (r.geometry.x, r.geometry.y), xytext=off, textcoords="offset points",
                    color="w", fontsize=6, fontweight="bold")
    b = g.total_bounds; pad = 45
    ax.set_xlim(b[0] - pad, b[2] + pad); ax.set_ylim(b[1] - pad - 40, b[3] + pad)
    cx.add_basemap(ax, source=cx.providers.Esri.WorldImagery, zoom=19, attribution_size=4)
    ax.set_xticks([]); ax.set_yticks([]); ax.set_xlabel(""); ax.set_ylabel("")
    ax.text(0.02, 0.98, "yellow: within 30 m of a point\ncyan: inside a polygon\nlines: GPS tags (nodes carried\naway after 01:51)",
            transform=ax.transAxes, va="top", fontsize=5.8, color="w")
    ax.set_title("Break test, Sandy Bay, Hobart, 31 Mar 2023")
    save(fig, "fig07_selection_map")
    print("radius:", sorted(sel_r.serial), "polygon:", sorted(sel_p.serial))


# --------------------------------------------------------------------------- #
def _envelope(tr, fmin=5, fmax=80, smooth=0.5, step=0.1):
    from scipy.signal import hilbert
    tr = tr.copy(); tr.detrend("demean"); tr.filter("bandpass", freqmin=fmin, freqmax=fmax)
    e = np.abs(hilbert(tr.data.astype(float)))
    k = int(smooth * tr.stats.sampling_rate); e = np.convolve(e, np.ones(k) / k, "same")
    n = int(step * tr.stats.sampling_rate)
    t0 = pd.Timestamp(tr.stats.starttime.datetime, tz="UTC")
    return pd.Series(e[::n], index=t0 + pd.to_timedelta(np.arange(len(e[::n])) * step, unit="s"))


def fig8_breaktest(tmp=ROOT / "output/paper_extract"):
    from obspy import UTCDateTime
    deps = loc.combine_deployments(loc.build_deployments(BT), loc.build_deployments_from_dld(BT))
    deps = deps[deps.start < pd.Timestamp("2023-04-01", tz="UTC")]
    idx = wf.index_waveforms([p for p in BT.rglob("seis000?.DLD")])
    t_ev = UTCDateTime("2023-03-31T01:35:12.5")
    t0, t1 = t_ev - 20, t_ev + 20
    st = wf.extract_waveforms(deps, idx, start=t0.datetime, end=t1.datetime, out_dir=tmp, out_format="MSEED")
    d0 = deps.set_index("serial")
    order = sorted(SERIALS, key=lambda s: loc.distance_m(d0.latitude[s], d0.longitude[s],
                                                         d0.latitude["453010047"], d0.longitude["453010047"]))
    dist = {s: loc.distance_m(d0.latitude[s], d0.longitude[s], d0.latitude["453010047"], d0.longitude["453010047"])
            for s in order}
    E = pd.DataFrame({s: _envelope(dld.read_dld(dfile(s))[0]) for s in SERIALS})

    fig = plt.figure(figsize=(W2, 5.6))
    gs = fig.add_gridspec(3, 2, height_ratios=[0.9, 1.6, 1.0], width_ratios=[1.6, 1], hspace=0.62, wspace=0.32)
    ax0 = fig.add_subplot(gs[0, :])
    for s in SERIALS:
        ax0.semilogy(E.index, E[s], lw=0.4, color=COL[s], label=s)
    ax0.axvline(t_ev.datetime, color="k", lw=0.6, ls=":")
    ax0.set(ylabel="Z envelope, 5–80 Hz\n(counts)", title="Six nodes, raw DLD: vehicle passes 01:34–01:50, handling after 01:50")
    ax0.set_ylim(5, 1e9); ax0.legend(ncol=6, loc="upper left", fontsize=5.6)
    ax0.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M")); label(ax0, "a", x=-0.04)
    ax1 = fig.add_subplot(gs[1, 0])
    for s in order:
        tr = st.select(station=s[-5:], channel="??Z").merge(fill_value=0)[0].copy(); tr.detrend("demean"); tr.filter("bandpass", freqmin=5, freqmax=80)
        y = tr.data / np.abs(tr.data).max() * 9
        ax1.plot(tr.times() - 20, y + dist[s], color=COL[s], lw=0.4)
        ax1.text(20.5, dist[s] + {"453004362": -4, "453009194": 4, "453010077": -3, "453010167": 3}.get(s, 0),
                 s[-5:], fontsize=5.5, va="center", color=COL[s])
    ax1.set(xlim=(-20, 20), xlabel="s from 01:35:12.5 UTC", ylabel="distance along road from 10047 (m)",
            title="Exported MiniSEED, ??.DPZ (×−1), 5–80 Hz")
    label(ax1, "b", x=-0.06)
    ax2 = fig.add_subplot(gs[1, 1])
    z = dld.read_dld(dfile("453010077"), starttime=t0, endtime=t1)[0]
    ax2.specgram(z.data.astype(float) - z.data.mean(), Fs=500, NFFT=512, noverlap=480, cmap="magma", vmin=-20)
    ax2.set(ylim=(0, 150), xlabel="s from 01:34:52.5", ylabel="Hz", title="453010077 Z spectrogram")
    label(ax2, "c")
    # speed between groups
    from scipy.signal import find_peaks
    T0, T1 = pd.Timestamp("2023-03-31 01:34:45", tz="UTC"), pd.Timestamp("2023-03-31 01:50:02", tz="UTC")

    def peaks(x, factor=8, sep=5.0):
        x = x.loc[T0:T1].dropna(); bg = x.median()
        p, pr = find_peaks(x.values, height=factor * bg, prominence=factor / 2 * bg, distance=int(sep / 0.1))
        return pd.Series(pr["peak_heights"] / bg, index=x.index[p])

    def pair(a, b, tol=5):
        pa, pb = peaks(E[a]), peaks(E[b]); out = []
        for t in pa.index:
            dt = np.abs((pb.index - t).total_seconds())
            if len(dt) and dt.min() <= tol:
                out.append(t + (pb.index[dt.argmin()] - t) / 2)
        return pd.DatetimeIndex(out)

    sw, ne = pair("453009194", "453010047"), pair("453010077", "453010167")
    pair_dist = np.mean([loc.distance_m(d0.latitude[a], d0.longitude[a], d0.latitude[b], d0.longitude[b])
                         for a in ("453009194", "453010047") for b in ("453010077", "453010167")])
    sp = []
    for t in ne:
        dt = (sw - t).total_seconds()
        if len(dt) and 2 < np.abs(dt).min() <= 20:
            sp.append(pair_dist / np.abs(dt).min() * 3.6)
    ax3 = fig.add_subplot(gs[2, 0])
    ax3.hist(sp, bins=np.arange(0, 105, 5), color="C2", ec="k", lw=0.5)
    ax3.set(xlabel="apparent speed between node groups (km/h)", ylabel="passes",
            title=f"{len(sw)} + {len(ne)} events, {len(sp)} matched passes, median {np.median(sp):.0f} km/h")
    label(ax3, "d", x=-0.06)
    ax4 = fig.add_subplot(gs[2, 1])
    from scipy.signal import welch
    tr = dld.read_dld(dfile("453010077"))[0]
    ev = [welch(tr.slice(UTCDateTime(t.to_pydatetime()) - 2, UTCDateTime(t.to_pydatetime()) + 2).data.astype(float),
                fs=500, nperseg=1024) for t in ne]
    q = E["453010077"].loc[T0:T1].idxmin()
    fq, pq = welch(tr.slice(UTCDateTime(q.to_pydatetime()) - 10, UTCDateTime(q.to_pydatetime()) + 10).data.astype(float),
                   fs=500, nperseg=1024)
    ax4.loglog(ev[0][0], np.median([e[1] for e in ev], 0), "C3", label="median of events")
    ax4.loglog(fq, pq, "k", label="quietest 20 s")
    ax4.set(xlim=(1, 250), xlabel="Hz", ylabel="PSD (counts²/Hz)", title="453010077 Z")
    ax4.legend(loc="lower left"); ax4.grid(alpha=.25, which="both"); label(ax4, "e")
    save(fig, "fig08_break_test")
    pd.DataFrame({"speed_kmh": sp}).to_csv(OUT / "fig08_speeds.csv", index=False)


FIGS = {1: fig1_workflow, 2: fig2_dld, 3: fig3_soh, 4: fig4_location, 5: fig5_pulse,
        6: fig6_timing, 7: fig7_selection, 8: fig8_breaktest}

if __name__ == "__main__":
    which = [int(a) for a in sys.argv[1:]] or list(FIGS)
    for k in which:
        FIGS[k]()
