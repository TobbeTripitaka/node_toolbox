# SmartSolo DLD file format (reverse-engineered)

Notes from IGU-16HR 3C files written by firmware V1.0.8.1be, V1.1.2.0be and
V1.1.4.2be at 250 and 1000 samples/s. Nothing here comes from DTCC
documentation – treat it as a working hypothesis and check against a SoloLite
export (`smartsolo_dld.compare_with_export`) when one is available.

Implemented in [`lib/smartsolo_dld.py`](../lib/smartsolo_dld.py).

## Files

One file per component and recording: `seis000X.DLD`, `seis000Y.DLD`,
`seis000Z.DLD`, then `seis001?.DLD` after the next power-up, etc. The file
number matches the `Start Acquisition FileName = "Seis000.DLD"` notices in
`DigiSolo.LOG`. File sizes are multiples of 512 bytes.

```
offset        size   content
0x000          512   file header
0x200         3000   block 0: 1000 samples, int24 little-endian, two's complement
0x200+3000      72   time tag 0
                     block 1 (3000 bytes) + tag 1 (72 bytes)
                     ...
                     block n-1 + tag n-1   (ends exactly at end of file)
```

Period: 3072 bytes = 1000 samples + one tag. Samples are continuous across
tags (verified by checking that the signal shows no extra jumps at the
block boundaries).

## File header (512 bytes, little-endian)

| offset | type | example | meaning |
|---|---|---|---|
| 0x000 | char[16] | `DTCCSZ-TEC-FTS-0` | magic |
| 0x010 | int32 | 2 | format version? |
| 0x020 | char[16] | `453038428` | node serial |
| 0x030 | char[16] | `80004000001` | hardware id? (same on all nodes) |
| 0x040 | char[32] | `IGU-16HR 3C 5Hz`, `DML` | project name from the script |
| 0x060 | int32[2] | 1, 1 | ? |
| 0x070 | char[16] | `V1.0.1.8` | bootloader version |
| 0x080 | char[16] | `V1.1.2.0be` | firmware version |
| 0x090 | char[16] | `022523.00` | start time UTC (HHMMSS.ss) = first tag |
| 0x0A0 | char[16] | `20241008` | start date |
| 0x0B0 | char[16] | `023455.00` | end time = last tag |
| 0x0C0 | char[16] | `20241008` | end date |
| 0x0D0 | int64 | | start tick (ms) |
| 0x0D8 | int64 | | end tick (ms); end − start = duration in ms |
| 0x100 | int32[7] | 0, 18, 171, 87, 0 … | ? (0x104 = 18 on V1.0.8 files: leap seconds?) |
| 0x11C | float32 | 1642.0 | altitude (m) |
| 0x120 | int32[4] | 1, 2, 3, 0 | ? |
| 0x140 | char[80] | `dmlparameterscript` | script name |
| 0x190 | float64 | 11.118683 | longitude |
| 0x198 | float64 | −71.5494405 | latitude |
| 0x1A0–0x1FF | | zeros | |

Header bytes are identical in the X, Y and Z files of one recording.

## Time tag (72 bytes, after every 1000 samples)

| offset | type | example | meaning |
|---|---|---|---|
| +0 | int32 | 0 | flag? |
| +4 | int64 | 10106305104488 | tick, ms: +1000 per tag at 1000 sps, +4000 at 250 sps |
| +12 | char[11] | `203718.00` | UTC time, whole seconds |
| +23 | char[9] | `20250211` | UTC date |
| +32 | float64 | −66.282305 | latitude |
| +40 | int32 | 0, ±1 | probably clock phase error |
| +44 | int32 | 0 … 3000 | 0, then +100 per second near the end of the file |
| +48 | float64 | 110.530844 | longitude |
| +56 | char[16] | `247057000` | GPS time of week in ms (whole seconds) |

- Sample rate = 1000 samples / (tick step / 1000 s). This matches the log:
  `Sample Rate` in `DigiSolo.LOG` / the script is the **sample interval in
  10 µs units** (100 → 1 ms → 1000 sps; 400 → 4 ms → 250 sps; 200 → 500 sps).
- The absolute tick value is not a linear clock across dates, so only its
  differences are used; UTC comes from the date/time text.
- **Timing convention (unverified)**: `tag_marks="block_start"` (default)
  takes tag *k* as the time of the first sample of block *k* (the block
  before the tag), so the file starts at the header start time.
  `"block_end"` shifts all samples one block earlier. Two nodes 10 m apart
  that started 4 s apart line up to within one sample (cc 0.9) either way,
  so the relative timing is right; the absolute offset needs a SoloLite
  export to confirm.

## Sample values

- Raw ADC counts, int24, **including the preamp gain** (`Channel N Gain`
  in the log: 0–36 dB). SoloLite's "Remove Gain" divides by 10^(gain/20).
- Raw SmartSolo polarity (AusPass: multiply by −1 for the FDSN convention).
- Full scale ±8 388 608 counts; clipping shows up as runs of ±2^23.
- The Z file often starts with a few dozen zero samples.
- X = north–south axis, Y = east–west, Z = vertical (SmartSolo manual);
  the toolbox maps X → N, Y → E.
