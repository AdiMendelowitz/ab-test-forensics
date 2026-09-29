# Data

`manifests/` is tracked: one JSON per generated dataset with the seed, the true effect, every incident parameter, row counts per table and file checksums.

`generated/` is not tracked: the simulated tables, rebuilt from a manifest with `uv run abf simulate <case>`.

`raw/` is not tracked: external downloads (case 03 uses Open Power System Data hourly load), fetched by a script that records the dataset version in the manifest.

A fresh clone runs case 01 with no download.
