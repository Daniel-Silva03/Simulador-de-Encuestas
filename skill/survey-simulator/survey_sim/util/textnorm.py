"""Normalización de textos y etiquetas para comparar (sin interpretar su contenido)."""

from __future__ import annotations

import difflib
import re
import unicodedata

_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize_text(s: str | None) -> str:
    """Minúsculas, sin tildes, solo letras y dígitos ASCII separados por un espacio."""
    if not s:
        return ""
    return _NON_ALNUM.sub(" ", strip_accents(s).lower()).strip()


def tokens(s: str | None) -> tuple[str, ...]:
    n = normalize_text(s)
    return tuple(n.split()) if n else ()


def normalize_label(s: str | None) -> str:
    """Etiqueta comparable: mayúsculas, sin tildes ni espacios ni puntuación final."""
    if not s:
        return ""
    return re.sub(r"[\s.:)]+$", "", strip_accents(s).upper().strip()).replace(" ", "")


def ratio(a: str, b: str) -> float:
    """Similitud determinista entre dos textos ya normalizados (0–1)."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def jaccard(a, b) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)
