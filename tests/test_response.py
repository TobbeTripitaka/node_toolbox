"""Instrument response files (instrument_responses/) and smartsolo_response."""

import sys
import warnings
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import smartsolo_node as sn  # noqa: E402
import smartsolo_response as sr  # noqa: E402

F = np.array([0.5, 1, 5, 10, 50, 100.0])


def h(r):
    return r.get_evalresp_response_for_frequencies(F)


def test_models_listed():
    t = sr.list_responses()
    assert {"auspass", "dtcc", "nominal", "nrl"} <= set(t.model)
    assert t.set_index("model").loc["auspass", "default"]


@pytest.mark.parametrize("gain", [0, 24])
def test_auspass_file_equals_embedded(gain):
    a = sr.get_response("auspass", gain_db=gain, sampling_rate=250)
    b = sn.staged_response("IGU-16HR 3C 5Hz", 250, gain, model="auspass")
    assert np.allclose(h(a), h(b), rtol=1e-6)


def test_fallback_without_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(sr, "RESPONSE_DIR", tmp_path / "missing")
    sr._meta.cache_clear()
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        r = sr.get_response("auspass", gain_db=12)
    assert any("embedded" in str(x.message) for x in w)
    assert np.allclose(h(r), h(sn.staged_response(gain_db=12)), rtol=1e-6)
    with pytest.raises(FileNotFoundError):
        sr.get_response("nrl")
    sr._meta.cache_clear()


def test_gain_rescaled_and_nrl_choice():
    m = sr.get_response("dtcc", gain_db=0)
    m0 = sr.get_response("dtcc", gain_db=-1)                        # no -1 dB file: rescaled from the 0 dB file
    assert np.allclose(np.abs(h(m)) / np.abs(h(m0)), 10 ** (1 / 20))
    lp = sr.get_response("nrl", gain_db=24, sampling_rate=250, filter_phase="linear")
    mp = sr.get_response("nrl", gain_db=24, sampling_rate=250, filter_phase="minimum")
    assert lp.instrument_sensitivity.value == pytest.approx(4.11409e9, rel=1e-4)
    assert not np.allclose(np.angle(h(lp)), np.angle(h(mp)))       # the FIR phase differs
    a = sr.get_response("auspass", gain_db=24, sampling_rate=250)
    # same passband shape; NRL preamp gains are powers of two (24 dB = x16, not x15.85)
    assert np.allclose(np.abs(h(lp))[1:4] / 16, np.abs(h(a))[1:4] / 10 ** (24 / 20), rtol=2e-3)
    with pytest.warns(UserWarning):
        sr.get_response("nrl", sampling_rate=2000)


def test_inspect():
    import matplotlib
    matplotlib.use("Agg")
    t, fig = sr.inspect_instrument_response(["auspass", "nrl", "nominal"], verbose=False)
    assert t.loc["auspass", "f0_hz"] == pytest.approx(5.0, abs=0.01)
    assert t.loc["auspass", "damping"] == pytest.approx(0.707, abs=0.001)
    assert fig is not None
