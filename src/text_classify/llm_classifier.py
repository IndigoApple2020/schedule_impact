"""Ollama-backed classification — embedding and structured-prompt modes.

Both modes are fully local: Ollama runs on the laptop and no row text leaves
the machine.

* :func:`score_all_embed`  — cosine similarity of row embedding vs. per-target
  pseudo-document embedding. Cheap; runs both sub-category and category passes.
* :func:`score_all_prompt` — per-row chat completion with the full taxonomy
  and a JSON-output instruction. Slower (~1-3 s/row) but catches paraphrases
  and produces a free-text rationale.

Both return :class:`ScoringResult` with full matrices and threshold-filtered
match lists for sub-category and category levels.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from typing import Any

from text_classify.preprocess import normalise
from text_classify.schemas import MatchRecord, RowScore, ScoringResult, Taxonomy

_logger = logging.getLogger(__name__)


@dataclass
class LlmEmbedConfig:
    model: str = "nomic-embed-text"
    threshold: float = 0.55
    host: str = "http://localhost:11434"


@dataclass
class LlmPromptConfig:
    model: str = "llama3.1:8b"
    threshold: float = 0.5
    host: str = "http://localhost:11434"
    temperature: float = 0.0
    max_retries: int = 2


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _sub_pseudo_doc(cat_seeds: tuple[str, ...], sub_label: str, sub_desc: str, sub_seeds: tuple[str, ...]) -> str:
    return ". ".join(p for p in [sub_label, sub_desc, *sub_seeds, *cat_seeds] if p)


def _cat_pseudo_doc(cat_label: str, cat_desc: str, cat_seeds: tuple[str, ...]) -> str:
    return ". ".join(p for p in [cat_label, cat_desc, *cat_seeds] if p)


# ---------------------------------------------------------------------------
# Embedding mode
# ---------------------------------------------------------------------------


def score_all_embed(
    rows: list[tuple[str, str]],
    taxonomy: Taxonomy,
    *,
    config: LlmEmbedConfig | None = None,
    run_id: str = "",
) -> ScoringResult:
    """Score every row × (sub-category, category) by Ollama-embedding cosine."""
    try:
        import ollama
    except ImportError as exc:
        raise RuntimeError(
            "The 'ollama' package is required. Install with: pip install -e \".[llm]\""
        ) from exc

    cfg = config or LlmEmbedConfig()
    client = ollama.Client(host=cfg.host)

    # Build target lists
    sub_meta: list[tuple[str, str]] = []
    sub_texts: list[str] = []
    for cat, sub in taxonomy.iter_sub_categories():
        sub_meta.append((cat.id, sub.id))
        sub_texts.append(_sub_pseudo_doc(cat.seed_keywords, sub.label, sub.description, sub.seed_keywords))

    cat_meta: list[str] = []
    cat_texts: list[str] = []
    for cat in taxonomy.categories:
        cat_meta.append(cat.id)
        cat_texts.append(_cat_pseudo_doc(cat.label, cat.description, cat.seed_keywords))

    # Embed targets once
    sub_vectors = [list(client.embeddings(model=cfg.model, prompt=t)["embedding"]) for t in sub_texts]
    cat_vectors = [list(client.embeddings(model=cfg.model, prompt=t)["embedding"]) for t in cat_texts]

    result = ScoringResult(method="llm_embed", threshold=cfg.threshold)

    for row_id, raw_text in rows:
        text = normalise(raw_text)
        if not text:
            result.summaries.append(RowScore(row_id, None, None, 0.0, 0, ""))
            # Emit zero-score rows for completeness
            for cat_id, sub_id in sub_meta:
                result.all_sub_scores.append(MatchRecord(row_id, cat_id, sub_id, 0.0, "llm_embed",
                                                         "", taxonomy.version, run_id))
            for cat_id in cat_meta:
                result.all_cat_scores.append(MatchRecord(row_id, cat_id, "", 0.0, "llm_embed",
                                                         "", taxonomy.version, run_id))
            continue

        row_vec = list(client.embeddings(model=cfg.model, prompt=text)["embedding"])

        # Sub-cat scores
        sub_row_scores: list[float] = []
        for j, (cat_id, sub_id) in enumerate(sub_meta):
            s = _cosine(row_vec, sub_vectors[j])
            sub_row_scores.append(s)
            rec = MatchRecord(row_id, cat_id, sub_id, round(s, 4), "llm_embed",
                              f"model={cfg.model}", taxonomy.version, run_id)
            result.all_sub_scores.append(rec)
            if s >= cfg.threshold:
                result.sub_matches.append(rec)

        # Cat scores
        for k, cat_id in enumerate(cat_meta):
            s = _cosine(row_vec, cat_vectors[k])
            rec = MatchRecord(row_id, cat_id, "", round(s, 4), "llm_embed",
                              f"model={cfg.model}", taxonomy.version, run_id)
            result.all_cat_scores.append(rec)
            if s >= cfg.threshold:
                result.cat_matches.append(rec)

        # Per-row summary based on sub-cat pass
        top_idx = max(range(len(sub_row_scores)), key=lambda j: sub_row_scores[j])
        top_score = sub_row_scores[top_idx]
        n_above = sum(1 for s in sub_row_scores if s >= cfg.threshold)
        top_pairs = sorted(
            ((sub_meta[j][0], sub_meta[j][1], sub_row_scores[j])
             for j in range(len(sub_row_scores)) if sub_row_scores[j] >= cfg.threshold),
            key=lambda p: -p[2],
        )[:5]
        summary_str = "; ".join(f"{c}/{s}:{score:.2f}" for c, s, score in top_pairs)
        result.summaries.append(RowScore(
            row_id=row_id,
            top_category_id=sub_meta[top_idx][0],
            top_sub_category_id=sub_meta[top_idx][1],
            top_score=round(top_score, 4),
            n_matches_above_threshold=n_above,
            match_summary=summary_str,
        ))

    return result


# ---------------------------------------------------------------------------
# Prompt mode
# ---------------------------------------------------------------------------

_PROMPT_TEMPLATE = """You are classifying an issue against a fixed taxonomy.

