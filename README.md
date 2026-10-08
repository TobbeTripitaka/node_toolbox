# node_toolbox
Tools to work with SmartSolo node type geophones. Especially in Antarctic settings where logistical limitations constrain the deployment.

Authors: Tobias Stål (UTAS)

<img src="https://github.com/TobbeTripitaka/telemetry_setup/blob/main/img/GRIT%20_Final.png" width="120" alt="GRIT project logo">

---

node_toolbox reads what SmartSolo nodes write: the state-of-health log, the raw
DLD data, the acquisition script and the boot-time sensor test. With it you can:

- find where and when each node recorded;
- select nodes by area and time;
- cut and convert the data to MiniSEED, SEG-Y or an SDS archive with StationXML;
- check sensor health and orientation.

No SoloLite export is needed.

**New to SmartSolo data?** Read [SmartSolo basics](docs/SMARTSOLO_BASICS.md)
first. It covers the files on a node, what the log records are and why there
are so many kinds, the boot self-test in `[DeviceInfo]`, and the timing,
polarity, gain and firmware details that matter.
The [user guide](docs/USER_GUIDE.md) explains every module in detail.

| Module | What it does |
|---|---|
| `smartsolo_log` | `DigiSolo.LOG` → pandas (temperature, voltage, GPS, tilt … against time; boot records) |
| `smartsolo_locate` | Deployments (one per power-up, at the first stable GPS fix); selection by radius, polygon and time |
| `smartsolo_dld` | Raw `.DLD` files read directly ([format](docs/DLD_FORMAT.md), [how it was made](docs/CLEAN_ROOM.md)) |
| `smartsolo_waveforms` | Index MiniSEED / SEG-Y / DLD files, cut windows with SEED codes, write MiniSEED + StationXML |
| `smartsolo_batch` | Whole harvests → SDS archive + harvest database, in parallel |
| `smartsolo_node` | Script, `device.ini`, pulse test, boot-by-boot sensor QC, instrument response |
| `smartsolo_orientation` | eCompass and tilt: circular statistics, rotation, IGRF declination, azimuths |
| `smartsolo_config` | All processing settings in one YAML/JSON file |

## Install

```bash
git clone https://github.com/TobbeTripitaka/node_toolbox.git
cd node_toolbox
git lfs install && git lfs pull      # raw DLD sample data (~45 MB, Git LFS)
pip install -r requirements.txt
pytest -q tests
```

