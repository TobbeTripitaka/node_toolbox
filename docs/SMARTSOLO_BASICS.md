# SmartSolo basics

What a SmartSolo node writes, and how to read it. This is for users new to SmartSolo data. For how to use the toolbox, see the [user guide](USER_GUIDE.md). The numbers behind each statement are in [FINDINGS.md](FINDINGS.md).

The examples come from DTCC SmartSolo IGU-16HR 3C 5 Hz nodes running firmware V1.0.5.6kp to V1.1.4.2be.

## The node and its files

A node is a geophone, digitiser, GNSS receiver, clock, battery and memory card in one sealed case. It starts recording when it is switched on and stops when it is switched off or the battery runs low. Each period between switching on and off is a **power-up**, and the toolbox calls it a **deployment**.

Connected to a computer through its reader cable or the rack, the node appears as a USB drive with these files:

| File | What it holds |
|---|---|
| `DigiSolo.LOG` | The state-of-health log: one text record per event or measurement (see below). |
| `seisNNN{X,Y,Z}.DLD` | Raw data, one file per component and power-up (`MiniSeed_Output_Mode = 0`). The format is described in [DLD_FORMAT.md](DLD_FORMAT.md). |
| `*.MiniSeed` | Node-written MiniSEED instead of DLD (`MiniSeed_Output_Mode = 1`). |
| `sct_par.xml`, `sct_par_b.xml` | The acquisition script: sample rate, gains, filters, GNSS mode and test limits. `_b` is an identical backup. |
| `SCT_INT.XML` | The script as the node interpreted it. |
| `PULSE_X/Y/Z.WAV` | The geophone pulse test of the latest power-up. |
| `device.ini` | Serial number and firmware version, which may be out of date (see [Firmware](#firmware)). |
| `DigiSolo.TXT`, `guardfile.db` | A file-system marker and a binary journal; the toolbox ignores them. |

The node always calls its log `DigiSolo.LOG`. Keep each node's files in a folder named after its serial number. The toolbox reads the serial number from inside the files, so renaming is safe. SoloLite harvests into `SERIAL/YYYYMMDDhhmmss/`.

## The log and its records

`DigiSolo.LOG` is a plain-text file in INI style. Its first line counts the records of each type, for example `<00004,06824,01963,00427,00659,00000,00001>`. The counts are for DeviceInfo, GPS, Temperature, Memory, Battery, Error and Notify, in that order. All counts match the records in the file except Notify, which is often off.

After the first line the log is a sequence of **records**. Each record is a header such as `[GPS00018]`, made of a record type and a running number, followed by `key = value` lines:

```
[Temperature00001]
UTC Time = "2024/12/25,15:20:42"
Temperature = -2.0000

[GPS00001]
GPS Status = GPS Cycle Off
GPS Tick = 91
UTC Time = "2024/12/25,15:20:42"
DAC Value = 2033
```

### Why the node writes new records of different types

The node keeps no table of fixed columns. It appends a record whenever something happens or a timer fires, and each subsystem writes its own record type with its own fields. A record type is a kind of event, and its number counts how many such events have occurred. On the two-week DML deployment of node 453022522 (V1.0.8.1be), the record types were:

| Record type | When it is written | Main fields | DML example |
|---|---|---|---|
| `DeviceInfo` | Once at every boot | Firmware, script settings, boot self-test, boot GPS, temperature, tilt (see below) | 4 records |
| `GPS` (`GNSS` on V1.1.4) | At each GNSS event: cycle on, sync, cycle off | Status, position, satellites, signal strength, clock discipline (DAC value, phase error) | 6824 records, about one cycle every 9 min |
| `Temperature` | Periodically | Temperature (°C) | Every 10 min |
| `Battery` | Periodically | Voltage (V) | Every 30 min |
| `Memory` | Periodically | Total, used and free memory | Every 45 min |
| `Notify` | When a data file is started or changed | File name (`Seis000.DLD`, …) | Every 48 h |
| `Error` | When something fails | Error and access type (for example `SD Driver Error`) | None |
| `BatteryPowerOnStop` | When the node stops on a low battery | No fields and no time | 3 records |

The record types alternate in time order, so a log is not a regular table. A GPS record holds no temperature, and a Temperature record holds no position.

`smartsolo_log.read_log` turns the records into one wide table, with one row per record and one column per field. Fields that do not belong to a record type are left empty (NaN). Use `.dropna()` on the column you need, or select one record type with `df[df.record_type == "GPS"]`.

Each record also gets a `session` number: the power-up it belongs to, that is the latest `DeviceInfo` before it.

### GPS and GNSS records

In the default cycle mode (`GPS_Mode = 1`, logged as `GPS Power Mode = CycleOff`), the receiver is switched on, synchronises the clock and is switched off again. Each step writes a record:

- `GPS Cycle On`;
- `GPS Synchronization`, with position, satellites and signal strength;
- `GPS Cycle Off`, with the clock discipline values.

Between synchronisations the clock runs free. The DLD time tags record how long ago the last synchronisation was (`sync_age_s`): about 10 min on the nodes tested.

With "GNSS always on" (`GPS_Mode = 2`) the receiver stays on. V1.1.4 firmware writes `[GNSSnnnnn]` records and `GNSS ...` keys; the reader maps them to the GPS names.

## `[DeviceInfo]`: the boot record and the sensor self-test

The node writes a `[DeviceInfoNNNNN]` block every time it boots. The block is the node's own record of how it was set up and whether its sensor worked at that power-up. It has four parts.

**1. Identity and boot.**

- `BootReason` is a code, not decoded: 0007 and 0300 were seen on normal power-ups, and 0087 on a boot without an RTC time.
- `Firmware Version` and `BootLoader Version`.
- `Boot RTC` is the clock at boot. It is a 2015–2016 date before the first GPS lock, which is normal.

**2. The script echo.** These are the settings the node actually runs with, taken from `sct_par.xml`:

- the script and project names;
- the device type and number of channels;
- `FIFO Storage MODE`;
- `GPS Power Mode`;
- the channel types and gains;
- `Sample Rate`, which is the sample interval in 10 µs units, so 100 means 1000 sps;
- `Anti-alias Filter Type`, `Low Cutter Filter` and `ADC LP Mode`;
- the voltage thresholds.

This echo is the only place to check that a script was applied as planned, so compare it with the script you meant to apply.

**3. The geophone self-test, per channel (`Ch1`, `Ch2`, `Ch3`).** At power-up the node drives a current through each geophone coil, switches it off and records how the mass rings. This is the pulse test whose raw trace is kept in `PULSE_X/Y/Z.WAV`, sampled at 1000 sps although the WAV header says 10 000 Hz. From the test it logs:

| Field | Meaning | Typical (5 Hz geophone) |
|---|---|---|
| `ChN Resistance` | Coil resistance in kΩ. One value on V1.0.5, two on later firmware. | 1.62–1.78 kΩ, rising with temperature like copper |
| `ChN Resonate Freq` | Natural frequency f0 (Hz) | 4.9–5.1 Hz |
| `ChN Damping` | Damping ratio h | 0.70–0.77 |
| `ChN Sensitivity` | Generator constant, V/(m/s) | 75–80 |
| `ChN RMS Noise` | Noise of the quiet part of the test, in µV (it equals the WAV's noise floor) | about 1.1 µV |
| `ChN Spread Noise` | Scatter during the test: a measure of movement | under about 5000 when the node is still |

These are followed by the limits from the script:

- `Upper/Lower Resistance Limit`, given twice: the second number is corrected for the boot temperature;
- `Geophone Test Passed` (1 or 0).

**4. Boot conditions.**

- `GPS Lock Time`, satellites and signal strengths;
- `Calendar Reference`, which links the RTC to the script's start date;
- `Voltage`;
- `Free TF Memory`;
- `Booting Temperature`;
- the booting eCompass, tilt, roll and pitch;
- `Sync Delay` and `Sync Count`.

How to use the self-test:

- **Do not rely on `Geophone Test Passed` alone.** A test taken while the node is carried or still settling has a large spread noise and gives out-of-spec damping or sensitivity, yet still reports passed. Node 453022522 at installation had a spread noise of 22 000, damping of 0.59 and sensitivity of 62, and was marked passed. `smartsolo_node.read_node_folder(...)["qc"]` compares every value with the limits and adds a `noisy` flag; use its `ok_all`.
- **Expect resistance to follow temperature.** It is about 1620 Ω at −2 °C and about 1780 Ω when warm, so compare it with the temperature-corrected limit.
- **Ignore bench and transport boots.** They give impossible values (f0 = 0 or 134 Hz, negative damping). One-off glitches also happen, such as f0 = 78 kHz on one channel once.
- **Rely on the test that was settled.** The test from the installation boot is often noisy; the test at recovery, or at a later reboot in place, is usually clean.
- **Check the test mode.** The test runs when the script's `Geophone_Test_mode` is 0 (full detection only once) and is skipped with 2 (no detection). Mode 1, full detection, is not available on the IGU-16HR 3C.
- **The mapping of Ch1–Ch3 to X/Y/Z is not documented.**

`smartsolo_log.read_device_info(...)` gives one row per boot with all these fields (`ch1_resistance_1`, `ch1_resonate_freq`, `ch1_damping`, …). `smartsolo_node.read_node_folder(...)` adds the limits, the pass/fail checks and the analysis of the pulse WAVs.

## Data files, timing and units

- **DLD files** have a 512-byte header (serial, firmware, start and end time, position when the file was closed). Then come blocks of 1000 int24 samples, each followed by a 72-byte time tag. The tag holds the GPS week and time of week, a text time, the position and the time since the last GPS synchronisation. See [DLD_FORMAT.md](DLD_FORMAT.md).
- **Timing.** Use the GPS time of week, which the toolbox does by default. The text time in the tags is not reliable, and its behaviour depends on the firmware. On V1.0.5–V1.0.8 it is 1 s late until the receiver knows the leap seconds, then jumps back 2 s within the file and is 1 s early after that. V1.1.4.2be files were 1 s early from the start, with the same leap-second field. See [FINDINGS.md](FINDINGS.md#dld-timing).
- **Sample rate.** `Sample Rate` / `Sample_Rate` is the sample interval in 10 µs units: 25, 50, 100, 200, 400, 1000 and 2000 mean 4000, 2000, 1000, 500, 250, 100 and 50 sps.
- **Gain.** Raw counts include the preamp gain (0–36 dB in steps of 6 dB), so remove it or put it in the response.
- **Polarity.** All IGU-16HR channels have negative polarity relative to FDSN conventions. Multiply the data by −1, as AusPass does; the toolbox does this by default. BD3C-5 nodes need no flip.
- **Components.** X is north–south (`?PN`), Y is east–west (`?PE`) and Z is vertical (`?PZ`).
- **Response.** The default is the AusPass IGU-16HR 3C response: 5 Hz, h = 0.707, 76.6 V/(m/s) × preamp gain × 3 355 342.4 counts/V.

## Firmware

- **Read the firmware from the log.** The firmware that is running is the `Firmware Version` in the latest `[DeviceInfo]`. `device.ini` is not updated when firmware is changed: on 7 Oct 2026 three nodes downgraded to V1.0.5.6kp, V1.0.7.8bke and V1.0.8.1be still had V1.1.4.2be in `device.ini`.
- **The DLD header records the firmware a file was recorded with.** This is correct for that file, even if the node has been flashed since.
- **Firmware changes the files.** Known differences include GNSS records (V1.1.4), the number of resistance values (V1.0.5), the eCompass range (−180…180° on V1.0.5) and the text-time offset. The reader handles all of these.

## Where to go next

- **[User guide](USER_GUIDE.md):** reading logs, finding deployments, selecting and cutting data, DLD files, batch conversion, QC and orientation.
- **[Tutorial notebooks](../notebooks/):** the same steps with real data.
- **[FINDINGS.md](FINDINGS.md):** all the numbers.
