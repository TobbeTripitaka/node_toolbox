# Seismica Software Report: manuscript

`node_toolbox_seismica.tex` is the manuscript, prepared with the Seismica
submission template (`seismica.cls` and `abbrvnat_seismica_upcasetitle.bst`
from https://github.com/WeAreSeismica/templates). The references are in
`references.bib`.

The figures in `figures/` (PDF and PNG) are made by `make_figures.py` from
the data in this repository:

```bash
git lfs pull                      # raw DLD data used in Figs 2, 6, 7, 8
cd paper
python make_figures.py            # all figures, or e.g. `python make_figures.py 3 4`
pdflatex node_toolbox_seismica && bibtex node_toolbox_seismica \
  && pdflatex node_toolbox_seismica && pdflatex node_toolbox_seismica
```

Fig. 7 downloads Esri World Imagery tiles through `contextily`, so it needs
internet access.

| figure | content | data |
|---|---|---|
| 1 | package structure | – |
| 2 | DLD layout, continuity across tags, tag counter | `data/break_test/453004362` |
| 3 | state of health on the ice | `data/nodes` |
| 4 | first stable fix and drift | `data/nodes` |
| 5 | pulse test vs boot test | `data/nodes/453022522`, `data/break_test` |
| 6 | timing: text label vs GPS time of week | `data/break_test` |
| 7 | deployments from DLD tags, radius/polygon selection | `data/break_test` |
| 8 | braking test: envelopes, exported record section, spectra, speeds | `data/break_test` |

To do before submission: author ORCID, affiliation and co-authors,
acknowledgements and funding, the Zenodo DOI of the release, and a check
against the journal's policy on disclosing AI assistance.
