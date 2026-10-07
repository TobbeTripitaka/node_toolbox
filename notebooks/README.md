# Notebooks

The functionality of node_toolbox is best shown through these notebooks. They
use only the data in this repository and are stored with their outputs, so they
can be read on GitHub without running them. Run them from their own folder
after `git lfs pull`. More detail on each: [main README](../README.md#notebooks).

## Tutorials

In the order of a typical workflow:

1. [Reading logs](tutorials/smartsolo_log_demo.ipynb) – `DigiSolo.LOG` state-of-health logs as one UTC-indexed pandas table: temperature, battery, GPS, tilt, errors, several nodes at once, and device metadata per boot.
2. [Deployments, selection and extraction](tutorials/select_and_extract_demo.ipynb) – one deployment per power-up at the first stable GPS fix, selection by radius, polygon and time, cutting waveforms with SEED codes, StationXML.
3. [Raw DLD files](tutorials/dld_demo.ipynb) – the DLD header and time tags, why the GPS time of week is used instead of the text time (the 2-s jump), reading samples, export to MiniSEED, SEG-Y and StationXML.
4. [Batch conversion](tutorials/batch_convert.ipynb) – a settings file, whole harvests to an SDS archive with a harvest database, reruns, timing diagnostics, window files, command line.
5. [Node QC and orientation](tutorials/node_qc_demo.ipynb) – acquisition script limits, the pulse test, boot-by-boot geophone QC, polarity, eCompass and tilt, instrument response.

## Case studies

- [Break test](case_studies/break_test.ipynb) – seismic signals of a braking Toyota Land Cruiser HJ60 recorded by six nodes: positions and orientation from the DLD files, event detection, vehicle speeds, spectra, particle motion, engine speed, and a search for a laughing baby.
