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
from typing import Any, Iterable

from text_classify.preprocess import normalise
from text_classify.schemas import MatchRecord, RowScore, ScoringResult, Taxonomy

_logger = logging.getLogger(__name__)


def _progress(iterable: Iterable, *, total: int | None = None, desc: str = "") -> Iterable:
    """Wrap an iterable with tqdm if available; otherwise return it unchanged."""
    try:
        from tqdm import tqdm
        return tqdm(iterable, total=total, desc=desc, unit="row")
    except ImportError:
        return iterable


@dataclass
class LlmEmbedConfig:
    model: str = "nomic-embed-text"
    threshold: float = 0.55
    # Backend selection: "ollama" (Ollama-native API) or "openai"
    # (OpenAI-compatible HTTP endpoint — works with llama.cpp server,
    # LM Studio, vLLM, OpenAI itself, and Ollama's /v1 endpoint).
    backend: str = "ollama"
    host: str = "http://localhost:11434"       # used when backend="ollama"
    base_url: str = "http://localhost:8080/v1"  # used when backend="openai"
    api_key: str = "not-needed"                 # required by openai client; ignored by local servers
    batch_size: int = 32       # rows per batch embed call
    show_progress: bool = True  # tqdm progress bar if installed


@dataclass
class LlmPromptConfig:
    model: str = "llama3.1:8b"
    threshold: float = 0.5
    # Backend selection: "ollama" or "openai" (see LlmEmbedConfig docstring)
    backend: str = "ollama"
    host: str = "http://localhost:11434"        # used when backend="ollama"
    base_url: str = "http://localhost:8080/v1"  # used when backend="openai" (llama.cpp server default)
    api_key: str = "not-needed"                 # required by openai client; ignored by local servers
    temperature: float = 0.0
    max_retries: int = 2
    # Optional path: write each row's parsed JSON response as it comes in,
    # so a crashed run can be resumed without losing progress. One JSON object
    # per line (NDJSON). Resume support is the caller's responsibility.
    checkpoint_path: str | None = None
    show_progress: bool = True   # tqdm progress bar if installed


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class _LlmClient:
    """Thin abstraction over Ollama and OpenAI-compatible HTTP backends.

    backend="ollama" uses the official ``ollama`` Python client.
    backend="openai" uses the ``openai`` Python client against any
    OpenAI-compatible endpoint — llama.cpp's ``llama-server``, LM Studio,
    vLLM, OpenAI itself, and Ollama's own ``/v1`` endpoint all work.

    The wrapper exposes only the methods our scoring loops need:
    ``ping()``, ``chat_json(prompt)``, and ``embed(texts)``.
    """

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.backend = getattr(cfg, "backend", "ollama")
        self._impl = None
        if self.backend == "openai":
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise RuntimeError(
                    "openai package is required for backend='openai'. "
                    "Install with: pip install -e \".[llm]\""
                ) from exc
            self._impl = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key or "not-needed")
        else:
            try:
                import ollama
            except ImportError as exc:
                raise RuntimeError(
                    "ollama package is required for backend='ollama'. "
                    "Install with: pip install -e \".[llm]\""
                ) from exc
            self._impl = ollama.Client(host=cfg.host)

    # --------------------------------------------------------------- chat
    def chat_json(self, prompt: str) -> str:
        if self.backend == "openai":
            resp = self._impl.chat.completions.create(
                model=self.cfg.model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=getattr(self.cfg, "temperature", 0.0),
            )
            return resp.choices[0].message.content or ""
        # ollama
        resp = self._impl.chat(
            model=self.cfg.model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": getattr(self.cfg, "temperature", 0.0)},
            format="json",
        )
        return resp["message"]["content"]

    def ping_chat(self) -> None:
        """Tiny round-trip to verify the model is reachable; raises on failure."""
        if self.backend == "openai":
            self._impl.chat.completions.create(
                model=self.cfg.model,
                messages=[{"role": "user", "content": "ping"}],
                temperature=0.0,
                max_tokens=1,
            )
        else:
            self._impl.chat(
                model=self.cfg.model,
                messages=[{"role": "user", "content": "ping"}],
                options={"temperature": 0.0, "num_predict": 1},
            )

    # ----------------------------------------------------------- embeddings
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Both backends support batched input."""
        if not texts:
            return []
        if self.backend == "openai":
            resp = self._impl.embeddings.create(model=self.cfg.model, input=texts)
            return [list(item.embedding) for item in resp.data]
        # ollama (newer Python clients have .embed; older only .embeddings)
        if hasattr(self._impl, "embed"):
            try:
                resp = self._impl.embed(model=self.cfg.model, input=texts)
                embs = resp.get("embeddings") if isinstance(resp, dict) else getattr(resp, "embeddings", None)
                if embs is not None:
                    return [list(v) for v in embs]
            except Exception as exc:
                _logger.debug("Ollama batch embed failed (%s), falling back to per-text", exc)
        out: list[list[float]] = []
        for t in texts:
            resp = self._impl.embeddings(model=self.cfg.model, prompt=t)
            out.append(list(resp["embedding"]))
        return out

    def ping_embed(self) -> None:
        self.embed(["ping"])


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _embed_batch(client: _LlmClient, texts: list[str], batch_size: int) -> list[list[float]]:
    """Embed ``texts`` in chunks of ``batch_size`` via the backend client."""
    out: list[list[float]] = []
    if not texts:
        return out
    for i in range(0, len(texts), batch_size):
        chunk = texts[i:i + batch_size]
        out.extend(client.embed(chunk))
    return out


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
    cfg = config or LlmEmbedConfig()
    client = _LlmClient(cfg)

    # Fail fast if the backend or model isn't reachable
    try:
        client.ping_embed()
    except Exception as exc:
        if cfg.backend == "openai":
            raise RuntimeError(
                f"OpenAI-compatible embedding endpoint at {cfg.base_url} (model '{cfg.model}') is "
                f"not reachable: {exc}. For llama.cpp, start the server with: "
                f"llama-server -m <model.gguf> --embedding"
            ) from exc
        raise RuntimeError(
            f"Ollama embed model '{cfg.model}' at {cfg.host} is not reachable: {exc}. "
            f"Run 'ollama serve' and 'ollama pull {cfg.model}' first."
        ) from exc

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

    # Embed targets once (small fixed set)
    sub_vectors = _embed_batch(client,sub_texts, cfg.batch_size)
    cat_vectors = _embed_batch(client,cat_texts, cfg.batch_size)

    # Partition rows by whether they have any text to embed
    norm_texts: list[str] = [normalise(r[1]) for r in rows]
    nonempty_indices = [i for i, t in enumerate(norm_texts) if t]
    nonempty_texts = [norm_texts[i] for i in nonempty_indices]

    # Embed all non-empty row texts in batches
    if cfg.show_progress and nonempty_texts:
        _logger.info("Embedding %d rows in batches of %d via Ollama %s",
                     len(nonempty_texts), cfg.batch_size, cfg.model)
    row_vectors_packed = _embed_batch(client,nonempty_texts, cfg.batch_size)
    row_vec_by_index: dict[int, list[float]] = dict(zip(nonempty_indices, row_vectors_packed, strict=True))

    result = ScoringResult(method="llm_embed", threshold=cfg.threshold)

    row_iter = enumerate(rows)
    if cfg.show_progress:
        row_iter = _progress(row_iter, total=len(rows), desc="score_embed")

    for i, (row_id, _raw_text) in row_iter:
        row_vec = row_vec_by_index.get(i)
        if row_vec is None:
            # Empty input row — emit zero-score records for every target for completeness
            result.summaries.append(RowScore(row_id, None, None, 0.0, 0, ""))
            for cat_id, sub_id in sub_meta:
                result.all_sub_scores.append(MatchRecord(
                    row_id, cat_id, sub_id, 0.0, "llm_embed", "", taxonomy.version, run_id,
                ))
            for cat_id in cat_meta:
                result.all_cat_scores.append(MatchRecord(
                    row_id, cat_id, "", 0.0, "llm_embed", "", taxonomy.version, run_id,
                ))
            continue

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


def _read_checkpoint(path) -> dict[str, dict[str, Any]]:
    """Load an NDJSON prompt-mode checkpoint into ``{row_id: parsed_response}``."""
    out: dict[str, dict[str, Any]] = {}
    from pathlib import Path as _Path
    p = _Path(path)
    if not p.is_file():
        return out
    with p.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = entry.get("row_id")
            parsed = entry.get("parsed") or {}
            if rid:
                out[str(rid)] = parsed
    return out


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


def _emit_prompt_records(
    result: ScoringResult,
    row_id: str,
    parsed: dict[str, Any],
    *,
    taxonomy: Taxonomy,
    valid_sub_pairs: set[tuple[str, str]],
    valid_cat_ids: set[str],
    threshold: float,
    run_id: str,
) -> None:
    """Translate a parsed LLM JSON response into MatchRecords + summary row.

    Single source of truth used both during live scoring and when rebuilding
    results from a resumed checkpoint.
    """
    sub_pairs_above: list[tuple[str, str, float]] = []
    sub_scores_seen: set[tuple[str, str]] = set()

    for m in parsed.get("sub_category_scores", []) or []:
        cat_id = m.get("category_id")
        sub_id = m.get("sub_category_id")
        try:
            score = float(m.get("score", 0.0))
        except (TypeError, ValueError):
            continue
        reasoning = str(m.get("reasoning", ""))[:200]
        if (cat_id, sub_id) not in valid_sub_pairs:
            continue
        sub_scores_seen.add((cat_id, sub_id))
        rec = MatchRecord(row_id, cat_id, sub_id, round(score, 4), "llm_prompt",
                          reasoning, taxonomy.version, run_id)
        result.all_sub_scores.append(rec)
        if score >= threshold:
            result.sub_matches.append(rec)
            sub_pairs_above.append((cat_id, sub_id, score))

    for cat_id, sub_id in valid_sub_pairs - sub_scores_seen:
        result.all_sub_scores.append(
            MatchRecord(row_id, cat_id, sub_id, 0.0, "llm_prompt", "",
                        taxonomy.version, run_id)
        )

    cat_scores_seen: set[str] = set()
    for m in parsed.get("category_scores", []) or []:
        cat_id = m.get("category_id")
        try:
            score = float(m.get("score", 0.0))
        except (TypeError, ValueError):
            continue
        reasoning = str(m.get("reasoning", ""))[:200]
        if cat_id not in valid_cat_ids:
            continue
        cat_scores_seen.add(cat_id)
        rec = MatchRecord(row_id, cat_id, "", round(score, 4), "llm_prompt",
                          reasoning, taxonomy.version, run_id)
        result.all_cat_scores.append(rec)
        if score >= threshold:
            result.cat_matches.append(rec)
    for cat_id in valid_cat_ids - cat_scores_seen:
        result.all_cat_scores.append(
            MatchRecord(row_id, cat_id, "", 0.0, "llm_prompt", "",
                        taxonomy.version, run_id)
        )

    sub_pairs_above.sort(key=lambda p: -p[2])
    top_score = sub_pairs_above[0][2] if sub_pairs_above else 0.0
    summary_str = "; ".join(f"{c}/{s}:{score:.2f}" for c, s, score in sub_pairs_above[:5])
    result.summaries.append(RowScore(
        row_id=row_id,
        top_category_id=sub_pairs_above[0][0] if sub_pairs_above else None,
        top_sub_category_id=sub_pairs_above[0][1] if sub_pairs_above else None,
        top_score=round(top_score, 4),
        n_matches_above_threshold=len(sub_pairs_above),
        match_summary=summary_str,
    ))


def score_all_prompt(
    rows: list[tuple[str, str]],
    taxonomy: Taxonomy,
    *,
    config: LlmPromptConfig | None = None,
    run_id: str = "",
) -> ScoringResult:
    """Per-row chat completion with structured JSON output for every category & sub-category."""
    cfg = config or LlmPromptConfig()
    client = _LlmClient(cfg)

    # Fail fast if the chat backend or model isn't reachable
    try:
        client.ping_chat()
    except Exception as exc:
        if cfg.backend == "openai":
            raise RuntimeError(
                f"OpenAI-compatible chat endpoint at {cfg.base_url} (model '{cfg.model}') is "
                f"not reachable: {exc}. For llama.cpp, start the server with: "
                f"llama-server -m <model.gguf> --port 8080"
            ) from exc
        raise RuntimeError(
            f"Ollama chat model '{cfg.model}' at {cfg.host} is not reachable: {exc}. "
            f"Run 'ollama serve' and 'ollama pull {cfg.model}' first."
        ) from exc

    tax_text = _format_taxonomy_for_prompt(taxonomy)

    valid_sub_pairs = {(c.id, s.id) for c, s in taxonomy.iter_sub_categories()}
    valid_cat_ids = {c.id for c in taxonomy.categories}

    result = ScoringResult(method="llm_prompt", threshold=cfg.threshold)

    # Open checkpoint NDJSON file if configured. Each completed row appends one line.
    # If the checkpoint already exists, pre-load completed row results — these
    # rows are skipped during scoring (resume support).
    checkpoint_handle = None
    prior_results: dict[str, dict[str, Any]] = {}
    if cfg.checkpoint_path:
        from pathlib import Path as _Path
        cp = _Path(cfg.checkpoint_path)
        cp.parent.mkdir(parents=True, exist_ok=True)
        prior_results = _read_checkpoint(cp)
        checkpoint_handle = cp.open("a", encoding="utf-8")

    # Replay checkpoint entries into the result (no LLM call needed)
    if prior_results:
        _logger.info("Resuming from checkpoint: %d rows already scored", len(prior_results))
        for row_id, parsed_prior in prior_results.items():
            _emit_prompt_records(
                result, str(row_id), parsed_prior,
                taxonomy=taxonomy,
                valid_sub_pairs=valid_sub_pairs,
                valid_cat_ids=valid_cat_ids,
                threshold=cfg.threshold,
                run_id=run_id,
            )

    # Score the rows that are not yet in the checkpoint
    rows_to_score = [r for r in rows if str(r[0]) not in prior_results]

    row_iter = rows_to_score
    if cfg.show_progress:
        row_iter = _progress(rows_to_score, total=len(rows_to_score), desc="score_prompt")

    for row_id, raw_text in row_iter:
        if not raw_text or not str(raw_text).strip():
            result.summaries.append(RowScore(row_id, None, None, 0.0, 0, ""))
            continue
        prompt = _PROMPT_TEMPLATE.format(taxonomy=tax_text, text=str(raw_text).strip())

        parsed: dict[str, list[dict[str, Any]]] = {"category_scores": [], "sub_category_scores": []}
        for attempt in range(cfg.max_retries + 1):
            try:
                content = client.chat_json(prompt)
                parsed = _parse_response(content)
                if parsed["sub_category_scores"] or parsed["category_scores"]:
                    break
            except Exception as exc:
                _logger.warning("LLM call failed for row %s (attempt %d): %s", row_id, attempt + 1, exc)

        _emit_prompt_records(
            result, row_id, parsed,
            taxonomy=taxonomy,
            valid_sub_pairs=valid_sub_pairs,
            valid_cat_ids=valid_cat_ids,
            threshold=cfg.threshold,
            run_id=run_id,
        )

        # Checkpoint: persist this row's parsed response immediately so a
        # crash doesn't lose progress on a long run.
        if checkpoint_handle is not None:
            checkpoint_handle.write(json.dumps({
                "row_id": row_id,
                "parsed": parsed,
            }, ensure_ascii=False) + "\n")
            checkpoint_handle.flush()

    if checkpoint_handle is not None:
        checkpoint_handle.close()

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
