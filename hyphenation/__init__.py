"""Multilingual hyphenation / line-break library (standard library only)."""
from .core import Hyphenator, RuleSet, insert_hyphens, remove_hyphens, SOFT_HYPHEN

__all__ = ["Hyphenator", "RuleSet", "insert_hyphens", "remove_hyphens", "SOFT_HYPHEN"]
