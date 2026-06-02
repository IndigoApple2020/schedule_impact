# User Guide — `text-classify`

End-to-end practical reference for running the `text_classify` toolkit on
your laptop. Cookbook style: copy-paste the commands. See
[`text-classification-strategy.md`](text-classification-strategy.md) for
the design rationale.

---

## 1. One-time setup

### 1.1 Install on the secure machine

```powershell
cd "C:\path\to\schedule_impact"
git pull                             # if the repo is already cloned
pip install -e .                     # core install
pip install -e ".[text]"             # adds scikit-learn (for TF-IDF)
pip install -e ".[llm]"              # adds ollama + tqdm (for LLM engines)
pip install -e ".[parquet]"          # adds pyarrow (for fast .parquet output)
```

After install:

```powershell
text-classify --help                 # should list 7 subcommands
```

### 1.2 Install Ollama (only if using LLM engines)

1. Download from <https://ollama.com> and install.
2. Pull the models you'll use:
   ```powershell
   ollama pull nomic-embed-text      # for classify-llm-embed
   ollama pull llama3.1:8b           # for classify-llm-prompt
   ```
3. Confirm the daemon is running: `ollama list` shows your models.

CPU-only laptops can run both models but expect slower inference — see
section 6 for timings.

### 1.3 Prepare your taxonomy

Copy the example taxonomy and edit:

```powershell
copy "config\taxonomies\construction_root_cause.example.yaml" `
     "config\taxonomies\construction_root_cause.yaml"
```

Then edit `construction_root_cause.yaml` to tune `seed_keywords` for your
programme's vocabulary. The `.yaml` file (without `.example`) is
**gitignored** so your programme-specific terms stay local.

### 1.4 Prepare your input CSV

The input is one CSV with **two essential columns**:
- An ID column (default name: `row_id`)
- A free-text column to classify (default name: `root_cause`)

Other columns are ignored. Example:

```
row_id,root_cause,raised_by,raised_date
1023,"Concrete had to be redone because rebar was missing key bars.",JL,2025-04-12
1024,"Late design change forced rework of partition walls",MR,2025-04-13
...
```

Use `--id-column` and `--text-column` if your columns are named differently.

---

## 2. Quick start (single engine)

```powershell
text-classify classify-tfidf `
  --input    "C:\...\issues.csv" `
  --taxonomy "C:\...\config\taxonomies\construction_root_cause.yaml" `
  --out      "C:\...\outputs\text_classify"
```

Outputs land in a timestamped subdirectory under `--out`. See section 7
for what each file contains.

---

## 3. The full validation workflow

This is the recommended workflow for assessing how well the engines work
on your data. Designed to fit "a few hours of compute + a labelling
session" — not 11 hours.

```
1. classify-tfidf on full 20k                          ~1 min
2. sample → sample.csv (1,500 rows balanced)           seconds
3. classify-llm-embed on sample.csv                    minutes
4. classify-llm-prompt on sample.csv                   ~1 hour
5. classify-llm-prompt on full 20k (overnight)         many hours, resumable

   ─── now you have predictions from each engine ───

6. Build labelling_set.csv from sample + engine outputs (seconds, see §4)
7. Label 200–500 rows manually                          (your time)
8. eval each engine against the same labels             seconds
9. Compare F1 numbers, tune taxonomy, repeat            iterate
```

### 3.1 Step 1 — TF-IDF on the full corpus

```powershell
text-classify classify-tfidf `
  --input    "C:\...\issues.csv" `
  --taxonomy "C:\...\construction_root_cause.yaml" `
  --out      "C:\...\outputs\tfidf_full"
```

The command prints the timestamped run subdirectory it created — note
this path, you'll reference it in step 2.

### 3.2 Step 2 — Stratified sample

```powershell
text-classify sample `
  --input              "C:\...\issues.csv" `
  --from-classify-run  "C:\...\outputs\tfidf_full\<run_subdir>" `
  --total              1500 `
  --out                "C:\...\sample.csv"
```

`sample.csv` is a balanced subset across categories and (easy/medium/hard)
score bands. It preserves all original input columns plus
`top_category_id`, `top_score`, `score_band` — so you can feed it
straight back into any classify command.

### 3.3 Step 3 — LLM embed on the sample

```powershell
text-classify classify-llm-embed `
  --input    "C:\...\sample.csv" `
  --taxonomy "C:\...\construction_root_cause.yaml" `
  --out      "C:\...\outputs\embed_sample" `
  --model    nomic-embed-text
