"""Zero-shot TF-IDF scoring: row text vs. per-(sub-)category pseudo-documents.

Two parallel passes:

* **Sub-category level** — pseudo-doc = sub.description + sub.seed_keywords
  + parent.seed_keywords. Tells you *which specific sub-category* applies.
* **Category level** — pseudo-doc = cat.description + cat.seed_keywords only
  (no sub-category content). Tells you *the parent category* applies, even
  when no single sub-category is a strong match.

Both passes share the same fitted vectorizer (vocabulary built from row corpus).
"""

from __future__ import annotations

from dataclasses import dataclass

from text_classify.preprocess import normalise, normalise_many
from text_classify.schemas import MatchRecord, RowScore, ScoringResult, Taxonomy


@dataclass
class TfidfConfig:
    threshold: float = 0.35
    ngram_min: int = 1
    ngram_max: int = 3
    min_df: int = 2
    max_df: float = 0.95
    max_features: int | None = 50_000
    top_k_signals: int = 5


def _sub_pseudo_documents(taxonomy: Taxonomy) -> list[tuple[str, str, str]]:
    """(category_id, sub_category_id, pseudo_doc_text) per sub-category."""
    out: list[tuple[str, str, str]] = []
    for cat, sub in taxonomy.iter_sub_categories():
        parts = [sub.description, *sub.seed_keywords, *cat.seed_keywords]
        out.append((cat.id, sub.id, normalise(" ".join(parts))))
    return out


def _cat_pseudo_documents(taxonomy: Taxonomy) -> list[tuple[str, str]]:
    """(category_id, pseudo_doc_text) per category (no sub-cat content)."""
    return [
        (cat.id, normalise(" ".join([cat.description, *cat.seed_keywords])))
        for cat in taxonomy.categories
    ]


def _matched_seeds(row_text_lower: str, seeds: tuple[str, ...], limit: int) -> list[str]:
    hits: list[str] = []
    for seed in seeds:
        seed_norm = seed.lower().strip()
        if seed_norm and seed_norm in row_text_lower:
            hits.append(seed)
            if len(hits) >= limit:
                break
    return hits


def score_all(
    rows: list[tuple[str, str]],
    taxonomy: Taxonomy,
    *,
    config: TfidfConfig | None = None,
    run_id: str = "",
) -> ScoringResult:
    """Score every row against every sub-category AND every category.

    Returns full matrices (unfiltered) plus threshold-filtered subsets.
    """
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "scikit-learn is required for TF-IDF classification. "
            "Install with: pip install -e \".[text]\""
        ) from exc

    cfg = config or TfidfConfig(threshold=taxonomy.default_threshold)
    threshold = cfg.threshold

    row_ids = [r[0] for r in rows]
    row_texts = normalise_many([r[1] for r in rows])
    raw_lower = [(r[1] or "").lower() for r in rows]

    result = ScoringResult(method="tfidf", threshold=threshold)

    if not any(row_texts):
        result.summaries = [RowScore(rid, None, None, 0.0, 0, "") for rid in row_ids]
        return result

    vectorizer = TfidfVectorizer(
        ngram_range=(cfg.ngram_min, cfg.ngram_max),
        min_df=cfg.min_df if len(rows) >= cfg.min_df else 1,
        max_df=cfg.max_df,
        max_features=cfg.max_features,
        stop_words="english",
        sublinear_tf=True,
    )
    row_matrix = vectorizer.fit_transform(row_texts)

    sub_pseudo = _sub_pseudo_documents(taxonomy)
    cat_pseudo = _cat_pseudo_documents(taxonomy)
    sub_matrix = vectorizer.transform([p[2] for p in sub_pseudo])
    cat_matrix = vectorizer.transform([p[1] for p in cat_pseudo])

    sub_sims = cosine_similarity(row_matrix, sub_matrix)  # (n_rows, n_subs)
    cat_sims = cosine_similarity(row_matrix, cat_matrix)  # (n_rows, n_cats)

    # Seed lookup for signal extraction
    sub_seed_lookup: dict[tuple[str, str], tuple[str, ...]] = {}
    cat_seed_lookup: dict[str, tuple[str, ...]] = {}
    for cat, sub in taxonomy.iter_sub_categories():
        sub_seed_lookup[(cat.id, sub.id)] = sub.seed_keywords + cat.seed_keywords
    for cat in taxonomy.categories:
        cat_seed_lookup[cat.id] = cat.seed_keywords

    # ------- Sub-category pass: full matrix and filtered matches
    for i, row_id in enumerate(row_ids):
        for j, (cat_id, sub_id, _) in enumerate(sub_pseudo):
            s = float(sub_sims[i, j])
            signals = ""
            if s >= threshold:
                signals = "; ".join(
                    _matched_seeds(raw_lower[i], sub_seed_lookup[(cat_id, sub_id)], cfg.top_k_signals)
                )
            record = MatchRecord(
                row_id=row_id,
                category_id=cat_id,
                sub_category_id=sub_id,
                score=round(s, 4),
                method="tfidf",
                signals=signals,
                taxonomy_version=taxonomy.version,
                run_id=run_id,
            )
            result.all_sub_scores.append(record)
            if s >= threshold:
                result.sub_matches.append(record)

    # ------- Category pass: full matrix and filtered matches
    for i, row_id in enumerate(row_ids):
        for k, (cat_id, _) in enumerate(cat_pseudo):
            s = float(cat_sims[i, k])
            signals = ""
            if s >= threshold:
                signals = "; ".join(
                    _matched_seeds(raw_lower[i], cat_seed_lookup[cat_id], cfg.top_k_signals)
                )
            record = MatchRecord(
                row_id=row_id,
                category_id=cat_id,
                sub_category_id="",
                score=round(s, 4),
                method="tfidf",
                signals=signals,
                taxonomy_version=taxonomy.version,
                run_id=run_id,
            )
            result.all_cat_scores.append(record)
            if s >= threshold:
                result.cat_matches.append(record)

    # ------- Per-row summary (based on the sub-category pass)
    for i, row_id in enumerate(row_ids):
        row_scores = sub_sims[i]
        if len(row_scores) == 0:
            result.summaries.append(RowScore(row_id, None, None, 0.0, 0, ""))
            continue
        top_idx = int(row_scores.argmax())
        top_score = float(row_scores[top_idx])
        n_above = int((row_scores >= threshold).sum())
        top_pairs = sorted(
            (
                (sub_pseudo[j][0], sub_pseudo[j][1], float(row_scores[j]))
                for j in range(len(sub_pseudo))
                if row_scores[j] >= threshold
            ),
            key=lambda p: -p[2],
        )[:5]
        summary_str = "; ".join(f"{c}/{s}:{score:.2f}" for c, s, score in top_pairs)
        result.summaries.append(
            RowScore(
                row_id=row_id,
                top_category_id=sub_pseudo[top_idx][0],
                top_sub_category_id=sub_pseudo[top_idx][1],
                top_score=round(top_score, 4),
                n_matches_above_threshold=n_above,
                match_summary=summary_str,
            )
        )

    return result


# Back-compat shim — older callers (and existing tests) expect (matches, summaries)
def score_rows(
    rows: list[tuple[str, str]],
    taxonomy: Taxonomy,
    *,
    config: TfidfConfig | None = None,
    run_id: str = "",
) -> tuple[list[MatchRecord], list[RowScore]]:
    result = score_all(rows, taxonomy, config=config, run_id=run_id)
    return result.sub_matches, result.summaries
