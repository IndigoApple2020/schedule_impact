"""Generic taxonomy-driven text classification.

Use cases: classify a free-text field on each row of a CSV against a
configurable taxonomy of categories and sub-categories, with two
interchangeable scoring engines (TF-IDF and Ollama-backed LLM) plus a
cross-row keyword discovery pass.

See ``docs/text-classification-strategy.md`` for the design rationale.
"""

__version__ = "0.1.0"
