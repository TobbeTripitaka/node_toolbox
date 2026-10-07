"""Synthetic huddle batch for testing the whole chain without nodes.

Writes fake node "drives" (``device.ini``, ``sct_par.xml``, ``seis000{X,Y,Z}.DLD``)
for every unit of a batch, with the planned sample rate, gain, filter phase
and rotation, raw SmartSolo polarity (x -1), and optional faults, plus a
reference station as MiniSEED. The analysis must recover the faults.

Faults (``faults`` dict, keyed by firmware or unit):
    offset_s       constant clock offset (s, + = node late)
    block_end      True: tags written at the end of each block
    flip           list of components with reversed polarity, e.g. ["Z"]
    gain_error     amplitude factor (1.0 = correct)
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pandas as pd

from . import config as hc

FS = 4000                         # internal "true ground" rate
GPS_EPOCH = pd.Timestamp("1980-01-06", tz="UTC")
LEAP = 18


def _header_template():
    f = next((hc.REPO / "data" / "break_test").rglob("seis000Z.DLD"))
    return bytearray(f.read_bytes()[:512])


def _put(buf, off, size, text):
    b = text.encode()[:size - 1]
    buf[off:off + size] = b + b"\x00" * (size - len(b))


def ground_motion(t0, duration_s, events, seed=0):
    """Ground velocity Z, N, E at FS: a 12 Hz Ricker-like pulse per impact with
    differing horizontal shapes, plus noise."""
    rng = np.random.default_rng(seed)
    n = int(duration_s * FS)
    t = np.arange(n) / FS
    out = {c: 0.02 * rng.standard_normal(n) for c in "ZNE"}
    for k, te in enumerate(events):
        tau = t - te
        for c, f, a in (("Z", 12.0, 1.0), ("N", 9.0, 0.7 * (1 if k % 2 else -1) + 0.1), ("E", 7.0, 0.5)):
            arg = (np.pi * f * tau) ** 2
            out[c] += a * (1 - 2 * arg) * np.exp(-arg) * (1 + 0.3 * np.sin(2 * np.pi * 3 * tau)) * (np.abs(tau) < 1.5)
    return out


def _node_filter(x, rate, phase):
    from scipy.signal import butter, sosfilt, sosfiltfilt
    sos = butter(8, 0.4 * rate, fs=FS, output="sos")
    return sosfiltfilt(sos, x) if phase == "linear" else sosfilt(sos, x)


def write_dld(path, data, rate, t_first, serial, firmware, block_end=False, lat=-42.9, lon=147.3):
    """Minimal DLD file (header, 1000-sample blocks, 72-byte tags)."""
    h = _header_template()
    _put(h, 0x20, 16, serial); _put(h, 0x80, 16, firmware); _put(h, 0x40, 32, "HUDDLE")
    struct.pack_into("<i", h, 0x104, LEAP)
    struct.pack_into("<dd", h, 0x190, lon, lat)
    nb = len(data) // 1000
    data = np.asarray(data[:nb * 1000], dtype=np.int64)
    raw = np.empty((len(data), 3), np.uint8)
    v = data & 0xFFFFFF
    raw[:, 0], raw[:, 1], raw[:, 2] = v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF
    block_s = 1000 / rate
    parts = [bytes(h)]
    for k in range(nb):
        parts.append(raw[k * 1000:(k + 1) * 1000].tobytes())
        tt = t_first + pd.Timedelta(seconds=(k + (1 if block_end else 0)) * block_s)
        gps = (tt - GPS_EPOCH).total_seconds() + LEAP
        week, tow = int(gps // 604800), int(round((gps % 604800) * 1000))
        lab = tt - pd.Timedelta(seconds=1)                      # text labels 1 s early once leap known
        tag = bytearray(72)
        struct.pack_into("<iq", tag, 0, 0, (week << 32) | tow)
        _put(tag, 12, 11, lab.strftime("%H%M%S") + ".00"); tag[21:23] = b"\x00\x00"
        _put(tag, 23, 9, lab.strftime("%Y%m%d"))
        struct.pack_into("<d", tag, 32, lat); struct.pack_into("<ii", tag, 40, 0, (k * 100) % 6000)
        struct.pack_into("<d", tag, 48, lon)
        _put(tag, 56, 16, str(tow))
        parts.append(bytes(tag))
    Path(path).write_bytes(b"".join(parts))


LOW_CUT_HZ = {"0.05": 0.05, "0.2": 0.2, "0.8": 0.8, "1.25": 1.25, "7.5": 7.5, "dc": 0.01,
              "unknown_2": 0.1, "unknown_10": 3.0}   # unknown_*: arbitrary values for the synthetic data only


def _low_cut(x, level):
    from scipy.signal import butter, sosfilt
    if str(level) in ("off", "", "nan"):
        return x
    return sosfilt(butter(2, LOW_CUT_HZ[str(level)], btype="highpass", fs=FS, output="sos"), x)


def _serials(cfg):
    units = hc.load_units(cfg)
    if (units.serial == "").any():
        units.loc[units.serial == "", "serial"] = [f"9990{i:05d}" for i in range(1, (units.serial == "").sum() + 1)]
        units.to_csv(cfg["units"], index=False)
    return dict(zip(units.unit, units.serial))


def _boot_block(no, fw, serial, rtc, tags):
    """A [DeviceInfo] block echoing the script tags, as real nodes write it."""
    aa = {"1": "Minimum", "2": "Linear"}.get(str(tags.get("Anti_Alias_Filter", "2")), "Linear")
    gps = {"1": "CycleOff", "2": "AlwaysOn"}.get(str(tags.get("GPS_Mode", "1")), "CycleOff")
    lines = [f"[DeviceInfo{no:05d}]", "BootReason = 0007", f"Firmware Version = {fw}", f"Serial Number = {serial}",
             "BootLoader Version = V1.0.1.8", f'Boot RTC ="{rtc}"',
             f"Script Name = {tags.get('script_file_name', 'factory')}", "Device Type = IGU-16HR 3C 5Hz",
             f"FIFO Storage MODE = {tags.get('Flash_FIFO_MODE', 1)}", f"GPS Power Mode = {gps}",
             "Channel Number = 3", f"Sample Rate = {tags.get('Sample_Rate', 100)}"]
    lines += [f"Channel {c} Gain = {tags.get(f'Channel_{c}_Gain', 0)}" for c in (1, 2, 3)]
    lines += [f"Anti-alias Filter Type = {aa}", f"Low Cutter Filter = {tags.get('Low_Cut_Filter', 0)}",
              f"ADC LP Mode = {tags.get('HR_LP_Mode', 0)}", "Voltage Threshold = 650,600", ""]
    return "\r\n".join(lines) + "\r\n"


def _append_boot(node, fw, serial, when, tags):
    log = Path(node) / "DigiSolo.LOG"
    txt = log.read_text() if log.exists() else "<00000,00000,00000,00000,00000,00000,00000>\r\n\r\n"
    import re
    no = len(re.findall(r"\[DeviceInfo\d+\]", txt)) + 1
    log.write_text(txt + _boot_block(no, fw, serial, pd.Timestamp(when).strftime("%Y/%m/%d,%H:%M:%S"), tags))


def make_nodes(cfg, mount_root, factory_firmware="V1.1.4.2be", stale_device_ini=True):
    """Blank fake nodes for all units in ``units.csv``, as they arrive for the
    test: a factory script, a log with one factory boot and one boot after
    flashing the unit's planned firmware, and (``stale_device_ini``) a
    device.ini still saying the factory firmware - as seen on real nodes."""
    serial = _serials(cfg)
    units = hc.load_units(cfg)
    for u in units.itertuples():
        node = Path(mount_root) / f"NODE_{serial[u.unit]}"
        node.mkdir(parents=True, exist_ok=True)
        fw_ini = factory_firmware if stale_device_ini else u.firmware
        (node / "device.ini").write_text(f"[deviceInfo]\r\nsn={serial[u.unit]}\r\nfirmwareVersion={fw_ini}\r\n")
        (node / "sct_par.xml").write_text("<script_Information>\n<script_file_name>factory</script_file_name>\n"
                                          "</script_Information>\n")
        (node / "DigiSolo.TXT").write_text("This is DigiSolo IGU-16 File System\r\n")
        _append_boot(node, factory_firmware, serial[u.unit], "2026-06-05T01:00:00", {})
        _append_boot(node, u.firmware, serial[u.unit], "2026-10-07T04:42:00", {})
    return serial


def make_batch(cfg, batch, mount_root, ref_dir, t0=None, duration_s=200.0,
               faults=None, seed=0, n_events=14, channel_map=None):
    """Fake mounted nodes for ``batch`` under ``mount_root`` and a reference in ``ref_dir``.
    Serial numbers are written to ``units.csv`` if missing (999000001...).
    ``channel_map``: which component each logged channel is (synthetic truth for
    per-channel gains; default Ch1 = X, Ch2 = Y, Ch3 = Z). Low-power ADC mode
    doubles the noise; node MiniSEED output writes ``seis000X.MiniSeed`` etc."""
    from obspy import Stream, Trace, UTCDateTime

    faults = faults or {}
    channel_map = channel_map or {1: "X", 2: "Y", 3: "Z"}
    m = hc.load_matrix(cfg)
    rows = m[m.batch == batch]
    serial = _serials(cfg)
    rng = np.random.default_rng(seed)
    gaps = rng.uniform(3, 12, n_events)                      # irregular impact spacing
    events = 40 + np.cumsum(gaps)
    events = events[events < duration_s - 40]
    t0 = pd.Timestamp(t0) if t0 else pd.Timestamp("2026-10-20T00:00:00Z") + pd.Timedelta(days=batch - 1)
    g = ground_motion(t0, duration_s, events, seed)
    sens = 5e4                                  # 36 dB stays inside 24 bits
    for r in rows.itertuples():
        fl = {**faults.get(r.firmware, {}), **faults.get(r.unit, {})}
        node = Path(mount_root) / f"NODE_{serial[r.unit]}"
        node.mkdir(parents=True, exist_ok=True)
        if not (node / "device.ini").exists():
            (node / "device.ini").write_text(f"[deviceInfo]\r\nsn={serial[r.unit]}\r\nfirmwareVersion={r.firmware}\r\n")
        if not (node / "sct_par.xml").exists():
            (node / "sct_par.xml").write_text("<script_Information></script_Information>\n")
        import re
        tags = dict(re.findall(r"<(\w+)>([^<]*)</\1>", (node / "sct_par.xml").read_text()))
        _append_boot(node, r.firmware, serial[r.unit], t0, tags)    # the boot that recorded this batch
        fileno = len({f.name[:7].lower() for f in node.glob("seis*") if f.name[4:7].isdigit()})  # next seisNNN
        th = np.deg2rad(r.orientation_deg)
        comp = {"Z": g["Z"], "X": g["N"] * np.cos(th) + g["E"] * np.sin(th),
                "Y": -g["N"] * np.sin(th) + g["E"] * np.cos(th)}
        step = int(FS / r.sample_rate_sps)
        off = int(round(fl.get("offset_s", 0.0) * FS))
        ch_of = {v: k for k, v in channel_map.items()}
        for c, x in comp.items():
            if str(getattr(r, "adc_mode", "normal")) == "low_power":
                x = x + 0.02 * np.random.default_rng(seed + 7).standard_normal(len(x))
            y = _low_cut(_node_filter(x, r.sample_rate_sps, r.filter_phase), getattr(r, "low_cut", "off"))
            pol = -1.0 * (-1 if ("ZNE"["ZXY".index(c)] in fl.get("flip", [])) else 1)
            gdb = float(getattr(r, f"gain_ch{ch_of[c]}", r.gain_db))
            cnt = pol * y * sens * 10 ** (gdb / 20) * fl.get("gain_error", 1.0)
            start = 4 * FS                                     # node starts 4 s after t0
            samples = cnt[start - off::step] if start - off >= 0 else cnt[start::step]
            t_first = t0 + pd.Timedelta(seconds=4)
            if str(getattr(r, "storage", "dld")) == "miniseed":
                tr = Trace(np.round(samples).astype(np.int32))
                tr.stats.update(dict(network="", station=serial[r.unit][-5:], channel=c,
                                     sampling_rate=float(r.sample_rate_sps),
                                     starttime=UTCDateTime(t_first.isoformat())))
                tr.write(str(node / f"seis{fileno:03d}{c}.MiniSeed"), format="MSEED", encoding="STEIM2", reclen=512)
                continue
            write_dld(node / f"seis{fileno:03d}{c}.DLD", np.round(samples), r.sample_rate_sps,
                      t_first, serial[r.unit], r.firmware, block_end=fl.get("block_end", False))
    # reference station at 1000 sps, true polarity and time
    Path(ref_dir).mkdir(parents=True, exist_ok=True)
    st = Stream()
    for c in "ZNE":
        y = _node_filter(g[c], 1000, "linear")[::4]
        tr = Trace((y * sens).astype(np.float32))
        tr.stats.update(dict(network="XX", station="PEG", channel=f"HH{c}", sampling_rate=1000.0,
                             starttime=UTCDateTime(t0.isoformat())))
        st.append(tr)
    st.write(str(Path(ref_dir) / f"reference_batch{batch:02d}.mseed"), format="MSEED")
    return events