Optional: `pip install ppigrf` for magnetic declination (compass headings to
true north). Everything else works without it, as explained in the
[user guide](docs/USER_GUIDE.md#compass-and-tilt).

No packaging is needed yet; put `lib/` on the Python path:

```python
import sys
sys.path.insert(0, "path/to/node_toolbox/lib")
import smartsolo_log as sl, smartsolo_locate as loc, smartsolo_waveforms as wf

df   = sl.read_log("data/nodes/453021267/DigiSolo.LOG")       # one row per log record
sl.plot_series(df, ["temperature", "voltage", "tilted_angle"])
deps = loc.build_deployments("data/nodes")                    # where and when each node recorded
sel  = loc.select_deployments(deps, point=(-71.549, 11.117), radius_km=2)
```

A whole drive of DLD files to MiniSEED and StationXML in one call:
`wf.convert_dld("/media/drive", out_dir="out", log_root="/media/drive")`.
A whole harvest to an SDS archive: `python lib/smartsolo_batch.py settings.yaml /media/harvest_01`.

## Notebooks

The functionality is best shown through the notebooks in
[`notebooks/`](notebooks/): five **tutorials**, in the order of a typical
workflow, and a **case study**. They use only the data in this repository and
are stored with their outputs, so they can be read on GitHub without running
them. To run them, start Jupyter in their folder after `git lfs pull` (the DLD,
batch and break-test notebooks read the raw DLD files).

### Tutorials

| Notebook | What it demonstrates | Data |
|---|---|---|
| [1. Reading logs](notebooks/tutorials/smartsolo_log_demo.ipynb) | Parsing `DigiSolo.LOG` into one wide, UTC-indexed DataFrame; checking the record counters in `df.attrs`; plotting temperature, battery voltage, GPS and tilt against time with pandas or `plot_series`; time windows and resampling; one record type at a time; expanding list fields (satellite IDs, signal strength); errors and notifications; several nodes at once; device metadata per boot (firmware, script, boot-time geophone test); export to CSV or Parquet | Antarctic logs (`data/nodes/`) |
| [2. Deployments, selection and extraction](notebooks/tutorials/select_and_extract_demo.ipynb) | Finding logs anywhere on a drive by their content; one deployment per power-up at the first stable GPS fix, and why cold-start fixes are skipped; position drift; selection by radius, polygon (shapely, GeoJSON, GeoPackage ...) and time; a cached waveform index; cutting windows with SEED codes from a mapping table; daily or hourly output without holding the data in memory; StationXML and station tables; the one-call `extract_region` | Antarctic logs + synthetic MiniSEED |
| [3. Raw DLD files](notebooks/tutorials/dld_demo.ipynb) | The DLD header (serial, firmware, start/end, position when the file was closed); the per-block time tags (GPS week and time of week, text time, position, time since GPS sync); why the text time is 1 s off and jumps 2 s while the GPS time of week does not, shown with the excerpts in `data/timing_example/`; reading samples and their continuity across tags; deployments from DLD positions; export to MiniSEED, SEG-Y and StationXML; `convert_dld` for a whole drive | break test, timing excerpts |
| [4. Batch conversion](notebooks/tutorials/batch_convert.ipynb) | The settings file (YAML/JSON) and a template of all settings; AusPass vs DTCC response constants and gain handling; a dry run; converting six nodes in parallel to an SDS archive; the harvest database (runs, source files, day files); reruns that skip converted files and redo files converted with other timing settings without duplicating samples; reading the archive with an ObsPy SDS client and removing the response; GPS sync diagnostics; window files (SEG-Y) instead of SDS; the command line | break test |
| [5. Node QC and orientation](notebooks/tutorials/node_qc_demo.ipynb) | What a node folder contains (`device.ini`, acquisition scripts, `PULSE_*.WAV`); test limits from the script; the pulse test read at its true 1000 sps, damped-oscillator fits of natural frequency and damping, and the noise floor in µV; polarity and channel naming (AusPass/ANSIR); boot-by-boot geophone QC, the effect of temperature on coil resistance, and noisy tests; eCompass and tilt with circular statistics, slow rotation and IGRF declination; azimuths in StationXML; instrument response options | Antarctic nodes (`data/nodes/`) |

### Case study

| Notebook | What it demonstrates | Data |
|---|---|---|
| [Break test](notebooks/case_studies/break_test.ipynb) | A complete analysis of six nodes read straight from their DLD files: positions and pick-up times from the GPS in the time tags; node health from logs and pulse tests; event detection on node pairs and apparent vehicle speeds; the ten strongest stops; record section, spectrogram and stacked spectrum; particle motion; engine speed from the firing tone; a search for a laughing baby; the signal as audio | break test (`data/break_test/`) |

## Repository layout

```
lib/                 the modules above
notebooks/           tutorials/ and case_studies/ (see above)
docs/                SMARTSOLO_BASICS, USER_GUIDE, DLD_FORMAT, FINDINGS, CLEAN_ROOM
examples/            example settings file for batch conversion
data/break_test/     6 nodes, raw DLD + logs + pulse tests (Git LFS), a Land Cruiser braking
data/nodes/          logs (and one node folder) from two nodes in Dronning Maud Land
data/timing_example/ two 3-min DLD excerpts showing the 2-s label jump (Git LFS)
scripts/             synthetic MiniSEED/SEG-Y test data
tests/               pytest tests and fixtures
paper/               figures for the software report
```

## Independence and trademarks

The DLD reader was written from scratch from a byte-level description
([docs/DLD_FORMAT.md](docs/DLD_FORMAT.md)). That description was worked out
only from our own data files and public information, without decompiling
or disassembling any vendor software ([docs/CLEAN_ROOM.md](docs/CLEAN_ROOM.md)).
It only reads `.DLD` files and never modifies them. SmartSolo, SoloLite and
DTCC are names and trademarks of their owners; this project is not
affiliated with or endorsed by them.
