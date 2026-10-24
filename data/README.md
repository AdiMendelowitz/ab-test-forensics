# Data

`manifests/` is tracked: one JSON per generated dataset with the seed, the true effect, every incident parameter, row counts per table and file checksums.

`generated/` is not tracked: the simulated tables, rebuilt from a manifest with `uv run abf simulate <case>`.

`raw/` is not tracked: external downloads. Case 02 reads the EB-NeRD small dataset from `raw/ebnerd_small` (source and licence terms in the Case 02 README, Data provenance); `uv run abf simulate case02 --synthetic` runs the case without it.

A fresh clone runs Case 01 with no download.
