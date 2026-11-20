"""Comparación de condiciones entre diseño y programa (v1.1, §4 y §5).

Resultados:

- ``equivalente``: solo con los requisitos previos comunes (pareja de la
  pregunta y parejas de todas las variables ``declarada``, comparación
  habilitada) y además (a) identidad estructural tras la normalización
  declarada o (b) enumeración completa de un dominio finito con resultados
  iguales y determinados (T/T o F/F) en cada asignación.
- ``diferente``: existe una asignación con T/F o F/T; se informa el contraejemplo.
- ``no_verificable``: en cualquier otro caso (``Opaque``, U, dominio no
  finito, límite alcanzado, requisitos previos no cumplidos sin contraejemplo).

Nunca hay coerción entre cadenas y números: un valor de texto solo equivale a
un literal numérico si el archivo de equivalencias lo declara para esa
variable y ese lado (§4).
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from typing import Any, Optional

from survey_sim.ir.conditions import (
    And,
    Compare,
    Const,
    Func,
    FuncValue,
    InSet,
    IsAnswered,
    IsSubstantive,
    Lit,
    Not,
    Opaque,
    Or,
    Range,
    Truth,
    VarRef,
    evaluate,
)
from survey_sim.ir.models import Item
from survey_sim.ir.values import MissingCode, NotApplicable, Unanswered, Valid

from survey_sim.correspondence.records import Equivalences

EQUIVALENT, DIFFERENT, NOT_VERIFIABLE = "equivalente", "diferente", "no_verificable"


@dataclass
class VarLink:
    """Variable de una pareja: nombre canónico y nombres e ítems en cada lado."""

    canon: str
    design: str
    program: str
    design_item: Item
    program_item: Item
    declared: bool


@dataclass
class ConditionComparison:
    result: str
    method: Optional[str]
    reasons: list[str] = field(default_factory=list)
    variables: list[dict] = field(default_factory=list)
    domain_size: Optional[int] = None
    evaluated: int = 0
    complete: bool = False
    limit: int = 0
    counterexample: Optional[dict] = None
    prerequisites: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "result": self.result, "method": self.method, "reasons": self.reasons, "variables": self.variables,
            "limits": {"domain_size": self.domain_size, "evaluated": self.evaluated, "complete": self.complete,
                       "enumeration_limit": self.limit},
            "counterexample": self.counterexample, "prerequisites": self.prerequisites,
        }


# --------------------------------------------------------------------------
# Utilidades sobre el AST
# --------------------------------------------------------------------------


def var_names(c: Any) -> set[str]:
    out: set[str] = set()

    def walk(n: Any) -> None:
        if isinstance(n, VarRef):
            out.add(n.name)
        elif isinstance(n, (Compare,)):
            walk(n.left)
            walk(n.right)
        elif isinstance(n, InSet):
            out.add(n.var)
        elif isinstance(n, (IsAnswered, IsSubstantive)):
            out.add(n.var)
        elif isinstance(n, (And, Or)):
            for a in n.args:
                walk(a)
        elif isinstance(n, Not):
            walk(n.arg)
        elif isinstance(n, (Func, FuncValue)):
            for a in n.args:
                walk(a)

    walk(c)
    return out


def has_opaque(c: Any) -> bool:
    if isinstance(c, Opaque):
        return True
    if isinstance(c, (And, Or)):
        return any(has_opaque(a) for a in c.args)
    if isinstance(c, Not):
        return has_opaque(c.arg)
    if isinstance(c, (Func, FuncValue)):
        return c.external or any(has_opaque(a) for a in c.args)
    if isinstance(c, Compare):
        return has_opaque(c.left) or has_opaque(c.right)
    return False


def canonical(c: Any, rename: dict[str, str], lit_map: dict[str, dict]) -> Any:
    """Forma canónica para la identidad estructural.

    Renombra variables al nombre canónico de su pareja, ordena los operandos de
    And/Or y los elementos de InSet, y aplica las equivalencias declaradas a los
    literales de la variable correspondiente (literal → texto declarado).
    """

    def lit(v: Any, var: Optional[str]) -> Any:
        if var is not None and var in lit_map and not isinstance(v, str) and v in lit_map[var]:
            return {"lit": lit_map[var][v], "t": "str"}
        return {"lit": v, "t": type(v).__name__}

    def go(n: Any, ctx_var: Optional[str] = None) -> Any:
        if isinstance(n, Const):
            return {"const": n.value}
        if isinstance(n, Opaque):
            return {"opaque": n.raw}
        if isinstance(n, Not):
            # NO (NO x) = x también en la lógica de Kleene (¬¬U = U): es una
            # normalización sin pérdida, no una coerción.
            if isinstance(n.arg, Not):
                return go(n.arg.arg)
            return {"not": go(n.arg)}
        if isinstance(n, (And, Or)):
            args = sorted((go(a) for a in n.args), key=lambda x: json.dumps(x, sort_keys=True, default=str))
            return {"and" if isinstance(n, And) else "or": args}
        if isinstance(n, InSet):
            items = []
            for x in n.items:
                if isinstance(x, Lit):
                    items.append(lit(x.value, n.var))
                elif isinstance(x, Range):
                    items.append({"range": [x.lo, x.hi]})
                else:
                    items.append({"other": str(x)})
            items = sorted(items, key=lambda x: json.dumps(x, sort_keys=True, default=str))
            return {"in": rename.get(n.var, "?" + n.var), "items": items}
        if isinstance(n, Compare):
            lv = n.left.name if isinstance(n.left, VarRef) else None
            rv = n.right.name if isinstance(n.right, VarRef) else None

            def opnd(o: Any, other_var: Optional[str]) -> Any:
                if isinstance(o, VarRef):
                    return {"var": rename.get(o.name, "?" + o.name)}
                if isinstance(o, Lit):
                    return lit(o.value, other_var)
                return {"expr": str(o)}

            return {"cmp": n.op.value, "l": opnd(n.left, rv), "r": opnd(n.right, lv)}
        if isinstance(n, IsAnswered):
            return {"answered": rename.get(n.var, "?" + n.var)}
        if isinstance(n, IsSubstantive):
            return {"substantive": rename.get(n.var, "?" + n.var)}
        return {"node": type(n).__name__, "repr": str(n)}

    return go(c)


# --------------------------------------------------------------------------
# Dominio y enumeración
# --------------------------------------------------------------------------


def _options(it: Item):
    return it.options or (it.grid.shared_options if it.grid else ())


def _code_key(c: str):
    return (0, int(c), c) if c.isdigit() else (1, 0, c)


def _domain(link: VarLink, eq: Equivalences) -> Optional[list[tuple[str, Any, Any]]]:
    """Dominio finito de una variable: (etiqueta, estado en diseño, estado en programa)."""
    do, po = _options(link.design_item), _options(link.program_item)
    if not do and not po:
        return None
    dmiss = {o.code: o.is_missing_code for o in do}
    pmiss = {o.code: o.is_missing_code for o in po}
    dmap = eq.for_var("design", link.design)
    pmap = eq.for_var("program", link.program)
    out = []
    for c in sorted(set(dmiss) | set(pmiss), key=_code_key):
        dv = dmap.get(c, c)
        pv = pmap.get(c, c)
        ds = MissingCode(value=dv) if dmiss.get(c) else Valid(value=dv)
        ps = MissingCode(value=pv) if pmiss.get(c) else Valid(value=pv)
        out.append((f"codigo:{c}", ds, ps))
    out.append(("UNANSWERED", Unanswered(), Unanswered()))
    out.append(("NOT_APPLICABLE", NotApplicable(), NotApplicable()))
    return out


def compare_conditions(cd: Any, cp: Any, links: dict[str, VarLink], *, item_pair_declared: bool,
                       comparison_enabled: bool, eq: Equivalences, limit: int) -> ConditionComparison:
    """Compara una condición del diseño (``cd``) con una del programa (``cp``).

    ``links`` va de nombre de variable del diseño a su pareja; las variables del
    programa se buscan por su nombre en las mismas parejas.
    """
    by_prog = {l.program: l for l in links.values()}
    dvars, pvars = var_names(cd), var_names(cp)
    reasons: list[str] = []
    missing = sorted([f"design:{v}" for v in dvars if v not in links] + [f"program:{v}" for v in pvars if v not in by_prog])
    used: dict[str, VarLink] = {}
    for v in dvars:
        if v in links:
            used[links[v].canon] = links[v]
    for v in pvars:
        if v in by_prog:
            used[by_prog[v].canon] = by_prog[v]
    var_info = [{"canon": k, "design": l.design, "program": l.program, "pair_declared": l.declared}
                for k, l in sorted(used.items())]
    prereq = {
        "item_pair_declared": item_pair_declared,
        "variable_pairs_declared": not missing and all(l.declared for l in used.values()),
        "comparison_enabled": comparison_enabled,
    }
    prereq_ok = all(prereq.values())
    res = ConditionComparison(NOT_VERIFIABLE, None, reasons, var_info, limit=limit, prerequisites=prereq)
    if missing:
        reasons.append("variables_sin_pareja:" + ",".join(missing))
        return res
    opaque = has_opaque(cd) or has_opaque(cp)
    if opaque:
        reasons.append("opaque")

    rename_d = {l.design: k for k, l in used.items()}
    rename_p = {l.program: k for k, l in used.items()}
    inv_p = {l.program: {v: t for t, v in eq.for_var("program", l.program).items()} for l in used.values()}
    inv_d = {l.design: {v: t for t, v in eq.for_var("design", l.design).items()} for l in used.values()}
    if not opaque and canonical(cd, rename_d, inv_d) == canonical(cp, rename_p, inv_p):
        res.method = "estructural"
        if prereq_ok:
            res.result = EQUIVALENT
        else:
            reasons.append("requisitos_previos_no_cumplidos")
        return res

    domains = {}
    for k, l in sorted(used.items()):
        dom = _domain(l, eq)
        if dom is None:
            reasons.append(f"dominio_no_finito:{k}")
            return res
        domains[k] = dom
    keys = sorted(domains)
    size = 1
    for k in keys:
        size *= len(domains[k])
    res.domain_size = size
    res.method = "enumeracion"
    any_unknown = False
    n = 0
    for combo in itertools.product(*(domains[k] for k in keys)):
        if n >= limit:
            break
        n += 1
        env_d = {used[k].design: combo[i][1] for i, k in enumerate(keys)}
        env_p = {used[k].program: combo[i][2] for i, k in enumerate(keys)}
        ed, ep = evaluate(cd, env_d), evaluate(cp, env_p)
        if Truth.UNKNOWN in (ed.truth, ep.truth):
            any_unknown = True
            continue
        if ed.truth is not ep.truth:
            res.evaluated = n
            res.complete = n == size
            res.result = DIFFERENT
            res.counterexample = {
                "assignment": {k: combo[i][0] for i, k in enumerate(keys)},
                "design": ed.truth.value, "program": ep.truth.value,
            }
            return res
    res.evaluated = n
    res.complete = n == size
    if not res.complete:
        reasons.append("limite_de_enumeracion_alcanzado")
    if any_unknown:
        reasons.append("evaluaciones_U")
    if res.complete and not any_unknown and not opaque:
        if prereq_ok:
            res.result = EQUIVALENT
        else:
            reasons.append("requisitos_previos_no_cumplidos")
    return res
