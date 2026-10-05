# node_toolbox
Tools to work with SmartSolo node type instruments. Especially in Antarctic settings where logistical limitations constrain the deployment.

- **`smartsolo_log`** – read `DigiSolo.LOG` state-of-health logs into pandas (temperature, voltage, GPS, tilt ... against time).
- **`smartsolo_locate`** – work out where and when each node recorded (one *deployment* per power-up, first stable GPS fix) and select deployments by radius, polygon and time.
- **`smartsolo_node`** – read the other files in a node folder (script, `device.ini`, `PULSE_*.WAV`): test limits, geophone pulse-test analysis, boot-by-boot sensor QC, orientation, instrument response.
- **`smartsolo_orientation`** – eCompass and tilt: circular statistics, rotation over time, boot vs settled heading, IGRF declination, writing azimuths to StationXML or rotating data.
- **`smartsolo_dld`** – read raw SmartSolo `.DLD` data files directly (no SoloLite export), see [docs/DLD_FORMAT.md](docs/DLD_FORMAT.md). Independent, read-only implementation; see [docs/CLEAN_ROOM.md](docs/CLEAN_ROOM.md) for how it was made.
- **`smartsolo_waveforms`** – index MiniSEED / SEG-Y files, cut the selected time windows for the selected nodes with ObsPy, apply SEED codes from a mapping table, write MiniSEED + StationXML.

