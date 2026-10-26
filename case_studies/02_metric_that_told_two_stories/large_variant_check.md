# Case 02: large-variant feasibility check (milestone M12)

## 1 Question

Does the headline mechanism (delta-method SE about 1.5 times the naive SE, `README:15`) hold on the EB-NeRD large variant, and is the operating-characteristic harness feasible there?

## 2 What was run

The archived script (PY), run unmodified (`TJ:115`) on the `train` split (`PY:29`) of the large variant by Codex card K5 on 14 October 2026 (`TJ:59`), commit 4fb21ff (`TJ:53`), Chrome closed (`TJ:91`), PyCharm open (`TJ:97`). The archived script is a byte-identical copy of the file as run. Data: 788,090 users (`LOG:4`). A/A: 400 splits (`PY:76`), per-user Bernoulli(0.5) assignment (`PY:78`), seed 20260929 (`PY:74`). SE ratio = se_delta / se_naive (`PY:83-84`), median and IQR reported (`PY:89`); naive test = pooled-proportion z, rejecting at |z| > 1.96 (`PY:81-82`, `PY:85`). Timing run: `abf simulate case02`, 115,200 draws (`TJ:83`), seed 20261002 (`TJ:77`).

## 3 Results

Every rate carries its replicate count and Monte Carlo standard error: S = 400 throughout (`PY:76`). The log's "MC SE about 0.011" is fixed at p = 0.05 (`PY:88`) and fits only the delta and Welch rows.

| Figure | Large (S = 400) | MC SE = sqrt(p(1-p)/400) | Small comparator (S = 400) |
|---|---|---|---|
| Median SE ratio | 1.46, IQR 1.46 to 1.46 (`LOG:15`) | none: median of a ratio | 1.48 (`README:15`) |
| Naive rejection | 0.150 = 60/400 (`LOG:16`) | sqrt(0.15 x 0.85/400) = 0.0179 | 0.168, MC SE 1.9 points (`README:9`) |
| Delta rejection | 0.050 = 20/400 (`LOG:16`) | sqrt(0.05 x 0.95/400) = 0.0109 | 0.045, MC SE 1.0 point (`README:9`) |
| Welch E1 rejection | 0.052 (`LOG:16`); count not printed (20.8, rounded; 21 fits) | sqrt(0.052 x 0.948/400) = 0.0111 | 0.055, MC SE about 0.011 (DR row D2) |
| CV of clicks per user; E2, E3, E3/E2 | CV 1.1522833456841122 all train users (`TJ:65`), 1.1521549428996425 section 6 base (`TJ:71`); 0.0907, 0.1011, 1.11 (`LOG:12-13`) | none | CV 1.153 (DR row D4); E2 0.0906, E3/E2 1.12 (DR row D3); E3 0.1012 (`README:36`) |

The naive gap to the small variant, 0.168 - 0.150 = 0.018, is under one SE of the difference, sqrt(0.0179^2 + 0.019^2) = 0.026. Errors exclude between-population variance. The 1.4 threshold (`LOG:17`, `PY:91`) governs the ratio only; neither CV gets a verdict. The old-tier mechanism check is not in the sources.

## 4 Feasibility of the harness

Measured once: load_base 707.4290107 s (`TJ:4`), build alone 201.9696 s (`TJ:10`); simulate 1,784.9134 s (`TJ:16`), which includes load_base's md5 pass (`TJ:109`); peak working set 9,996,099,584 B after load (`TJ:22`) and 10,171,904,000 B after simulate (`TJ:28`), each a script's process peak (`TJ:103`). Read as kilobytes (unit not printed), the 16,571,704 reported (`TJ:34`) gives 16,571,704 x 1,024 = 16,969,424,896 B, so the peaks are 58.9% and 59.9% of memory. The small-variant harness uses two workers (`README:161`); each holding the base, they would need 2 x 9,996,099,584 = 19,992,199,168 B, 117.8% of memory; sharing one copy is not in the sources.

- Ceiling, if a replicate cost a full simulate: B = 200 gives 200 x 1,784.913 = 356,982.7 s (99.2 h); B = 1,000 gives 1,784,913.4 s (495.8 h). A small-variant replicate costs 0.1075 s (DR row D15) against 33.5 s for a simulate (`README:199`).
- Comparator, load once plus that 0.1075 s per replicate (no large-variant measurement): B = 200 gives 707.43 + 200 x 0.1075 = 728.9 s; B = 1,000 gives 814.9 s.

MC SE at 5% for those B: 1.54 and 0.69 points (`README:159`).

## 5 Verdict

On one population of 788,090 users the median SE ratio was 1.46 over 400 A/A splits (`LOG:15`), above the 1.4 threshold (`LOG:17`) and 0.02 below the small variant's 1.48 (`README:15`), and the naive test rejected 60 of 400 (0.150, MC SE 0.0179; `LOG:16`), so the ratio headline holds at scale; the old-tier mechanism was not checked there. One process fits the machine (58.9% of installed memory after load, `TJ:22`), but per-replicate seconds on the large variant are not in the sources, so the cost of B replicates cannot be stated.

## 6 Sources

LOG = `results/case02/large_variant_feasibility/k5_feasibility.log`; PY = `results/case02/large_variant_feasibility/ebnerd_feasibility.py` (the script as run, byte-identical); TJ = `results/case02/large_variant_feasibility/timing.json` (transcribed from the Codex K5 run report of 2026-10-14, kept outside the repository, with each source line quoted); README = `case_studies/02_metric_that_told_two_stories/README.md`; DR = the design record kept outside the repository, section 17, cited by row. Line numbers refer to the committed copies.
