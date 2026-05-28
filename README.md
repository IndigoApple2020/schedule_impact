# Schedule Impact

Analyse periodic Primavera P6 (`.xer`) schedule snapshots alongside programme narrative PDFs to detect schedule impacts, quantify delays, link explanatory text to incidents, and flag quality-related delays.

## Documentation

| Document | Purpose |
|----------|---------|
| [docs/architecture.md](docs/architecture.md) | System design, data model, pipelines, and interfaces |
| [docs/strategy.md](docs/strategy.md) | Delivery approach, assumptions, linking logic, and quality classification |
| [docs/development-with-sensitive-data.md](docs/development-with-sensitive-data.md) | Work on real data locally; develop in Cursor without sharing content |
| [docs/xer-schema.md](docs/xer-schema.md) | Text XER format and column mapping for this programme |
| [docs/memo-labeling-and-ml.md](docs/memo-labeling-and-ml.md) | Export TASKMEMO for team labeling and train quality classifier |

## Repository layout

```
schedule_impact/
├── config/                 # YAML settings, keyword lists, project mappings
├── data/                   # Local data zones (raw → staging → processed); gitignored
├── docs/                   # Architecture and strategy
├── notebooks/              # Exploratory analysis (optional)
├── outputs/                # Reports and exports per run (gitignored)
├── scripts/                # One-off utilities and CLI entrypoints
├── sql/schema/             # Warehouse / analytics DDL
├── src/schedule_impact/    # Application package
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

## Status

Phase 1: incident detection, TASKMEMO linking, keyword + ML quality scoring. PDF ingest and project mapping refinements follow `docs/strategy.md`.
