# What I learned from the SmartSolo DLD files

A record of everything found while building this toolbox, including results
from sample files that are no longer in the repository (raw DLD files from
Dronning Maud Land, Casey, a two-node side-by-side test, and a longer later
recording of the break-test nodes). Numbers are given so they can be checked against new data.

Nodes: DTCC SmartSolo IGU-16HR 3C 5 Hz, firmware V1.0.5.6kp, V1.0.8.1be,
V1.1.2.0be and V1.1.4.2be.

## Files on a node

| file | content |
|---|---|
| `DigiSolo.LOG` | state-of-health log (INI-like text) |
| `seisNNN{X,Y,Z}.DLD` | raw data, one file per component per power-up ([format](DLD_FORMAT.md)) |
| `PULSE_{X,Y,Z}.WAV` | geophone pulse test of the latest power-up |
| `sct_par.xml` (+ identical `sct_par_b.xml`) | acquisition script with test limits |
| `SCT_INT.XML` | script as interpreted by the node |
| `device.ini` | serial number and firmware (not on all nodes) |
| `DigiSolo.TXT` | file-system marker ("This is DigiSolo IGU-16 File System") |
| `guardfile.db` | binary journal of recent log keys – nothing new |

SoloLite harvests a node into `.../SERIAL/YYYYMMDDhhmmss/` (the break-test
data keep that layout).

## Log file

- First line `<a,b,c,d,e,f,g>` = number of DeviceInfo, GPS, Temperature,
  Memory, Battery, Error and Notify records. All match the parsed counts
  except Notify, which is often off.
- `Sample Rate` (log and script) is the **sample interval in 10 µs units**:
  100 → 1 ms → 1000 sps, 200 → 500 sps, 400 → 250 sps. Confirmed by the DLD
  time tags (1000 samples per tag; tag spacing 1, 2 and 4 s). The DML nodes
  therefore recorded at **1000 sps**, not 100.
- `Channel N Gain` is the preamp gain in dB (0, 18 and 36 dB seen).
- `ChN RMS Noise` is in µV (it equals the noise floor of the pulse test).
- `ChN Resistance` is in kΩ; V1.0.5 logs one value, later firmware two.
- Upper/Lower Resistance Limit: the second numbers are the limits corrected
  for the boot temperature.
- Firmware differences handled by the reader:
  - V1.1.4 writes `[GNSSnnnnn]` and `GNSS ...` keys instead of GPS
    (header still counts them as GPS).
  - `UTC Time = ","` before the first fix; `Altitude = Unknown`.
  - `ADC Sync Value` has 2 or 3 numbers.
  - V1.0.5 logs `eCompass North` in −180…180°, later firmware 0…360°.
- Boots without `Boot RTC`, and RTC times of 2015/2016 before the first GPS
  lock, are normal. `[BatteryPowerOnStop]` has no time.
- The log's first record after a power-up (`Start Acquisition FileName`) is
  20–45 s after the first sample in the DLD file.

## Deployments and position

- Location = first stable GPS position after power-up (5 fixes within
  10 m). Cold-start fixes can be 45–100 m off (453022522, DML).
- DML (2 weeks on ice): drift 2.6–3.1 m, ~0.2 m/day, within GPS noise.
- The 72-byte DLD tags carry a GPS position every 1000 samples, so nodes
  without a log can be located – and **handling shows up**: on 31 March 2023
  five break-test nodes were carried 70–230 m while still recording.
- The DLD *header* position is written when the file is closed, so it is
  the position at the end (after any move), not at installation.

## Compass and tilt

- Tilt = √(roll² + pitch²) exactly.
- Headings are stable to ~1° (circular std). 453021267 (DML) rotated
  +0.17°/day (2.3° in two weeks) with constant tilt – ice motion or compass
  drift.
- Boot-time readings are taken before the node is planted (DML: 24° tilt and
  36° off the settled heading) – use the settled heading.
