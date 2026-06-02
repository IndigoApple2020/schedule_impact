# Architecture & Strategy — Text Classification (`text_classify`)

A generic, taxonomy-driven text classification toolkit sitting alongside the
schedule analytics package. The first use case is **construction issues log**
→ **root cause categories + sub-categories**, but the design is reusable for
any single-input-text, multi-label-against-taxonomy classification problem.

---

## 0. Current state (built)

| Capability | Status |
|------------|--------|
| Generic taxonomy loader (YAML → typed dataclasses) | ✓ |
| TF-IDF zero-shot scoring against seed pseudo-documents | ✓ |
| LLM-embedding zero-shot scoring (Ollama, e.g. `nomic-embed-text`) | ✓ |
| LLM-prompt scoring with structured JSON output (Ollama chat models) | ✓ |
| **Sub-category-level pass** (every row × every sub-category) | ✓ |
| **Category-level pass** (every row × every category, parallel) | ✓ |
| **Multi-engine runs** with combined wide CSV + ensemble score | ✓ |
| Cross-row keyword/phrase discovery (n-gram TF-IDF) | ✓ |
| Full unfiltered score matrix in long + wide formats | ✓ |
| Threshold-filtered match CSVs (sub + cat) | ✓ |
| Per-row top-match summary | ✓ |
| Ollama checkpoint (NDJSON per row) for resumable prompt runs | ✓ |
| Ollama preflight check (fail fast if daemon/model not ready) | ✓ |
| CLI: `text-classify {classify-tfidf, classify-llm-embed, classify-llm-prompt, classify-multi, discover-keywords}` | ✓ |

---

## 1. Problem

- ~20,000 issue rows with a free-text **Root Cause** field
- Target output: for each row, **zero or more** `(category, sub_category)` matches
  with a confidence score
- A configurable **threshold** decides which matches are kept
- Bonus: surface **recurring keywords/phrases** (procedures, software, contracts,
  document numbers) that cross multiple rows but are not in the taxonomy

Two parallel implementations of the same scoring contract:

| Method | Engine | When it shines | Data leaves laptop? |
|--------|--------|----------------|---------------------|
| **TF-IDF** | scikit-learn, zero-shot via seed terms + descriptions | Fast, deterministic, fully offline, audit-friendly | No |
| **LLM (embed)** | Ollama embedding model (e.g. `nomic-embed-text`) | Cheaper than prompt; catches paraphrase in dense space | No (local) |
| **LLM (prompt)** | Ollama chat model with JSON-output schema | Most accurate; produces free-text rationale per match | No (local) |

Outputs are interchangeable across engines — downstream tools and review
queues don't care which method ran.

---

## 2. Guiding principles

| Principle | Implication |
|-----------|-------------|
| **Generic codebase, specific taxonomy** | Taxonomy is YAML; code makes no assumptions about category names |
| **Anonymity preserved** | All engines run locally; sensitive text never leaves the laptop |
| **Zero-shot first, supervised later** | No labelled data required to get useful output; labels can refine seeds (and eventually train a supervised model) downstream |
| **Auditable** | Every score is reproducible from logged config + commit SHA; signals (matched terms / model rationale) stored alongside scores |
| **Same output schema across engines** | One contract: `MatchRecord` |
| **Emit raw + filtered** | Full score matrix is always written; threshold-filtered files are conveniences |

---

## 3. Repository layout

```
src/text_classify/
├── __init__.py
├── schemas.py            ─ dataclasses: Taxonomy, MatchRecord, RowScore,
│                           KeywordRecord, ScoringResult
├── taxonomy.py           ─ YAML loader + validator
├── preprocess.py         ─ lowercase, strip punctuation (keep hyphens/codes)
├── tfidf_classifier.py   ─ score_all() + score_rows() back-compat shim
├── llm_classifier.py     ─ score_all_embed() / score_all_prompt() + checkpoint
├── keyword_discovery.py  ─ cross-row n-gram phrase mining
├── runner.py             ─ orchestrators: run_classify, run_classify_multi,
│                           run_discover_only; CSV emission
└── cli.py                ─ text-classify entry point (5 subcommands)

config/taxonomies/
├── construction_root_cause.example.yaml   ─ committed
└── construction_root_cause.yaml           ─ gitignored (programme-specific)

docs/text-classification-strategy.md       ─ this document

tests/
├── fixtures/text_classify/
│   ├── issues_sample.csv
│   └── taxonomy.yaml
├── test_text_classify_taxonomy.py
├── test_text_classify_tfidf.py            ─ also covers runner & multi-engine
└── test_text_classify_keywords.py
```

---

## 4. Taxonomy schema

Stored as YAML in `config/taxonomies/`. Generic structure:

