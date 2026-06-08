# User Guide — `schedule-impact`

End-to-end practical reference for running the `schedule_impact` package
on your laptop. Cookbook style: copy-paste the commands. See
[`architecture.md`](architecture.md) and [`strategy.md`](strategy.md) for
the design rationale.

> This guide covers the **`schedule-impact`** CLI (P6 XER + PDF analysis).
> For the **`text-classify`** CLI (taxonomy text classification), see
> [`text-classification-user-guide.md`](text-classification-user-guide.md).

---

## 1. One-time setup

### 1.1 Install on the secure machine

```powershell
# Clone (first time only)
git clone https://github.com/IndigoApple2020/schedule_impact.git
cd schedule_impact

# Or pull latest if already cloned
cd "C:\path\to\schedule_impact"
git pull
```

#### Pick the right install

The repo ships several optional dependency groups. Pick by what you want
to do:

| Extra | Adds | You need this if… |
|---|---|---|
| **core** (no extras) | pandas, pydantic, pyyaml, pdfplumber, python-dateutil | Always — base install |
| `[text]` | scikit-learn | Running `text-classify` (the classification toolkit in the same repo) |
| `[llm]` | ollama, tqdm | Using Ollama-backed engines in `text-classify` |
| `[parquet]` | pyarrow | Want `.parquet` siblings of the big CSVs (faster pandas re-reads) |
| `[ml]` | scikit-learn, joblib | Training a `schedule-impact` quality classifier from labelled memos |
| `[ocr]` | pymupdf | Your PDFs are **scanned images** rather than text-selectable. Most modern programme PDFs are text-selectable — you can skip this. |

#### Recommended install (most users)

```powershell
pip install -e ".[text,llm,parquet]"
```

That gets you everything except `[ml]` (only for memo classifier training)
and `[ocr]` (only for scanned PDFs). Add them later if needed — pip is
idempotent.

#### Minimal install (only running schedule-impact, ignoring text_classify)

```powershell
pip install -e .
```

After install:

```powershell
schedule-impact --help               # should list 8 subcommands
text-classify --help                 # listed only if you installed [text]
```

### 1.2 Shell choice

All commands in this guide use **PowerShell** syntax — backtick `` ` `` for
line continuation. For Git Bash, swap backticks for backslashes `\`.

### 1.3 Prepare your config

```powershell
# Copy the example settings (gitignored after copy)
copy config\settings.example.yaml config\settings.yaml
```

Edit `config\settings.yaml` to set:

```yaml
incidents:
  impact_slip_threshold_days: 0     # any positive slip on a critical task = impact
  float_slip_threshold_days: 1      # >= 1 day slip on a non-critical task = float incident

pdf:
  # Maps the top-level numbered section of the programme narrative
  # to a stable project_row_id. Leave {} to skip PDF section → project mapping.
  project_row_map:
    "3": "PRJ-CONSOLIDATION"
    "4": "PRJ-ENABLING-WORKS"
    "8": "PRJ-HS2"
    "9": "PRJ-LU"
```

`config\p6_schema.yaml` and `config\quality_keywords.yaml` are committed
and used by default — only edit them if you need programme-specific
overrides (e.g. additional MEMOTYPE labels for planner notes).

### 1.4 Place your input files

```
data/
├── raw/
│   ├── xer/{programme}/{YYYY-MM}/schedule.xer
│   └── pdf/{programme}/{YYYY-MM}/narrative.pdf
```

Anything under `data/raw/` is gitignored. The naming convention above is
recommended but not enforced — `--current-xer` accepts any path.

---

## 2. Quick start (compare two months)

```powershell
schedule-impact run-monthly `
  --programme       HS2 `
  --period          2025-04 `
  --previous-period 2025-03 `
  --current-xer     "C:\...\C38.xer" `
  --previous-xer    "C:\...\C37.xer" `
  --output-dir      "C:\...\outputs"
```

Output lands in `outputs\HS2\2025-04\`. See section 7 for the file
inventory.

Add `--pdf "C:\...\narrative.pdf"` (repeatable) to also extract PDF
narrative chunks and link them to incidents by `project_row_id`.

---

## 3. CLI command reference

Every command supports `--help` for full argument detail. Below is the
"why and when" for each.

### 3.1 `profile-all` — combined structure report (safe to share)

Anonymous structural metadata: column names, row counts, MEMOTYPE labels,
PDF section headings. **No row-level values.** Useful for sharing setup
issues without exposing programme data.

```powershell
schedule-impact profile-all `
  --xer          "C:\...\C38.xer" `
  --previous-xer "C:\...\C37.xer" `
  --pdf          "C:\...\narrative.pdf" `
  --programme    HS2 `
  --out          "C:\...\outputs\profile_reports\2025-04-combined.json"
```

