# Strategy — Text Classification (Root Cause taxonomy)

A generic, taxonomy-driven text classification toolkit sitting alongside the
schedule analytics package. The first use case is **construction issues log**
→ **root cause categories + sub-categories**, but the design is reusable for
any single-input-text, multi-label-against-taxonomy classification problem.

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
| **TF-IDF** | scikit-learn (zero-shot via seed terms + descriptions) | Fast, deterministic, fully offline, audit-friendly | No |
| **LLM** | Ollama against a local open-source model | Catches paraphrase / domain-specific language the seeds miss | No (local) |

The two methods produce **interchangeable output records** so downstream
analysis is identical regardless of which engine ran.

---

## 2. Guiding principles

| Principle | Implication |
|-----------|-------------|
| **Generic codebase, specific taxonomy** | Taxonomy is a YAML file the user controls; code makes no assumptions about category names |
| **Anonymity preserved** | Both engines run locally; sensitive issue text never leaves the laptop |
| **Auditable** | Every score is reproducible from a logged config + commit SHA; signals (matched terms / model rationale) are stored |
| **Same output schema across engines** | Downstream tools and review queues don't care which method ran |
| **Iterate via review queue** | Borderline-score rows feed a labelling workflow; labels improve seeds and (optionally) train a supervised model later |

---

## 3. Taxonomy schema

Stored as YAML in `config/taxonomies/`. Generic structure:

```yaml
name: construction_root_cause
version: 1
default_threshold: 0.35      # any score >= this is reported as a match

categories:
  - id: poor_planning_design
    label: "Poor Planning and design"
    description: >-
      Issues caused by design errors, incomplete plans, late changes,
      or design–delivery handoff problems.
    seed_keywords:
      - design
      - drawing
      - specification
      - clash
      - rework due to design
    sub_categories:
      - id: inadequate_design
        label: "Inadequate design"
        description: "Design itself is flawed, incomplete, or misses requirements."
        seed_keywords:
          - incomplete design
          - design error
          - missing detail
      - id: design_delivery_miscom
        label: "Miscommunication between design and delivery teams"
        description: "Handoff problems between designers and site teams."
        seed_keywords: [...]
      - id: late_design_change
        label: "Changes to design during delivery"
        description: "Design changed after construction had started."
        seed_keywords: [...]
```

The `description` and `seed_keywords` serve **both engines**:

- **TF-IDF** combines them into a per-(sub-)category pseudo-document and scores
  each row by cosine similarity against that pseudo-document
- **LLM** sends them as the category definition in the prompt

This means tuning the taxonomy improves both engines simultaneously.

---

## 4. Pipeline

```
input.csv (row_id, root_cause_text, ...)
        │
        ▼
┌─────────────────┐
│  preprocess     │  lowercase, strip punctuation (preserve hyphens/codes),
│                 │  optional stemming, normalise whitespace
└────────┬────────┘
         │
         ├──────────────────────────────────────┐
         ▼                                      ▼
┌─────────────────┐                  ┌──────────────────────┐
│ TF-IDF scorer   │                  │ LLM scorer (Ollama)  │
│  • fit on rows  │                  │  • prompt or embed   │
│  • build seed   │                  │  • per category      │
│    pseudo-docs  │                  │  • JSON output       │
│  • cosine sim   │                  └──────────────────────┘
└────────┬────────┘                              │
         │                                       │
         └──────────────┬────────────────────────┘
                        ▼
              ┌──────────────────┐
              │  match records   │  (row_id, category_id, sub_category_id,
              │  above threshold │   score, signals, method)
              └────────┬─────────┘
                       │
                       ├─────────► matches.csv
                       │
                       ▼
              ┌──────────────────┐
              │ keyword discover │  cross-row n-gram mining of phrases
              │                  │  outside the taxonomy
              └─────────┬────────┘
                        ▼
                  keywords.csv
```

---

## 5. TF-IDF scoring (primary)

**Why zero-shot:** the 20k rows are unlabelled. Training a supervised classifier
without labels is not possible; we use seed terms + descriptions as anchor
"pseudo-documents" instead.

**Algorithm:**

1. **Fit** a `TfidfVectorizer` on the row texts only (vocabulary reflects the
   actual data; n-gram range 1–3; English stopwords; min_df=2 to drop noise)
2. For each `(category, sub_category)`, build a pseudo-document:
   `description + " " + " ".join(seed_keywords)`
