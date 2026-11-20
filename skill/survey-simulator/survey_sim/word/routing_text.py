"""Reconocimiento de navegación en texto libre (sección 5, paso 4).

Solo se reconoce lo que coincide con un patrón del léxico. Lo que contiene
palabras de navegación pero no coincide con ningún patrón se informa como
no resuelto: nunca se adivina un destino ni una condición.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional

from .lexicon import Lexicon

NavKind = Literal["show_if", "applies_if", "goto", "terminate", "continue"]

# Orden de búsqueda: primero lo más específico.
_ORDER: tuple[NavKind, ...] = ("show_if", "applies_if", "goto", "terminate", "continue")


@dataclass(frozen=True)
class NavMatch:
    kind: NavKind
    raw: str
    start: int
    end: int
    target: Optional[str] = None
    source: Optional[str] = None
    codes: tuple[str, ...] = ()
    termination: Optional[str] = None


def parse_codes(s: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[0-9]+", s))  # H-15: solo dígitos ASCII


def find_navigation(text: str, lex: Lexicon) -> list[NavMatch]:
    """Coincidencias de navegación sin solaparse, en orden de aparición."""
    taken: list[tuple[int, int]] = []
    found: list[NavMatch] = []
    for kind in _ORDER:
        for rx in lex.nav[kind]:
            for m in rx.finditer(text):
                s, e = m.span()
                if any(s < te and ts < e for ts, te in taken):
                    continue
                g = m.groupdict()
                found.append(
                    NavMatch(
                        kind=kind,
                        raw=m.group(0).strip(),
                        start=s,
                        end=e,
                        target=g.get("target"),
                        source=g.get("source"),
                        codes=parse_codes(g["codes"]) if g.get("codes") else (),
                        termination=(g.get("termination") or None),
                    )
                )
                taken.append((s, e))
    return sorted(found, key=lambda n: n.start)


def remainder(text: str, matches: list[NavMatch]) -> str:
    out, pos = [], 0
    for m in sorted(matches, key=lambda n: n.start):
        out.append(text[pos:m.start])
        pos = m.end
    out.append(text[pos:])
    return re.sub(r"\s+", " ", "".join(out)).strip(" .;,")


def has_unparsed_navigation(text: str, matches: list[NavMatch], lex: Lexicon) -> bool:
    """True si, quitando lo reconocido, quedan palabras de navegación."""
    return lex.has_navigation_keyword(remainder(text, matches))


def termination_code(label: str) -> str:
    """Código estable de una incidencia: mayúsculas y guiones bajos."""
    return re.sub(r"[^0-9A-ZÁÉÍÓÚÑ]+", "_", label.upper()).strip("_")
