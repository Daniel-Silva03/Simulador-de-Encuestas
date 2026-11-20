"""Modelo de flujo común a la comparación y a la simulación.

Traduce la IR de un instrumento (diseño o programa) a una secuencia de
preguntas con sus reglas de presentación, navegación, validación y cálculo,
cada una con su certeza y, si depende de semántica no confirmada, la
hipótesis de la que depende. **No ejecuta nada**: solo ordena lo que la IR ya
contiene. La IR no se modifica.

Semántica usada (todas son hipótesis salvo lo explícito de la IR):

- Diseño: el universo de una pregunta la presenta si su condición es V; la
  navegación de una opción se aplica si se elige esa opción; las reglas
  ``skip``/``terminate`` sin condición del Word llevan la condición implícita
  (D-12) y se sitúan tras su pregunta (``word:after_item``).
- Programa (GIM): ``filtros`` excluye la pregunta cuando la condición es V
  (hipótesis ``filtros`` del registro, inferred); ``habil_cnd`` la presenta
  cuando es V y ``oculta_cnd`` la oculta cuando es V (sin entrada en el
  registro); ``saltos`` salta al destino cuando es V; ``controles`` señala un
  error cuando es V; ``valdefs`` calcula un valor. Todas son ``inferred``.
- Reglas opacas: se sitúan en su pregunta (GIM: prefijo del id; Word: bloque
  del documento) y nunca se ejecutan.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from survey_sim.ir.conditions import Not
from survey_sim.ir.models import Certainty, Instrument, Item, OpaqueRule, Rule, RuleKind

FLOW = "flow"
VALIDATION = "validation"
COMPUTATION = "computation"

_BLOCK = re.compile(r"#block(\d+)")
_GIM_KIND = re.compile(r"/(filtros|saltos|controles|valdefs|habil_cnd|oculta_cnd)(?:\[\d+\])?$")
_GIM_KIND_CLASS = {"filtros": FLOW, "saltos": FLOW, "habil_cnd": FLOW, "oculta_cnd": FLOW,
                   "controles": VALIDATION, "valdefs": COMPUTATION}


@dataclass(frozen=True)
class ShowRule:
    """Regla de presentación normalizada: la pregunta se presenta si ``show`` es V."""

    rule_id: str
    origin: str  # universe | filter | enable | hide
    show: Any  # condición (AST) en forma «presentar si»
    raw_condition: Any
    certainty: Certainty
    hypothesis: Optional[str]


@dataclass(frozen=True)
class Nav:
    """Navegación tras responder una pregunta."""

    rule_id: str
    kind: str  # goto | terminate | continue | unknown
    target: Optional[str]
    condition: Any  # None = por opción (codes) o incondicional
    codes: tuple[str, ...]  # navegación por opción: códigos que la disparan
    certainty: Certainty
    hypothesis: Optional[str]
    implicit: bool = False


@dataclass(frozen=True)
class Check:
    rule_id: str
    condition: Any
    action: Optional[str]
    certainty: Certainty
    hypothesis: Optional[str]


@dataclass(frozen=True)
class Calc:
    rule_id: str
    target: str
    condition: Any
    certainty: Certainty
    hypothesis: Optional[str]


@dataclass(frozen=True)
class Opq:
    rule_id: str
    klass: str  # flow | validation | computation
    reason: str


@dataclass
class FlowItem:
    item: Item
    pos: int
    show: list[ShowRule] = field(default_factory=list)
    option_display: list[tuple[str, Any, Certainty, Optional[str]]] = field(default_factory=list)
    nav: list[Nav] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    calcs: list[Calc] = field(default_factory=list)
    opaque: list[Opq] = field(default_factory=list)


@dataclass
class FlowModel:
    role: str
    items: list[FlowItem]
    start_opaque: list[Opq]
    unplaced: list[str]
    by_id: dict[str, FlowItem]
    terminations: dict[str, str]  # código -> tipo
    variables: dict[str, Optional[str]]  # nombre -> item_id

    def index(self, item_id: str) -> Optional[int]:
        fi = self.by_id.get(item_id)
        return fi.pos if fi else None


def _hyp_for(role: str, kind: RuleKind, rule: Rule) -> Optional[str]:
    if rule.certainty is Certainty.EXPLICIT:
        return None
    if role == "program":
        names = {RuleKind.FILTER: "filtros", RuleKind.ENABLE: "habil_cnd", RuleKind.HIDE: "oculta_cnd",
                 RuleKind.SKIP: "saltos", RuleKind.VALIDATION: "controles", RuleKind.COMPUTATION: "valdefs"}
        return f"gim:{names.get(kind, kind.value)}"
    return f"word:{kind.value}"


def _block(loc: str) -> Optional[int]:
    m = _BLOCK.search(loc or "")
    return int(m.group(1)) if m else None


def build(inst: Instrument) -> FlowModel:
    role = inst.role.value
    items = [i for s in inst.sections for i in s.items]
    fis = [FlowItem(i, k) for k, i in enumerate(items)]
    by_id = {f.item.id: f for f in fis}
    start_opq: list[Opq] = []
    unplaced: list[str] = []

    for f in fis:
        it = f.item
        if it.universe is not None:
            u = it.universe
            f.show.append(ShowRule(f"{it.id}#universe", "universe", u.condition, u.condition, u.certainty,
                                   None if u.certainty is Certainty.EXPLICIT else f"{role}:universe"))
        for o in it.options:
            if o.display_if is not None:
                f.option_display.append((o.code, o.display_if, o.certainty,
                                         None if o.certainty is Certainty.EXPLICIT else f"{role}:option_display"))
            if o.routing is not None and o.routing.action != "continue":
                f.nav.append(Nav(f"{it.id}#opt:{o.code}", o.routing.action, o.routing.target, None, (o.code,),
                                 o.certainty, None if o.certainty is Certainty.EXPLICIT else f"{role}:option_routing"))

    item_blocks = [(_block(f.item.provenance.locator), f) for f in fis]
    for r in inst.rules:
        if isinstance(r, OpaqueRule):
            klass = FLOW
            owner: Optional[FlowItem] = None
            if role == "program":
                head = r.id.split("/", 1)[0]
                owner = by_id.get(head)
                m = _GIM_KIND.search(r.id)
                klass = _GIM_KIND_CLASS.get(m.group(1), FLOW) if m else FLOW
            else:
                after = next((n[len("word:after_item="):] for n in r.notes if n.startswith("word:after_item=")), None)
                owner = by_id.get(after) if after else None
                if owner is None:
                    b = _block(r.provenance.locator)
                    if b is not None:
                        prev = [f for (bb, f) in item_blocks if bb is not None and bb < b]
                        owner = prev[-1] if prev else None
            if owner is None:
                start_opq.append(Opq(r.id, klass, r.reason))
            else:
                owner.opaque.append(Opq(r.id, klass, r.reason))
            continue
        hyp = _hyp_for(role, r.kind, r)
        if r.kind in (RuleKind.FILTER, RuleKind.ENABLE, RuleKind.HIDE):
            f = by_id.get(r.target or "")
            if f is None:
                unplaced.append(r.id)
                continue
            c = r.when
            show = Not(arg=c) if r.kind in (RuleKind.FILTER, RuleKind.HIDE) and c is not None else c
            f.show.append(ShowRule(r.id, r.kind.value, show, c, r.certainty, hyp))
        elif r.kind in (RuleKind.SKIP, RuleKind.TERMINATE):
            if role == "program":
                owner = by_id.get(r.id.split("/", 1)[0])
            else:
                after = next((n[len("word:after_item="):] for n in r.notes if n.startswith("word:after_item=")), None)
                owner = by_id.get(after or "")
            if owner is None:
                unplaced.append(r.id)
                continue
            implicit = any(n.startswith("word:implicit_condition") for n in r.notes) or r.when is None
            owner.nav.append(Nav(r.id, "goto" if r.kind is RuleKind.SKIP else "terminate", r.target, r.when, (),
                                 r.certainty, hyp, implicit))
        elif r.kind is RuleKind.VALIDATION:
            f = by_id.get(r.target or "")
            if f is None:
                unplaced.append(r.id)
                continue
            f.checks.append(Check(r.id, r.when, r.action.value if r.action else None, r.certainty, hyp))
        elif r.kind in (RuleKind.COMPUTATION, RuleKind.RECODE):
            f = by_id.get(r.target or "")
            if f is None:
                unplaced.append(r.id)
                continue
            f.calcs.append(Calc(r.id, r.target or "", r.when, r.certainty, hyp))
        else:
            unplaced.append(r.id)
    terms = {t.code: t.kind.value for t in inst.terminations}
    variables = {v.name: v.item_id for v in inst.variables}
    return FlowModel(role, fis, start_opq, unplaced, by_id, terms, variables)
