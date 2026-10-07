# Huddle test

## What we are doing and why

SmartSolo nodes are used with several firmware versions, and the firmware is not documented. Earlier work found behaviour that changes between versions:

- the text time labels in the DLD files are 1 s late on some firmware and 1 s early on others;
- the labels jump 2 s within a file;
- `device.ini` keeps the old firmware version after a node is reflashed;
- the boot records and the record names in the log differ.

Before the toolbox can be trusted on data from any node, we need to know how timing, gain, filtering and the other settings behave on every firmware.

In a huddle test, 24 IGU-16HR 3C nodes stand side by side on one pad, next to a reference broadband station (Pegasus + Compact). There are three nodes for each of 8 firmware versions. Every node feels the same ground motion, so any difference between their records comes from the node, its firmware or its settings. The nodes record hammer impacts in **7 batches**; between batches each node gets a different acquisition script. The test answers these questions:

| Question | How |
|---|---|
| Is every node's absolute timing right, at every sample rate and on every firmware? | Lag of each node against the reference, impact by impact |
| Does a DLD time tag mark the start or the end of its 1000-sample block? | Fit lag = a + b × block length per firmware: b ≈ 0 means start, b ≈ 1 means end |
| How do text labels relate to GPS time of week, and does this depend on firmware? | Label − TOW, the 2-s jumps and the leap field, per firmware |
| Is the gain linear from 0 to 36 dB? | Amplitude against 10^(gain/20) |
| How much delay does minimum-phase filtering add, at each rate? | Minimum- against linear-phase units |
| Is the polarity the same on every channel and firmware? | The sign of each channel after node_toolbox's ×−1 |
| Which logged channel (Ch1–3) is X, Y and Z? | Different gains per channel (0/12/24 dB) |
| What do GNSS always-on, the constellations, low cut (including the unknown codes 2 and 10), low-power ADC mode, node MiniSEED and "no geophone test" change? | One setting changed per unit, against reference units |
| How much do identical nodes scatter? | Units with the same setting |
| Does every node record what its script says? | The script echo in the log's `[DeviceInfo]`, and the DLD header |

## Running the test: the sandbox

Everything the tester needs is in [`sand_box_huddle_test/`](sand_box_huddle_test/):

```
sand_box_huddle_test/
  run_huddle.py        start here: guides the tester through every step of every batch
  huddle_matrix.csv    the plan: one row per unit and batch, batches 1-7 (168 rows)
  units.csv            unit -> firmware -> node serial (filled in by run_huddle.py)
  huddle.yaml          settings: data folder, where nodes appear, reference station, analysis
  xml/                 the acquisition script for every row (B<batch>-<unit>.xml) + manifest.csv
  data/                everything recorded and derived - git-ignored, never pushed to GitHub
```

```bash
cd tests/huddle_test/sand_box_huddle_test
python run_huddle.py --simulate   # practice run with fake nodes first (no hardware needed)
python run_huddle.py              # the real test; run it again after each break
python run_huddle.py --status     # progress of every batch
```

It works on Windows, macOS and Linux. Nodes connected to the reader or rack appear as USB drives: drive letters D:–Z: on Windows, `/Volumes` on macOS, and `/media`, `/run/media` or `/mnt` on Linux. The script finds them by their content.

The script remembers where it stopped (`data/state.json`), so it can be closed at any point. Each batch goes through these steps:

1. **Connect.** The script shows the batch plan and any scripts with unconfirmed codes. It finds the nodes and reads each one's serial number and firmware. The firmware is read from the last boot in the log, because `device.ini` is not updated when a node is reflashed; the script warns when the two disagree. Unknown serial numbers are assigned to units, suggesting free units with the same firmware. Any data already on a node is copied to `data/raw/batch_00/` first.
2. **Apply.** A dry run shows which script goes onto which node. Nodes whose firmware differs from the plan, or which hold data that has not been copied, are refused. Then the tester types `yes`. The node's current scripts are backed up, and the new `sct_par.xml` (and `sct_par_b.xml` if present) is written and verified with SHA-256.
3. **Record.** A field checklist follows:
   - layout and north arrows, as planned;
   - the reference station recording;
   - GPS lock;
   - at least 1 h of recording, and 3 h for batch 5 (GNSS always on against cycle);
   - three series of about 15 hammer blows at irregular intervals of 3–12 s.

   The script records the start time, site, tester and notes, then stops while the nodes record.
