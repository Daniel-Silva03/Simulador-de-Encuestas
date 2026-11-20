"""Cobertura del parser sobre el inventario de expresiones de un programa (paso 4).

Usa el inventario que produce el lector estructural (paso 3) sin
modificarlo. Para cada aparición informa el estado del análisis
(``parsed``, ``partial``, ``unsupported``, ``invalid``, ``empty``) y los
motivos de los nodos opacos, por sección y tanto por aparición como por
expresión única. Nunca ejecuta nada y nunca lanza por una expresión.
"""

from __future__ import annotations

from collections import Counter

from .expr_parser import PARSER_VERSION, ParseResult, parse_condition, parse_value
from .reader import RawProgram

#: Campos que son condiciones; ``xpr`` es una expresión de valor; ``func``
#: (camposid) es una referencia a una función externa.
CONDITION_FIELDS = ("cnd", "habil_cnd", "oculta_cnd", "cond_cuota", "condiciones")
VALUE_FIELDS = ("xpr", "func")
STATUSES = ("parsed", "partial", "unsupported", "invalid", "empty")


def parse_inventory_item(field_name: str, raw: str, path: str) -> ParseResult:
    if field_name in VALUE_FIELDS:
        return parse_value(raw, path)
    return parse_condition(raw, path)


def coverage(raw: RawProgram) -> dict:
    by_section: dict[str, Counter] = {}
    unique: dict[str, Counter] = {}
    seen: dict[tuple[str, str], str] = {}
    reasons: Counter = Counter()
    rows = []
    for e in raw.expressions:
        r = parse_inventory_item(e.field, e.raw, e.path)
        by_section.setdefault(e.section, Counter())[r.status] += 1
        key = (e.field in VALUE_FIELDS and "value" or "condition", e.raw)
        if key not in seen:
            seen[key] = r.status
            unique.setdefault(e.section, Counter())[r.status] += 1
        rs = list(r.opaque_reasons) or [d.code for d in r.diagnostics if r.status in ("unsupported", "invalid")]
        for code in rs:
            reasons[code.split("@")[0]] += 1
        rows.append({"path": e.path, "field": e.field, "section": e.section, "mode": r.mode, "status": r.status,
                     "reasons": rs, "constructs": list(r.constructs)})
    total = Counter()
    for c in by_section.values():
        total.update(c)
    return {
        "parser_version": PARSER_VERSION,
        "occurrences": {s: {k: c.get(k, 0) for k in STATUSES} for s, c in sorted(by_section.items())},
        "unique": {s: {k: c.get(k, 0) for k in STATUSES} for s, c in sorted(unique.items())},
        "total": {k: total.get(k, 0) for k in STATUSES},
        "total_occurrences": sum(total.values()),
        "reasons": dict(sorted(reasons.items())),
        "expressions": rows,
    }