3. **Transform** each pseudo-document with the fitted vectorizer
4. Score each row against each pseudo-document via **cosine similarity** → 0–1
5. Apply the threshold; record matches with their score and the top-K matched
   terms from the row vs. the seed (the "signals")

**Pros:** fast (20k rows × ~25 sub-categories in seconds), deterministic,
auditable (matched terms are concrete), no external dependencies.

**Limits:** can miss paraphrases. Tuning is via the seed keywords and
descriptions — users iterate by reading low-score rows that should have matched.

---

## 6. Cross-row keyword discovery

**Goal:** surface phrases that recur across rows but aren't in the taxonomy —
procedures, software names, contract numbers, document references, recurring
sites or contractors.

**Algorithm:**

1. Tokenise each row, retaining hyphenated / alphanumeric tokens (codes)
2. Compute n-gram frequencies (1–3 grams) across the corpus
3. Filter candidates:
   - Document frequency in `[min_docs, max_docs]` (default `[5, 5000]` —
     not too rare, not too generic for a 20k corpus)
   - Not in the configured stopword list
   - Optional bias toward "interesting" shapes: contains a digit, ALLCAPS
     acronym, or starts with a capital
4. Optionally remove n-grams that are entirely subsumed by a taxonomy seed
5. Rank by `document_frequency × mean_tfidf` (recurring **and** discriminative)

**Output:** `keywords.csv` with `phrase, doc_count, sample_row_ids` (a few row IDs
where the phrase appears, for manual triage).

---

## 7. LLM scoring (Ollama)

**Two modes:**

### 7a. Prompt-based classification (simpler, more accurate)

For each row, send a single chat prompt with:

- The full taxonomy (labels + descriptions, no seeds needed)
- The row text
- A schema instruction to return JSON: `{ "matches": [{category_id, sub_category_id, score, reasoning}] }`

Use a model that supports structured/JSON output well (e.g. `llama3.1:8b`,
`qwen2.5:7b`, `mistral`).

**Pros:** captures paraphrases; "reasoning" field gives a free-text rationale
for review. **Cons:** slower (~1–3 s per row at 20k rows = hours on a laptop);
non-deterministic; needs JSON validation.

### 7b. Embedding similarity (faster, less nuanced)

1. Embed each row text once using Ollama's embedding model (e.g. `nomic-embed-text`)
2. Embed each `(description + seeds)` pseudo-document once
3. Cosine similarity → score per row × sub-category

This is essentially the TF-IDF approach but in a dense embedding space — better
at semantic matches, but the score interpretation differs (typical cosines are
0.3–0.6 even for matches; threshold needs separate tuning per model).

**Recommendation:** start with **7b** for speed and use **7a** only on rows that
the TF-IDF and embedding scorers disagree about (smart triage).

---

## 8. Output schema

Every classify run writes a full set of CSVs into a timestamped subdirectory.
The threshold-filtered files are conveniences; the `all_scores_*` files contain
**every** row × target pair so you can apply your own thresholding downstream.

### Sub-category level
| File | Filter | Format |
|------|--------|--------|
| `matches.csv` | score ≥ threshold | long |
| `all_scores_sub_long.csv` | unfiltered (every row × every sub-category) | long |
| `all_scores_sub_wide.csv` | unfiltered, pivoted | wide — one column per `{category_id}.{sub_category_id}` |

### Category level (parallel pass)
| File | Filter | Format |
|------|--------|--------|
| `category_matches.csv` | score ≥ threshold | long |
| `all_scores_cat_long.csv` | unfiltered | long |
| `all_scores_cat_wide.csv` | unfiltered, pivoted | wide — one column per `{category_id}` |

### Per-row summary and side outputs
| File | Description |
|------|-------------|
| `row_scores.csv` | top-1 sub-category match per row |
| `keywords.csv` | cross-row phrase discovery |

### Multi-engine runs (`classify-multi`)
| File | Description |
|------|-------------|
| `combined_scores_sub.csv` | one row per (row × sub_category), columns: `tfidf_score`, `llm_embed_score`, `llm_prompt_score` (only the engines run) |
| `combined_scores_cat.csv` | same at category level |

Plus a per-engine subdirectory (`tfidf/`, `llm_embed/`, ...) with the full
single-engine output set.

### Long format example (`all_scores_sub_long.csv`)

