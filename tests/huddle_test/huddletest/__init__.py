"""Huddle test of SmartSolo firmware versions, sample rates, gains and filters.

Modules: config (settings, matrix, units), wizard (the guided sandbox run), xmlgen (one sct_par.xml per matrix
row), nodes (find mounted nodes, apply scripts, collect data, batch state),
analysis (measurements against the reference), checks (registered tests),
report (figures), design (new batches: random / optimised), synthetic (fake
nodes for testing). Command line: ``python tests/huddle_test/huddle.py -h``.
"""
