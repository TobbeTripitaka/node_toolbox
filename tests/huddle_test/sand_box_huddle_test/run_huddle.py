#!/usr/bin/env python
"""Guided SmartSolo huddle test - start here.

    python run_huddle.py              # guided, picks up where the last session stopped
    python run_huddle.py --status     # progress of every batch
    python run_huddle.py --simulate   # practice run with fake nodes (no hardware needed)
    python run_huddle.py --batch 3    # work on batch 3 only

Works on Windows, macOS and Linux. Nodes connected to the reader/rack are found
as USB drives. Data, backups and results go to data/ (git-ignored).
See README.md in this folder.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))                     # tests/huddle_test (huddletest package)
sys.path.insert(0, str(HERE.parents[2] / "lib"))         # node_toolbox/lib

from huddletest import wizard  # noqa: E402

if __name__ == "__main__":
    try:
        wizard.main()
    except KeyboardInterrupt:
        print("\nstopped - run again to continue from the same step")
