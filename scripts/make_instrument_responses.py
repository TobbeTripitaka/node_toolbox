#!/usr/bin/env python
"""
(Re)write instrument_responses/: StationXML files and response.yaml for
every response model.

    python scripts/make_instrument_responses.py            # all models
    python scripts/make_instrument_responses.py --no-nrl   # without downloading the NRL files

auspass, dtcc and nominal are built from the constants in
lib/smartsolo_node.py; nrl is downloaded from the EarthScope (IRIS) Nominal
Response Library web service.
"""

import argparse
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import smartsolo_node as sn  # noqa: E402

OUT = ROOT / "instrument_responses"
GAINS = (0, 6, 12, 18, 24, 30, 36)
NRL = "https://service.earthscope.org/irisws/nrl/1/combine"
NRL_SENSOR = {"IGU-16HR3C": "sensor_DTCC_DT-SOLO_LF5_RC1850_RS43000_SG76.6_STgroundVel",
              "BD3C-5": "sensor_DTCC_DT-SOLO-BB_LP5_SG209.4_STgroundVel"}
NRL_LOGGER = {"IGU-16HR3C": "datalogger_DTCC_SmartSolo-IGU-16HR3C_PD{g}_FR{r}_FP{p}_DFOff_OUcount",
              "BD3C-5": "datalogger_DTCC_SmartSolo-IGU-BD3C-5_PD{g}_FR{r}_FP{p}_DFOff_OUcount"}
NRL_RATES = {"IGU-16HR3C": (50, 100, 125, 250, 500, 1000), "BD3C-5": (100, 250, 500, 1000)}
NRL_GAINS = {"IGU-16HR3C": GAINS, "BD3C-5": (0, 6)}


def cplx(z):
    return [[float(np.real(x)), float(np.imag(x))] for x in z]


def inventory(resp, rate, device, model, gain):
    from obspy import UTCDateTime
    from obspy.core.inventory import Channel, Equipment, Inventory, Network, Site, Station
    t0 = UTCDateTime("2000-01-01")
    code = ("H" if rate >= 80 else "S") + ("H" if device == "BD3C-5" else "P") + "Z"
    ch = Channel(code=code, location_code="", latitude=0, longitude=0, elevation=0, depth=0, azimuth=0, dip=-90,
                 sample_rate=float(rate), start_date=t0, response=resp,
                 sensor=Equipment(type="Seismometer" if device == "BD3C-5" else "Geophone",
                                  manufacturer="DTCC SmartSolo", model=device,
                                  description=f"node_toolbox response model '{model}', preamp gain {gain} dB"))
    sta = Station(code="RESP", latitude=0, longitude=0, elevation=0, start_date=t0, channels=[ch],
                  site=Site(name=f"{model} {device} {gain} dB"))
    return Inventory(networks=[Network(code="XX", stations=[sta])], source="node_toolbox")


def write_yaml(model, meta):
    (OUT / model).mkdir(parents=True, exist_ok=True)
    (OUT / model / "response.yaml").write_text(
        "# Instrument response model for node_toolbox (smartsolo_response.get_response)\n"
        + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=100))


def staged_files(model, device_types=("IGU-16HR 3C 5Hz",)):
    files = []
    for dt in device_types:
        dev = "BD3C-5" if sn.is_bd3c(dt) else "IGU-16HR3C"
        for g in (GAINS if dev == "IGU-16HR3C" else (0, 6)):
            r = sn.staged_response(dt, 1000.0, g, model=model)
            f = f"{dev}_{g:02d}dB.xml"
            inventory(r, 1000.0, dev, model, g).write(str(OUT / model / f), format="STATIONXML")
            files.append(dict(file=f, device=dev, gain_db=g))
    return files


def make_auspass():
    (OUT / "auspass").mkdir(parents=True, exist_ok=True)
    files = staged_files("auspass", ("IGU-16HR 3C 5Hz", "IGU-16 BD3C 5s"))
    p, b = sn.AUSPASS_16HR3C, sn.BD3C_5S
    write_yaml("auspass", dict(
        title="AusPass / ANSIR published SmartSolo response (default)",
        use="Default. Published, huddle-tested response for counts with the polarity inverted (x -1).",
        source=[dict(name="AusPass - SmartSolo Node Seismometers",
                     url="https://auspass.edu.au/xwiki/bin/view/Instrumentation/SmartSolo%20Nodes/")],
        devices={"IGU-16HR3C": dict(
            zeros=cplx(p["zeros"]), poles=cplx(p["poles"]),
            f0_hz=5.0, damping=0.707,
            overall_counts_per_m_s_0dB=p["sensitivity"],
            stages=["sensor 76.6 V/(m/s) (poles/zeros, normalised at 15 Hz)",
                    "preamp 10^(gain/20) V/V (gain contained in the data)",
                    f"ADC {sn.RESPONSE_CONSTANTS['auspass']['adc_counts_per_v']:.1f} counts/V "
                    "(= AusPass overall / 76.6)"]),
            "BD3C-5": dict(zeros=cplx(b["zeros"]), poles=cplx(b["poles"]),
                           norm_factor=b["norm_factor"], norm_freq_hz=b["norm_freq"],
                           sensor_v_per_m_s=b["sensor_v_per_ms"], adc_counts_per_v=b["adc_counts_per_v"])},
        notes=["AusPass: the same response for all channels and units (no bespoke calibration), "
               "apparently insensitive to sample rate; IGU-16 1C nodes appear to share it.",
               "The anti-alias FIR filters are not included (see the nrl model for them).",
               "Embedded in lib/smartsolo_node.py as well, used when this folder is missing.",
               "The files are written at 1000 sps; get_response puts the requested rate into the ADC stage."],
        files=files))


