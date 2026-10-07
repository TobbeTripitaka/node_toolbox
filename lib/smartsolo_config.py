"""
smartsolo_config
================

All processing settings in one place, read from a YAML or JSON file.

>>> import smartsolo_config as cfg
>>> settings = cfg.load_config("my_project.yaml")   # missing keys -> defaults
>>> cfg.apply_config(settings)                       # sets the DLD timing defaults
>>> cfg.write_template("settings_template.yaml")     # every setting, with comments

A settings file only needs the values that differ from the defaults, e.g.::

    output:
      root: /data/sds
    metadata:
      network: XX
      mapping: stations.csv
    data:
      remove_gain: false

The settings actually used are stored with every batch run (harvest
database and ``settings_used.yaml`` in the output root), so a dataset can
always be traced back to how it was made.

Authors: Tobias Stål (UTAS) 2026
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

__all__ = ["DEFAULT_CONFIG", "load_config", "apply_config", "write_template", "settings_key"]

DEFAULT_CONFIG = {
    "timing": {
        # "tow": UTC from the binary GPS week + time of week in every DLD tag (default).
        # "label": the HHMMSS/YYYYMMDD text in the tag - 1 s late before the receiver knows
        # the leap seconds, 1 s early after; jumps 2 s. Diagnostics only.
        "time_source": "tow",
        # Seconds added to the GPS time of week. 0 matches co-located permanent stations
        # (R. Pickle, ANU); the earlier -1 s left the data 1 s early.
        "tow_offset_s": 0.0,
        # Which sample a tag refers to: "block_start" (first sample of the block before it;
        # working hypothesis) or "block_end". To be confirmed with a SoloLite export.
        "tag_marks": "block_start",
        # Read samples after the last tag (file cut short at power-off).
        "include_trailing": True,
        # GPS - UTC. null = built-in table (18 s since 2017); set an integer if a new leap
        # second is introduced.
        "leap_seconds": None,
    },
    "data": {
        # "auto": x -1 for IGU-16HR (AusPass tests: negative polarity on all channels),
        # no flip for BD3C-5; true / false to force.
        "invert_polarity": "auto",
        # false (default): keep raw integer counts and put the preamp gain in the response
        # (no loss of resolution). true: divide by 10^(gain/20) like SoloLite "Remove Gain" /
        # the harvest script; with integer encoding this rounds away resolution at gains > 0 dB.
        "remove_gain": False,
        # MiniSEED encoding: "STEIM2" (integers) or "FLOAT32" (keeps fractions when the gain
        # is removed).
        "encoding": "STEIM2",
        "record_length": 4096,
        # Components to process (null = all of Z, N, E).
        "components": None,
    },
    "metadata": {
        "network": "XX",
        "location": "",
        # CSV serial,network,station[,location,channel_prefix,start,end] (see smartsolo_waveforms)
        "mapping": None,
        # Instrument response in StationXML:
        #   "auspass"  - AusPass/ANSIR published IGU-16HR 3C response (default): 5 Hz, h 0.707,
        #                257 019 225.55 counts/(m/s) for gain-removed counts, split here into
        #                76.6 V/(m/s) sensor x preamp gain x 3 355 342.4 counts/V
        #   "dtcc" - DTCC data-sheet values as in the harvest script: 76.6 V/(m/s) x 3 355 500 counts/V
        #                (+0.005 % vs AusPass)
        #   "test"     - the node's own boot-time geophone test values (falls back to nominal)
        #   "nominal"  - DT-SOLO data sheet: 5 Hz, h 0.70, 80 V/(m/s)
        #   null       - no response
        "response": "auspass",
        # Orientation in StationXML: "north" (assume N arrow to north) or "compass"
        # (settled eCompass heading + IGRF declination, needs ppigrf)
        "azimuth": "north",
    },
    "deployments": {
        # First stable GPS position: n consecutive fixes within tolerance of their median.
        "n_stable": 5,
        "stable_tol_m": 10.0,
        # Locate nodes without a log from the GPS positions in the DLD tags.
        "use_dld_positions": True,
    },
    "selection": {
        # Optional UTC window and spatial filter (applied to deployments).
        "start": None,
        "end": None,
        "point": None,          # [lat, lon]
        "radius_km": None,
        "polygon": None,        # path to a GeoJSON/GPKG/shapefile
    },
    "output": {
        # "sds": SeisComP Data Structure day files + harvest database (default);
        # "files": one file per window/chunk (MiniSEED or SEG-Y) as extract_waveforms.
        "layout": "sds",
        "root": "output/sds",
        "format": "MSEED",      # for layout "files": MSEED or SEGY
        "chunk": "1D",          # for layout "files"
        "stationxml": True,
        "station_table": True,
    },
    "batch": {
        "workers": 4,
        # Skip source files already harvested with the same settings.
        "skip_harvested": True,
        # Redo files harvested with other timing settings (e.g. before the TOW offset fix).
        "redo_stale_timing": True,
    },
}


def _merge(base: dict, new: dict, path="") -> dict:
    out = copy.deepcopy(base)
    for k, v in (new or {}).items():
        if k not in out:
            raise KeyError(f"unknown setting '{path}{k}' (see smartsolo_config.DEFAULT_CONFIG)")
        if isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _merge(out[k], v, f"{path}{k}.")
        else:
            out[k] = v
    return out


def load_config(source=None, **overrides) -> dict:
    """
    Settings from a YAML/JSON file, a dict or nothing (defaults), merged over
    :data:`DEFAULT_CONFIG`. Keyword overrides use ``section__key=value``,
    e.g. ``load_config("p.yaml", output__root="/tmp/sds")``. Unknown keys raise
    ``KeyError`` (catches typos).
    """
    if source is None:
        user = {}
    elif isinstance(source, dict):
        user = source
    else:
        p = Path(source)
        text = p.read_text()
        if p.suffix.lower() in (".yaml", ".yml"):
            import yaml
            user = yaml.safe_load(text) or {}
        else:
            user = json.loads(text)
    cfg = _merge(DEFAULT_CONFIG, user)
    if source is not None and not isinstance(source, dict):
        # relative paths in a settings file are relative to that file
        base = Path(source).resolve().parent
        for sec, key in (("output", "root"), ("metadata", "mapping"), ("selection", "polygon")):
            v = cfg[sec][key]
            if isinstance(v, str) and v and not Path(v).is_absolute():
                cfg[sec][key] = str((base / v).resolve())
    for k, v in overrides.items():
        sec, key = k.split("__", 1)
        cfg = _merge(cfg, {sec: {key: v}})
    return cfg


def apply_config(cfg: dict) -> dict:
    """Push the timing settings into ``smartsolo_dld.DEFAULTS``. Returns ``cfg``."""
    import smartsolo_dld as dld
    for k, v in cfg["timing"].items():
        dld.DEFAULTS[k] = v
    dld._CACHE.clear()
    return cfg


def settings_key(cfg: dict) -> str:
    """Compact description of the settings that change the sample values or times
    (stored per harvested file; a change means the file must be redone)."""
    t, d = cfg["timing"], cfg["data"]
    return (f"{t['time_source']}{t['tow_offset_s']:+g}/{t['tag_marks']}/"
            f"trail{int(bool(t['include_trailing']))}/leap{t['leap_seconds']}|"
            f"pol:{d['invert_polarity']}|gain:{'removed' if d['remove_gain'] else 'kept'}|"
            f"{d['encoding']}")


def write_template(path, cfg: dict | None = None):
    """Write all settings (defaults, or ``cfg``) as YAML with the comments of this module."""
    import inspect
    import re

    cfg = cfg or DEFAULT_CONFIG
    src = inspect.getsource(inspect.getmodule(write_template))
    block = src[src.index("DEFAULT_CONFIG = {"):src.index("\n}\n", src.index("DEFAULT_CONFIG = {")) + 2]
    lines = ["# node_toolbox settings (smartsolo_config). Only values that differ from the",
             "# defaults are needed; this template lists all of them.", ""]
    section = None
    for line in block.splitlines()[1:-1]:
        s = line.strip()
        m_sec = re.match(r'"(\w+)": \{', s)
        m_key = re.match(r'"(\w+)":\s*(.+?),?\s*(#.*)?$', s)
        if s.startswith("#"):
            lines.append(("  " if section else "") + s)
        elif m_sec:
            section = m_sec.group(1)
            lines.append(f"{section}:")
        elif s.startswith("},"):
            lines.append("")
            section = None
        elif m_key and section:
            key = m_key.group(1)
            val = cfg[section][key]
            dumped = json.dumps(val) if not isinstance(val, str) else f'"{val}"'
            dumped = {"true": "true", "false": "false", "null": "null"}.get(dumped, dumped)
            comment = f"   {m_key.group(3)}" if m_key.group(3) else ""
            lines.append(f"  {key}: {dumped}{comment}")
    Path(path).write_text("\n".join(lines).rstrip() + "\n")
    return Path(path)
