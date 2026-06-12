# Config & Command Reference

Single-page lookup for **where** each config file lives, **what** it controls,
and **which command** consumes it. All user-specific config files are
gitignored — `git pull` never overwrites them.

---

## Config files at a glance

| File | Tracked? | What it controls | Created by |
|---|---|---|---|
| `config/settings.yaml` | gitignored | `schedule-impact run-monthly` thresholds + `pdf.project_row_map` | copy `config/settings.example.yaml` |
| `config/taxonomies/*.yaml` | gitignored (only `*.example.yaml` tracked) | Categorisation taxonomies for `text-classify` | copy any `*.example.yaml`, or write from scratch |
| `scripts/multi-period-analysis.config.ps1` | gitignored | Dataset paths for `multi-period-analysis.ps1` (50-month batch) | copy `multi-period-analysis.config.example.ps1` |
| `scripts/keyword-batch.config.ps1` | gitignored | `$Jobs` array for `keyword-batch.ps1` (multi-dataset TF-IDF) | copy `keyword-batch.config.example.ps1` |

---

## First-time setup on a new machine

Copy each example file you'll use, then edit:

```powershell
cd "C:\path\to\schedule_impact"

# 1. schedule-impact runtime settings (required for run-monthly)
copy config\settings.example.yaml config\settings.yaml
notepad config\settings.yaml

# 2. A taxonomy YAML (one per categorisation exercise)
copy config\taxonomies\construction_root_cause.example.yaml `
     config\taxonomies\construction_root_cause.yaml
notepad config\taxonomies\construction_root_cause.yaml

# 3. Multi-period batch script config (only if running across many XER months)
copy scripts\multi-period-analysis.config.example.ps1 `
     scripts\multi-period-analysis.config.ps1
notepad scripts\multi-period-analysis.config.ps1

# 4. Multi-dataset keyword batch config (only if classifying 2+ datasets)
copy scripts\keyword-batch.config.example.ps1 `
     scripts\keyword-batch.config.ps1
notepad scripts\keyword-batch.config.ps1
```

---

## What each config controls

### 1. `config/settings.yaml`
**Consumer:** `schedule-impact run-monthly` (and `run-batch`)
**Doc:** [schedule-impact-user-guide §1.3](schedule-impact-user-guide.md#13-prepare-your-config)

Key fields:
```yaml
incidents:
  impact_slip_threshold_days: 0     # any positive slip on a critical task = IMPACT
  float_slip_threshold_days: 1      # >= 1 day slip on a non-critical task = FLOAT

pdf:
  project_row_map:                  # PDF section number -> project_row_id
    "3": "PRJ-CONSOLIDATION"
    "8": "PRJ-HS2"
```

### 2. `config/taxonomies/*.yaml`
**Consumer:** every `text-classify` command (`classify-*`, `discover-keywords`, `eval`)
**Doc:** [text-classification-user-guide §1.3](text-classification-user-guide.md#13-prepare-your-taxonomy)

Each YAML defines categories + sub-categories + seed_keywords for one
classification exercise. You can have many (`construction_root_cause.yaml`,
`activity.yaml`, `qms_process.yaml`, …) in `config/taxonomies/`.

### 3. `scripts/multi-period-analysis.config.ps1`
**Consumer:** `scripts/multi-period-analysis.ps1` (the 50-month batch)
**Doc:** [schedule-impact-user-guide §4.4](schedule-impact-user-guide.md#44-multi-period-theme-analysis-50-months--recurring-themes)

Defines:
```powershell
$Programme    = "HS2"
$XerRoot      = "C:\...\data\raw\xer\HS2"
$Taxonomy     = "C:\...\config\taxonomies\construction_root_cause.yaml"
$OutputsRoot  = "C:\...\outputs"
$AnalysisDir  = "C:\...\outputs\HS2_analysis"
$LlmModel     = "llama3.1:8b"
$LlmBackend   = "ollama"        # or "openai" for llama.cpp etc
```

### 4. `scripts/keyword-batch.config.ps1`
**Consumer:** `scripts/keyword-batch.ps1` (multi-dataset coordinator)
**Doc:** [text-classification-user-guide §8.6](text-classification-user-guide.md#86-batch-multiple-datasets-in-one-command-keyword-batch)

Defines `$Jobs` array — one hashtable per dataset:
```powershell
$Jobs = @(
    @{
        Name        = "root_cause"
        Input       = "C:\...\root_causes.csv"
        TextColumn  = "root_cause"
        Taxonomy    = "C:\...\construction_root_cause.yaml"
        Output      = "C:\...\outputs\keyword_analysis\root_cause"
    },
    # ... more jobs
)
```

---

## Commands by use case

### Single month comparison
```powershell
schedule-impact run-monthly `
  --programme HS2 --period 2025-04 --previous-period 2025-03 `
  --current-xer "C:\...\C38.xer" --previous-xer "C:\...\C37.xer" `
  --output-dir "C:\...\outputs"
```
Consumes: `config/settings.yaml`, `config/p6_schema.yaml`

### Many months at once (with optional LLM theme analysis)
```powershell
.\scripts\multi-period-analysis.ps1 -KeywordsOnly     # fast stages only
.\scripts\multi-period-analysis.ps1                   # full pipeline
.\scripts\multi-period-analysis.ps1 -SkipBatch        # analysis only, monthly outputs already exist
```
Consumes: `scripts/multi-period-analysis.config.ps1`, the taxonomy YAML it references

### Many free-text datasets at once (TF-IDF + keyword discovery)
```powershell
.\scripts\keyword-batch.ps1                   # all jobs, sequential
.\scripts\keyword-batch.ps1 -Parallel         # all jobs concurrently
.\scripts\keyword-batch.ps1 -DiscoverOnly     # skip the classify step
.\scripts\keyword-batch.ps1 -Jobs root_cause  # only the named job(s)
```
Consumes: `scripts/keyword-batch.config.ps1`, the taxonomy YAMLs it references

### One-off `text-classify` commands (no batch script)
```powershell
text-classify discover-keywords --input ... --out ...
text-classify classify-tfidf    --input ... --taxonomy ... --out ...
text-classify eval              --matches ... --labels ... --out ...
```
Consumes: the taxonomy YAML you pass via `--taxonomy`

---

## Verifying setup

Before running anything end-to-end:

```powershell
# Show every config file the scripts can find
.\scripts\multi-period-analysis.ps1 -DryRun
.\scripts\keyword-batch.ps1 -DryRun

# Verify the schedule-impact CLI sees your settings
schedule-impact run-monthly --help
```

`-DryRun` on either script prints what it *would* call without executing,
which is the fastest way to catch a missing or wrong-path config field.

---

## Where config DOESN'T live (so you know what's safe to overwrite)

- `config/*.example.yaml` and `scripts/*.config.example.ps1` are **templates** — committed, replaced by `git pull`. Never edit these directly.
- `config/p6_schema.yaml` and `config/quality_keywords.yaml` are committed defaults — edit if you need programme-specific changes, knowing your edits go into git for everyone.
- Anything under `data/`, `outputs/`, or `notebooks/` is gitignored — safe to delete/recreate; not config per se.
