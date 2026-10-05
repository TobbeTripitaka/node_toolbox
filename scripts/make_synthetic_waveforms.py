"""
Generate small synthetic SmartSolo-like waveform files for testing / the demo
notebook, matching the two sample logs in data/nodes/.

    python scripts/make_synthetic_waveforms.py            # -> data/seismic_traces

Layout written (one folder per node serial, as SoloLite exports do):

    data/seismic_traces/453021267/453021267.0001.2024.12.26.00.00.00.000.Z.miniseed   (MiniSEED, 1 h files)
    data/seismic_traces/453022522/453022522.2024.12.26.00.00.00.Z.sgy                 (SEG-Y, 5 min files)

Header station/network codes are left blank, as in raw exports, so the serial
must be recovered from the file/folder name. The data are random noise plus a
common "icequake" arriving at both nodes, in int32 counts at 100 Hz.
"""

import argparse
from pathlib import Path

import numpy as np
from obspy import Stream, Trace, UTCDateTime

SR = 100.0
START = UTCDateTime("2024-12-26T00:00:00")
EVENTS = [UTCDateTime("2024-12-26T00:12:34"), UTCDateTime("2024-12-26T00:47:10"),
          UTCDateTime("2024-12-26T01:21:05")]


def synth(serial, comp, t0, npts, rng):
    x = rng.normal(0, 200, npts)
    delay = 0.0 if serial.endswith("267") else 0.08   # ~200 m apart
    for ev in EVENTS:
        dt = (t0 - (ev + delay)) + np.arange(npts) / SR
        m = dt >= 0
        amp = 8000 if comp == "Z" else 4000
        x[m] += amp * np.exp(-dt[m] / 0.4) * np.sin(2 * np.pi * 12 * dt[m])
    tr = Trace(np.round(x).astype(np.int32))
    tr.stats.sampling_rate = SR
    tr.stats.starttime = t0
    return tr


def main(out="data/seismic_traces", hours_mseed=2, minutes_segy=60):
    out = Path(out)
    rng = np.random.default_rng(42)

    # node 1: MiniSEED, 1-hour files per component
    serial = "453021267"
    d = out / serial
    d.mkdir(parents=True, exist_ok=True)
    for h in range(hours_mseed):
        t0 = START + 3600 * h
        for comp in "ZNE":
            tr = synth(serial, comp, t0, int(3600 * SR), rng)
            fn = d / f"{serial}.{h + 1:04d}.{t0.strftime('%Y.%m.%d.%H.%M.%S')}.000.{comp}.miniseed"
            Stream([tr]).write(str(fn), format="MSEED", encoding="STEIM2", reclen=4096)

    # node 2: SEG-Y, 5-minute files per component (SEG-Y npts limit is 32767)
    serial = "453022522"
    d = out / serial
    d.mkdir(parents=True, exist_ok=True)
    for k in range(minutes_segy // 5):
        t0 = START + 300 * k
        for comp in "ZNE":
            tr = synth(serial, comp, t0, int(300 * SR), rng)
            tr.data = tr.data.astype(np.float32)
            fn = d / f"{serial}.{t0.strftime('%Y.%m.%d.%H.%M.%S')}.{comp}.sgy"
            Stream([tr]).write(str(fn), format="SEGY", data_encoding=5)
    print("synthetic data written to", out.resolve())


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data/seismic_traces")
    main(ap.parse_args().out)
