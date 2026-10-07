"""Write one sct_par.xml per matrix row (named by row_id)."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd


def _codes(cfg):
    import yaml
    return yaml.safe_load(Path(cfg["xml_codes"]).read_text())


def _set(text: str, tag: str, value, after: str | None = None) -> str:
    """Set <tag>value</tag>; insert it after <after> if missing (KeyError if impossible)."""
    new, n = re.subn(rf"<{tag}>[^<]*</{tag}>", f"<{tag}>{value}</{tag}>", text)
    if n == 1:
        return new
    if n == 0 and after is not None:
        m = re.search(rf"(\n[ \t]*)<{after}>[^<]*</{after}>", text)
        if m:
            return text[:m.end()] + f"{m.group(1)}<{tag}>{value}</{tag}>" + text[m.end():]
    raise KeyError(f"tag <{tag}> found {n} times in the template")


def _drop(text: str, tag: str) -> str:
    return re.sub(rf"\n[ \t]*<{tag}>[^<]*</{tag}>", "", text)


def row_xml(row, template: str, codes: dict) -> tuple[str, list[str]]:
    """XML text for one matrix row and the list of unconfirmed codes used."""
    unconfirmed = []
    sr = int(row.sample_rate_sps)
    interval = 100000 / sr
    if interval != int(interval):
        raise ValueError(f"{row.row_id}: {sr} sps is not a whole number of 10 us")
    text = template
    for k, v in codes.get("set_fields", {}).items():
        text = _set(text, k, v)
    text = _set(text, "script_file_name", f"huddle_{row.row_id.replace('-', '_').lower()}")
    text = _set(text, "Sample_Rate", int(interval))
    if sr not in codes["Sample_Rate"]["confirmed_rates"]:
        unconfirmed.append(f"Sample_Rate={int(interval)} ({sr} sps)")
    for ch in (1, 2, 3):
        g = int(getattr(row, f"gain_ch{ch}", row.gain_db))
        text = _set(text, f"Channel_{ch}_Gain", g)
        if g not in codes["Channel_Gain"]["confirmed_values"]:
            unconfirmed.append(f"Channel_{ch}_Gain={g}")
    aa = codes["Anti_Alias_Filter"][row.filter_phase]
    text = _set(text, "Anti_Alias_Filter", aa["code"])
    if not aa["confirmed"]:
        unconfirmed.append(f"Anti_Alias_Filter={aa['code']} ({row.filter_phase})")
    for factor, spec in codes.get("factors", {}).items():
        level = str(getattr(row, factor, "")) if hasattr(row, factor) else None
        if level is None or level in ("", "nan"):
            continue
        if level not in spec["levels"]:
            raise ValueError(f"{row.row_id}: {factor}={level} has no code in xml_codes.yaml")
        c = spec["levels"][level]
        try:
            text = _set(text, spec["field"], c["code"])
        except KeyError:
            raise KeyError(f"{row.row_id}: template has no <{spec['field']}> for {factor}={level} "
                           f"(firmware {row.firmware}) - use a template exported for this firmware") from None
        if not c["confirmed"]:
            unconfirmed.append(f"{spec['field']}={c['code']} ({factor} {level})")
        extras = spec.get("extra", {})
        for lev, fields in extras.items():
            for tag, val in fields.items():
                text = _set(text, tag, val, after=spec["field"]) if lev == level else _drop(text, tag)
    return text, unconfirmed


def template_for(cfg, codes, firmware) -> Path:
    """Template for a firmware: longest matching prefix in ``templates_by_firmware``."""
    best = None
    for prefix, path in (codes.get("templates_by_firmware") or {}).items():
        if str(firmware).startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, path)
    if best is None:
        return Path(cfg["template"])
    p = Path(best[1])
    return p if p.is_absolute() else Path(cfg["xml_codes"]).parent / p


def write_all(cfg, matrix: pd.DataFrame, batches=None) -> pd.DataFrame:
    """Write ``xml/<row_id>.xml`` for every row (or only ``batches``) and
    ``xml/manifest.csv`` (row, file, settings, unconfirmed fields)."""
    codes = _codes(cfg)
    cache = {}

    def load(fw):
        p = template_for(cfg, codes, fw)
        if p not in cache:
            raw = p.read_bytes()
            cache[p] = (raw.decode("utf-8").replace("\r\n", "\n"), b"\r\n" in raw, p.name)
        return cache[p]
    out = Path(cfg["xml_dir"])
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    sel = matrix if batches is None else matrix[matrix["batch"].isin(batches)]
    for r in sel.itertuples():
        template, crlf, tname = load(r.firmware)
        text, unc = row_xml(r, template, codes)
        f = out / f"{r.row_id}.xml"
        f.write_bytes(text.replace("\n", "\r\n").encode() if crlf else text.encode())
        rows.append(dict(row_id=r.row_id, batch=r.batch, unit=r.unit, firmware=r.firmware,
                         file=f.name, sample_rate_sps=r.sample_rate_sps, gain_db=r.gain_db,
                         filter_phase=r.filter_phase, template=tname,
                         template_verified=any(str(r.firmware).startswith(p)
                                               for p in codes.get("verified_template_firmware", [])),
                         unconfirmed="; ".join(unc)))
    man = pd.DataFrame(rows)
    old = out / "manifest.csv"
    if batches is not None and old.exists():
        prev = pd.read_csv(old)
        man = pd.concat([prev[~prev["row_id"].isin(man["row_id"])], man]).sort_values(["batch", "unit"])
    man.to_csv(old, index=False)
    return man