```yaml
name: construction_root_cause
version: 1
default_threshold: 0.35

categories:
  - id: poor_planning_design
    label: "Poor Planning and design"
    description: >-
      Issues caused by design errors, incomplete plans, late changes, or
      design–delivery handoff problems.
    seed_keywords:
      - design
      - drawing
      - specification
    sub_categories:
      - id: inadequate_design
        label: "Inadequate design"
        description: "Design itself is flawed, incomplete, or misses requirements."
        seed_keywords:
          - incomplete design
          - design error
          - missing detail
```

The `description` and `seed_keywords` are used by **both engines**:

- **TF-IDF** combines them into a per-(sub-)category pseudo-document; rows are
  scored by cosine similarity against the pseudo-document
- **LLM prompt** sends them as category definitions in the prompt
- **LLM embed** combines them into a string to embed

Tuning the taxonomy improves all three engines simultaneously.

---

## 5. Pipeline

```
input.csv (row_id, root_cause_text, ...)
        │
        ▼
┌─────────────────┐
│  preprocess     │  lowercase, strip punct (keep hyphens/slashes),
│                 │  normalise whitespace; preserves codes like ITP-123
└────────┬────────┘
         │
         ├────────────────────────────────┬───────────────────────┐
         ▼                                ▼                       ▼
┌──────────────────┐         ┌────────────────────┐    ┌─────────────────────┐
│  TF-IDF scorer   │         │  LLM-embed scorer  │    │ LLM-prompt scorer   │
│   sklearn        │         │     Ollama         │    │     Ollama          │
│  cosine vs       │         │  cosine vs         │    │  per-row JSON       │
│  seed pseudo-    │         │  seed embeddings   │    │  ckpt to NDJSON     │
│  docs            │         │                    │    │                     │
└────────┬─────────┘         └─────────┬──────────┘    └─────────┬───────────┘
         │                             │                          │
         │   For each engine:          │                          │
         │     sub-category pass       │                          │
         │     + category pass         │                          │
         │     → ScoringResult         │                          │
         │                             │                          │
         └─────────────┬───────────────┴──────────────────────────┘
                       ▼
        ┌─────────────────────────────────────────┐
        │   write CSVs (per-engine subdirectory)   │
        │                                          │
        │  matches.csv / category_matches.csv      │
        │  all_scores_sub_long.csv / _wide.csv     │
        │  all_scores_cat_long.csv / _wide.csv     │
        │  row_scores.csv                          │
        └────────────┬────────────────────────────┘
                     │
              (multi-engine runs)
                     ▼
        ┌─────────────────────────────────────────┐
        │  combined_scores_sub.csv                 │
        │  combined_scores_cat.csv                 │
        │    columns:   row_id, category_id,       │
        │               sub_category_id,           │
        │               tfidf_score,               │
        │               llm_embed_score,           │
        │               ensemble_score (mean)      │
        └─────────────────────────────────────────┘

       Separate pass (always, optional):
        ┌─────────────────────────────────────────┐
        │  keyword_discovery → keywords.csv        │
        │   recurring n-grams not in taxonomy seeds│
        └─────────────────────────────────────────┘
```

---

## 6. Scoring details

### 6.1 Sub-category-level pseudo-document

```
sub.description + sub.seed_keywords + parent.seed_keywords
```

Parent label/description deliberately omitted — they would dilute the
discriminative power between sibling sub-categories.

### 6.2 Category-level pseudo-document (parallel pass)

```
cat.description + cat.seed_keywords
```

Sub-category content is **not** included. The category pass answers
"does the category as a whole apply?" independently of which specific
sub-category, so it catches rows that match the category gist without
naming a specific sub-mechanism (e.g. "design issues across the package").

### 6.3 TF-IDF

- `TfidfVectorizer` fitted on the row corpus (vocabulary reflects real data)
- N-grams 1–3, English stopwords, `min_df=2`, `max_df=0.95`, `sublinear_tf=True`
- Pseudo-documents transformed through the fitted vectorizer
- Score = cosine similarity, 0.0 – 1.0

### 6.4 LLM embedding

- Each pseudo-document embedded once (≤ 100 calls per taxonomy)
- Each row embedded once
- Score = cosine in dense space (typical positive matches 0.5–0.7)
- Default threshold 0.55 (looser than TF-IDF — embedding cosines are higher on average)

### 6.5 LLM prompt

- Single chat call per row with the full taxonomy and a JSON-output schema:
  ```json
  {
    "category_scores":    [{"category_id": ..., "score": 0..1, "reasoning": "..."}],
    "sub_category_scores":[{"category_id": ..., "sub_category_id": ..., "score": 0..1, "reasoning": "..."}]
  }
  ```
