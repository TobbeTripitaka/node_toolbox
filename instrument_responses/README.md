# Instrument responses

Suggested instrument responses for SmartSolo nodes, one folder per model.

- **Format.** Each folder holds FDSN StationXML files (the standard format, read by ObsPy, SeisComP and others) and a readable `response.yaml` with the source, the constants, notes and the list of files.
- **Default.** `auspass` is the default. It is also embedded in `lib/smartsolo_node.py`, so code keeps working without this folder.
- **Regenerating.** Run `python scripts/make_instrument_responses.py` (`--no-nrl` skips the download).

| Model | What it is | Devices | Files | When to use |
|---|---|---|---|---|
| `auspass` | AusPass / ANSIR published SmartSolo response (default) | BD3C-5, IGU-16HR3C | 9 | Default. Published, huddle-tested response for counts with the polarity inverted (x -1). |
| `dtcc` | DTCC data-sheet constants (as in the harvest script) | IGU-16HR3C | 7 | For comparison with archives made by the harvest script. |
| `nominal` | DT-SOLO 5 Hz data-sheet geophone | IGU-16HR3C | 7 | A textbook geophone from the data sheet; useful to see how much the details matter. |
| `nrl` | EarthScope (IRIS) Nominal Response Library: DT-SOLO + SmartSolo datalogger | BD3C-5, IGU-16HR3C | 100 | The manufacturer-supplied nominal response with the full digital filter chain: use it to see the anti-alias FIR filters, their delay and the minimum-phase option. |

## Using a model

```python
import smartsolo_response as sr
sr.list_responses()
resp = sr.get_response("auspass", "IGU-16HR 3C 5Hz", gain_db=24, sampling_rate=250)
sr.inspect_instrument_response(["auspass", "nrl", "nominal"], normalise=True)
inv = wf.build_inventory(sel, response="nrl")                   # any folder name works
```

- **Gain in the data.** `gain_db` is the preamp gain contained in the data. Use the log gain for raw DLD counts, and 0 when the gain was removed.
- **Missing gains.** A model without a file for a gain uses the nearest file with its preamp stage rescaled.
- **Sample rate and filter phase.** `nrl` has a file for each sample rate and filter phase, because the decimation FIR stages differ. The other models have no FIR stages, and the requested rate is written into their ADC stage.

## Adding a model

1. **Create the files.** Make a folder with StationXML files and a `response.yaml` listing them under `files:` (`file`, `device`, `gain_db`, and optionally `sample_rate` and `filter_phase`).
2. **Use it.** `get_response("<folder>")` and `build_inventory(response="<folder>")` pick it up.

The notebook [instrument responses](../notebooks/tutorials/instrument_responses.ipynb) compares the models.