```

### 3.4 Step 4 — LLM prompt on the sample

```powershell
text-classify classify-llm-prompt `
  --input    "C:\...\sample.csv" `
  --taxonomy "C:\...\construction_root_cause.yaml" `
  --out      "C:\...\outputs\prompt_sample" `
  --model    llama3.1:8b
```

A `tqdm` progress bar shows estimated time remaining.

### 3.5 Step 5 — LLM prompt on the full corpus (overnight, resumable)

```powershell
text-classify classify-llm-prompt `
  --input    "C:\...\issues.csv" `
  --taxonomy "C:\...\construction_root_cause.yaml" `
  --out      "C:\...\outputs\prompt_full" `
  --model    llama3.1:8b
```

If the run is interrupted (Ctrl-C, sleep, crash), look in the run subdir:

```
C:\...\outputs\prompt_full\<run_subdir>\llm_prompt_checkpoint.ndjson
```

The line count of that file = how many rows are done. To **resume**:

```powershell
text-classify classify-llm-prompt `
  --input      "C:\...\issues.csv" `
  --taxonomy   "C:\...\construction_root_cause.yaml" `
  --resume-dir "C:\...\outputs\prompt_full\<run_subdir>" `
  --out        "C:\...\outputs\prompt_full"
```

Rows already in the checkpoint are skipped; new rows are appended. Final
CSVs are rebuilt from the complete checkpoint when the run finishes.

---

## 4. Labelling the sample

You label **after** all the scoring runs so you can see what each engine
predicted, which makes labelling faster.

### 4.1 Build a labelling-friendly CSV

Run this in a Python REPL (or save as a script) on the secure machine:

```python
import pandas as pd

# Paths to update for your filesystem
SAMPLE   = "C:/.../sample.csv"
TFIDF    = "C:/.../outputs/tfidf_full/<run_subdir>/row_scores.csv"
EMBED    = "C:/.../outputs/embed_sample/<run_subdir>/row_scores.csv"
PROMPT   = "C:/.../outputs/prompt_sample/<run_subdir>/row_scores.csv"
OUT      = "C:/.../labelling_set.csv"

sample = pd.read_csv(SAMPLE)
def _rename(df, prefix):
    return df.rename(columns={
        "top_category_id":     f"{prefix}_top_cat",
        "top_sub_category_id": f"{prefix}_top_sub",
        "top_score":           f"{prefix}_score",
    })[["row_id", f"{prefix}_top_cat", f"{prefix}_top_sub", f"{prefix}_score"]]

tfidf  = _rename(pd.read_csv(TFIDF),  "tfidf")
embed  = _rename(pd.read_csv(EMBED),  "embed")
prompt = _rename(pd.read_csv(PROMPT), "prompt")

labelling = (
    sample
    .merge(tfidf,  on="row_id", how="left")
    .merge(embed,  on="row_id", how="left")
    .merge(prompt, on="row_id", how="left")
)

# Add empty columns for your true labels
labelling["true_category_id"] = ""
labelling["true_sub_category_id"] = ""

labelling.to_csv(OUT, index=False)
print(f"Wrote {len(labelling)} rows to {OUT}")
```

`labelling_set.csv` now has, per row: the original text, the top pick from
each engine, plus empty `true_category_id` / `true_sub_category_id`
columns for you to fill.

### 4.2 Label by hand

Open `labelling_set.csv` in Excel and fill in 200–500 rows:

- `true_category_id` — the top-level category from your taxonomy
- `true_sub_category_id` — the specific sub-category, or leave blank if
  none clearly applies (the eval will still count the category match)

**Multi-label**: a single row can apply to multiple sub-categories. To
represent that, **duplicate the row** in `labelling_set.csv` and fill in
a different label on each copy. The `eval` command treats labels as a set
of positive (row_id, cat, sub) triples.

### 4.3 How many to label?

| Goal | Sample size |
|---|---|
| Quick sanity check | 50–100 |
| Per-category F1 numbers stable | 200–300 |
| Compare engines confidently | 300–500 |
| Diminishing returns | 500+ |

Bias toward labelling rows where the engines **disagree** — those are the
high-signal cases for engine comparison.

### 4.4 Convert to `labels.csv`

The `eval` command needs a CSV with just `row_id, category_id,
sub_category_id`. From `labelling_set.csv` (after labelling):

```python
import pandas as pd
df = pd.read_csv("C:/.../labelling_set.csv")
labels = df[df["true_category_id"].notna() & (df["true_category_id"] != "")][[
    "row_id", "true_category_id", "true_sub_category_id"
]].rename(columns={
    "true_category_id": "category_id",
    "true_sub_category_id": "sub_category_id",
})
labels.to_csv("C:/.../labels.csv", index=False)
print(f"{len(labels)} labels saved")
```

---

## 5. Evaluation

### 5.1 Eval one engine

```powershell
text-classify eval `
  --matches  "C:\...\outputs\tfidf_full\<run_subdir>\matches.csv" `
  --labels   "C:\...\labels.csv" `
  --out      "C:\...\outputs\tfidf_full\<run_subdir>\eval"
```