def make_dtcc():
    (OUT / "dtcc").mkdir(parents=True, exist_ok=True)
    files = staged_files("dtcc")
    write_yaml("dtcc", dict(
        title="DTCC data-sheet constants (as in the harvest script)",
        use="For comparison with archives made by the harvest script.",
        source=[dict(name="DTCC SmartSolo IGU-16HR 3C data sheet", url="https://smartsolo.com/sc/IGU-16HR%203C.pdf")],
        devices={"IGU-16HR3C": dict(zeros=cplx(sn.GEOPHONE_PAZ["zeros"]), poles=cplx(sn.GEOPHONE_PAZ["poles"]),
                                     sensor_v_per_m_s=76.6, adc_counts_per_v=3355500.0)},
        notes=["0.005 % more sensitive than auspass; same poles and zeros.",
               "76.6 V/(m/s) is the DT-SOLO open-circuit 80 V/(m/s) loaded by the 43 kOhm shunt: "
               "80 x 43000 / (43000 + 1850) = 76.7."],
        files=files))


def make_nominal():
    from obspy.core.inventory.response import (CoefficientsTypeResponseStage, InstrumentSensitivity,
                                               PolesZerosResponseStage, Response, ResponseStage)
    (OUT / "nominal").mkdir(parents=True, exist_ok=True)
    n = sn.NOMINAL_5HZ
    w0 = 2 * np.pi * n["f0"]; h = n["damping"]
    poles = [complex(-h * w0, w0 * np.sqrt(1 - h * h)), complex(-h * w0, -w0 * np.sqrt(1 - h * h))]
    zeros = [0j, 0j]
    fn = 15.0
    nf = 1.0 / sn._shape_at(fn, zeros, poles)
    adc = sn.COUNTS_PER_MV_0DB * 1000.0
    files = []
    for g in GAINS:
        gg = 10 ** (g / 20)
        stages = [PolesZerosResponseStage(1, n["sensitivity"] / nf, fn, "M/S", "V",
                                          pz_transfer_function_type="LAPLACE (RADIANS/SECOND)",
                                          normalization_frequency=fn, normalization_factor=nf, zeros=zeros,
                                          poles=poles),
                  ResponseStage(2, gg, fn, "V", "V", description=f"preamp gain {g} dB in the data"),
                  CoefficientsTypeResponseStage(3, adc, fn, "V", "COUNTS", "DIGITAL", numerator=[], denominator=[],
                                                decimation_input_sample_rate=1000.0, decimation_factor=1,
                                                decimation_offset=0, decimation_delay=0.0, decimation_correction=0.0)]
        r = Response(instrument_sensitivity=InstrumentSensitivity(1.0, fn, "M/S", "COUNTS"), response_stages=stages)
        r.recalculate_overall_sensitivity(fn)
        f = f"IGU-16HR3C_{g:02d}dB.xml"
        inventory(r, 1000.0, "IGU-16HR3C", "nominal", g).write(str(OUT / "nominal" / f), format="STATIONXML")
        files.append(dict(file=f, device="IGU-16HR3C", gain_db=g))
    write_yaml("nominal", dict(
        title="DT-SOLO 5 Hz data-sheet geophone",
        use="A textbook geophone from the data sheet; useful to see how much the details matter.",
        source=[dict(name="SmartSolo IGU-16HR 3C data sheet", url="https://smartsolo.com/sc/IGU-16HR%203C.pdf")],
        devices={"IGU-16HR3C": dict(f0_hz=n["f0"], damping=n["damping"], sensor_v_per_m_s=n["sensitivity"],
                                     adc_counts_per_v=adc, zeros=cplx(zeros), poles=cplx(poles))},
        notes=["Mixes the open-circuit sensitivity (80 V/(m/s)) with the shunted damping (0.70), as the data sheet "
               "lists them; the shunted sensitivity is 76.6-76.7 V/(m/s), so amplitudes are ~4 % high.",
               "ADC 3355.4428 counts/mV = 2^23 counts / 2.5 V (24 bits over the +-2.5 V full scale)."],
        files=files))


