# node_toolbox
Tools to work with SmartSolo node type instruments. Especially in Antarctic settings where logistical limitations constrain the deployment.

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
lib/smartsolo_log.py              the parser (import this)
notebooks/smartsolo_log_demo.ipynb  worked example with plots
data/DigiSolo_453021267.LOG       sample log, node 453021267
data/DigiSolo_453022522.LOG       sample log, node 453022522
requirements.txt
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

df = sl.read_log("data/DigiSolo_453021267.LOG")

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
nodes = sl.read_logs("data")                       # folder (recursive *.LOG)
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
info = sl.read_device_info("data")
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
