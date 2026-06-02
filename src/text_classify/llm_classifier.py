"""Ollama-backed classification — embeddings and structured-prompt modes.

Both modes are fully local: Ollama runs on the laptop and no row text leaves
the machine.

* :func:`score_rows_embed`  — cheap, cosine similarity of row embedding vs.
  per-sub-category pseudo-document embedding. Best for the first pass on
  a large corpus.
* :func:`score_rows_prompt` — sends each row to a chat model with the full
  taxonomy and a JSON-output instruction. Slower (~1-3 s/row) but catches
  paraphrases and produces a free-text rationale.

Both functions return the same ``MatchRecord`` shape so callers don't have
to special-case the engine.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from typing import Any

from text_classify.preprocess import normalise
from text_classify.schemas import MatchRecord, RowScore, Taxonomy

_logger = logging.getLogger(__name__)


@dataclass
class LlmEmbedConfig:
    model: str = "nomic-embed-text"
    threshold: float = 0.55           # cosine in embedding space; tune per model
    host: str = "http://localhost:11434"
    batch_size: int = 32


@dataclass
class LlmPromptConfig:
    model: str = "llama3.1:8b"
    threshold: float = 0.5
    host: str = "http://localhost:11434"
    temperature: float = 0.0
    max_retries: int = 2


# ---------------------------------------------------------------------------
# Embedding mode
# ---------------------------------------------------------------------------


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _pseudo_doc(cat_seeds: tuple[str, ...], sub_label: str, sub_desc: str, sub_seeds: tuple[str, ...]) -> str:
    parts = [sub_label, sub_desc, *sub_seeds, *cat_seeds]
    return ". ".join(p for p in parts if p)


def score_rows_embed(
    rows: list[tuple[str, str]],
    taxonomy: Taxonomy,
    *,
    config: LlmEmbedConfig | None = None,
    run_id: str = "",
) -> tuple[list[MatchRecord], list[RowScore]]:
    """Score rows by cosine similarity between Ollama embeddings.

    Requires the ``ollama`` Python package and a running Ollama daemon.
    """
    try:
        import ollama
    except ImportError as exc:
        raise RuntimeError(
            "The 'ollama' package is required. Install with: pip install -e \".[llm]\""
        ) from exc

    cfg = config or LlmEmbedConfig()
    client = ollama.Client(host=cfg.host)

    # Embed seed pseudo-documents once
    sub_meta: list[tuple[str, str]] = []  # (cat_id, sub_id)
    seed_texts: list[str] = []
    for cat, sub in taxonomy.iter_sub_categories():
        sub_meta.append((cat.id, sub.id))
        seed_texts.append(_pseudo_doc(cat.seed_keywords, sub.label, sub.description, sub.seed_keywords))

    seed_vectors: list[list[float]] = []
    for text in seed_texts:
        resp = client.embeddings(model=cfg.model, prompt=text)
        seed_vectors.append(list(resp["embedding"]))

    matches: list[MatchRecord] = []
    summaries: list[RowScore] = []

    for row_id, raw_text in rows:
        text = normalise(raw_text)
        if not text:
            summaries.append(RowScore(row_id, None, None, 0.0, 0, ""))
            continue
        resp = client.embeddings(model=cfg.model, prompt=text)
        row_vec = list(resp["embedding"])

        top_idx: int | None = None
        top_score = 0.0
        n_above = 0
        top_pairs: list[tuple[str, str, float]] = []
        for j, sv in enumerate(seed_vectors):
            s = _cosine(row_vec, sv)
            if s > top_score:
                top_score = s
                top_idx = j
            if s >= cfg.threshold:
                n_above += 1
                cat_id, sub_id = sub_meta[j]
                matches.append(
                    MatchRecord(
                        row_id=row_id,
                        category_id=cat_id,
                        sub_category_id=sub_id,
                        score=round(s, 4),
                        method="llm_embed",
                        signals=f"model={cfg.model}",
                        taxonomy_version=taxonomy.version,
                        run_id=run_id,
                    )
                )
                top_pairs.append((cat_id, sub_id, s))

        top_pairs.sort(key=lambda p: -p[2])
        top_cat = sub_meta[top_idx][0] if top_idx is not None else None
        top_sub = sub_meta[top_idx][1] if top_idx is not None else None
        summary_str = "; ".join(f"{c}/{s}:{score:.2f}" for c, s, score in top_pairs[:5])
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


# ---------------------------------------------------------------------------
# Prompt mode
# ---------------------------------------------------------------------------

_PROMPT_TEMPLATE = """You are classifying a construction quality issue against a fixed taxonomy.

TAXONOMY:
{taxonomy}