4. **Stop.** The script records the end time and notes, and copies the reference station's MiniSEED for the batch to `data/reference/batch_NN/`.
5. **Collect.** Every log, data file, script, `device.ini` and pulse file is copied off the nodes to `data/raw/batch_NN/<unit>_<serial>/`. Each copy is written as `.part`, verified with SHA-256 and renamed. DLD files collected in an earlier batch are listed but not copied again. `collect_manifest.csv` lists every file with its size, hash and status. Nothing is deleted from the nodes; format them in SoloLite only after checking the copy.
6. **Analyse.** The script makes the measurements and runs all checks and figures. It writes `data/results/summary.md` and shows the main results: settings as planned, timing labels per firmware, firmware offsets and the block convention.
7. **Next batch.** The script asks whether to start the next batch.

These safety rules apply throughout:

- Only folders that look like a node (`device.ini`, or `DigiSolo.LOG` with `sct_par.xml`) are touched, and drives A:–C: never.
- Only `sct_par.xml` and `sct_par_b.xml` are ever written to a node, and only after a backup.
- Nothing is ever deleted.

At the end, the data folder looks like this:

```
data/
  state.json                         progress, field notes (start, end, site, tester, notes)
  backups/<serial>/<time>/           every script that was replaced
  raw/batch_00/pre_<serial>/         data found on the nodes before the test
  raw/batch_NN/<unit>_<serial>/      everything copied off each node + collect_manifest.csv
  reference/batch_NN/                the reference station's MiniSEED
  results/batch_NN/measurements.csv  one row per unit, component and impact
  results/<check>.csv, figures/      check results and figures
  results/summary.md                 every check with its question and result
  simulation/                        practice runs (--simulate)
```

The data are too large for GitHub (several GB per batch). Keep `data/` on the test computer and back it up, or set `data_dir` in `huddle.yaml` to a folder on an external drive outside the repository.

## The batches

Each unit keeps its firmware for the whole test.

| Firmware | Units | | Firmware | Units |
|---|---|---|---|---|
| V1.0.5.6kp | F1A, F1B, F1C | | V1.0.7.8bke | F5A, F5B, F5C |
| V1.0.5.8ke | F2A, F2B, F2C | | V1.0.7.9be | F6A, F6B, F6C |
| V1.0.5.9ke | F3A, F3B, F3C | | V1.0.8.1be | F7A, F7B, F7C |
| V1.0.7.5ke | F4A, F4B, F4C | | V1.1.4.2be | F8A, F8B, F8C |

**Batches 1–4** follow a Latin square of sample rate (all 8 rates on every firmware), gain (0–36 dB), filter phase and rotation. They include a baseline unit at 1000 sps, 0 dB and linear phase on every firmware.

**Batches 5–7** change one setting per unit from the reference setting (1000 sps, 0 dB, linear phase, not rotated, GNSS cycle, GPS only, low cut off, normal ADC, DLD, geophone test once). Each result can therefore be read on its own.

| Batch | Unit A | Unit B | Unit C |
|---|---|---|---|
| 5 | GNSS always on (all firmware) | low cut 0.05, 0.2, 0.8, 1.25, 7.5 Hz, DC removed, 0.8, 7.5 Hz (F1…F8) | reference |
| 6 | Ch1 0 dB, Ch2 12 dB, Ch3 24 dB: channel mapping (all firmware) | node MiniSEED (F1, F3, F5, F7); low-power ADC (F2, F4, F6, F8) | GPS+GLONASS (F2, F6); GPS+BeiDou (F4, F8); reference (F1, F3, F5, F7) |
| 7 | minimum phase at 50, 100, 125, 250, 500, 2000 or 4000 sps | minimum phase at a second rate (1000 sps on F1–F3) | reference (F1, F5); **low-cut code 2** (F2, F6); **low-cut code 10** (F3, F7); no geophone test (F4, F8) |

Notes on the design:

- **Batch 7 is the optimiser's choice plus the unknowns.** Before it, minimum phase had been tried only at 1000 sps, on five firmware versions. Its delay scales with the block length, so batch 7 covers every rate twice across the firmware versions. Each minimum-phase unit is compared with the linear-phase unit of the same rate and firmware from the Latin square.
- **The frequencies of low-cut codes 2 and 10 are not known.** They appear in SoloLite scripts without a name. They are marked `unknown_2` and `unknown_10` and flagged as unconfirmed, so the measured phase and amplitude change shows what they do.
- **Test mode "full detection" (code 1) is not available on the IGU-16HR 3C**, so it is not tested.
- **Reference units for comparisons.** Each comparison uses reference-setting units of the same firmware in the same batch where possible, else in other batches, so firmware offsets cancel.

