# Roadmap

The screener works today against the Ashby API. This roadmap makes it **runnable by anyone**, more **complete as a hiring workflow**, and **verifiable end-to-end** — without locking it to a single ATS.

---

## Phase 1 — Run it anywhere

Decouple ingestion from any single ATS so the pipeline runs on any set of résumés.

- **Input-source seam** — the résumé-ingestion step sits behind a small interface (`AshbySource`, `LocalSource`). Everything downstream (triage → deep eval → synthesis → report) is unchanged, because the candidate contract is identical.
- **Local input** — point it at a folder of résumés (`.md` / `.txt` / `.pdf`) plus a `candidates.csv`. No ATS required.
- **Zero-key demo** — `python run.py --demo` runs end-to-end on bundled synthetic data with no API keys and opens a sample report.
- **Bundled `examples/`** — a set of synthetic candidates you can run immediately (and that double as test fixtures in Phase 3).

## Phase 2 — A complete hiring workflow

Make the output match how recruiting teams actually operate.

- **Combined multi-role report** — one consolidated view across roles and recruiters: *"who do we collectively need to meet this week."* (Today this is a stub.)
- **Shortlist export** — export the top candidates as CSV / Markdown to hand directly to a hiring manager.

## Phase 3 — Confidence & reach

- **Test suite + CI** — automated tests over the scoring, dedup, state, and reporting logic, run on every push (with a status badge).
- **Live demo** — the sample report published as a hosted page so anyone can see the output without running anything.

---

*Phases are sequential; each is independently useful. Phase 1 is the keystone — it unlocks local testing, broad adoption, the zero-key demo, and the Phase 3 fixtures.*
