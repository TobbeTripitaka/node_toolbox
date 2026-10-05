# node_toolbox
Tools to work with SmartSolo node type instruments. Especially in Antarctic settings where logistical limitations constrain the deployment.

- **`smartsolo_log`** – read `DigiSolo.LOG` state-of-health logs into pandas (temperature, voltage, GPS, tilt ... against time).
- **`smartsolo_locate`** – work out where and when each node recorded (one *deployment* per power-up, first stable GPS fix) and select deployments by radius, polygon and time.
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
notebooks/smartsolo_log_demo.ipynb    log parsing and plotting
notebooks/select_and_extract_demo.ipynb  selection + waveform extraction
scripts/make_synthetic_waveforms.py   synthetic MiniSEED/SEG-Y test data
tests/test_toolbox.py                 pytest tests
data/logfiles/                        two sample logs (nodes 453021267, 453022522)
data/station_mapping.csv              example serial -> SEED code table
data/example_area.geojson             example selection polygon
data/seismic_traces/                  (generated, not in git) synthetic waveforms
```

### Install

```bash
git clone https://github.com/TobbeTripitaka/node_toolbox.git
cd node_toolbox
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

df = sl.read_log("data/logfiles/DigiSolo_453021267.LOG")

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
nodes = sl.read_logs("data/logfiles")              # folder (recursive *.LOG)
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
info = sl.read_device_info("data/logfiles")
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
- Traces get `stats.coordinates` (lat/lon/elevation of the first stable fix),
  `stats.serial` and `stats.deployment_id`.
- Files are written as `out_dir/NET/STA/NET.STA.LOC.CHA__start__end.mseed`;
  `chunk="1D"` / `"1h"` splits them, `return_stream=False` saves memory for
  big jobs.
- The StationXML has one station epoch per deployment with coordinates,
  orientation (Z dip −90°, N az 0°, E az 90°), sample rate and the node as
  sensor. No instrument response is attached yet (data stay in counts).

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

### Assumptions to check against real SmartSolo exports

- File/folder names contain the 9-digit serial (otherwise use `serial_from=`).
- Waveform time stamps are UTC (GPS-disciplined), the same as the logs.
- SEG-Y exports have one component per trace with the start time in the
  trace headers; how SoloLite labels components in SEG-Y may need `component_func=lambda path, trace: ...`
  (in both `index_waveforms` and `extract_waveforms`).