## Checks

Each check answers one question. Its results go to `data/results/<name>.csv`, and all of them together to `summary.md`.

| Check | Question |
|---|---|
| `settings` | Did every node record with the planned settings? The DLD header gives firmware, rate, leap field, label − TOW, label jumps and gaps. The log's last `[DeviceInfo]` gives firmware, rate, gain per channel, filters, ADC mode and GNSS mode |
| `time_labels` | Per firmware: the leap field, label − TOW at the start and end of the file, files with a 2-s jump, and the longest time without GPS sync |
| `timing` | The lag of each node against the reference: the median over all impacts, with spread and correlation |
| `block_convention` | Per firmware, the fit lag = a + b × block length: b ≈ 0 means tags mark the block start (the default), and a is the constant offset |
| `firmware` | Lag, amplitude and polarity per firmware at the same setting |
| `polarity` | Is every channel +1 (FDSN) after the ×−1? |
| `gain` | Amplitude against 10^(gain/20), relative to the 0 dB nodes |
| `filter_phase` | Delay and waveform change of minimum phase against linear phase, per rate |
| `orientation` | Horizontal rotation measured from the data, against the plan |
| `unit_scatter` | Spread of lag and amplitude between nodes with identical settings |
| `factors` | Effect of each extra setting: lag difference and scatter, correlation, amplitude and noise ratio |
| `channel_mapping` | Which logged channel is X, Y and Z |

Lags are measured in these steps:

1. **Common band and rate.** All data are bandpassed to 2–15 Hz (below Nyquist at 50 sps) and resampled to 200 sps.
2. **Coarse lag.** The envelopes are correlated with a search of ±25 s, which catches block errors of up to 20 s.
3. **Fine lag.** Each impact is correlated within ±0.5 s.
4. **Responses.** With `reference.stationxml` set, the geophone response is removed from the nodes and the full response from the reference.
5. **Without a reference station**, the lags are relative to the median of the reference-setting nodes.

To add a check, register a function:

```python
from huddletest import checks

@checks.check("my_check", "What does it answer?")
def my_check(meas, ctx):          # meas: all measurements; ctx: cfg, matrix, batches, earlier results
    return meas.groupby("unit").lag_s.median().reset_index()
```

## Scripts and codes

`xml_codes.yaml` maps each matrix setting to its `sct_par.xml` code. The codes come from 32 scripts exported by SoloLite for node 453046173 (V1.1.4.2be), kept in `templates/sololite_reference_V1.1.4.2be/`, and from logs of nodes booted with them. `xml/manifest.csv` lists, for every script, the template used, whether that template is verified for the firmware, and any unconfirmed codes. These codes are unconfirmed:

- `Sample_Rate` 800 and 1000 (125 and 100 sps);
- low-cut codes 2 and 10 (frequency unknown).

The templates differ by firmware:

- V1.1.4.x units use the SoloLite Linear Phase script.
- Other firmware use the DML script of a V1.0.8.1be node (`templates/sct_par_template.xml`). This template is not verified for V1.0.5.x and V1.0.7.x. Export one script from a node with each older firmware and add it under `templates_by_firmware`.

## Other tools

The same steps can be run one at a time with the command-line tool, for scripting or repairs:

```bash
python huddle.py make-xml                     # rewrite xml/ from the matrix
python huddle.py nodes                        # connected nodes, serial, firmware (log and device.ini), unit
python huddle.py apply 1 [--yes]              # dry run unless --yes
python huddle.py collect 1
python huddle.py analyse 1 && python huddle.py checks
python huddle.py propose --method optimised   # append batch 8 to the matrix (or --method random)
python huddle.py demo                         # synthetic batches end to end in /tmp/huddle_demo
```

- **`huddle_analysis.ipynb`** shows the analysis with tables and figures. Until real data exist it runs on synthetic batches 1, 5 and 6, with known faults that the checks must find: tags at the block end on V1.0.7.5ke, a 20 ms offset on V1.0.7.9be and a reversed Z on F2B.
- **`../test_huddle.py`** runs the chain, including `run_huddle.py --simulate`, on synthetic data.
