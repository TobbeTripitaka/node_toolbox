# Example node folders (logs only)

Two SmartSolo IGU-16HR 3C 5 Hz nodes from **Dronning Maud Land, Antarctica**.
These are the long state-of-health examples (temperature, battery, GPS,
tilt, compass over weeks in the cold). There are no waveform data here; the
raw-data examples are in [`../break_test/`](../break_test/README.md).

| serial | files | firmware | settings | log span (UTC) | deployment (first stable fix) |
|---|---|---|---|---|---|
| 453021267 | `DigiSolo.LOG` | V1.0.8.1be | 1000 sps, 0 dB | 2024-11-13 – 2025-01-08 | 2024-12-25 14:26 – 2025-01-08 09:04, −71.5482, 11.1157, 1649 m |
| 453022522 | `DigiSolo.LOG`, `sct_par.xml`, `sct_par_b.xml`, `SCT_INT.XML`, `device.ini`, `PULSE_{X,Y,Z}.WAV`, `DigiSolo.TXT`, `guardfile.db` | V1.0.8.1be | 1000 sps, 0 dB | 2024-11-13 – 2025-01-08 | 2024-12-25 15:20 – 2025-01-08 10:09, −71.5500, 11.1176, 1644 m |

What they show:

- **Temperature:** −14.0 to +7.7 °C (453021267) and −17.4 to +5.2 °C
  (453022522), with a daily cycle under the polar summer sun.
- **Battery:** from 8.25 V down to 7.6 V over two weeks at 1000 sps.
- **GPS:** cold-start fixes 45–100 m off, so the first stable fix is
  used. Drift is about 3 m in two weeks, which is within GPS noise.
- **Compass and tilt:** 453021267 rotates slowly (+0.17°/day) at 1.7° tilt.
  453022522 is tilted 5.1°, beyond the 3° spec for the horizontal
  geophones. Readings taken at boot, before the node was planted, are off.
- **Before deployment:** bench and transport boots from November 2024 are
  in the logs too (the reader splits the log into one session per
  power-up).
- **Other files:** 453022522 also has the acquisition script with its test
  limits, `device.ini` and a geophone pulse test (`node_qc_demo.ipynb`).

Used by `smartsolo_log_demo.ipynb`, `select_and_extract_demo.ipynb`,
`node_qc_demo.ipynb` and the tests. Details and numbers are in
[`docs/FINDINGS.md`](../../docs/FINDINGS.md).

Other log examples in the repository:

- `data/break_test/*/*/DigiSolo.LOG` – six nodes with firmware V1.0.5.6kp
  (500 sps, one coil resistance, eCompass −180…180°).
- `tests/fixtures/DigiSolo_V1.1.4_GNSS.LOG` – a short log from firmware
  V1.1.4.2be, which writes `GNSS` records instead of `GPS`. It is kept as a
  test fixture for the parser.
