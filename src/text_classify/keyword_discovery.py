"""Cross-row phrase mining — surface recurring terms that aren't in the taxonomy.

Looking for things like document numbers, procedure names, software, contracts,
or specific contractors that show up in multiple rows but wouldn't be picked
up by category seed terms.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from text_classify.preprocess import normalise_many
from text_classify.schemas import KeywordRecord, Taxonomy


@dataclass
class KeywordConfig:
    min_doc_count: int = 5      # phrase must appear in at least this many rows
    max_doc_count: int = 5000   # ignore very common phrases
    ngram_min: int = 1
    ngram_max: int = 3
    max_results: int = 500
    bias_alphanumeric: bool = True   # boost phrases containing digits / ALLCAPS
    sample_row_limit: int = 5        # row IDs to attach per phrase


_HAS_DIGIT = re.compile(r"\d")
_ALLCAPS_TOKEN = re.compile(r"\b[A-Z]{2,}\b")


def _taxonomy_seed_phrases(taxonomy: Taxonomy) -> set[str]:
    """Normalised set of every seed term used anywhere in the taxonomy."""
    seeds: set[str] = set()
    for cat in taxonomy.categories:
        for term in cat.seed_keywords:
            seeds.add(term.lower().strip())
        for sub in cat.sub_categories:
            for term in sub.seed_keywords:
                seeds.add(term.lower().strip())
    return seeds


def discover(
    rows: list[tuple[str, str]],
    *,
    config: KeywordConfig | None = None,
    taxonomy: Taxonomy | None = None,
) -> list[KeywordRecord]:
    """Return ranked list of cross-row phrases.

    ``rows`` = list of ``(row_id, raw_text)``. ``taxonomy`` (optional) suppresses
    phrases that exactly match a known seed term.
    """
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "scikit-learn is required for keyword discovery. "
            "Install with: pip install -e \".[text]\""
        ) from exc

    cfg = config or KeywordConfig()
    row_ids = [r[0] for r in rows]
    raw_texts = [r[1] or "" for r in rows]
    norm_texts = normalise_many(raw_texts)

    if not any(norm_texts):
        return []

    suppress = _taxonomy_seed_phrases(taxonomy) if taxonomy else set()

    # Document frequency via the binary vectorizer is sufficient for counting,
    # but we also want the TF-IDF weights to rank interestingness.
    min_df = min(cfg.min_doc_count, len(rows))
    vectorizer = TfidfVectorizer(
        ngram_range=(cfg.ngram_min, cfg.ngram_max),
        min_df=min_df,
        max_df=cfg.max_doc_count / max(len(rows), 1),
        stop_words="english",
        sublinear_tf=True,
    )
    try:
        matrix = vectorizer.fit_transform(norm_texts)
    except ValueError:
        # min_df too high relative to corpus — corpus is too small
        return []

    vocab = vectorizer.get_feature_names_out()
    # Document frequency per term: count of non-zero entries per column
    df_arr = (matrix > 0).sum(axis=0).A1  # noqa: PD011 (numpy array, not pandas)
    mean_tfidf_arr = matrix.mean(axis=0).A1
    # Map term -> row IDs where it appears (sample up to limit)
    term_to_rows: dict[int, list[str]] = defaultdict(list)
    coo = matrix.tocoo()
    for r, c in zip(coo.row, coo.col, strict=False):
        if len(term_to_rows[int(c)]) < cfg.sample_row_limit:
            term_to_rows[int(c)].append(row_ids[r])

    candidates: list[KeywordRecord] = []
    for col, term in enumerate(vocab):
        doc_count = int(df_arr[col])
        if doc_count < cfg.min_doc_count or doc_count > cfg.max_doc_count:
            continue
        if term in suppress:
            continue
        mean_tfidf = float(mean_tfidf_arr[col])
        interest = mean_tfidf * doc_count
        # Bias toward phrases that look like entity references (codes / acronyms)
        if cfg.bias_alphanumeric:
            if _HAS_DIGIT.search(term):
                interest *= 1.5
            # Acronym shape — check ORIGINAL casing across rows
            if any(_ALLCAPS_TOKEN.search(raw_texts[row_ids.index(rid)] or "")
                   for rid in term_to_rows[col][:1]):
                interest *= 1.1
        candidates.append(
            KeywordRecord(
                phrase=term,
                doc_count=doc_count,
                mean_tfidf=round(mean_tfidf, 5),
                interestingness=round(interest, 5),
                sample_row_ids=list(term_to_rows[col]),
            )
        )

    candidates.sort(key=lambda k: -k.interestingness)
    return candidates[: cfg.max_results]
