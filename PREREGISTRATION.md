# Pre-registration

Each row fingerprints a design specification frozen at its gate, before any operating-characteristic rate of that case was read. The specification is published byte-identical when its case is released, so anyone can recompute both hashes and compare them with the row.

| Case | Gate | Specification | Frozen on | SHA-256 | MD5 |
|---|---|---|---|---|---|
| 03 | G3 | Case 03 design specification v1 | 2026-11-17 | `096abebfebcae013dec8429e4271f3893f0a8f39473cf454738f70ba010b8e6c` | `84a2909301dc512437711ab698e900ed` |

Rules: no operating-characteristic rate of a case is read before its row is committed here; a row is never edited; a revision of a specification is a dated v2 with a row of its own.