- Whether `eCompass North` is the magnetic azimuth of the N arrow is not
  documented. IGRF declination is −29.5° in DML. The two DML nodes would
  point ~127° and ~97° true; the six break-test nodes read 96–162° (and 31–152° on a later power-up), so the
  nodes were not aligned to north (or the compass wasn't calibrated).
- Tilt beyond spec: 453022522 (DML) 5.1° (> 3° horizontal-geophone spec);
  453010167 (break test, 31 Mar) 11.3° (> 10° vertical spec).

## Boot-time geophone test

- Script limits: resistance 1640–1905 Ω, damping 0.647/0.648–0.752,
  f0 4.63–5.37 Hz, sensitivity 71–82 V/(m/s).
- Coil resistance follows temperature like copper: ~1620 Ω at −2 °C vs
  ~1780 Ω warm (DML); 1730–1750 Ω at 17–19 °C (Hobart).
- Tests taken while the node moves are unreliable: spread noise
  > 5000 (DML 453022522 at installation: up to 22 000; break test 453010047:
  144 000) gives out-of-spec damping/sensitivity even though the log says
  `Geophone Test Passed = 1`. Use the `noisy` / `ok_all` flags.
- One-off glitches happen: 453010077 Ch3 on 31 March returned f0 = 78 kHz
  without being noisy; the next boot was fine.
- Bench/transport boots give impossible values (f0 = 0, 134 Hz, −385 Hz,
  negative damping).

## Pulse test (`PULSE_*.WAV`)

- 24-bit mono WAV, header says 10 000 Hz, but it is the 16-s "Stage 3"
  geophone/ADC test (SmartSolo manual) in 16 000 samples → **1000 sps**.
- The rate is **fixed**: it is 1000 sps on nodes recording at 1000 sps (DML)
  and at 500 sps (break test).
- Sequence: ~1.9 s clipped pre-test, current steps (+, off, −, off, +, off,
  ~1 s each), ~6 s quiet.
- Damped-oscillator fits after the switch-offs: f0 4.95–5.11 Hz,
  h 0.67–0.73, within ~2 % of the logged boot test. The positive-current
  switch-off gives ~5.35 Hz (suspension non-linearity at larger drive).
- Quiet part: 3.9–4.2 counts RMS = 1.15–1.25 µV, the log's `RMS Noise`,
  which confirms 3355.4428 counts/mV.

## Polarity, codes and response

- AusPass (controlled tests, Dec 2025): **all IGU-16HR channels have
  negative polarity** relative to FDSN (positive up/north/east) → ×(−1).
  Matches the manual: positive X = case moves south, Y = west, Z = down.
  BD3C-5 broadband nodes need no flip.
- X = north–south, Y = east–west of the node's arrow → `?PN`, `?PE`.
- Band codes from the sample rate: 250/500 sps → `DP?`, 1000 sps → `GP?`.
- AusPass response: zeros 0, 0; poles −22.211059 ± 22.217768j
  (5.000 Hz, h 0.707); 257 019 225.55 counts/(m/s) flat gain (= 76.6 V/(m/s)
  × 3355.4428 counts/mV) for gain-removed data. Raw DLD counts include the
  preamp gain: ×10^(gain/20).
- SoloLite pitfalls (AusPass): missing `smartsoloconfig.xml` → constant
  ~18 s time offset; "Remove Gain" must match the programmed gain.

## DLD timing

- Format: 512-byte header, then [1000 × int24 samples + 72-byte tag]
  repeated; samples are contiguous across tags (checked on 250, 500 and
  1000 sps files). See [DLD_FORMAT.md](DLD_FORMAT.md).
- The tag's **text time is unreliable at the 2-s level**: it jumps back 2 s
  when the GPS receiver learns the leap seconds (header field 0x104 = 0
  before, 18 after) – 1 s late before, 1 s early after (relative to TOW,
  below). Seen in a later 4-hour
  recording of four break-test nodes (removed; jumps at 23:56, 00:21, 00:58
  and 02:38 UTC), in the V1.1.2 side-by-side test files and in all six
  31 March break-test files (label 1 s late throughout, leap field 0).
- The tag's **GPS time of week (TOW)** is continuous. UTC = GPS week + TOW
  − (GPS−UTC); `smartsolo_dld` uses this by default (`time_source="tow"`).
- Proof: cross-correlating ambient noise between nodes every 10 min over
  the later 4-hour recording, text times gave lags of exactly ±2 s whenever
  one node's labels had jumped and the other's hadn't; TOW times gave
  0 ± 4 ms throughout. A 3-minute excerpt around the jump of 453009194 at
  00:58:37, with the same minutes from 453010047, is kept in
  `data/timing_example/` (2-s lag with labels before the jump, 0 after and
  with TOW; `notebooks/dld_demo.ipynb`, test `test_dld_label_jump_excerpt`).
- Two nodes 10 m apart started 4 s apart (Beijing test, 250 sps, removed
  sample files): lag 0 samples, cc 0.96 (X), 0.93 (Z) – blocks and tags
  decoded correctly.
- The tag `counter` field resets to 0 at each GPS synchronisation and then
  counts 100 per second (time since the last sync in 10 ms units).
- **Absolute offset.** An earlier version used UTC = week + TOW − 1 s −
  (GPS−UTC), chosen so that TOW agreed with the labels once the leap seconds
  are known. A comparison by another group with co-located permanent stations
  (S1.AUANU, M8.AUANU) showed node data **1 s early** with it, so the offset
  is now 0 (`smartsolo_dld.DEFAULTS["tow_offset_s"]`, kept as a setting).
  At 1000 sps one block is also 1 s, so the block convention below should
  be confirmed independently (co-located test at 250 or 500 sps, or a SoloLite
  export).
- Still open: whether a tag marks the first sample of the block before it
  (`tag_marks="block_start"`, default) or after it – a constant shift of one
  block (1, 2 or 4 s). One SoloLite export of the same file settles it
  (`smartsolo_dld.compare_with_export`).

## Sample files seen (most now removed)

| serial | site | data | key facts |
|---|---|---|---|
| 453021267, 453022522 | DML, Antarctica (−71.55, 11.12) | logs, 2 weeks (**kept**, `data/nodes/`) | 1000 sps, 0 dB, −17…+8 °C |
| 453022317 | DML | `seis001?.DLD`, 3 min | 1000 sps, header altitude 1642 m |
| 453027665 | near Casey (−66.28, 110.53) | `seis006?.DLD`, 72 s | 1000 sps |
| break-test nodes | Hobart, 6–7 Apr 2023 | `seis001?.DLD`, 4 h (removed; 3-min excerpts of two nodes in `data/timing_example/`) | label jumps, see DLD timing |
| 453038428, 453038431 | Beijing test (39.596, 116.760) | logs + `seis000?.DLD` (removed) | 250 sps, 18 dB, side by side, 4 s apart |
| 4530462xx, 4530261xx | short tests | logs only (removed; one V1.1.4 log kept as `tests/fixtures/DigiSolo_V1.1.4_GNSS.LOG`) | V1.1.2/V1.1.4, GNSS records, 500 sps, 36 dB |