| Column | Description |
|--------|-------------|
| `row_id` | Stable input row identifier |
| `category_id` | Top-level category from taxonomy |
| `sub_category_id` | Sub-category from taxonomy (empty for category-level files) |
| `score` | 0–1 confidence (cosine in TF-IDF / embed space; LLM-self-reported for prompt mode) |
| `method` | `tfidf` \| `llm_embed` \| `llm_prompt` |
| `signals` | Matched seed terms (TF-IDF) or short rationale (LLM prompt) |
| `taxonomy_version` | For reproducibility |
| `run_id` | Pipeline run hash |

### Why a separate category-level pass

A row whose text matches the *spirit* of a category (e.g. "design issues
across the package") but doesn't name any specific sub-category will score
weakly at sub-category level but strongly at category level. Running both
lets you decide whether to fall back to the category when no sub-category
clears the threshold.

`keywords.csv` (cross-row keyword discovery):

| Column | Description |
|--------|-------------|
| `row_id` | Stable input row identifier |
| `category_id` | Top-level category from taxonomy |
| `sub_category_id` | Sub-category from taxonomy |
| `score` | 0–1 confidence |
| `method` | `tfidf` \| `llm_embed` \| `llm_prompt` |
| `signals` | Matched terms (tfidf) or short rationale (llm) — semicolon-delimited |
| `taxonomy_version` | Version field from the YAML, for reproducibility |
| `run_id` | Pipeline run hash |

`row_scores.csv` (wide format, one row per input row):

| Column | Description |
|--------|-------------|
| `row_id` | |
| `top_category_id` | Highest-scoring category overall |
| `top_score` | |
| `n_matches_above_threshold` | |
| `match_summary` | `cat1:0.62; cat2:0.41` (compact human-readable) |

`keywords.csv` columns:

| Column | Description |
|--------|-------------|
| `phrase` | Discovered phrase (1–3 gram) |
| `doc_count` | Number of rows it appears in |
| `mean_tfidf` | Average TF-IDF weight where it appears |
| `interestingness` | `doc_count × mean_tfidf` |
| `sample_row_ids` | Up to N row IDs (for triage) |

---

## 9. Reuse for other taxonomies

To classify a different domain (e.g. safety incidents, customer complaints):

1. Create a new `config/taxonomies/{name}.yaml` with the same schema
2. Run `text-classify classify-tfidf --taxonomy config/taxonomies/{name}.yaml --input ...`

No code changes needed. The CLI takes the taxonomy path as an argument so
multiple taxonomies coexist.

---

## 10. Phased delivery

| Phase | Scope | Deliverables |
|-------|-------|--------------|
| **1 — MVP** | TF-IDF scoring + keyword discovery + CLI | `classify-tfidf`, `discover-keywords`; matches.csv, keywords.csv |
| **2 — Validation** | Label sample of 100–200 rows; tune seeds | Precision/recall report per category |
| **3 — LLM** | Ollama integration (embed + prompt) | `classify-llm`; same output shape as TF-IDF |
| **4 — Ensemble** | Combine TF-IDF + LLM scores (mean / max / vote) | `classify-ensemble`; routing for borderline rows |
| **5 — Supervised** | Train scikit-learn classifier from labelled rows | `train-classifier`; per-category precision targets |

This document describes phase 1 + 3 as the initial build.

---

## 11. Anonymity and storage

| Item | Where |
|------|-------|
| Raw issues CSV (`data/issues/*.csv`) | Local only; gitignored |
| Taxonomy YAML | Committed (generic vocabulary); programme-specific taxonomies in gitignored `config/taxonomies/*.yaml` (committed examples carry `.example.yaml` suffix) |
| Matches / keywords outputs | `outputs/text_classify/{run_id}/`, gitignored |
| Ollama models | Local model store (no network calls per inference) |

The repository should never receive raw issue text, scored outputs containing
the original text, or anything that re-identifies the programme.

---

## 12. Open decisions

1. **Threshold value** — use `default_threshold: 0.35` for TF-IDF initially;
   tune from validation set
2. **Multi-label policy** — allow multiple matches per row (yes) or force
   single-best (no — issues genuinely span categories)
3. **Stemming** — off by default; enable if seed words and row words use
   different morphological forms heavily
4. **Embedding model choice** for phase 3 — `nomic-embed-text` is a sensible
   default; `bge-m3` for multilingual
5. **Prompt LLM choice** — start with whatever the user already has pulled in
   Ollama; benchmark accuracy vs. cost (latency)
