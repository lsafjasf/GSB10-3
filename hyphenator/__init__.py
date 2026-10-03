"""Multilingual hyphenation library (standard library only)."""
from .core import CORPUS_PATH, RULES_DIR, SOFT_HYPHEN, Hyphenator

__all__ = ["Hyphenator", "SOFT_HYPHEN", "RULES_DIR", "CORPUS_PATH"]
__version__ = "1.0.0"
