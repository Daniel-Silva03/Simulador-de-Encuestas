"""Avisos del validador como elementos no resueltos de la IR (AUT-1, decisión D-09).

Los avisos pertinentes de ``validate_instrument`` (referencias que no existen)
se añaden a ``Instrument.unresolved`` con el código ``validation:<código>``.

Determinismo y ausencia de duplicados:

1. Solo se consideran los avisos de ``PERTINENT``; los errores no (abortan).
2. Cada aviso se ubica en el elemento de la IR más profundo con procedencia
   que señala su ruta (``sections[i].items[j].universe.condition`` → el
   universo; ``rules[k]`` → la regla). Si no se puede resolver, el
   localizador es ``ir:<ruta>``.
3. Clave de unicidad: ``(código, localizador, nombre referido)``.
4. Se omite el aviso si el lector ya registró el mismo hecho: el llamador
   pasa los pares ``(localizador, nombre)`` equivalentes.
5. Se omite si la IR ya contiene esa entrada (aplicar dos veces no cambia
   nada: es idempotente).
6. Las entradas nuevas se ordenan por ``(localizador, código, nombre)`` y van
   después de las del lector.

El validador no lee ``unresolved``, así que validar antes o después de
añadirlas produce los mismos avisos.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

from survey_sim.ir.models import Instrument, Provenance, UnresolvedEntry
from survey_sim.ir.validate import Issue, validate_instrument

PERTINENT = ("unresolved_condition_var", "unresolved_rule_target", "unresolved_grid_row", "unresolved_variable_item")
PREFIX = "validation:"

_TOKEN = re.compile(r"([A-Za-z_]+)(?:\[(\d+)\])?")


def _resolve(inst: Instrument, path: str) -> Optional[Provenance]:
    """Procedencia del elemento más profundo con procedencia en la ruta del aviso."""
    obj: object = inst
    found: Optional[Provenance] = None
    for part in path.split("."):
        m = _TOKEN.fullmatch(part)
        if m is None:
            return found
        name, idx = m.group(1), m.group(2)
        obj = getattr(obj, name, None)
        if obj is None:
            return found
        if idx is not None:
            try:
                obj = obj[int(idx)]
            except (IndexError, TypeError):
                return found
        prov = getattr(obj, "provenance", None)
        if isinstance(prov, Provenance):
            found = prov
    return found


def _referred_name(issue: Issue) -> str:
    """Nombre referido: lo que sigue a ': ' en el mensaje del validador."""
    return issue.message.split(": ", 1)[1] if ": " in issue.message else issue.message


def entries_from_issues(
    inst: Instrument,
    issues: Iterable[Issue],
    reader_equivalents: Iterable[tuple[str, str]] = (),
) -> list[UnresolvedEntry]:
    equivalents = set(reader_equivalents)
    existing = {(u.code, u.provenance.locator, u.text) for u in inst.unresolved}
    seen: set[tuple[str, str, str]] = set()
    out: list[UnresolvedEntry] = []
    for i in issues:
        if i.level != "warning" or i.code not in PERTINENT:
            continue
        prov = _resolve(inst, i.path)
        source = prov.source if prov is not None else inst.source.name
        locator = prov.locator if prov is not None else f"ir:{i.path}"
        name = _referred_name(i)
        code = PREFIX + i.code
        key = (code, locator, name)
        if key in seen or key in existing or (locator, name) in equivalents:
            continue
        seen.add(key)
        out.append(UnresolvedEntry(code=code, text=name, provenance=Provenance(source=source, locator=locator),
                                   detail=f"{i.message} (aviso del validador en {i.path})"))
    out.sort(key=lambda u: (u.provenance.locator, u.code, u.text))
    return out


def with_validation_unresolved(
    inst: Instrument,
    issues: Optional[list[Issue]] = None,
    reader_equivalents: Iterable[tuple[str, str]] = (),
) -> Instrument:
    """Devuelve la IR con los avisos pertinentes añadidos al final de ``unresolved``."""
    if issues is None:
        issues = validate_instrument(inst)
    new = entries_from_issues(inst, issues, reader_equivalents)
    if not new:
        return inst
    return inst.model_copy(update={"unresolved": inst.unresolved + tuple(new)})