ISSUE TEXT:
\"\"\"{text}\"\"\"

Return ONLY a JSON object with this exact shape:
{{
  "matches": [
    {{"category_id": "...", "sub_category_id": "...", "score": 0.0, "reasoning": "..."}},
    ...
  ]
}}

Rules:
- Score is 0.0 to 1.0 (your confidence the sub-category applies).
- Only include matches with score >= 0.3.
- Multiple matches are allowed if the issue genuinely spans categories.
- If nothing applies, return {{"matches": []}}.
- "reasoning" must be a single short sentence (max 25 words).
"""


def _format_taxonomy_for_prompt(taxonomy: Taxonomy) -> str:
    lines: list[str] = []
    for cat in taxonomy.categories:
        lines.append(f"- {cat.id} ({cat.label}): {cat.description}")
        for sub in cat.sub_categories:
            lines.append(f"    * {sub.id} ({sub.label}): {sub.description}")
    return "\n".join(lines)


def _parse_response(content: str) -> list[dict[str, Any]]:
    """Best-effort JSON parse. Returns the list of match dicts or empty list."""
    content = content.strip()
    # Strip code fences if present
    if content.startswith("```"):
        content = content.strip("`")
        if content.startswith("json"):
            content = content[len("json"):]
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return []
    matches = parsed.get("matches") if isinstance(parsed, dict) else None
    return matches if isinstance(matches, list) else []


def score_rows_prompt(
    rows: list[tuple[str, str]],
    taxonomy: Taxonomy,
    *,
    config: LlmPromptConfig | None = None,
    run_id: str = "",
) -> tuple[list[MatchRecord], list[RowScore]]:
    """Score rows via per-row chat completion with structured JSON output."""
    try:
        import ollama
    except ImportError as exc:
        raise RuntimeError(
            "The 'ollama' package is required. Install with: pip install -e \".[llm]\""
        ) from exc

    cfg = config or LlmPromptConfig()
    client = ollama.Client(host=cfg.host)
    tax_text = _format_taxonomy_for_prompt(taxonomy)

    valid_pairs = {(c.id, s.id) for c, s in taxonomy.iter_sub_categories()}

    matches: list[MatchRecord] = []
    summaries: list[RowScore] = []

    for row_id, raw_text in rows:
        if not raw_text or not str(raw_text).strip():
            summaries.append(RowScore(row_id, None, None, 0.0, 0, ""))
            continue
        prompt = _PROMPT_TEMPLATE.format(taxonomy=tax_text, text=str(raw_text).strip())

        parsed: list[dict[str, Any]] = []
        for attempt in range(cfg.max_retries + 1):
            try:
                resp = client.chat(
                    model=cfg.model,
                    messages=[{"role": "user", "content": prompt}],
                    options={"temperature": cfg.temperature},
                    format="json",
                )
                content = resp["message"]["content"]
                parsed = _parse_response(content)
                if parsed is not None:
                    break
            except Exception as exc:
                _logger.warning("LLM call failed for row %s (attempt %d): %s", row_id, attempt + 1, exc)
        else:
            parsed = []

        top_score = 0.0
        top_pair: tuple[str | None, str | None] = (None, None)
        n_above = 0
        top_pairs: list[tuple[str, str, float]] = []

        for m in parsed:
            cat_id = m.get("category_id")
            sub_id = m.get("sub_category_id")
            score = float(m.get("score", 0.0))
            reasoning = str(m.get("reasoning", ""))[:200]
            if (cat_id, sub_id) not in valid_pairs:
                _logger.debug("Discarding invalid pair from LLM: %s/%s", cat_id, sub_id)
                continue
            if score > top_score:
                top_score = score
                top_pair = (cat_id, sub_id)
            if score >= cfg.threshold:
                n_above += 1
                matches.append(
                    MatchRecord(
                        row_id=row_id,
                        category_id=cat_id,
                        sub_category_id=sub_id,
                        score=round(score, 4),
                        method="llm_prompt",
                        signals=reasoning,
                        taxonomy_version=taxonomy.version,
                        run_id=run_id,
                    )
                )
                top_pairs.append((cat_id, sub_id, score))

        top_pairs.sort(key=lambda p: -p[2])
        summary_str = "; ".join(f"{c}/{s}:{score:.2f}" for c, s, score in top_pairs[:5])
        summaries.append(
            RowScore(
                row_id=row_id,
                top_category_id=top_pair[0],
                top_sub_category_id=top_pair[1],
                top_score=round(top_score, 4),
                n_matches_above_threshold=n_above,
                match_summary=summary_str,
            )
        )

    return matches, summaries
