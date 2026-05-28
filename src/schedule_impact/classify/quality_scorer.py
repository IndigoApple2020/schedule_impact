"""Score incidents for quality-related delays (keywords + optional ML model)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from schedule_impact.classify.quality_keywords import keyword_quality_score
from schedule_impact.config_loader import load_settings

_logger = logging.getLogger(__name__)


def assess_incident_quality(
    incident: dict[str, Any],
    links: list[dict[str, Any]],
    *,
    model_path: Path | None = None,
    review_threshold: float = 0.75,
) -> dict[str, Any]:
    texts = [lnk.get("text_plain") or "" for lnk in links if lnk.get("incident_id") == incident["incident_id"]]
    combined = " ".join(t for t in texts if t.strip())

    kw_score, kw_hits = keyword_quality_score(combined)
    ml_flag: bool | None = None
    ml_conf: float | None = None
    classifier = "keywords"

    if model_path and model_path.is_file() and combined.strip():
        try:
            from schedule_impact.labeling.train_quality import predict_quality

            ml_flag, ml_conf = predict_quality(combined, model_path)
            classifier = "hybrid"
        except Exception:
            _logger.warning("ML quality prediction failed; falling back to keywords.", exc_info=True)

    if ml_conf is not None:
        is_quality = bool(ml_flag)
        quality_confidence = ml_conf
        classifier = "ml_v1"
        if kw_hits:
            classifier = "hybrid"
            if is_quality:
                quality_confidence = max(ml_conf, kw_score)
            elif kw_score >= 0.6:
                is_quality = True
                quality_confidence = max(ml_conf, kw_score)
    else:
        is_quality = kw_score >= 0.5
        quality_confidence = kw_score

    review_status = "auto"
    if quality_confidence < review_threshold:
        review_status = "needs_review"

    signals: list[str] = []
    if kw_hits:
        signals.extend(kw_hits[:5])
    if ml_conf is not None:
        signals.append(f"ml_p={ml_conf:.2f}")

    return {
        "incident_id": incident["incident_id"],
        "is_quality_related": is_quality,
        "quality_confidence": round(quality_confidence, 4),
        "quality_signals": "; ".join(signals),
        "classifier": classifier,
        "review_status": review_status,
        "has_linked_memo": bool(combined.strip()),
    }


def assess_all(
    incidents: list[dict[str, Any]],
    links: list[dict[str, Any]],
    *,
    model_path: Path | None = None,
) -> list[dict[str, Any]]:
    settings = load_settings()
    quality_cfg = settings.get("quality", {})
    threshold = float(quality_cfg.get("require_human_review_below", 0.75))
    model = model_path
    if model is None:
        mp = quality_cfg.get("model_path")
        if mp:
            model = Path(mp)
    return [
        assess_incident_quality(inc, links, model_path=model, review_threshold=threshold)
        for inc in incidents
    ]