Outputs in the eval directory:
- `eval_summary.csv` — per (sub-)category TP/FP/FN/precision/recall/F1, plus
  micro and macro overalls
- `eval_errors.csv` — every FP and FN with the row_id (for spot-checking)

### 5.2 Compare all engines on the same labels

```powershell
text-classify eval --matches "C:\...\outputs\tfidf_full\<run>\matches.csv"      --labels "C:\...\labels.csv" --out "C:\...\eval\tfidf"
text-classify eval --matches "C:\...\outputs\embed_sample\<run>\matches.csv"   --labels "C:\...\labels.csv" --out "C:\...\eval\embed"
text-classify eval --matches "C:\...\outputs\prompt_sample\<run>\matches.csv"  --labels "C:\...\labels.csv" --out "C:\...\eval\prompt"
```

Compare the `eval_summary.csv` files — the row with `level=overall,
category_id=micro` gives you the headline P / R / F1 per engine.

### 5.3 Sweep thresholds without re-classifying

`matches.csv` is pre-filtered at the run's threshold. To evaluate at a
different threshold without re-running classification, point `eval` at
the full score matrix:

```powershell
text-classify eval `
  --all-scores "C:\...\outputs\tfidf_full\<run>\all_scores_sub_long.csv" `
  --threshold  0.45 `
  --labels     "C:\...\labels.csv" `
  --out        "C:\...\eval\tfidf_t045"
```

Re-run with `0.30`, `0.40`, `0.50` etc. to find the threshold that
maximises F1 for your data.

### 5.4 Eval scope: labelled rows only

The `eval` command **ignores predictions for rows that aren't in
`labels.csv`**. This means you can label only 300 rows out of a 20k
corpus and still get meaningful precision/recall — the engines'
predictions on the other 19,700 rows don't count against them.

---

## 6. Reality on engine timings

Rough numbers for a 20k-row corpus and ~25 sub-categories:

| Engine | CPU laptop | Apple Silicon | NVIDIA GPU |
|---|---|---|---|
| TF-IDF | ~1 min | ~1 min | ~1 min |
| LLM embed (batched) | ~30 min | ~5 min | ~2 min |
| LLM prompt (8B) | ~24 hours | ~3 hours | ~1 hour |
| LLM prompt (3B, e.g. `qwen2.5:3b`) | ~8 hours | ~1 hour | ~30 min |

For CPU-only laptops on a "few hours" budget:
- Run TF-IDF + LLM embed on the **full corpus**
- Run LLM prompt on the **1,500-row sample only** (~1–3 hours)
- Use resume capability if you do want to attempt full prompt overnight

---

## 7. Output reference

Every classify run writes into
`<out_dir>/<YYYYMMDDTHHMMSS-engine[s]-hash>/`.

| File | Content | Filter |
|------|---------|--------|
| `matches.csv` | Sub-category matches above threshold | filtered |
| `category_matches.csv` | Category-level matches above threshold | filtered |
| `row_scores.csv` | Top-1 sub-category match per row | one per row |
| `all_scores_sub_long.csv` (+`.parquet`) | Every row × every sub-category | unfiltered |
| `all_scores_sub_wide.csv` (+`.parquet`) | Wide pivot (col per sub-category) | unfiltered |
| `all_scores_cat_long.csv` (+`.parquet`) | Every row × every category | unfiltered |
| `all_scores_cat_wide.csv` (+`.parquet`) | Wide pivot (col per category) | unfiltered |
| `keywords.csv` | Global cross-row recurring phrases | ranked |
| `keywords_by_category.csv` | Per-top-category recurring phrases | ranked |
| `llm_prompt_checkpoint.ndjson` | NDJSON, one line per scored row | LLM prompt only |
| `eval/eval_summary.csv` | Per (sub-)category metrics + micro/macro | from `eval` |
| `eval/eval_errors.csv` | Every FP and FN with row_id | from `eval` |

