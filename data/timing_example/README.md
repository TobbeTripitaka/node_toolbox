# Timing example: the 2-s jump in the DLD time labels

Here is abn example. 

Two 3-minute excerpts of raw `seis001Z.DLD` files from the same deployment
(nodes 453009194 and 453010047, 7 April 2023, 00:57:07–01:00:07 UTC, 500 sps,
firmware V1.0.5.6kp, 21 m apart). Each file is the original 512-byte header
(unchanged, so its start/end/size fields describe the full file), followed by
90 complete blocks (1000 samples + 72-byte tag each) cut from the original
file:

| file | blocks of the original file | text label − UTC from GPS time of week |
|---|---|---|
| `453009194_seis001Z_excerpt.DLD` | 2152–2241 | +1 s for 45 blocks, then −1 s (jump at 00:58:37) |
| `453010047_seis001Z_excerpt.DLD` | 2107–2196 | −1 s throughout (its jump came at 23:56:09) |

What it shows:

- The text time in the tags of 453009194 jumps back 2 s in the middle of
  the file: the label `00:58:37` appears twice. The receiver has just learned
  the GPS–UTC leap seconds (16 s default → 18 s).
- With `time_source="label"`, `scan_dld` splits the file into two segments
  that overlap by 2 s, and cross-correlating the two nodes gives a lag of
  2 s before the jump and 0 after.
- With `time_source="tow"` (the default), the file is one continuous segment
  and the lag between the nodes is 0 throughout.

See `notebooks/dld_demo.ipynb` and `test_dld_label_jump_excerpt` in
`tests/test_toolbox.py`.
