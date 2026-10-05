# Break test data

**The seismic signal of a Toyota Land Cruiser HJ60 that brakes – with a
laughing baby in the back seat.** Roughly ten braking manoeuvres; exact
times were not noted.

Five SmartSolo IGU-16HR 3C 5 Hz nodes along the road beside the sports oval
in Sandy Bay, Hobart, on **31 March 2023, 01:28–02:00 UTC** (12:28–13:00
AEDT). Project `UTASTesting`, firmware V1.0.5.6kp, 500 sps, 0 dB gain.
Folders as harvested by SoloLite (`SERIAL/YYYYMMDDhhmmss/`).

| serial | recording (UTC) | position |
|---|---|---|
| 453009194 | 01:30–01:59 | SW group |
| 453004362 | 01:29–02:00 | SW group, 5 m from 453009194 (carried away 01:55) |
| 453010047 | 01:28–01:59 | SW group, 21 m from 453009194 (carried away 01:55) |
| 453010077 | 01:33–02:00 | NE pair, ~110 m from the SW group (moved 01:51) |
| 453010167 | 01:34–01:58 | NE pair, 12 m from 453010077 (moved 01:51) |

Each folder: `DigiSolo.LOG`, `sct_par.xml`, `SCT_INT.XML`, `DigiSolo.TXT`,
`PULSE_{X,Y,Z}.WAV` and the raw data `seis000{X,Y,Z}.DLD` (2.2–2.8 MB each).
The logs also contain a later power-up of the nodes; its data files are
not included.

The `.DLD` files are stored with **Git LFS** (~40 MB). After cloning:

```bash
git lfs install
git lfs pull
```

See `notebooks/break_test.ipynb` for the map and analysis and
`notebooks/dld_demo.ipynb` for reading the files.
