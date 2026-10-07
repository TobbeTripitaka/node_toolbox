# Huddle test sandbox

Start here:

```bash
python run_huddle.py --simulate   # practice run with fake nodes
python run_huddle.py              # the real test, batch by batch
python run_huddle.py --status
```

- **`huddle_matrix.csv`:** the plan, batches 1–7.
- **`units.csv`:** units, their firmware and node serial numbers.
- **`huddle.yaml`:** the settings.
- **`xml/`:** the acquisition scripts.
- **`data/`:** all recorded data and results. This folder is git-ignored.

What the test does, the steps and the safety rules are explained in [../README.md](../README.md).
