"""End-to-end test of the huddle-test tooling on a synthetic batch with known faults."""

import sys
from pathlib import Path

import pytest

HT = Path(__file__).resolve().parent / "huddle_test"
sys.path.insert(0, str(HT))


def test_huddle_synthetic(tmp_path):
    if not next((HT.parents[1] / "data" / "break_test").rglob("seis000Z.DLD")).stat().st_size > 1000:
        pytest.skip("Git LFS files not pulled")
    import huddle
    faults = {"V1.0.7.5ke": {"block_end": True}, "V1.0.7.9be": {"offset_s": 0.020}, "F2B": {"flip": ["Z"]}}
    cfg, res = huddle.demo(tmp_path / "demo", batch=1, faults=faults, duration_s=140.0, verbose=False)
    assert res["settings"].ok.all()
    bc = res["block_convention"].set_index("firmware")
    assert bc.loc["V1.0.7.5ke", "slope_b"] == pytest.approx(1.0, abs=0.02)        # block-end tags found
    assert bc.drop("V1.0.7.5ke").slope_b.abs().max() < 0.02
    assert bc.loc["V1.0.7.9be", "offset_a_s"] == pytest.approx(0.020, abs=0.001)  # 20 ms offset found
    pol = res["polarity"].set_index("unit")
    assert pol.loc["F2B", "Z"] < 0 and pol.drop("F2B").all_positive.all()
    # repository files untouched: the demo works on copies
    assert "999000001" not in (HT / "sand_box_huddle_test" / "units.csv").read_text()


def test_huddle_synthetic_batch6(tmp_path):
    """Per-channel gains (channel mapping), node MiniSEED output, low-power ADC, constellations."""
    if not next((HT.parents[1] / "data" / "break_test").rglob("seis000Z.DLD")).stat().st_size > 1000:
        pytest.skip("Git LFS files not pulled")
    import huddle
    cfg, res = huddle.demo(tmp_path / "demo6", batch=6, faults={}, duration_s=140.0, verbose=False)
    assert res["settings"].ok.all()
    cm = res["channel_mapping"]
    assert len(cm) == 8 and cm.unique.all() and (cm.mapping == "Ch1=X, Ch2=Y, Ch3=Z").all()  # synthetic truth
    f = res["factors"].set_index(["factor", "level"])
    assert (f.loc[("storage", "miniseed"), "lag_diff_s"].abs() < 1e-4).all()       # MiniSEED read and aligned
    assert (f.loc[("adc_mode", "low_power"), "noise_ratio"] > 1.2).all()            # extra noise found


def test_huddle_wizard_simulation(tmp_path):
    """The guided sandbox script, run end to end on fake nodes with every default accepted."""
    if not next((HT.parents[1] / "data" / "break_test").rglob("seis000Z.DLD")).stat().st_size > 1000:
        pytest.skip("Git LFS files not pulled")
    import yaml
    from huddletest import nodes, wizard
    sb = HT / "sand_box_huddle_test"
    s = tmp_path / "huddle.yaml"
    s.write_text(yaml.safe_dump({"data_dir": str(tmp_path / "data"), "matrix": str(sb / "huddle_matrix.csv"),
                                 "units": str(sb / "units.csv"), "xml_dir": str(sb / "xml"),
                                 "xml_codes": str(HT / "xml_codes.yaml"),
                                 "template": str(HT / "templates" / "sct_par_template.xml")}))
    out = []
    w = wizard.main(["--settings", str(s), "--simulate", "--auto", "--batch", "1"])
    st = nodes.load_state(w.cfg)
    assert st["batches"]["1"]["step"] == "done" and st["batches"]["1"]["collected_complete"]
    assert (w.cfg["data_dir"] / "results" / "summary.md").exists()
    res = __import__("pandas").read_csv(w.cfg["data_dir"] / "results" / "settings.csv")
    assert res.ok.all() and (res.firmware_log == res.planned_firmware).all()
    # the units file of the repository is not touched by a simulation
    assert set(__import__("pandas").read_csv(sb / "units.csv", dtype=str, keep_default_na=False).serial) == {""}
