# Schedule Impact

This repo hosts **two packages**:

1. **`schedule_impact`** — analyse periodic Primavera P6 (`.xer`) schedule snapshots alongside programme narrative PDFs to detect schedule impacts, quantify delays, link explanatory text to incidents, and flag quality-related delays.
2. **`text_classify`** — generic taxonomy-driven text classification (TF-IDF + LLM via Ollama) for free-text fields like an issues-log Root Cause column. Reusable across any single-text-field, multi-label-against-taxonomy problem.

## Documentation

### `schedule_impact`
| Document | Purpose |
|----------|---------|
| **[docs/schedule-impact-user-guide.md](docs/schedule-impact-user-guide.md)** | **Step-by-step how-to: install, profile, run-monthly, export-schedule, export-memos, troubleshooting** |
| [docs/architecture.md](docs/architecture.md) | System design, data model, pipelines, and interfaces |
| [docs/strategy.md](docs/strategy.md) | Delivery approach, assumptions, linking logic, and quality classification |
| [docs/xer-schema.md](docs/xer-schema.md) | Text XER format and column mapping for this programme |
| [docs/memo-labeling-and-ml.md](docs/memo-labeling-and-ml.md) | Export TASKMEMO for team labeling and train quality classifier |

### `text_classify`
| Document | Purpose |
|----------|---------|
| **[docs/text-classification-user-guide.md](docs/text-classification-user-guide.md)** | **Step-by-step how-to: install, classify, sample, label, eval, resume, troubleshoot** |
| [docs/text-classification-strategy.md](docs/text-classification-strategy.md) | Architecture + strategy: design rationale, output schema, phased delivery |

### General
| Document | Purpose |
|----------|---------|
| [docs/development-with-sensitive-data.md](docs/development-with-sensitive-data.md) | Work on real data locally; develop in Cursor without sharing content |

## Repository layout

```
schedule_impact/
├── config/
│   ├── settings.yaml             # local-only schedule_impact runtime config
│   ├── p6_schema.yaml            # P6 XER column mapping
│   ├── quality_keywords.yaml     # quality classification keyword lists
│   └── taxonomies/               # text_classify taxonomies (gitignored except .example.yaml)
├── data/                         # Local data zones (gitignored)
├── docs/                         # Architecture, strategy, and the text-classification user guide
├── notebooks/                    # Exploratory analysis (optional)
├── outputs/                      # Reports and exports per run (gitignored)
├── scripts/                      # One-off utilities
├── sql/schema/                   # Warehouse DDL
├── src/
│   ├── schedule_impact/          # P6 XER + PDF analysis package
│   └── text_classify/            # Generic taxonomy text classification package
└── tests/
```

## Quick start (once implemented)

1. Copy `config/settings.example.yaml` to `config/settings.yaml`.
2. Place monthly `.xer` files under `data/raw/xer/{programme}/{YYYY-MM}/`.
3. Place matching PDFs under `data/raw/pdf/{programme}/{YYYY-MM}/`.
4. Run the monthly pipeline (see `docs/strategy.md` for phased rollout).

## Sensitive data

Real `.xer` / `.pdf` files never need to leave your laptop. Run `profile-xer` / `profile-pdf` there to produce **structure-only** JSON you can optionally paste into chat. All tests use **synthetic** fixtures in `tests/fixtures/synthetic/`. See [docs/development-with-sensitive-data.md](docs/development-with-sensitive-data.md).

## Monthly pipeline (Phase 1)

```bash
schedule-impact run-monthly \
  --programme your_programme \
  --period 2025-04 \
  --previous-period 2025-03 \
  --current-xer data/raw/xer/your_programme/2025-04/schedule.xer \
  --previous-xer data/raw/xer/your_programme/2025-03/schedule.xer
```

Optional: `--quality-model outputs/labeling/models/quality_classifier.joblib`

## Text classification (issues log → root cause)

Generic taxonomy-driven classification, with TF-IDF (always available) and
optional Ollama-backed LLM engines (embedding and prompt modes). See
**[docs/text-classification-user-guide.md](docs/text-classification-user-guide.md)** for the full how-to.

```bash
# Quick example
text-classify classify-tfidf \
  --input    issues.csv \
  --taxonomy config/taxonomies/construction_root_cause.yaml \
  --out      outputs/text_classify
```

## Status

- `schedule_impact` — Phase 2 complete: incident detection, TASKMEMO + PDF linking, keyword + ML quality scoring, export-schedule CLI. PDF ingest extractor calibrated for programme narrative format.
- `text_classify` — Phases 1–8 complete: TF-IDF + LLM embed/prompt engines, multi-engine combined output, stratified sampler, resumable LLM runs, evaluation against hand labels, Parquet output.