def make_nrl(sleep=0.2):
    from obspy import read_inventory
    (OUT / "nrl").mkdir(parents=True, exist_ok=True)
    files = []
    for dev in ("IGU-16HR3C", "BD3C-5"):
        for g in NRL_GAINS[dev]:
            for r in NRL_RATES[dev]:
                for ph in ("LP", "MP"):
                    cfg = f"{NRL_SENSOR[dev]}:{NRL_LOGGER[dev].format(g=g, r=r, p=ph)}"
                    f = f"{dev}_{g:02d}dB_{r}sps_{ph}.xml"
                    path = OUT / "nrl" / f
                    if not path.exists():
                        url = f"{NRL}?instconfig={cfg}&format=stationxml&nodata=404"
                        data = urllib.request.urlopen(url, timeout=60).read()
                        path.write_bytes(data)
                        time.sleep(sleep)
                    read_inventory(str(path))          # must parse
                    files.append(dict(file=f, device=dev, gain_db=g, sample_rate=r, filter_phase=ph, nrl=cfg))
    write_yaml("nrl", dict(
        title="EarthScope (IRIS) Nominal Response Library: DT-SOLO + SmartSolo datalogger",
        use="The manufacturer-supplied nominal response with the full digital filter chain: "
            "use it to see the anti-alias FIR filters, their delay and the minimum-phase option.",
        source=[dict(name="NRL datalogger DTCC SmartSolo", url="https://ds.iris.edu/ds/nrl/datalogger/dtcc/"),
                dict(name="NRL sensor DTCC DT-SOLO", url="https://ds.iris.edu/ds/nrl/sensor/dtcc/dt-solo/"),
                dict(name="NRL web service", url="https://service.earthscope.org/irisws/nrl/1/")],
        devices={"IGU-16HR3C": dict(sensor=NRL_SENSOR["IGU-16HR3C"], sensor_v_per_m_s=76.6,
                                     zeros=[[0, 0], [0, 0]], poles=[[-22.2111, 22.2178], [-22.2111, -22.2178]],
                                     adc_counts_per_v=3355440.0, fir="4 decimating FIR stages from 32 kHz",
                                     rates_sps=list(NRL_RATES["IGU-16HR3C"])),
                 "BD3C-5": dict(sensor=NRL_SENSOR["BD3C-5"], sensor_v_per_m_s=209.4,
                                rates_sps=list(NRL_RATES["BD3C-5"]))},
        notes=["File names: <device>_<gain>dB_<rate>sps_<LP|MP>.xml; LP = linear phase, MP = minimum phase "
               "(the script's Anti_Alias_Filter 2 / 1). Low cut off (DFOff).",
               "No IGU-16HR3C files above 1000 sps exist in the NRL; get_response uses the nearest rate with a warning.",
               "The NRL also has the optional low-cut IIR filter (DC removal and 1 Hz); for other corners it advises "
               "replacing the pole by 2 pi fc.",
               "The overall sensitivity is given at a frequency within the passband (e.g. 210 Hz at 1000 sps)."],
        files=files))


def write_readme():
    import smartsolo_response as sr
    sr._meta.cache_clear()
    t = sr.list_responses()
    rows = "\n".join(f"| `{r.model}` | {r.title} | {', '.join(r.devices)} | {r.n_files} | {r.use} |"
                     for r in t.itertuples())
    (OUT / "README.md").write_text(f"""# Instrument responses

Suggested instrument responses for SmartSolo nodes, one folder per model.

- **Format.** Each folder holds FDSN StationXML files (the standard format, read by ObsPy, SeisComP and others) and a readable `response.yaml` with the source, the constants, notes and the list of files.
- **Default.** `auspass` is the default. It is also embedded in `lib/smartsolo_node.py`, so code keeps working without this folder.
- **Regenerating.** Run `python scripts/make_instrument_responses.py` (`--no-nrl` skips the download).

| Model | What it is | Devices | Files | When to use |
|---|---|---|---|---|
{rows}

## Using a model

```python
import smartsolo_response as sr
sr.list_responses()
resp = sr.get_response("auspass", "IGU-16HR 3C 5Hz", gain_db=24, sampling_rate=250)
sr.inspect_instrument_response(["auspass", "nrl", "nominal"], normalise=True)
inv = wf.build_inventory(sel, response="nrl")                   # any folder name works
```

- **Gain in the data.** `gain_db` is the preamp gain contained in the data. Use the log gain for raw DLD counts, and 0 when the gain was removed.
- **Missing gains.** A model without a file for a gain uses the nearest file with its preamp stage rescaled.
- **Sample rate and filter phase.** `nrl` has a file for each sample rate and filter phase, because the decimation FIR stages differ. The other models have no FIR stages, and the requested rate is written into their ADC stage.

## Adding a model

1. **Create the files.** Make a folder with StationXML files and a `response.yaml` listing them under `files:` (`file`, `device`, `gain_db`, and optionally `sample_rate` and `filter_phase`).
2. **Use it.** `get_response("<folder>")` and `build_inventory(response="<folder>")` pick it up.

The notebook [instrument responses](../notebooks/tutorials/instrument_responses.ipynb) compares the models.
""")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-nrl", action="store_true")
    a = ap.parse_args()
    make_auspass(); make_dtcc(); make_nominal()
    if not a.no_nrl:
        make_nrl()
    write_readme()
    print("written to", OUT)
