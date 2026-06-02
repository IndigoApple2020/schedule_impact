"""Zero-shot TF-IDF scoring: row text vs. per-sub-category pseudo-documents.

For each ``(category, sub_category)``, we build a pseudo-document from the
sub-category description + seed_keywords (plus the parent category's seeds for
context). The fitted vectorizer is built from the **row corpus only** so the
vocabulary reflects the real data; the pseudo-documents are then transformed
through that same vectorizer and compared by cosine similarity.

The seed_keywords double as the "signals" reported on each match — for any
match above threshold we list which seed words actually appeared in the row.
"""

from __future__ import annotations

from dataclasses import dataclass

from text_classify.preprocess import normalise, normalise_many
from text_classify.schemas import MatchRecord, RowScore, Taxonomy


@dataclass
class TfidfConfig:
    threshold: float = 0.35
    ngram_min: int = 1
    ngram_max: int = 3
    min_df: int = 2          # ignore terms appearing in fewer rows than this
    max_df: float = 0.95     # ignore terms appearing in more than this fraction of rows
    max_features: int | None = 50_000
    top_k_signals: int = 5   # seed words to report per match


def _pseudo_documents(taxonomy: Taxonomy) -> list[tuple[str, str, str]]:
    """Return list of (category_id, sub_category_id, pseudo_doc_text).

    Pseudo-doc = sub.description + sub.seed_keywords + parent.seed_keywords.
    Parent label/description is intentionally NOT included — it would dilute
    the discriminative power between sibling sub-categories. Parent seeds are
    included only as weaker context.
    """
    out: list[tuple[str, str, str]] = []
    for cat, sub in taxonomy.iter_sub_categories():
        parts = [sub.description]
        parts.extend(sub.seed_keywords)
        parts.extend(cat.seed_keywords)
        out.append((cat.id, sub.id, normalise(" ".join(parts))))
    return out


def _matched_seeds(row_text_lower: str, seeds: tuple[str, ...], limit: int) -> list[str]:
    """Return up to ``limit`` seed phrases that appear (substring) in the row."""
    hits: list[str] = []
    for seed in seeds:
        seed_norm = seed.lower().strip()
        if not seed_norm:
            continue
        if seed_norm in row_text_lower:
            hits.append(seed)
            if len(hits) >= limit:
                break
    return hits


def score_rows(
    rows: list[tuple[str, str]],
    taxonomy: Taxonomy,
    *,
    config: TfidfConfig | None = None,
    run_id: str = "",
) -> tuple[list[MatchRecord], list[RowScore]]:
    """Score every (row × sub_category) pair and return matches above threshold.

    ``rows``: list of ``(row_id, raw_text)`` tuples.

    Returns ``(matches, per_row_summaries)``:
      * ``matches``  — every (row, sub_category) pair with score >= threshold
      * ``per_row_summaries`` — one ``RowScore`` per input row (top category + count)
    """
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError as exc:  # pragma: no cover - dependency error path
        raise RuntimeError(
            "scikit-learn is required for TF-IDF classification. "
            "Install with: pip install -e \".[text]\""
        ) from exc

    cfg = config or TfidfConfig(threshold=taxonomy.default_threshold)
    threshold = cfg.threshold

    row_ids = [r[0] for r in rows]
    row_texts = normalise_many([r[1] for r in rows])
    raw_lower = [(r[1] or "").lower() for r in rows]  # preserve original casing/punct for signal hits

    if not any(row_texts):
        # Empty corpus — return nothing rather than crash
        return [], [RowScore(rid, None, None, 0.0, 0, "") for rid in row_ids]

    vectorizer = TfidfVectorizer(
        ngram_range=(cfg.ngram_min, cfg.ngram_max),
        min_df=cfg.min_df if len(rows) >= cfg.min_df else 1,
        max_df=cfg.max_df,
        max_features=cfg.max_features,
        stop_words="english",
        sublinear_tf=True,
    )
    row_matrix = vectorizer.fit_transform(row_texts)

    pseudo = _pseudo_documents(taxonomy)
    seed_matrix = vectorizer.transform([p[2] for p in pseudo])

    sims = cosine_similarity(row_matrix, seed_matrix)  # shape: (n_rows, n_subs)

    # Index seeds for signal extraction
    sub_seed_lookup: dict[tuple[str, str], tuple[str, ...]] = {}
    for cat, sub in taxonomy.iter_sub_categories():
        sub_seed_lookup[(cat.id, sub.id)] = sub.seed_keywords + cat.seed_keywords

    matches: list[MatchRecord] = []
    summaries: list[RowScore] = []
    for i, row_id in enumerate(row_ids):
        row_scores = sims[i]
        top_idx: int | None = None
        top_score = 0.0
        n_above = 0
        top_pairs: list[tuple[str, str, float]] = []

        for j, (cat_id, sub_id, _) in enumerate(pseudo):
            s = float(row_scores[j])
            if s > top_score:
                top_score = s
                top_idx = j
            if s >= threshold:
                n_above += 1
                signals = _matched_seeds(
                    raw_lower[i],
                    sub_seed_lookup[(cat_id, sub_id)],
                    cfg.top_k_signals,
                )
                matches.append(
                    MatchRecord(
                        row_id=row_id,
                        category_id=cat_id,
                        sub_category_id=sub_id,
                        score=round(s, 4),
                        method="tfidf",
                        signals="; ".join(signals),
                        taxonomy_version=taxonomy.version,
                        run_id=run_id,
                    )
                )
                top_pairs.append((cat_id, sub_id, s))

        # Compact human-readable summary of matches above threshold
        top_pairs.sort(key=lambda p: -p[2])
        summary_str = "; ".join(f"{c}/{s}:{score:.2f}" for c, s, score in top_pairs[:5])
        top_cat = pseudo[top_idx][0] if top_idx is not None else None
        top_sub = pseudo[top_idx][1] if top_idx is not None else None
        summaries.append(
            RowScore(
                row_id=row_id,
                top_category_id=top_cat,
                top_sub_category_id=top_sub,
                top_score=round(top_score, 4),
                n_matches_above_threshold=n_above,
                match_summary=summary_str,
            )
        )

    return matches, summaries
