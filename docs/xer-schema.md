# XER schema notes (programme sample)

Your exports use the **standard Primavera text XER** format (`%T` table, `%F` fields, `%R` rows), not SQLite. Fields are **tab-delimited**.

## Format detection

| First bytes | Format |
|-------------|--------|
| `SQLite format 3` | SQLite wrapper (rare) |
| `%T` / `ERMHDR` | Text XER (your programme) |

The ingest layer supports both via `detect_xer_format()`.

## Tables confirmed from sample

### TASK (primary for incidents)

Key columns for month-on-month delay and criticality:

| Column | Role |
|--------|------|
| `task_id` | Stable internal key |
| `proj_id` | Project scope |
| `wbs_id` | Join to PROJWBS |
| `task_code` | **Primary match key** between months |
| `task_name` | Display / fuzzy linking |
| `status_code` | e.g. `TK_Complete` |
| `early_start_date` / `early_end_date` | **Forecast slip** comparison |
| `late_start_date` / `late_end_date` | Late dates |
| `target_start_date` / `target_end_date` | Target baseline |
| `act_start_date` / `act_end_date` | Actualised dates |
| `total_float_hr_cnt` / `free_float_hr_cnt` | Float in **hours** |
| `driving_path_flag` | `Y` / `N` |

**Note:** Finish column is `early_end_date`, not `early_finish`.

### TASKMEMO (in-schedule narrative)

Planner explanations are stored as **HTML** in `task_memo`, keyed to tasks:

| Column | Role |
|--------|------|
| `memo_id` | Unique memo row |
| `task_id` | Join to `TASK` |
| `memo_type_id` | Join to `MEMOTYPE` (e.g. planner notes CP) |
| `proj_id` | Project |
| `task_memo` | HTML body (MSHTML / P6 rich text) |

Processing pipeline:

1. Load `TASKMEMO` + `MEMOTYPE` (+ `TASK` for `task_code`).
2. Filter to planner memo types (`config/p6_schema.yaml` → `memo_types.planner_note_labels`).
3. **Strip HTML** → plain text (`normalize/html_memo.py`).
4. Emit **narrative chunks** with `source: xer_taskmemo` for direct link to incidents via `task_id` / `task_code` (often stronger than PDF fuzzy match).

Memos may contain **historical PfA paragraphs** in one field (month-stamped lines). Treat each `memo_id` as one chunk; optional future step: split on date patterns inside the text.

### MEMOTYPE

| Column | Role |
|--------|------|
| `memo_type_id` | Key |
| `memo_type` | Label (e.g. planner notes categories) |

### PROJWBS

| Column | Role |
|--------|------|
| `wbs_id` | Key |
| `proj_id` | Project |
| `wbs_name` | Narrative / link matching |
| `parent_wbs_id` | Hierarchy |

### TASKPRED

| Column | Role |
|--------|------|
| `task_id` / `pred_task_id` | Edge |
| `pred_type` | e.g. `PR_FS` |
| `lag_hr_cnt` | Lag hours |

### Lower priority for Phase 1

| Table | Role |
|-------|------|
| `TASKRSRC` | Resource assignments |
| `TASKPROC` | Procedures / steps |

## Narrative sources (priority for linking)

| Source | Link strength | Notes |
|--------|---------------|-------|
| **TASKMEMO** | High (`task_id` / `task_code`) | HTML; strip before keywords/LLM |
| **PDF** | Medium–low | Fuzzy match; may be programme-level |

## Incident rules

See `config/p6_schema.yaml`:

- **Finish slip**: `early_end_date` month-on-month.
- **Impact**: `total_float_hr_cnt <= 0` and/or `driving_path_flag = Y`.
- **Float**: slip with float remaining.

## Profile on laptop

```bash
schedule-impact profile-xer --xer path/to/file.xer --out outputs/profile_reports/schema.json
```

Look for `TASKMEMO` row counts and column list in the JSON report.
