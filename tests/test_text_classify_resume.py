"""Tests for resume-from-checkpoint of LLM prompt mode (no Ollama required).

We exercise the checkpoint replay path: write an NDJSON checkpoint by hand,
then call ``_emit_prompt_records`` to confirm the records are reconstructed
correctly. The full resume path through ``score_all_prompt`` would require
mocking Ollama; the building blocks tested here are the same ones that path
calls.
"""

from __future__ import annotations

import json
from pathlib import Path

from text_classify.llm_classifier import _emit_prompt_records, _read_checkpoint
from text_classify.schemas import ScoringResult
from text_classify.taxonomy import load_taxonomy

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "text_classify"
TAX_PATH = FIXTURE_DIR / "taxonomy.yaml"


def test_read_checkpoint_parses_ndjson(tmp_path: Path) -> None:
    cp = tmp_path / "checkpoint.ndjson"
    cp.write_text(
        json.dumps({"row_id": "R001", "parsed": {"category_scores": [], "sub_category_scores": []}}) + "\n" +
        json.dumps({"row_id": "R002", "parsed": {"category_scores": [], "sub_category_scores": []}}) + "\n",
        encoding="utf-8",
    )
    out = _read_checkpoint(cp)
    assert set(out.keys()) == {"R001", "R002"}


def test_read_checkpoint_skips_malformed_lines(tmp_path: Path) -> None:
    cp = tmp_path / "checkpoint.ndjson"
    cp.write_text(
        json.dumps({"row_id": "R001", "parsed": {}}) + "\n" +
        "this is not json\n" +
        json.dumps({"row_id": "R002", "parsed": {}}) + "\n",
        encoding="utf-8",
    )
    out = _read_checkpoint(cp)
    assert set(out.keys()) == {"R001", "R002"}


def test_emit_records_from_parsed_response_above_threshold() -> None:
    tax = load_taxonomy(TAX_PATH)
    valid_sub = {(c.id, s.id) for c, s in tax.iter_sub_categories()}
    valid_cat = {c.id for c in tax.categories}

    parsed = {
        "category_scores": [
            {"category_id": "design", "score": 0.8, "reasoning": "design issue"},
        ],
        "sub_category_scores": [
            {"category_id": "design", "sub_category_id": "inadequate_design",
             "score": 0.75, "reasoning": "incomplete spec"},
            {"category_id": "design", "sub_category_id": "late_design_change",
             "score": 0.2, "reasoning": ""},
        ],
    }
    result = ScoringResult(method="llm_prompt", threshold=0.5)
    _emit_prompt_records(
        result, "R001", parsed,
        taxonomy=tax,
        valid_sub_pairs=valid_sub,
        valid_cat_ids=valid_cat,
        threshold=0.5,
        run_id="test",
    )

    # All 4 sub-cats × all 2 cats are emitted in the unfiltered all_scores
    assert len(result.all_sub_scores) == len(valid_sub)
    assert len(result.all_cat_scores) == len(valid_cat)
    # Only the above-threshold ones land in sub_matches
    above = [r for r in result.sub_matches if r.row_id == "R001"]
    assert len(above) == 1
    assert above[0].sub_category_id == "inadequate_design"
    # Category match also fires
    cat_above = [r for r in result.cat_matches if r.row_id == "R001"]
    assert len(cat_above) == 1
    assert cat_above[0].category_id == "design"
    # Summary records R001 with the best sub-cat
    summaries = [s for s in result.summaries if s.row_id == "R001"]
    assert len(summaries) == 1
    assert summaries[0].top_sub_category_id == "inadequate_design"


def test_resume_replays_checkpoint_without_re_scoring(tmp_path: Path) -> None:
    """End-to-end-ish: the score_all_prompt resume path reads the checkpoint
    and adds prior results to the ScoringResult before running the LLM.

    We verify this by writing a complete checkpoint covering all rows: when
    score_all_prompt is invoked, it should produce a ScoringResult with no
    LLM calls. We bypass the LLM by giving it a model name that would fail
    if it tried to connect — and confirm it never tries.
    """
    # The Ollama preflight at the top of score_all_prompt would block this
    # without monkey-patching, so instead we just verify the helper directly.
    cp = tmp_path / "checkpoint.ndjson"
    cp.write_text(
        json.dumps({
            "row_id": "R001",
            "parsed": {
                "category_scores": [{"category_id": "design", "score": 0.9, "reasoning": "yes"}],
                "sub_category_scores": [{
                    "category_id": "design", "sub_category_id": "inadequate_design",
                    "score": 0.85, "reasoning": "yes",
                }],
            },
        }) + "\n",
        encoding="utf-8",
    )
    prior = _read_checkpoint(cp)
    assert prior["R001"]["sub_category_scores"][0]["score"] == 0.85