See [Selecting stations and cutting waveforms](#selecting-stations-and-cutting-waveforms) below for the second part.

## SmartSolo log reader

`lib/smartsolo_log.py` reads the state-of-health log (`DigiSolo.LOG`) written by
SmartSolo nodes (e.g. IGU-16HR 3C 5Hz) and turns it into a **pandas DataFrame**:

- one row per log record (`[GPS00018]`, `[Temperature00001]`, `[Battery00001]`, ...)
- every kind of log field in its own column (`temperature`, `voltage`,
  `latitude`, `longitude`, `altitude`, `tilted_angle`, `gps_status`,
  `available_memory`, `error_type`, ...), `NaN` where a field doesn't belong to
  that record type
- the index is the record's `UTC Time` as a timezone-aware pandas
  `DatetimeIndex` (UTC). Other times (`RTC Time`, `Latest Fix UTC Time`, ...)
  are also `datetime64[..., UTC]` columns
- several files/nodes can be read at once and are tagged by node `serial`
- a separate table holds the device metadata from each boot (`[DeviceInfo]`)

### Repository layout

```
lib/smartsolo_log.py                  log parser
lib/smartsolo_locate.py               deployments, radius/polygon/time selection
lib/smartsolo_waveforms.py            waveform index, extraction, StationXML
lib/smartsolo_node.py                 node folder files, pulse test, geophone QC, response
lib/smartsolo_orientation.py          eCompass / tilt tools
lib/smartsolo_dld.py                  raw DLD reader
docs/DLD_FORMAT.md                    reverse-engineered DLD format
notebooks/smartsolo_log_demo.ipynb    log parsing and plotting
notebooks/select_and_extract_demo.ipynb  selection + waveform extraction
notebooks/node_qc_demo.ipynb          pulse test, sensor QC, polarity, orientation, response
notebooks/dld_demo.ipynb              raw DLD files -> MiniSEED / SEG-Y / StationXML, DLD timing
notebooks/break_test.ipynb            the Land Cruiser break test: map, events, speeds, spectra, fun
scripts/make_synthetic_waveforms.py   synthetic MiniSEED/SEG-Y test data
tests/test_toolbox.py                 pytest tests
data/break_test/                      6 nodes, raw DLD + logs + pulse tests (Git LFS):
                                      a Land Cruiser HJ60 braking (see data/break_test/README.md)
data/nodes/<serial>/                  example logs, no waveform data (see data/nodes/README.md):
  453021267, 453022522                  DML, Antarctica, 2 weeks (453022522: + script, device.ini, PULSE_*.WAV)
tests/fixtures/                       a V1.1.4 log with GNSS records (parser test)
docs/FINDINGS.md                      everything learned from the files, with numbers
```

### Install

```bash
git clone https://github.com/TobbeTripitaka/node_toolbox.git
cd node_toolbox
git lfs install && git lfs pull      # raw DLD sample data (~45 MB, Git LFS)
pip install -r requirements.txt
```

No packaging needed (might do later),  just put `lib/` on the Python path:

```python
import sys
sys.path.insert(0, "path/to/node_toolbox/lib")
import smartsolo_log as sl
```

### Quick start

```python
import smartsolo_log as sl

df = sl.read_log("data/nodes/453021267/DigiSolo.LOG")

# Temperature or battery voltage against time
df["temperature"].dropna().plot()
df["voltage"].dropna().plot()

# or several at once, one subplot each
sl.plot_series(df, ["temperature", "voltage", "tilted_angle"])

# usual pandas time handling
df.loc["2024-12-26", "temperature"].dropna()
df["temperature"].dropna().resample("1h").mean()
```

Columns are sparse (each record type fills only its own fields), so use
`.dropna()` on the column you plot, or select one record type:

```python
gps = df[df.record_type == "GPS"].dropna(axis=1, how="all")
```

### Many nodes

```python
nodes = sl.read_logs("data/nodes")              # folder (recursive *.LOG)
nodes = sl.read_logs("survey/**/DigiSolo*.LOG")    # glob
nodes = sl.read_logs(["a.LOG", "b.LOG"])           # list

nodes.groupby("serial")["temperature"].describe()
sl.plot_series(nodes, ["temperature", "voltage"], by="serial")   # one line per node
```

> The node writes every log as `DigiSolo.LOG`, so rename the files (or keep them
> in per-node folders) when collecting them. The serial number is read from the
> file contents, not the file name.

### Device metadata

```python
info = sl.read_device_info("data/nodes")
```

One row per `[DeviceInfoNNNNN]` block (written at each boot), indexed by
`boot_time` (`Boot RTC`): serial, firmware, boot reason, sample rate, gains,
geophone self-test per channel (`ch1_resistance_1/2`, `ch1_resonate_freq`,
`ch1_damping`, `ch1_sensitivity`, `ch1_rms_noise`, ...), SD card info, and
boot-time GPS lock, voltage, temperature and tilt. Boots without an RTC time get
`NaT`.

### Main columns in the wide DataFrame

| column | from record | notes |
|---|---|---|
| `time` (index) | all | `UTC Time`, `datetime64[..., UTC]` |
| `serial`, `file` | all | node serial number and source file |
| `record_type`, `record_no`, `line_no` | all | e.g. `GPS`, `18`, line in file |
| `session` | all | power-up number (latest `[DeviceInfoNNNNN]` before the record) |
| `time_inferred` | all | `True` for records without their own time (e.g. `BatteryPowerOnStop`), which take the time of the preceding record |
| `temperature` | Temperature | °C |
| `voltage` | Battery | V |
| `total_memory`, `available_memory`, `used_memory` | Memory | as logged by the node |
| `gps_status` | GPS | `GPS Cycle On`, `GPS Cycle Off`, `GPS Synchronization` |
| `latitude`, `longitude`, `altitude` | GPS (sync) | decimal degrees, m |
| `ecompass_north`, `tilted_angle`, `roll_angle`, `pitch_angle` | GPS (sync) | degrees |
| `satellite_number`, `gps_strength`, `satellite_id`, `gps_fix`, `gps_power` | GPS (sync) | lists kept as strings – expand with `sl.split_list_column(df, "gps_strength")` |
| `gps_tick`, `dac_value`, `adc_sync_value_1/2`, `phase_error`, `max_phase_error` | GPS (cycle off) | clock discipline |
| `rtc_time`, `latest_fix_utc_time` | GPS | datetime |
| `error_type`, `access_type` | Error | e.g. `SD Driver Error` |
| `start_acquisition_filename`, `changed_acquisition_filename` | Notify | data file names |

Parsing rules: field names are converted to snake_case; values like
`"2024/12/25,14:20:04"` become UTC datetimes, numbers become floats, number
pairs such as `2907,39` are split into `<name>_1` and `<name>_2`, IDs/codes
(serial number, boot reason, firmware) stay as text. Fields not listed above are
still parsed – any new `key = value` appears as a column automatically.

`df.attrs["header_counts"]` holds the counters from the first line of the file
(`<DeviceInfo,GPS,Temperature,Memory,Battery,Error,Notify>`) and
`df.attrs["parsed_counts"]` what was actually parsed, as a sanity check. (The
Notify counter in the header does not match the number of Notify blocks in the
sample files; all other counts match.)

### Export

```python
nodes.to_csv("soh.csv")
nodes.to_parquet("soh.parquet")   # needs pyarrow
```

See `notebooks/smartsolo_log_demo.ipynb` for a full walk-through with plots.

## Selecting stations and cutting waveforms

The use case: a drive full of node data where you don't know which files come
from where. The logs tell you where each node was and when, so:

```
DigiSolo.LOG files ──► deployments (where/when) ──► select (radius / polygon / time)
                                                         │
MiniSEED / SEG-Y files ──► header index ─────────────────┴──► ObsPy cut ──► MiniSEED + StationXML + station table
```

### Deployments and the "first stable fix"

A **deployment** is one power-up of one node: from a `[DeviceInfoNNNNN]` boot
block to the next. Its **location is the first stable GPS position after
power-up**: the first run of `n_stable=5` consecutive fixes all within
`stable_tol_m=10` m of their median. This skips cold-start fixes (in the sample
data node 453022522's first two fixes are 45–100 m off) and fixes the station
position at installation time even if the ice moves the node later. The
movement is reported, not used: `median_offset_m`, `max_offset_m`, `drift_m`
(last day vs. power-up position), `drift_m_per_day`.

If a node is powered up again at another site it becomes a new deployment
with its own position, so a selection only includes the power-ups that were
inside the area. Power-ups without any GPS fix (bench tests) are dropped
(`keep_unlocated=True` keeps them).

```python
import smartsolo_locate as loc

logs = loc.find_logs("/media/bigdrive")          # recursive, checks file contents, any file name
deps = loc.build_deployments(logs, cache="deployments.gpkg")   # cache = parse once
```

`deps` is a GeoDataFrame (EPSG:4326), one row per deployment: `deployment_id`
(`<serial>_<session>`), `serial`, `session`, `start`, `end`, `latitude`,
`longitude`, `elevation`, `fix_time`, quality columns, `device_type`,
`sample_rate`, `log_file`.

### Selecting

```python
# within 2 km of a point (lat, lon), geodesic distance
sel = loc.select_deployments(deps, point=(-71.549, 11.117), radius_km=2)

# inside a polygon: (lon, lat) vertices, shapely geometry, GeoDataFrame, or a file
sel = loc.select_deployments(deps, polygon="area.gpkg")

# plus a time window (UTC if no time zone given); can combine all of these
sel = loc.select_deployments(deps, polygon="area.gpkg",
                             start="2024-12-26", end="2024-12-28", serials=None)
```

Polygon selection uses `geopandas.sjoin` (`predicate="within"`, or
`"intersects"` to include points on the edge); polygon attributes are joined
onto the result. The result has `sel_start` / `sel_end`: the part of each
deployment inside the requested time window.

### Waveform index

```python
import smartsolo_waveforms as wf
index = wf.index_waveforms("/media/bigdrive", cache="waveform_index.csv")
```

Scans `*.miniseed, *.mseed, *.msd, *.seed, *.sgy, *.segy` (headers only) and
returns one row per trace id per file with `serial`, `component`, start/end
times and sample rate. With `cache`, only new or changed files are read again.

The **serial** of each file is found from a 9-digit number in the file name,
else in a parent folder name (e.g. `453021267/…`), else the header station
code (directly or through the mapping table). The **component** comes from the
channel code, else from the file name (`….Z.miniseed`). If your files are
named differently pass `serial_from=lambda path, trace: ...`,
`component_func=lambda path, trace: ...` and/or
`component_map={"X": "E", "Y": "N"}`.

### Mapping table

`data/station_mapping.csv`:

```
serial,network,station,location,channel_prefix,start,end
453021267,XX,GL01,,,,
453022522,XX,GL02,,,,
```

`location`, `channel_prefix`, `start`, `end` are optional; `start`/`end` let
one serial map to different stations at different times. Without
`channel_prefix` the SEED band code is set from the sample rate with
instrument code `P` (geophone): 80–250 Hz → `EP?`, 250–1000 Hz → `DP?`,
1000–5000 Hz → `GP?`. Serials missing from the table get
`XX.<last 5 digits>` and a warning.

### Cutting

```python
st = wf.extract_waveforms(sel, index, mapping="data/station_mapping.csv",
                          components=["Z", "N", "E"],
                          out_dir="output/mseed", chunk="1D")      # chunk optional
inv = wf.build_inventory(sel, mapping="data/station_mapping.csv", stream=st)
inv.write("output/stations.xml", format="STATIONXML")
loc.export_stations(sel, "output/stations.csv")    # or .gpkg / .geojson / .shp
```

- Reads only the needed records from MiniSEED (`starttime`/`endtime` read);
  SEG-Y files are read and trimmed. SEG-Y headers are removed so the result is a
  normal ObsPy `Stream`.
- Windows are `[start, end)` (end exclusive) so consecutive cuts don't
  share samples. Gaps stay masked (`fill_value=` to fill them).
- **Polarity**: IGU-16HR data are multiplied by −1 by default
  (`invert_polarity="auto"`), see [Polarity and channel naming](#polarity-and-channel-naming).
- Traces get `stats.coordinates` (lat/lon/elevation of the first stable fix),
  `stats.serial` and `stats.deployment_id`.
- Files are written as `out_dir/NET/STA/NET.STA.LOC.CHA__start__end.mseed`;
  `chunk="1D"` / `"1h"` splits them, `return_stream=False` saves memory for
  big jobs.
- The StationXML has one station epoch per deployment with coordinates,
  orientation (Z dip −90°, N az 0°, E az 90° after the polarity flip;
  `polarity_inverted=False` → Z +90°, N 180°, E 270° for unflipped data),
  sample rate, the node as sensor and optionally a response
  (`response="auspass"` / `"test"` / `"nominal"`).

### All in one call

```python
sel, st, inv = wf.extract_region(
    log_root="/media/bigdrive/logs", waveform_root="/media/bigdrive/data",
    mapping="station_mapping.csv",
    polygon="area.gpkg",              # or point=(lat, lon), radius_km=5
    start="2024-12-26", end="2024-12-27",
    out_dir="output/area1", chunk="1h")
# -> output/area1/{stations.csv, stations.gpkg, stations.xml, mseed/...}
```

### Test data and tests

The repository has real logs but no real waveforms. Make synthetic ones that
match the logs (MiniSEED for 453021267, SEG-Y for 453022522, blank header
codes, a few "icequakes"):

```bash
python scripts/make_synthetic_waveforms.py      # -> data/seismic_traces/
pytest -q tests
```

## Break test

`data/break_test/` holds six nodes recording **a Toyota Land Cruiser HJ60
braking, with a laughing baby in the back seat** (road beside the sports
oval in Sandy Bay, Hobart, 31 March 2023, 500 sps). `notebooks/break_test.ipynb`
maps the nodes on aerial imagery, detects the vehicle events, estimates speeds from
the travel time between the node pairs, picks the ten most likely brake
stops, shows spectrograms with Doppler-gliding engine tones, stacks and
spectra, particle motion, an engine-rpm estimate, an (unsuccessful) search
for the baby's laughter, and an audio version of the strongest stop.

## Raw DLD files

The nodes store data as `seisNNN{X,Y,Z}.DLD` (`MiniSeed_Output_Mode = 0`).
`lib/smartsolo_dld.py` reads them directly – no SoloLite export – and the
rest of the toolbox treats them like any other waveform file. The format
(reverse-engineered, see [docs/DLD_FORMAT.md](docs/DLD_FORMAT.md)): a
512-byte header (serial, firmware, start/end, position), then blocks of 1000
int24 samples, each followed by a 72-byte time tag (UTC second, ms tick, GPS
position, GPS time of week).

```python
import smartsolo_dld as dld
dld.read_dld_header("seis000Z.DLD")       # serial, firmware, start, end, lat/lon/alt ...
dld.read_dld_tags("seis000Z.DLD")         # one row per 1000 samples
st = dld.read_dld("seis000Z.DLD", starttime="2024-10-08T02:30", endtime="2024-10-08T02:31")
```

In the pipeline:

```python
index = wf.index_waveforms("/media/drive")                 # finds *.DLD (and MiniSEED/SEG-Y)
deps  = loc.combine_deployments(loc.build_deployments("/media/drive"),        # from logs
                                loc.build_deployments_from_dld("/media/drive"))  # nodes without logs
sel   = loc.select_deployments(deps, polygon="area.gpkg", start=..., end=...)
st    = wf.extract_waveforms(sel, index, mapping="station_mapping.csv",
                             out_dir="out", out_format="MSEED")   # or "SEGY"
inv   = wf.build_inventory(sel, stream=st, response="auspass")    # gain from log included
# or everything on a drive in one go:
wf.convert_dld("/media/drive", out_dir="out", log_root="/media/drive", out_format="SEGY")
```

- **Sample rate**: from the tag spacing. The log/script `Sample Rate` is
  the sample *interval* in 10 µs units: 100 → **1000 sps** (the DML nodes),
  200 → 500 sps (break test), 400 → 250 sps (`sample_rate_hz` column).
- **Components**: X = north–south → `?PN`, Y = east–west → `?PE`, Z → `?PZ`.
- **Polarity**: raw DLD counts have SmartSolo polarity; `extract_waveforms`
  multiplies by −1 (AusPass).
- **Gain**: raw counts include the preamp gain (0–36 dB). It is put into the
  response (`gain_db="auto"` uses the log's `Channel 1 Gain`); nodes without a
  log are assumed 0 dB.
- **Position without logs**: from the GPS positions in the tags (first
  stable fix). Log deployments are widened to cover the recorded data (the
  log's first record is ~45 s after recording starts).
- **Timing**: tag times come from the GPS time of week (`time_source="tow"`,
  default). The text time in the tags is 2 s late until the receiver knows
  the leap seconds and then jumps back 2 s mid-file – cross-correlating
  nodes shows false 2-s offsets with text times and 0 ± 4 ms with TOW.
  Whether a tag marks the start (default `tag_marks="block_start"`) or the
  end of the preceding 1000-sample block is not yet confirmed – check once
  with `dld.compare_with_export(dld_trace, sololite_trace)`.
- **Position**: the DLD header position is written when the file is
  closed; use the tag positions (`build_deployments_from_dld`), which also
  show when nodes were picked up while recording.
- SEG-Y output splits traces into ≤32767 samples (whole seconds); MiniSEED
  uses Steim-2.

Newer firmware (V1.1.4) writes `[GNSSnnnnn]` records and `GNSS ...` keys; the
log reader maps them to the GPS names, and handles `UTC Time = ","`,
`Altitude = Unknown` and 3-value `ADC Sync Value`.

## Node folder files, pulse test and sensor QC

`lib/smartsolo_node.py` reads the small files every node writes next to
`DigiSolo.LOG`:

| file | content | function |
|---|---|---|
| `device.ini` | serial, firmware | `read_device_ini` |
| `sct_par.xml` (+ identical backup `sct_par_b.xml`) | acquisition script incl. test limits | `read_script`, `script_limits` |
| `SCT_INT.XML` | script as interpreted by the node | `read_script` |
| `PULSE_X/Y/Z.WAV` | geophone pulse test of the **latest** power-up | `read_pulse`, `analyse_pulse` |
| `DigiSolo.TXT`, `guardfile.db` | file-system marker, binary log journal | ignored |

```python
import smartsolo_node as sn
node = sn.read_node_folder("data/nodes/453022522")
node["limits"]   # resistance 1640–1905 Ω, damping 0.647–0.752, f0 4.63–5.37 Hz, 71–82 V/m/s, battery 6.5/6.0/8.35 V
node["pulse"]    # per axis: f0, damping, noise floor (µV), step plateaus (mV)
node["qc"]       # per boot and channel: logged test values, pass/fail vs limits, 'noisy' flag
```

What the files tell us (sample node 453022522):

- **Pulse WAVs are 1000 sps, not the 10 000 Hz in the WAV header.** The file
  is the 16-s geophone/ADC test stage (SmartSolo manual) in 16 000 samples;
  only at 1000 sps does the ringing give the ~5 Hz / h≈0.7 of the geophone.
  `read_pulse` uses 1000 Hz by default (`sampling_rate=None` = header).
- The test: ~1.9 s clipped pre-test, then current steps (+, off, −, off, +,
  off; ~1 s each), then ~6 s quiet. Fitting a damped oscillator after each
  switch-off gives f0 = 4.95–4.98 Hz and h = 0.71–0.72, within ~2 % of the
  log's boot-4 values. The quiet part is 3.9 counts RMS = **1.16 µV**, the
  same as the log's `ChN RMS Noise`, so that field is µV and the
  **3355.4428 counts/mV** ADC factor is confirmed.
- **Boot-test QC**: coil resistance follows temperature like copper
  (~1620 Ω at −2 °C vs ~1780 Ω warm), and the script uses
  temperature-corrected limits. 453022522's deployment-time test was taken
  while it was moving (spread noise up to 22 000) and two channels are out of
  spec although the log says *passed*; its recovery test is clean. Use
  `noisy` / `ok_all` rather than `Geophone Test Passed`.
- **Orientation** (now in the deployment table): tilt 1.7° and **5.1°**
  (above the 3° horizontal-geophone spec for 453022522); eCompass 157° and
  126° (std ~1°). With IGRF declination −29.5° (`magnetic_declination`,
  needs `ppigrf`), the N arrows would point ~127° and ~97° true – check field
  notes / compass calibration before rotating horizontals.
- **Data format**: `MiniSeed_Output_Mode = 0` → raw data are `.DLD` files
  (see `Notify` records), which must be exported with SoloLite.

### Instrument response

```python
inv = wf.build_inventory(sel, mapping="data/station_mapping.csv", response="auspass")
```

| `response=` | source |
|---|---|
| `"auspass"` (recommended) | AusPass/ANSIR published IGU-16HR 3C response: zeros 0, 0; poles −22.211059 ± 22.217768j (5.000 Hz, h 0.707); 257 019 225.55 counts/(m/s) flat gain, same for all channels/units, for polarity-flipped data in counts with gain removed |
| `"test"` | each deployment's own boot-time geophone test (if it passed and wasn't noisy), else nominal |
| `"nominal"` | DT-SOLO data sheet: 5 Hz, h 0.70, 80 V/(m/s) × 3355.4428 counts/mV × gain |

`geophone_response(f0, damping, sensitivity, gain_db)` builds the latter two.
The anti-alias FIR is not included. All responses assume the preamp gain was
removed at export ("Remove Gain" in SoloLite) – otherwise amplitudes are
×15.85 at 24 dB.

## Polarity and channel naming

From [AusPass – SmartSolo Node Polarity Issues](https://auspass.edu.au/xwiki/bin/view/Data/AusPass%20Data/)
and [AusPass – SmartSolo Nodes](https://auspass.edu.au/xwiki/bin/view/Instrumentation/SmartSolo%20Nodes/):

- **All IGU-16HR channels (Z, N and E) have negative polarity** relative to
  FDSN StationXML (positive up / north / east). Freshly exported data need
  ×(−1) on every channel. AusPass flips the waveform data rather than the
  metadata, and all its waveforms are corrected since Dec 2025. Earlier only
  Z was thought to be affected (e.g. the EarthScope notice), so don't mix
  metadata from different groups. BD3C-5 broadband nodes need no flip.
- This matches the SmartSolo manual: positive X = case moving south,
  Y = west, Z = down.
- Channel codes use instrument code **P** (geophone): `DPZ/DPN/DPE` at
  250 sps, `GPZ/GPN/GPE` at 1000 sps (SEED band code from the sample rate).
- Other AusPass notes worth knowing: bury nodes flush (horizontal noise);
  take compass readings away from the node; a constant ~18 s time offset
  means SoloLite's leap-second file `smartsoloconfig.xml` is missing.

In this toolbox: `extract_waveforms(invert_polarity="auto")` flips IGU-16HR
data and records it in `stats.processing` / `stats.polarity_inverted`;
`build_inventory(polarity_inverted=True)` then writes the FDSN orientation. If
you keep raw polarity (`invert_polarity=False`), use
`polarity_inverted=False` so the metadata describe it (Z dip +90, N 180°,
E 270°).

## Compass and tilt

`lib/smartsolo_orientation.py` makes no assumption about what `eCompass North`
means. It keeps the raw reading and adds interpretations:

```python
import smartsolo_orientation as so
df   = sl.read_logs("data/nodes")
deps = loc.build_deployments("data/nodes")
ot   = so.orientation_table(df, deps, device_info=sl.read_device_info("data/nodes"))
so.plot_orientation(df)
```

`orientation_table` gives one row per deployment: `heading_raw`,
`heading_settled` (after `settle="6h"`), `heading_first/last`,
`heading_std` (circular), `rotation_deg`, `rotation_deg_per_day`, tilt / roll /
pitch, boot-time readings and `boot_vs_settled_deg`, IGRF `declination`,
`inclination`, `horizontal_nT`, `heading_true_if_mag` (= settled + declination),
and flags `heading_unstable`, `tilt_over_horizontal_spec` (>3°),
`tilt_over_vertical_spec` (>10°). Circular statistics are used throughout.

Sample deployments: headings stable to ~0.9°, but 453021267 rotates
+0.17°/day (2.3° in two weeks) with constant tilt – ice motion or compass
drift. Boot readings are taken before planting (453021267: 24° tilt, 36° off),
so use `heading_settled`. Declination −29.5°, horizontal field 19 000 nT.

To use an orientation, add a `heading_used` column (field notes,
`heading_true_if_mag`, or 0 if aligned to north) and a `station` column, then
either

```python
so.set_channel_azimuths(inv, ot)          # metadata: N = heading, E = heading + 90
so.rotate_to_ne(st, north_azimuth=127.2)  # or rotate the data to geographic N/E
```

### Assumptions to check against real SmartSolo exports

- File/folder names contain the 9-digit serial (otherwise use `serial_from=`).
- Waveform time stamps are UTC (GPS-disciplined), the same as the logs.
- Log Ch1/Ch2/Ch3 ↔ X/Y/Z for the boot-test values: not documented.
- DLD timing convention (`tag_marks`), see above.
- That SoloLite was *not* already set to invert polarity at export (then the
  default flip would double it - use `invert_polarity=False`).
- Meaning of `eCompass North` (magnetic azimuth of the N arrow?) and whether
  the compass was calibrated.
- SEG-Y exports have one component per trace with the start time in the
  trace headers; how SoloLite labels components in SEG-Y may need `component_func=lambda path, trace: ...`
  (in both `index_waveforms` and `extract_waveforms`).

## Independence and trademarks

The DLD reader was written from scratch from a byte-level description
([docs/DLD_FORMAT.md](docs/DLD_FORMAT.md)). That description was worked out
only from our own data files and public information, without decompiling
or disassembling any vendor software ([docs/CLEAN_ROOM.md](docs/CLEAN_ROOM.md)).
It only reads `.DLD` files and never modifies them. SmartSolo, SoloLite and
DTCC are names and trademarks of their owners; this project is not
affiliated with or endorsed by them.
