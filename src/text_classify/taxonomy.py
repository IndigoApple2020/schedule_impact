"""Load and validate a taxonomy YAML into typed dataclasses."""

from __future__ import annotations

from pathlib import Path

import yaml

from text_classify.schemas import Category, SubCategory, Taxonomy


def load_taxonomy(path: Path) -> Taxonomy:
    """Read a taxonomy YAML file and return a validated :class:`Taxonomy`.

    The schema is permissive on field order but strict on required keys.
    """
    if not path.is_file():
        raise FileNotFoundError(f"Taxonomy file not found: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return _validate(raw, path)


def _validate(raw: dict, source: Path) -> Taxonomy:
    name = raw.get("name")
    if not name:
        raise ValueError(f"{source}: 'name' is required")

    cats_raw = raw.get("categories") or []
    if not cats_raw:
        raise ValueError(f"{source}: 'categories' must be a non-empty list")

    categories: list[Category] = []
    seen_cat_ids: set[str] = set()
    for c in cats_raw:
        cid = c.get("id")
        if not cid:
            raise ValueError(f"{source}: every category needs 'id'")
        if cid in seen_cat_ids:
            raise ValueError(f"{source}: duplicate category id '{cid}'")
        seen_cat_ids.add(cid)

        subs_raw = c.get("sub_categories") or []
        if not subs_raw:
            raise ValueError(f"{source}: category '{cid}' has no sub_categories")

        subs: list[SubCategory] = []
        seen_sub_ids: set[str] = set()
        for s in subs_raw:
            sid = s.get("id")
            if not sid:
                raise ValueError(f"{source}: every sub_category in '{cid}' needs 'id'")
            if sid in seen_sub_ids:
                raise ValueError(f"{source}: duplicate sub_category id '{sid}' in '{cid}'")
            seen_sub_ids.add(sid)
            subs.append(
                SubCategory(
                    id=sid,
                    label=str(s.get("label") or sid),
                    description=str(s.get("description") or ""),
                    seed_keywords=tuple(str(k) for k in (s.get("seed_keywords") or [])),
                )
            )

        categories.append(
            Category(
                id=cid,
                label=str(c.get("label") or cid),
                description=str(c.get("description") or ""),
                seed_keywords=tuple(str(k) for k in (c.get("seed_keywords") or [])),
                sub_categories=tuple(subs),
            )
        )

    return Taxonomy(
        name=str(name),
        version=int(raw.get("version", 1)),
        description=str(raw.get("description") or ""),
        default_threshold=float(raw.get("default_threshold", 0.35)),
        categories=tuple(categories),
    )
