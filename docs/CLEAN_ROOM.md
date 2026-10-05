# How the DLD reader was made (clean-room statement)

`lib/smartsolo_dld.py` reads SmartSolo `.DLD` data files. This page records
how the file format was worked out and how the code was written, so that
anyone using or sharing it can see where it came from.

## What was used

Only these were used:

1. **Data files recorded by our own nodes.** These are the `seisNNN?.DLD`
   files and the matching `DigiSolo.LOG`, `sct_par.xml`, `SCT_INT.XML`,
   `PULSE_*.WAV` and `device.ini` files from instruments run by the
   project.
2. **Looking at those files.** We used hex dumps, byte statistics, sizes,
   repeating patterns, and checks against values in the node's own log
   (start time, GPS position, sample rate, serial number, firmware).
3. **Checking against the physical signal.** We checked that samples run on
   smoothly across block boundaries, and that nodes standing side by side
   record the same signal at the same time (cross-correlation).
4. **Public, freely available information.** This means the AusPass
   SmartSolo pages (polarity, response, channel naming), the public
   SmartSolo hardware user manual (axis directions, ADC counts per mV),
   product data sheets, and general GPS documentation (GPS time of week,
   leap seconds). None of these describe the DLD byte layout.

## What was not used

- **No software was taken apart.** No SmartSolo / DTCC software, firmware,
  SDK, library or app (including SoloLite and deviceconfig) was decompiled,
  disassembled, debugged, traced or otherwise analysed. Vendor software was
  only used as intended, to harvest data from the nodes, and never to work
  out the format.
- **No vendor code.** No source code, header files, constants or tables were
  copied from any vendor product.
- **No confidential documents.** No confidential, licensed or
  non-disclosure material was used, and no format specification was
  obtained from the vendor.
- **No locks bypassed.** No encryption, licence check, copy protection or
  other technical protection was circumvented. The files are plain binary
  data.

## How the code was written

1. Everything learned about the layout was written down as a plain
   description of what each byte means in
   [`DLD_FORMAT.md`](DLD_FORMAT.md). Uncertain points are marked as such.
2. The reader in `lib/smartsolo_dld.py` was written from scratch from that
   description, using only standard Python, NumPy and ObsPy. It contains no
   third-party code.
3. The reader only **reads**. Files are opened read-only (`open(path, "rb")`),
   nothing is ever written to a `.DLD` file, and a test
   (`test_dld_reader_never_modifies_files`) checks that the files are
   byte-for-byte unchanged after reading. Exports (MiniSEED, SEG-Y,
   StationXML) are written as new files in a separate output folder.

## Purpose

The reader exists so that users can read **their own scientific data** in
open, standard formats (MiniSEED, SEG-Y, StationXML). It is not a
replacement for the vendor's software. Where the format is uncertain, the
documentation recommends checking against an official SoloLite export
(`smartsolo_dld.compare_with_export`).

## Contributing

To keep the reader clean:

- Base changes only on data files you are entitled to use and on public
  information.
- Never decompile or disassemble vendor software, and never paste vendor
  code or confidential documents into this repository.
- Describe any new finding in `DLD_FORMAT.md` first, then implement it.

## Trademarks and disclaimer

SmartSolo, SoloLite and DTCC are names and trademarks of their owners. This
project is independent and is not affiliated with, endorsed by or supported
by them. The software is provided "as is" under the MIT licence (see
[`LICENSE`](../LICENSE)).

This statement describes what was done. It is not legal advice. Before
redistributing in a commercial setting, check the terms of your own
instrument purchase or software licence agreements.