TAXONOMY:
{taxonomy}

ISSUE TEXT:
\"\"\"{text}\"\"\"

Return ONLY a JSON object with this exact shape:
{{
  "category_scores": [
    {{"category_id": "...", "score": 0.0, "reasoning": "..."}}
  ],
  "sub_category_scores": [
    {{"category_id": "...", "sub_category_id": "...", "score": 0.0, "reasoning": "..."}}
  ]
}}

Rules:
- Score is 0.0 to 1.0 (your confidence the category or sub-category applies).
- Include EVERY category and EVERY sub-category in the taxonomy, with its score.
- "reasoning" must be a single short sentence (max 25 words). Empty string when score = 0.
- Output JSON only — no commentary, no code fences.
"""


def _format_taxonomy_for_prompt(taxonomy: Taxonomy) -> str:
    lines: list[str] = []
    for cat in taxonomy.categories:
        lines.append(f"- {cat.id} ({cat.label}): {cat.description}")
        for sub in cat.sub_categories:
            lines.append(f"    * {sub.id} ({sub.label}): {sub.description}")
    return "\n".join(lines)


def _parse_response(content: str) -> dict[str, list[dict[str, Any]]]:
    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.startswith("json"):
            content = content[len("json"):]
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return {"category_scores": [], "sub_category_scores": []}
    if not isinstance(parsed, dict):
        return {"category_scores": [], "sub_category_scores": []}
    return {
        "category_scores": parsed.get("category_scores") or [],
        "sub_category_scores": parsed.get("sub_category_scores") or [],
    }


def score_all_prompt(
    rows: list[tuple[str, str]],
    taxonomy: Taxonomy,
    *,
    config: LlmPromptConfig | None = None,
    run_id: str = "",
) -> ScoringResult:
    """Per-row chat completion with structured JSON output for every category & sub-category."""
    try:
        import ollama
    except ImportError as exc:
        raise RuntimeError(
            "The 'ollama' package is required. Install with: pip install -e \".[llm]\""
        ) from exc

    cfg = config or LlmPromptConfig()
    client = ollama.Client(host=cfg.host)
    tax_text = _format_taxonomy_for_prompt(taxonomy)

    valid_sub_pairs = {(c.id, s.id) for c, s in taxonomy.iter_sub_categories()}
    valid_cat_ids = {c.id for c in taxonomy.categories}

    result = ScoringResult(method="llm_prompt", threshold=cfg.threshold)

    for row_id, raw_text in rows:
        if not raw_text or not str(raw_text).strip():
            result.summaries.append(RowScore(row_id, None, None, 0.0, 0, ""))
            continue
        prompt = _PROMPT_TEMPLATE.format(taxonomy=tax_text, text=str(raw_text).strip())

        parsed = {"category_scores": [], "sub_category_scores": []}
        for attempt in range(cfg.max_retries + 1):
            try:
                resp = client.chat(
                    model=cfg.model,
                    messages=[{"role": "user", "content": prompt}],
                    options={"temperature": cfg.temperature},
                    format="json",
                )
                parsed = _parse_response(resp["message"]["content"])
                if parsed["sub_category_scores"] or parsed["category_scores"]:
                    break
            except Exception as exc:
                _logger.warning("LLM call failed for row %s (attempt %d): %s", row_id, attempt + 1, exc)

        # Sub-category records
        sub_scores_seen: dict[tuple[str, str], float] = {}
        sub_pairs: list[tuple[str, str, float]] = []
        for m in parsed["sub_category_scores"]:
            cat_id = m.get("category_id")
            sub_id = m.get("sub_category_id")
            score = float(m.get("score", 0.0))
            reasoning = str(m.get("reasoning", ""))[:200]
            if (cat_id, sub_id) not in valid_sub_pairs:
                continue
            sub_scores_seen[(cat_id, sub_id)] = score
            rec = MatchRecord(row_id, cat_id, sub_id, round(score, 4), "llm_prompt",
                              reasoning, taxonomy.version, run_id)
            result.all_sub_scores.append(rec)
            if score >= cfg.threshold:
                result.sub_matches.append(rec)
                sub_pairs.append((cat_id, sub_id, score))

        # Emit zero-score rows for any sub-cat the model didn't include
        for cat_id, sub_id in valid_sub_pairs:
            if (cat_id, sub_id) not in sub_scores_seen:
                result.all_sub_scores.append(
                    MatchRecord(row_id, cat_id, sub_id, 0.0, "llm_prompt",
                                "", taxonomy.version, run_id)
                )

        # Category records
        cat_scores_seen: set[str] = set()
        for m in parsed["category_scores"]:
            cat_id = m.get("category_id")
            score = float(m.get("score", 0.0))
            reasoning = str(m.get("reasoning", ""))[:200]
            if cat_id not in valid_cat_ids:
                continue
            cat_scores_seen.add(cat_id)
            rec = MatchRecord(row_id, cat_id, "", round(score, 4), "llm_prompt",
                              reasoning, taxonomy.version, run_id)
            result.all_cat_scores.append(rec)
            if score >= cfg.threshold:
                result.cat_matches.append(rec)
        for cat_id in valid_cat_ids - cat_scores_seen:
            result.all_cat_scores.append(
                MatchRecord(row_id, cat_id, "", 0.0, "llm_prompt", "", taxonomy.version, run_id)
            )

        # Per-row summary
        sub_pairs.sort(key=lambda p: -p[2])
        top_score = sub_pairs[0][2] if sub_pairs else 0.0
        summary_str = "; ".join(f"{c}/{s}:{score:.2f}" for c, s, score in sub_pairs[:5])
        result.summaries.append(RowScore(
            row_id=row_id,
            top_category_id=sub_pairs[0][0] if sub_pairs else None,
            top_sub_category_id=sub_pairs[0][1] if sub_pairs else None,
            top_score=round(top_score, 4),
            n_matches_above_threshold=len(sub_pairs),
            match_summary=summary_str,
        ))

    return result


# Back-compat shims
def score_rows_embed(
    rows: list[tuple[str, str]],
    taxonomy: Taxonomy,
    *,
    config: LlmEmbedConfig | None = None,
    run_id: str = "",
) -> tuple[list[MatchRecord], list[RowScore]]:
    r = score_all_embed(rows, taxonomy, config=config, run_id=run_id)
    return r.sub_matches, r.summaries


def score_rows_prompt(
    rows: list[tuple[str, str]],
    taxonomy: Taxonomy,
    *,
    config: LlmPromptConfig | None = None,
    run_id: str = "",
) -> tuple[list[MatchRecord], list[RowScore]]:
    r = score_all_prompt(rows, taxonomy, config=config, run_id=run_id)
    return r.sub_matches, r.summaries