**When to use:**
- First time setting up on a new programme — share the JSON for column-mapping calibration
- Schema drift suspected (a column changed name)
- PDF section structure changed (the extractor's regex may need tuning)

### 3.2 `profile-xer` — XER structure only

Subset of `profile-all`. Use when you don't have a PDF or want a quick
XER-only check.

```powershell
schedule-impact profile-xer `
  --xer  "C:\...\C38.xer" `
  --pair "C:\...\C37.xer" `
  --out  "C:\...\outputs\profile_reports\2025-04-xer.json"
```

The `--pair` flag adds month-on-month task match rate statistics
(how many `task_code` values are stable between the two exports).

### 3.3 `profile-pdf` — PDF structure only

PDF metadata: page count, section headings, character count per section,
table shapes. No body text.

```powershell
schedule-impact profile-pdf `
  --pdf "C:\...\narrative.pdf" `
  --out "C:\...\outputs\profile_reports\2025-04-pdf.json"
```

### 3.4 `run-monthly` — the main pipeline

Detect incidents, link narratives, score quality. This is the command
that produces the deliverable CSVs each month.

```powershell
schedule-impact run-monthly `
  --programme       HS2 `
  --period          2025-04 `
  --previous-period 2025-03 `
  --current-xer     "C:\...\C38.xer" `
  --previous-xer    "C:\...\C37.xer" `
  --pdf             "C:\...\narrative.pdf" `
  --output-dir      "C:\...\outputs"
```

Optional flags:
- `--pdf <path>` (repeatable) — extract narrative chunks from one or more PDFs
- `--project-row-id PRJ-X` — override the default proj_id → project_row_id mapping
- `--quality-model "C:\...\quality_classifier.joblib"` — use a trained ML model alongside keyword scoring (Phase-5 hybrid)

What it does:
1. Loads both XERs (text or SQLite format auto-detected)
2. Normalises tasks; computes finish slip per matched task
3. Detects IMPACT incidents (critical-path tasks slipping) and FLOAT
   incidents (non-critical slip)
4. Extracts TASKMEMO planner notes from the current XER, strips HTML
5. Links incidents to memos by `task_id`
6. Extracts PDF narrative sections (if `--pdf` given) and links them to
   incidents by shared `project_row_id`
7. Scores each incident's linked text for quality-related keywords
8. Writes CSVs + run_manifest.json

### 3.5 `run-batch` — run-monthly across many consecutive XER pairs

Auto-discovers periods under a root directory and runs the monthly
pipeline for every consecutive pair. See §4.4 for full multi-period
workflow.

**Two layouts supported (auto-detected):**

**Subdirectory layout** (preferred):
```
xer_root\2025-03\schedule.xer
xer_root\2025-04\schedule.xer
pdf_root\2025-04\narrative.pdf
```
Period = subdirectory name.

**Flat layout** (used automatically when no subdirectory contains XERs):
```
xer_root\HS2-PfA36.xer
xer_root\HS2-PfA37.xer
xer_root\schedule_2025-04.xer
```
Period is extracted from the filename:
1. `YYYY-MM` or `YYYY_MM` pattern (e.g. `schedule_2025-04.xer` → `"2025-04"`)
2. `C{N}` or `PfA{N}` cycle pattern (e.g. `HS2-PfA38.xer` → `"PfA38"`)
3. Custom `--period-regex` if your naming is different
4. Fallback: the filename stem

```powershell
schedule-impact run-batch `
  --programme    HS2 `
  --xer-root     "data\raw\xer\HS2" `
  --pdf-root     "data\raw\pdf\HS2" `
  --output-dir   "outputs" `
  --skip-existing
```

Optional:
- `--pdf-root` — PDFs matched by period (subdir or filename, same rules)
- `--skip-existing` — don't re-run periods that already produced output
- `--period-regex` — override filename extraction (flat layout only)
- `--project-row-id` / `--quality-model` — same as `run-monthly`

Custom regex example — if your files are like `cycle_3.xer`, `cycle_4.xer`:
```powershell
schedule-impact run-batch ... --period-regex "cycle_(\d+)"
```
The first match (or joined capture groups if there are groups) is used as the period.

### 3.6 `aggregate-memos` — combine taskmemo_chunks across runs

Walks an outputs directory and concatenates every
`taskmemo_chunks_*.csv` into one wide CSV ready for `text-classify`.

```powershell
schedule-impact aggregate-memos `
  --outputs-root "outputs\HS2" `
  --out          "outputs\HS2_analysis\all_memos.csv"
```

Output columns: `row_id` and `root_cause` (named for text-classify
defaults), plus `programme_id`, `reporting_period`, `task_id`,
`task_code`, `memo_type_label`, `source_file` for downstream joining.
Empty memos are dropped.

### 3.7 `export-schedule` — dump XER tables to CSV

Local-only analysis tool. Writes one CSV per useful XER table (TASK,
PROJWBS, TASKPRED, TASKMEMO, etc.) plus, when given a previous XER, a
wide month-over-month `task_changes.csv`.

```powershell
schedule-impact export-schedule `
  --xer          "C:\...\C38.xer" `
  --previous-xer "C:\...\C37.xer" `
  --out          "C:\...\outputs\extracted\C38"
```

Optional:
- `--all-tables` — also dump P6 internal tables (RSRC, OBS, UDFs etc.)

`task_changes.csv` is wider than `incidents_*.csv` — it includes every
task code, classified by `delta_category` (new, removed, slipped,
accelerated, completed, became_critical, lost_criticality,
status_changed, stable). Use it to manually sanity-check incident
detection: filter by `delta_category=slipped` and `is_critical_curr=True`
— those rows should appear in the IMPACT incidents CSV.

### 3.8 `export-memos` — bulk-export TASKMEMO for labelling

For teams that want to hand-label planner memos to train a quality
classifier.

```powershell
# All planner-labelled memos from one or more XERs
schedule-impact export-memos `
  --programme    HS2 `
  --xer          "C:\...\xer_folder\" `
  --period       2025-04 `
  --out          "C:\...\outputs\labeling\memos_for_review.csv"

# Slipped-task memos only (recommended — focuses labelling effort)
schedule-impact export-memos `
  --programme    HS2 `
  --xer          "C:\...\C38.xer" `
  --previous-xer "C:\...\C37.xer" `
  --period       2025-04 `
  --slipped-only `
  --min-slip-days 1 `
  --out          "C:\...\outputs\labeling\slipped_memos.csv"
```

The output CSV has memo text plus blank columns for the human to fill in
quality / not-quality classification. See
[`memo-labeling-and-ml.md`](memo-labeling-and-ml.md) for the full
labelling workflow.

### 3.9 `label-stats` — summarise a labelled memo CSV

```powershell
schedule-impact label-stats `
  --labels "C:\...\outputs\labeling\memos_labelled.csv" `
  --out    "C:\...\outputs\labeling\labels_report.html"
```

Reports: how many labelled, % quality, per-memo-type breakdown.

### 3.10 `train-quality-model` — train scikit-learn classifier

```powershell
schedule-impact train-quality-model `
  --labels    "C:\...\outputs\labeling\memos_labelled.csv" `
  --model     "C:\...\outputs\labeling\models\quality_classifier.joblib" `
  --report    "C:\...\outputs\labeling\models\train_report.json" `
  --test-size 0.2
```

Trains TF-IDF + Logistic Regression on labelled memos. The resulting
`.joblib` file can be passed to `run-monthly --quality-model <path>` for
hybrid keyword + ML scoring on future runs.

---

## 4. Workflows

### 4.1 First time on a new programme

Goal: validate that the column mapping and detection thresholds work
before producing deliverable outputs.

```
1. profile-all on the first two XERs + a PDF
   → share the JSON in this chat for column-mapping calibration

2. (If needed) Edit config\p6_schema.yaml to add programme-specific
   MEMOTYPE labels, task_type exclusions, etc.

3. run-monthly on the first two XERs (no --pdf yet)
   → spot-check 20 incidents from incidents_*.csv against your judgement
   → adjust impact_slip_threshold_days / float_slip_threshold_days in
     settings.yaml if the volumes look wrong

4. export-schedule with --previous-xer
   → open task_changes.csv in Excel
   → filter delta_category and cross-reference with incidents CSV
   → confirms incident detection is consistent

5. Add --pdf to run-monthly
   → check narrative_chunks_*.csv looks right
   → fill in pdf.project_row_map in settings.yaml so PDF chunks link to
     project rows

6. Share counts only ("47 IMPACT, 152 FLOAT, 38 PDF chunks linked")
   if you need help interpreting results.
```

### 4.2 Monthly cadence

Once setup is validated, every month:

```powershell
# Put new files in place
#   data\raw\xer\HS2\2025-05\schedule.xer
#   data\raw\pdf\HS2\2025-05\narrative.pdf

# Run the pipeline
schedule-impact run-monthly `
  --programme       HS2 `
  --period          2025-05 `
  --previous-period 2025-04 `
  --current-xer     "data\raw\xer\HS2\2025-05\schedule.xer" `
  --previous-xer    "data\raw\xer\HS2\2025-04\schedule.xer" `
  --pdf             "data\raw\pdf\HS2\2025-05\narrative.pdf" `
  --output-dir      "outputs"

# Outputs:
#   outputs\HS2\2025-05\incidents_2025-05.csv
#   outputs\HS2\2025-05\incident_memo_links_2025-05.csv
#   outputs\HS2\2025-05\narrative_chunks_2025-05.csv
#   outputs\HS2\2025-05\quality_assessment_2025-05.csv
#   outputs\HS2\2025-05\run_manifest.json
```

Typical runtime: 30–90 seconds for a 24k-task schedule.

### 4.3 Validating against your judgement

When the incident counts feel surprising:

```powershell
# Dump everything for cross-reference
schedule-impact export-schedule `
  --xer          "data\raw\xer\HS2\2025-05\schedule.xer" `
  --previous-xer "data\raw\xer\HS2\2025-04\schedule.xer" `
  --out          "outputs\extracted\HS2_2025-05"

# task_changes.csv: filter delta_category, is_critical_curr, finish_slip_calendar_days
# Compare against incidents_2025-05.csv
```

Common discoveries from this workflow:
- A task type you forgot to exclude (e.g. LOE tasks treated as critical)
- A `total_float_hr_cnt` value that's NULL on real activities — different
  from the milestones we already filter out
- Tasks renamed between months (loses the match) — adjust
  `comparison.match_keys` / `match_fallback` in `p6_schema.yaml`

### 4.4 Multi-period theme analysis (50+ months → recurring themes)

End-to-end flow for "I have N months of XERs, I want to see what themes
come up across all the planner memos and how they relate to schedule
slips".

**Folder layout assumed:**
```
data\raw\xer\HS2\2021-01\schedule.xer
data\raw\xer\HS2\2021-02\schedule.xer
...
data\raw\xer\HS2\2025-04\schedule.xer
data\raw\pdf\HS2\2025-04\narrative.pdf   (optional, matched by period)
```

**One-shot wrapper script:** `scripts\multi-period-analysis.ps1` chains
all the steps. Edit the variables at the top for your dataset, then:

```powershell
# Full run (batch → aggregate → keywords → LLM classify)
.\scripts\multi-period-analysis.ps1

# Just the fast stages — stop after keyword discovery for manual review
.\scripts\multi-period-analysis.ps1 -KeywordsOnly

# Already ran the monthly pipeline; only do the analysis
.\scripts\multi-period-analysis.ps1 -SkipBatch
```

**What each stage does, individually:**

#### Stage 1 — `run-batch`: run-monthly across all consecutive XER pairs

```powershell
schedule-impact run-batch `
  --programme HS2 `
  --xer-root  "data\raw\xer\HS2" `
  --pdf-root  "data\raw\pdf\HS2" `
  --output-dir "outputs" `
  --skip-existing
```

Auto-discovers period subdirectories, sorts chronologically, runs the
monthly pipeline for every consecutive pair. `--skip-existing` skips
periods that already produced `incidents_*.csv` (so you can resume).
Each pair takes 30–90 seconds; 50 pairs ≈ 30–75 min total.

#### Stage 2 — `aggregate-memos`: combine all memo CSVs into one

```powershell
schedule-impact aggregate-memos `
  --outputs-root "outputs\HS2" `
  --out          "outputs\HS2_analysis\all_memos.csv"
```

Walks every `outputs\HS2\<period>\taskmemo_chunks_<period>.csv` and
produces one wide CSV with the schema that `text-classify` expects
(`row_id`, `root_cause`), plus provenance columns (`programme_id`,
`reporting_period`, `task_id`, `task_code`, `memo_type_label`).

#### Stage 3 — `discover-keywords`: recurring phrases across all memos

```powershell
text-classify discover-keywords `
  --input    "outputs\HS2_analysis\all_memos.csv" `
  --taxonomy "config\taxonomies\construction_root_cause.yaml" `
  --out      "outputs\HS2_analysis\keywords" `
  --min-doc-count 10
```

Fast (~seconds). Output `keywords.csv` ranks phrases by document
frequency × TF-IDF — surfaces recurring entities (procedures,
contractors, document references) the taxonomy doesn't yet name. Use
this to decide whether the taxonomy needs new categories before doing
the slow classification step.

#### Stage 4 — `classify-llm-prompt`: score every memo against your taxonomy

```powershell
text-classify classify-llm-prompt `
  --input     "outputs\HS2_analysis\all_memos.csv" `
  --taxonomy  "config\taxonomies\construction_root_cause.yaml" `
  --out       "outputs\HS2_analysis\classify" `
  --model     llama3.1:8b `
  --threshold 0.4
```

Slow (~hours on CPU). Use `--resume-dir` to continue after a cancel.
Outputs include `all_scores_sub_long.csv` (every memo × every category
score) and `matches.csv` (above-threshold hits).

#### Stage 5 — Link themes back to schedule changes

After Stage 4, each memo (`chunk_id`) has scores per quality category.
Each memo is also linked to a `task_id` and to one or more
`incident_id`s via `incident_memo_links_*.csv`. To produce the answer
to "which incidents are quality-related, and which themes drive them?",
join in pandas:

```python
import pandas as pd, glob

# 1. Memo scores from the LLM run
scores  = pd.read_csv("outputs/HS2_analysis/classify/<run>/matches.csv")
# 2. Aggregated memos (carries task_id back to the schedule)
memos   = pd.read_csv("outputs/HS2_analysis/all_memos.csv")
# 3. Every period's incident ↔ chunk links
links   = pd.concat([pd.read_csv(p) for p in
                     glob.glob("outputs/HS2/*/incident_memo_links_*.csv")])
# 4. Every period's incidents
incidents = pd.concat([pd.read_csv(p) for p in
                       glob.glob("outputs/HS2/*/incidents_*.csv")])

# Memo themes back to incidents
memo_themes = scores.merge(memos[["row_id","task_id","task_code","reporting_period"]],
                           on="row_id")
incident_themes = (links.merge(memo_themes, left_on="chunk_id", right_on="row_id")
                        .merge(incidents,  on="incident_id"))

# Quality-related slips, by theme
incident_themes.to_csv("outputs/HS2_analysis/incident_themes.csv", index=False)
```

That CSV has one row per (incident, theme, memo) triple — sort by
`delay_days` descending for "the biggest slips that are quality-related".

### 4.5 Quality scoring tune-up

After a few months you'll have a sense of which incidents are *truly*
quality-related and which the keyword scoring got wrong.

```
1. export-memos --slipped-only          (your team labels a few hundred)
2. label-stats                          (sanity-check label distribution)
3. train-quality-model                  (produces .joblib + train report)
4. run-monthly --quality-model <path>   (hybrid scoring on next run)
```

See [`memo-labeling-and-ml.md`](memo-labeling-and-ml.md) for detail.

### 4.6 Working with sensitive data

The whole workflow runs locally. Only `profile-*` output should ever
leave the machine — it carries column names, row counts, section
headings, and labels but **no row-level values**.

```powershell
# Always safe to share
schedule-impact profile-all --xer ... --pdf ... --out report.json

# Local only — contains real task names, dates, memo text
schedule-impact run-monthly ...
schedule-impact export-schedule ...
schedule-impact export-memos ...
```

See [`development-with-sensitive-data.md`](development-with-sensitive-data.md)
for the full anonymity policy.

---

## 5. Output reference — `run-monthly`

All paths relative to `<output-dir>/<programme>/<period>/`.

### 5.1 `incident_review_<period>.csv` (recommended starting point)

**Pre-joined review surface — open this first.** One row per
(incident × linked narrative chunk). Incidents with no linked narrative
still appear (with blank link/chunk columns), so nothing is lost.

| Column | Description |
|---|---|
| `incident_id`, `incident_type` | From the incident |
| `project_row_id`, `primary_task_id`, `task_code`, `task_name`, `task_type` | From the incident |
| `delay_days`, `is_critical`, `total_float_hours`, `finish_field_used` | From the incident |
| `link_chunk_id` | Which chunk this row is paired with (`xer-memo-...` or `pdf-...`) |
| `link_method` | `task_memo` (high-confidence task_id match) or `project_row` (PDF section ↔ project row) |
| `link_confidence` | 0–1; 0.95 for memo task_id matches, 0.4–0.65 for PDF section matches |
| `link_rationale` | One-line audit string explaining the link |
| `chunk_source` | `xer_taskmemo` or `pdf` (blank if incident has no link) |
| `chunk_section_label` | Memo type for memos, section title for PDFs |
| `chunk_text` | Full plain-text body of the linked chunk |

Sort by `incident_type` then `delay_days` descending for triage. Filter
by `chunk_source` to look at memo-explained vs PDF-explained vs
unexplained incidents.

This file is the equivalent of the manual pandas join shown in earlier
versions of this guide — the join is now done for you on every run.

### 5.2 `incidents_<period>.csv`

The raw incident list, one row per detected schedule incident.

| Column | Description |
|---|---|
| `incident_id` | `{programme}-{period}-{type}-{seq:04d}` (independent counters per type) |
| `incident_type` | `impact` \| `float` |
| `project_row_id` | Programme reporting unit (defaults to P6 `proj_id`) |
| `proj_id` | P6 project id |
| `reporting_period` | `YYYY-MM` |
| `previous_period` | `YYYY-MM` of the compared baseline |
| `primary_task_id` | P6 task id |
| `task_code` | P6 task code (`A1010`, etc.) |
| `task_name` | Task name |
| `task_type` | `TT_Task`, `TT_Mile`, etc. — excluded types (`TT_LOE`, `TT_WBS`) never appear here |
| `delay_days` | Calendar days slipped (positive = later) |
| `delay_metric` | Always `finish_slip_calendar_days` |
| `finish_field_used` | `early_end_date` (forecast) or `act_end_date` (actualised) |
| `is_critical` | True if total float ≤ 0 OR driving_path_flag = Y |
| `total_float_hours` | Float in hours from `total_float_hr_cnt`; blank if null |

### 5.3 `incident_memo_links_<period>.csv`

| Column | Description |
|---|---|
| `incident_id` | Links to incidents_*.csv |
| `chunk_id` | TASKMEMO chunk id or PDF section chunk id |
| `link_method` | `task_memo` (high-confidence task_id match) or `project_row` (PDF section ↔ project row) |
| `confidence` | 0.95 for task_memo; 0.65 for PDF cp/mitigation sections; 0.4 for other PDF sections |
| `rationale` | One-line audit string |
| `memo_type_label` | For TASKMEMO chunks: planner note category |

### 5.4 `taskmemo_chunks_<period>.csv` (always written)

Body text of every planner memo extracted from the current XER's TASKMEMO
table, after HTML stripping. One row per memo chunk.

| Column | Description |
|---|---|
| `chunk_id` | `xer-memo-{memo_id}` |
| `document_id` | `xer:{programme}:{proj_id}:taskmemo` |
| `source` | Always `xer_taskmemo` |
| `reporting_period` | `YYYY-MM` |
| `proj_id` | P6 project id the memo belongs to |
| `task_id` | P6 task id the memo is attached to |
| `task_code` | P6 task code for the same task |
| `memo_type_id` | MEMOTYPE row id |
| `memo_type_label` | MEMOTYPE label (e.g. "Planners Notes (CP)") |
| `section_title` | Same as `memo_type_label` |
| `text` | Plain-text body after HTML strip |

The links file (`incident_memo_links_<period>.csv`) carries the
`chunk_id` reference; join the two CSVs on `chunk_id` to see the memo
body alongside the incident it links to.

### 5.5 `narrative_chunks_<period>.csv` (only when `--pdf` passed)

| Column | Description |
|---|---|
| `chunk_id` | `pdf-{document}-{section_number}` |
| `document_id` | `pdf:{programme}:{filename}` |
| `source` | Always `pdf` |
| `project_row_id` | From `pdf.project_row_map` (blank if unmapped) |
| `section_number` | e.g. `8.2` |
| `parent_section_number` | e.g. `8` (top-level) |
| `section_type` | `period_overview` \| `cp_float_analysis` \| `mitigation` \| `period_progress` \| `lookahead` \| `key_decisions` \| `assumptions` |
| `section_title` | Full heading text |
| `first_page` | 1-indexed PDF page |
| `text` | Body prose (no tables) |
| `activity_codes` | `;`-delimited regex-extracted code-like strings |
| `dates` | `;`-delimited regex-extracted date strings |

### 5.6 `quality_assessment_<period>.csv`

| Column | Description |
|---|---|
| `incident_id` | |
| `is_quality_related` | bool |
| `quality_confidence` | 0–1 |
| `quality_signals` | `;`-separated matched keywords + optional `ml_p=<prob>` |
| `classifier` | `keywords` \| `ml_v1` \| `hybrid` |
| `review_status` | `auto` (confident) \| `needs_review` (confidence < 0.75) |
| `has_linked_memo` | bool — false when no narrative was linked to this incident |

### 5.7 `run_manifest.json`

Run metadata: timestamps, programme, period, file basenames, incident
counts, link counts, PDF chunk count, quality-flagged count. Useful for
reproducibility audits.

---

## 6. Output reference — `export-schedule`

Paths under `--out` directory.

### 6.1 Per-table CSVs

Default subset (when `--all-tables` not passed):

| File | Description |
|---|---|
| `PROJECT.csv` | Project header (1 row typically) |
| `PROJWBS.csv` | WBS hierarchy |
| `CALENDAR.csv` | Calendar definitions |
| `TASK.csv` | All activities |
| `TASKPRED.csv` | Predecessor relationships |
| `TASKACTV.csv` | Task ↔ activity-code links |
| `ACTVCODE.csv` | Activity code values |
| `ACTVTYPE.csv` | Activity code type definitions |
| `MEMOTYPE.csv` | Memo type definitions |
| `TASKMEMO.csv` | Planner memos (HTML body) |
| `WBSMEMO.csv` | WBS-level memos |

All columns from the source XER are preserved verbatim.

### 6.2 `task_changes.csv` (only with `--previous-xer`)

One row per task_code that appears in either XER:

| Column | Description |
|---|---|
| `task_code` | |
| `delta_category` | Comma-list: `new`, `removed`, `slipped`, `accelerated`, `completed`, `became_critical`, `lost_criticality`, `status_changed`, `stable` |
| `task_id_curr` / `task_id_prev` | |
| `task_name`, `task_type`, `wbs_id` | |
| `status_code_prev` / `status_code_curr` | |
| `early_end_date_prev` / `early_end_date_curr` | |
| `act_end_date_prev` / `act_end_date_curr` | |
| `finish_slip_calendar_days` | |
| `total_float_hr_cnt_prev` / `_curr` / `_change_hr` | |
| `driving_path_flag_prev` / `_curr` | |
| `is_critical_prev` / `_curr` | |
| `finish_field_used_prev` / `_curr` | |

Open in Excel and slice by `delta_category` to manually audit incident
detection — every row with `slipped` + `is_critical_curr=True` should
appear as an IMPACT incident.

---

## 7. Output reference — `profile-*`

`profile-xer` and `profile-pdf` write standalone JSON. `profile-all`
writes a combined JSON with the same nested structure.

### 7.1 `profile-all` JSON shape

```
{
  "generated_at": "2026-06-01T23:34:28+00:00",
  "programme_id": "HS2",
  "xer": {
    "source_basename": "C38.xer",
    "sha256_prefix": "4c6c6ac50f53",
    "xer_format": "text",
    "xer_tables": ["PROJECT", "PROJWBS", "TASK", ...],
    "tables": {                       // per-table column metadata
      "TASK": {
        "row_count": 23973,
        "columns": [
          {"name": "task_id", "declared_type": "TEXT", "null_fraction": 0.0},
          ...
        ]
      },
      ...
    },
    "memotype_labels": [              // structural — safe to share
      "Planners Notes",
      "HS2-CPP-Planners notes + explanations",
      ...
    ],
    "actv_code_type_names": [         // structural — safe to share
      "EUS - Area",
      "HS2 - Section",
      ...
    ],
    "notes": [...]
  },
  "xer_pair_match": [                 // present when --previous-xer given
    {
      "key": "task_code",
      "count_previous": 22183,
      "count_current": 23973,
      "matched": 21590,
      "match_rate_previous": 0.9733,
      "match_rate_current": 0.9006
    }
  ],
  "pdfs": [                           // one entry per --pdf
    {
      "source_basename": "narrative.pdf",
      "page_count": 42,
      "sections": [
        {
          "heading": "8.2 Critical Path & Float Analysis",
          "level": 2,
          "first_page": 21,
          "char_count": 831,
          "table_count": 0
        },
        ...
      ],
      "activity_code_like_count": 18,
      "date_like_count": 5
    }
  ]
}
```

No task names, no row values, no narrative text — only structural
metadata.

---

## 8. Config reference

### 8.1 `config\settings.yaml`

Runtime config for `run-monthly`. Created from `settings.example.yaml`.

```yaml
incidents:
  impact_slip_threshold_days: 0       # critical task with slip > this = IMPACT
  float_slip_threshold_days: 1        # non-critical task with slip >= this = FLOAT

quality:
  require_human_review_below: 0.75    # quality_confidence < this → needs_review
  model_path: null                    # path to trained .joblib; null = keywords only

pdf:
  project_row_map:                    # top-level section number → project_row_id
    "3": "PRJ-CONSOLIDATION"
    "8": "PRJ-HS2"
```

If `settings.yaml` is missing, the pipeline silently falls back to
`settings.example.yaml` defaults.

### 8.2 `config\p6_schema.yaml` (committed; programme-tunable)

The canonical XER column mapping. Sections worth knowing about:

```yaml
criticality:
  float_hours_critical_threshold: 0   # ≤ this hours = critical
  use_driving_path_flag: true         # also treat driving_path_flag = Y as critical

comparison:
  match_keys: [task_code]             # primary key for month-on-month matching
  match_fallback: [task_id]           # fallback if primary doesn't hit

task_type:
  exclude_from_detection:             # task_type values never treated as incidents
    - TT_LOE                          # Level of Effort
    - TT_WBS                          # WBS Summary

memo_types:
  planner_note_labels:                # MEMOTYPE labels treated as planner notes
    - "Planners Notes"
    - "Planners Notes (CP)"
    - "HS2-CPP-Planners notes + explanations"
    - ...
```

After programme-specific tuning, you can edit this file directly — it's
committed, so changes flow to the team.

### 8.3 `config\quality_keywords.yaml`

Keywords used by the quality classifier. Two lists:

```yaml
categories:
  quality:                            # presence raises quality_confidence
    - rework
    - non-conformance
    - failed inspection
    - ...
  not_quality:                        # presence lowers quality_confidence
    - weather delay
    - permit
    - ...
```

After Phase-5 labelling, you can replace this with a learned model via
`--quality-model`.

---

## 9. Troubleshooting

### "Empty incidents file"
- Check `task_changes.csv` — are there any `slipped` rows at all?
- Threshold may be too high. Try `impact_slip_threshold_days: -1` and
  re-run to see if any incidents appear at all
- The two XERs may have completely different `task_code` values; check
  `pair_match.match_rate_current` from `profile-xer --pair`

### "Too many IMPACT incidents — every task looks critical"
- 40%+ of tasks in real schedules have NULL float; the code intentionally
  treats NULL as not-critical. If you're still seeing too many, check
  the `driving_path_flag` distribution — some exports set Y on
  non-critical tasks

### "PDF chunks aren't linking to incidents"
- Open `narrative_chunks_*.csv` — is `project_row_id` populated?
- If blank, your `pdf.project_row_map` in `settings.yaml` doesn't match
  the top-level section numbers (1, 2, 3 …) used in the PDF
- Re-run `profile-pdf` and confirm the section headings

### "schedule-impact: command not found"
Re-install:
```powershell
pip install -e .
```

### "Argparse error about a % character"
This was an old bug in `label-stats` help text, fixed in commit `b107d71`.
`git pull` to pick up the fix.

### "Pre-commit hook failed during git commit"
Not applicable — we don't currently use pre-commit hooks. If you see this,
the message likely says exactly what's wrong; fix and re-commit.

### "Worried I'm about to leak data"
- `outputs/` and `data/` are both gitignored
- Verify before commit: `git status` should NOT show any `.xer`, `.pdf`,
  `incidents_*.csv`, `extracted/`, `labeling/` paths
- `profile-*` JSON is structural only — safe to share if you're unsure,
  open it in a text editor first to confirm no row content

---

## 10. Where to look next

- **Understand the design**: [`architecture.md`](architecture.md) — module
  map, data model, detection logic
- **Strategic context**: [`strategy.md`](strategy.md) — phased rollout,
  KPIs, validation approach
- **Sensitive data policy**: [`development-with-sensitive-data.md`](development-with-sensitive-data.md)
- **TASKMEMO labelling deep-dive**: [`memo-labeling-and-ml.md`](memo-labeling-and-ml.md)
- **XER schema details**: [`xer-schema.md`](xer-schema.md)
- **Text classification toolkit** (different package, same repo):
  [`text-classification-user-guide.md`](text-classification-user-guide.md)