For multi-engine runs (`classify-multi`), additionally:

| File | Content |
|------|---------|
| `combined_scores_sub.csv` (+`.parquet`) | One row per (row × sub-category) with columns per engine + `ensemble_score` (mean) |
| `combined_scores_cat.csv` (+`.parquet`) | Same at category level |
| `<engine>/` | Per-engine subdirectory with the full single-engine output set |

### Reading Parquet quickly in pandas

```python
import pandas as pd
df = pd.read_parquet("C:/.../<run>/all_scores_sub_long.parquet")
# 50× faster than the CSV equivalent, ~10× smaller on disk
```

---

## 8. Common patterns

### 8.1 Try different seed terms without re-labelling

Edit `construction_root_cause.yaml` → re-run `classify-tfidf` → re-run
`eval` with the same `labels.csv`. The F1 numbers tell you whether the
change helped.

### 8.2 Find the best threshold

Run `classify-tfidf` once. Then re-run `eval --all-scores ... --threshold X`
for a few values of X. Pick the threshold that maximises micro-F1.

### 8.3 Discover entities outside your taxonomy

`keywords.csv` (global) and `keywords_by_category.csv` (per category)
surface recurring phrases — procedures, software, contracts, document
numbers — that aren't in the taxonomy. Skim them as a hint that your
taxonomy might be missing a category, or that a specific entity
deserves its own field on each row.

### 8.4 Combine multiple engines

```powershell
text-classify classify-multi `
  --engines  tfidf,llm_embed `
  --input    "C:\...\sample.csv" `
  --taxonomy "C:\...\construction_root_cause.yaml" `
  --out      "C:\...\outputs\multi"
```

`combined_scores_sub.csv` gives you `tfidf_score`, `llm_embed_score`, and
`ensemble_score` (mean) per row × sub-category. Sort by `ensemble_score`
to see the best-supported matches across methods.

### 8.5 Run only the keyword discovery

If you just want to see what recurring entities show up in your text,
without classifying:

```powershell
text-classify discover-keywords `
  --input "C:\...\issues.csv" `
  --out   "C:\...\outputs\keywords_only"
```

---

## 9. Troubleshooting

### "scikit-learn is required for TF-IDF classification"
```powershell
pip install -e ".[text]"
```

### "ollama package is required"
```powershell
pip install -e ".[llm]"
```

### "Ollama chat model 'llama3.1:8b' at http://localhost:11434 is not reachable"
- Is Ollama running? `ollama list`
- Is the model pulled? `ollama pull llama3.1:8b`

### LLM prompt run is too slow on CPU
- Use a smaller model: `--model qwen2.5:3b` or `--model llama3.2:3b`
- Run on the **1,500-row sample** rather than the full corpus
- Run overnight with resume enabled if a crash happens

### No `.parquet` files appearing
```powershell
pip install -e ".[parquet]"
```
CSVs are always written; Parquet siblings appear only when pyarrow is
installed.

### "Eval shows precision=1.0, but predictions look wrong"
The eval is scoped to labelled rows only. If you labelled 10 rows and the
engine got them all right, precision is 1.0 within scope — even though the
engine may be wrong on the other 19,990 unlabelled rows. Label more, or
label a stratified mix (the `sample` command does this).

### Resume not picking up my checkpoint
The `--resume-dir` must point at the run subdirectory containing
`llm_prompt_checkpoint.ndjson`, not the top-level `--out` directory. Run
subdirectories look like `20260601T120000-llm_prompt-abc12345`.

### Excel showing weird characters in CSV
The CSVs are UTF-8 with BOM (`utf-8-sig`) — Excel should handle them
correctly. If you see `Â£` or similar, your Excel may be configured to
read as a different encoding; open via Data → From Text/CSV and select
UTF-8.

---

## 10. Anonymity reminder

- Your `construction_root_cause.yaml` (programme-tuned seeds) is gitignored
- `data/issues/*.csv` is gitignored
- `outputs/text_classify/...` is gitignored
- All scoring runs locally; no row text leaves the machine even with LLM engines

Only commit `*.example.yaml` versions of taxonomies — never the programme-
tuned versions, and never any input/output CSVs.
