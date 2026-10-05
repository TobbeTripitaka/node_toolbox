# Break test data

**The seismic signal of a Toyota Land Cruiser HJ60 that brakes – with a
laughing baby in the back seat.** Roughly ten braking manoeuvres per
recording; exact times were not noted.

Four complete SmartSolo IGU-16HR 3C 5 Hz nodes, as harvested by SoloLite
(`SERIAL/YYYYMMDDhhmmss/`), project `UTASTesting`, firmware V1.0.5.6kp,
500 sps, 0 dB gain:

| serial | `seis000` 31 Mar 2023 (UTC) | `seis001` 6–7 Apr 2023 (UTC) |
|---|---|---|
| 453009194 | 01:30–01:59, SW pair | 23:45–04:11 |
| 453010047 | 01:28–01:59, SW pair (carried away 01:55) | 23:46–04:10 |
| 453010077 | 01:33–02:00, NE pair (moved 01:51) | 23:45–04:05 |
| 453010167 | 01:34–01:58, NE pair (moved 01:51) | 23:47–04:09 |

- 31 March: road beside a sports oval in Sandy Bay, Hobart; two pairs
  ~110 m apart (pair spacing 21 m and 12 m).
- 6–7 April: sports field in the Queens Domain, Hobart; nodes within 50 m.

Each folder: `DigiSolo.LOG`, `sct_par.xml`, `SCT_INT.XML`, `DigiSolo.TXT`,
`PULSE_{X,Y,Z}.WAV`, and the raw data `seis000{X,Y,Z}.DLD` (≈2.6 MB each) and
`seis001{X,Y,Z}.DLD` (≈24 MB each).

The `.DLD` files are stored with **Git LFS** (~300 MB). After cloning:

```bash
git lfs install
git lfs pull                     # or: git lfs pull --include "*/seis000*"  (the short session only)
```

See `notebooks/break_test.ipynb` for maps and analysis and
`notebooks/dld_demo.ipynb` for reading the files.