- Self-reported score (model's confidence)
- Free-text `reasoning` retained as the `signals` field on each match
- Each completed row's parsed response is appended to a checkpoint NDJSON file
  so a crashed run can be resumed without losing progress

---

## 7. Cross-row keyword discovery

Surface phrases that recur across rows but aren't in the taxonomy — procedures,
software, contracts, document references, recurring sites or contractors.

Algorithm:

1. N-gram TF-IDF (1–3) over all rows
2. Document frequency in `[min_doc_count, max_doc_count]` (defaults `[5, 5000]`)
3. Filter out phrases already in any taxonomy seed
4. Score = `mean_tfidf × doc_count`, with a small boost for digit-containing
   phrases (likely codes/references)
5. Return top-N by score with sample row IDs for triage

---

## 8. Output schema (per run)

Every classify run writes into a timestamped subdirectory
`{out}/{YYYYMMDDTHHMMSS-engine[s]-hash}/`.

### Per-engine output set
| File | Filter | Format |
|------|--------|--------|
| `matches.csv` | score ≥ threshold | long |
| `category_matches.csv` | score ≥ threshold | long |
| `all_scores_sub_long.csv` | unfiltered (every row × every sub-category) | long |
| `all_scores_sub_wide.csv` | unfiltered, pivoted | wide — one col per `{category_id}.{sub_category_id}` |
| `all_scores_cat_long.csv` | unfiltered (every row × every category) | long |
| `all_scores_cat_wide.csv` | unfiltered, pivoted | wide — one col per `{category_id}` |
| `row_scores.csv` | top-1 per row | wide |
| `llm_prompt_checkpoint.ndjson` | one line per completed row (LLM prompt mode only) | NDJSON |

### Multi-engine additions (`classify-multi`)
| File | Description |
|------|-------------|
| `combined_scores_sub.csv` | one row per `(row_id × sub_category)`; columns `tfidf_score`, `llm_embed_score`, `llm_prompt_score` (only the engines run), plus `ensemble_score` = mean of the populated scores |
| `combined_scores_cat.csv` | same at category level |
| `{engine}/...` | per-engine subdirectory with the full single-engine output set |
| `keywords.csv` | shared across the multi-engine run |

### Long-format `MatchRecord` fields
| Column | Description |
|--------|-------------|
| `row_id` | Stable input row identifier |
| `category_id` | Top-level category from taxonomy |
| `sub_category_id` | Sub-category from taxonomy (empty for category-level files) |
| `score` | 0–1 confidence |
| `method` | `tfidf` \| `llm_embed` \| `llm_prompt` |
| `signals` | Matched seed terms (TF-IDF) or short rationale (LLM prompt) |
| `taxonomy_version` | For reproducibility |
| `run_id` | Pipeline run hash |

### Output ordering
- `all_scores_*_wide.csv` rows: sorted by `row_id`; columns: taxonomy declaration order
- `combined_scores_*.csv` rows: sorted by `(row_id, category_id [, sub_category_id])`
- Other long files: declaration order × row order from input

---

## 9. Usage

### Single engine

```powershell
text-classify classify-tfidf `
  --input "C:\...\issues.csv" `
  --taxonomy "C:\...\config\taxonomies\construction_root_cause.yaml" `
  --out "C:\...\outputs\text_classify" `
  --id-column issue_id `
  --text-column root_cause

# Same shape for the other engines:
text-classify classify-llm-embed  --model nomic-embed-text  ...
text-classify classify-llm-prompt --model llama3.1:8b       ...
```

### Multi-engine (run both, get side-by-side comparison + ensemble)

```powershell
text-classify classify-multi `
  --engines tfidf,llm_embed `
  --input ... --taxonomy ... --out ...
```

### Keyword discovery only

```powershell
text-classify discover-keywords --input ... --out ...
```

### Threshold choice

`default_threshold` in the taxonomy YAML is the fallback. CLI `--threshold`
overrides. Each engine has a sensible default if neither is set:

| Engine | Default threshold | Reason |
|--------|-------------------|--------|
| TF-IDF | `taxonomy.default_threshold` (0.35) | Cosine in sparse TF-IDF space — values typically 0.1–0.6 |
| LLM embed | 0.55 | Dense cosines run higher; raise threshold to compensate |
| LLM prompt | 0.5 | Self-reported confidence — middle-of-range is conservative |

You don't need to commit to a single threshold up front. The `all_scores_*`
files contain the full matrix; you can pivot in pandas/Excel and try several
thresholds without re-running.

---

## 10. Anonymity and storage

| Item | Where |
|------|-------|
| Raw issues CSV (`data/issues/*.csv`) | Local only; gitignored |
| Programme-specific taxonomy (`config/taxonomies/*.yaml`) | Gitignored; commit `.example.yaml` versions only |
| Matches / keywords outputs | `outputs/text_classify/{run_id}/`, gitignored |
| Ollama models | Local model store — no network calls per inference |

The repository should never receive raw issue text, scored outputs containing
the original text, or anything that re-identifies the programme.

---

## 11. Phased delivery

| Phase | Scope | Status |
|-------|-------|--------|
| **1 — MVP** | TF-IDF scoring + keyword discovery + CLI | ✓ done |
| **2 — Sub + Category pass** | Parallel category-level pass for fallback signals | ✓ done |
| **3 — Full matrix output** | Emit unfiltered long + wide score CSVs | ✓ done |
| **4 — LLM integration** | Ollama embed + prompt modes; checkpoint for prompt | ✓ done |
| **5 — Multi-engine + ensemble** | `classify-multi` with combined wide CSV + ensemble_score | ✓ done |
| **6 — Validation** | Label sample of 100–200 rows; tune seeds; precision/recall | open |
| **7 — Threshold sweep tooling** | CLI to plot match counts vs threshold from `all_scores_*_long.csv` | open |
| **8 — Supervised refinement** | Train a sklearn classifier from validation labels; merge with zero-shot scores | open |

---

## 12. Review findings (post-implementation)

A quick honest pass over what's built — what's solid, and what's worth doing
later but isn't yet:

| Area | Status | Notes |
|------|--------|-------|
| Taxonomy → YAML schema | Solid | Validation rejects duplicates and missing fields |
| TF-IDF scoring | Solid | Vocab from corpus; min_df scales down for tiny corpora |
| Null/empty input handling | Solid | Empty corpus returns empty result; empty rows get zero scores |
| Category-level pseudo-doc | Solid | Excludes sub-category content (avoids dilution) |
| Multi-engine combined CSV | Solid | Sorted deterministically; `ensemble_score` column added |
| Ollama failure path | Solid | Preflight ping; raises clearly if model not pulled |
| Crash recovery (LLM prompt) | Partial | NDJSON checkpoint written per row; no resume CLI yet (caller can grep) |
| Batching for LLM embed | **Missing** | One Ollama call per row. 20k rows × ~50 ms = ~17 min. Batch API would cut this 3–5×. |
| Progress indicator | **Missing** | No `tqdm`-style output for long LLM runs. User has no feedback during multi-hour prompt runs. |
| Threshold-sweep tool | **Missing** | All scores are persisted, but no built-in `analyse-thresholds` command to tabulate match counts at multiple thresholds |
| Validation / metrics CLI | **Missing** | No `eval-against-labels` command for measuring precision/recall once some rows are hand-labelled |
| Stratified keyword discovery | **Missing** | Currently global; per-top-category keyword discovery would surface category-specific recurring entities |
| Memory at 20k × 25 sub-categories × 3 engines | OK | Combined CSV is ~1.5 M rows / ~60 MB — fine for pandas, slow but openable in Excel |
| Document-level deduplication | N/A | Out of scope: input is assumed to already be one issue per row |
| Reproducibility | Solid | `run_id` includes timestamp + taxonomy fingerprint; `taxonomy_version` on every score |

The four items marked **Missing** above are the natural next slice of work
once you've validated the current outputs on real data.

---

## 13. Reuse for other taxonomies

To classify a different domain (safety incidents, customer complaints, design
review queries):

1. Create `config/taxonomies/{name}.yaml` with the same schema
2. Run `text-classify classify-tfidf --taxonomy config/taxonomies/{name}.yaml --input ...`

No code changes needed. The CLI accepts the taxonomy path as an argument so
multiple taxonomies coexist in the same repo.

---

## 14. Open decisions

1. **Threshold value(s)** — use `default_threshold: 0.35` for TF-IDF initially;
   tune from the first validation set. Different defaults per engine remain.
2. **Multi-label policy** — allow multiple matches per row (yes). Issues
   genuinely span categories; forcing single-best loses signal.
3. **Ensemble aggregation rule** — currently mean of populated engine scores.
   Alternatives: max (any-engine-flagged), product (both-engines-confident),
   or weighted. Move to weighted once we have validation data to fit weights.
4. **Embedding model** — `nomic-embed-text` as default; `bge-m3` if multilingual
   text appears.
5. **Prompt LLM** — start with whatever you already have pulled in Ollama
   (`llama3.1:8b` works well for English). Re-benchmark when a stronger 7-8B
   model becomes available.
6. **Resume contract for LLM prompt runs** — NDJSON checkpoint exists; whether
   to add an explicit `--resume` flag (rather than re-running on the unprocessed
   subset) is open.
