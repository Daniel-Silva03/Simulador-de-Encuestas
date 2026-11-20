"""Árbol estático de recorridos posibles (v1.1, §7.4).

Es un análisis estructural, separado de la base de agentes. Cada rama es:

- ``rama_demostrada``: su condición se evalúa solo con reglas explícitas y hay
  un **testigo**, una asignación del dominio finito compatible con las
  restricciones explícitas anteriores del mismo recorrido, que hace verdadera
  la condición de la rama. El testigo se incluye.
- ``rama_estructural_no_verificada``: identificada en la estructura, sin
  posibilidad semántica demostrada (condición opaca, semántica inferida o no
  confirmada, U, dominio no finito o sin testigo dentro del límite).

En modo experimental, las ramas que dependen de hipótesis siguen siendo
estructurales no verificadas y llevan la marca HIPOTÉTICO. El árbol describe
alternativas estructurales: **no prueba que esos recorridos ocurran en el
programa**; una rama demostrada solo es satisfacible según las reglas
explícitas del instrumento.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Optional

from survey_sim.compare.conditions import has_opaque, var_names
from survey_sim.flowmodel import FLOW, FlowItem, FlowModel
from survey_sim.ir.conditions import Not, Truth, evaluate
from survey_sim.ir.models import Certainty
from survey_sim.ir.values import MissingCode, NotApplicable, Unanswered, Valid

from .engine import EXPERIMENTAL

DEMONSTRATED, STRUCTURAL = "rama_demostrada", "rama_estructural_no_verificada"
DISCLAIMER = ("El árbol describe alternativas estructurales del instrumento. No prueba que esos recorridos ocurran "
              "realmente en el programa. Una rama_demostrada solo demuestra que es satisfacible según las reglas "
              "explícitas del instrumento, no que el programa la ejecute así.")
MAX_NODES = 2000
WITNESS_LIMIT = 10000


@dataclass
class Node:
    id: int
    parent: Optional[int]
    item: Optional[str]
    decision: str
    branch: str
    klass: Optional[str]
    witness: Optional[dict] = None
    hypotheses: list[str] = field(default_factory=list)
    hypothetical: bool = False
    reasons: list[str] = field(default_factory=list)
    end: Optional[str] = None


class _Tree:
    def __init__(self, flow: FlowModel, mode: str, grid_opts: dict, max_nodes: int, limit: int):
        self.flow = flow
        self.mode = mode
        self.grid_opts = grid_opts
        self.max_nodes = max_nodes
        self.limit = limit
        self.nodes: list[Node] = []
        self.truncated = False

    def add(self, **kw) -> Optional[Node]:
        if len(self.nodes) >= self.max_nodes:
            self.truncated = True
            return None
        n = Node(id=len(self.nodes), **kw)
        self.nodes.append(n)
        return n

    def domain(self, var: str) -> Optional[list[tuple[str, Any]]]:
        fi = self.flow.by_id.get(var)
        if fi is None:
            return None
        opts = fi.item.options or self.grid_opts.get(var, ())
        if not opts:
            return None
        out = [(f"codigo:{o.code}", MissingCode(value=o.code) if o.is_missing_code else Valid(value=o.code)) for o in opts]
        return out + [("UNANSWERED", Unanswered()), ("NOT_APPLICABLE", NotApplicable())]

    def witness(self, cond: Any, constraints: dict[str, set]) -> tuple[Optional[dict], list[str]]:
        """Busca una asignación que haga V la condición, dentro de las restricciones del recorrido."""
        if has_opaque(cond):
            return None, ["opaque"]
        vs = sorted(var_names(cond))
        doms = []
        for v in vs:
            d = self.domain(v)
            if d is None:
                return None, [f"dominio_no_finito:{v}"]
            if v in constraints:
                d = [x for x in d if x[0] in constraints[v]]
            doms.append(d)
        n = 0
        for combo in itertools.product(*doms):
            n += 1
            if n > self.limit:
                return None, ["limite_de_testigos"]
            r = evaluate(cond, {v: combo[i][1] for i, v in enumerate(vs)})
            if r.truth is Truth.TRUE:
                return {v: combo[i][0] for i, v in enumerate(vs)}, []
        return None, ["sin_testigo"]

    def classify(self, cond: Any, certainty: Certainty, hyp: Optional[str], constraints: dict) -> tuple[str, Optional[dict], list[str], list[str], bool]:
        hyps = [hyp] if hyp and certainty is not Certainty.EXPLICIT else []
        hypothetical = bool(hyps) and self.mode == EXPERIMENTAL
        if certainty is not Certainty.EXPLICIT:
            return STRUCTURAL, None, ["regla_no_explicita"], hyps, hypothetical
        w, why = self.witness(cond, constraints)
        return (DEMONSTRATED if w is not None else STRUCTURAL), w, why, hyps, hypothetical

    def walk(self, pos: int, parent: Optional[int], constraints: dict[str, set], depth: int) -> None:
        items = self.flow.items
        while pos < len(items):
            if self.truncated:
                return
            fi = items[pos]
            it = fi.item
            pres_opq = [o for o in fi.opaque if o.klass == FLOW and any(k in o.rule_id for k in ("/filtros", "/habil_cnd", "/oculta_cnd"))]
            if fi.show or pres_opq:
                branches = []
                if pres_opq or any(r.certainty is not Certainty.EXPLICIT or r.show is None for r in fi.show):
                    hyps = [r.hypothesis for r in fi.show if r.hypothesis]
                    reason = ["opaque"] if pres_opq else ["regla_no_explicita"]
                    for b, nc in (("presentada", None), ("excluida", "NOT_APPLICABLE")):
                        branches.append((b, STRUCTURAL, None, reason, hyps, self.mode == EXPERIMENTAL and bool(hyps), nc))
                else:
                    show = fi.show[0].show if len(fi.show) == 1 else None
                    if show is None:
                        from survey_sim.ir.conditions import And
                        show = And(args=tuple(r.show for r in fi.show))
                    for b, cond, nc in (("presentada", show, None), ("excluida", Not(arg=show), "NOT_APPLICABLE")):
                        k, w, why, hyps, hy = self.classify(cond, Certainty.EXPLICIT, None, constraints)
                        if k == STRUCTURAL and why == ["sin_testigo"]:
                            continue  # rama imposible según las reglas explícitas
                        branches.append((b, k, w, why, hyps, hy, nc))
                if len(branches) > 1:
                    for b, k, w, why, hyps, hy, nc in branches:
                        n = self.add(parent=parent, item=it.id, decision="presentacion", branch=b, klass=k, witness=w,
                                     hypotheses=hyps, hypothetical=hy, reasons=why)
                        if n is None:
                            return
                        c2 = dict(constraints)
                        if nc:
                            c2[it.id] = {"NOT_APPLICABLE"}
                            self.walk(pos + 1, n.id, c2, depth + 1)
                        else:
                            self._after_shown(pos, n.id, c2, depth + 1)
                    return
                if branches and branches[0][6]:
                    constraints = dict(constraints)
                    constraints[it.id] = {"NOT_APPLICABLE"}
                    pos += 1
                    continue
            self._after_shown(pos, parent, constraints, depth)
            return
        self.add(parent=parent, item=None, decision="fin", branch="fin del instrumento", klass=None, end="fin")

    def _after_shown(self, pos: int, parent: Optional[int], constraints: dict, depth: int) -> None:
        items = self.flow.items
        fi = items[pos]
        it = fi.item
        flow_opq = [o for o in fi.opaque if o.klass == FLOW and not any(k in o.rule_id for k in ("/filtros", "/habil_cnd", "/oculta_cnd"))]
        opt_navs = [n for n in fi.nav if n.codes]
        cond_navs = [n for n in fi.nav if not n.codes]
        if not (opt_navs or cond_navs or flow_opq):
            self.walk(pos + 1, parent, constraints, depth)
            return
        branches = []
        # Navegación por opción: una rama por destino.
        groups: dict[tuple, list] = {}
        for n in opt_navs:
            groups.setdefault((n.kind, n.target), []).append(n)
        nav_codes = {c for n in opt_navs for c in n.codes}
        all_codes = [o.code for o in (it.options or self.grid_opts.get(it.id, ()))]
        cont_codes = [c for c in all_codes if c not in nav_codes]
        for (kind, target), ns in sorted(groups.items(), key=lambda x: (x[0][0], x[0][1] or "")):
            codes = sorted({c for n in ns for c in n.codes})
            allowed = constraints.get(it.id)
            cands = [c for c in codes if allowed is None or f"codigo:{c}" in allowed]
            explicit = all(n.certainty is Certainty.EXPLICIT for n in ns) and not any(
                c in {x[0] for x in fi.option_display} for c in codes)
            hyps = [n.hypothesis for n in ns if n.hypothesis]
            if not cands:
                continue
            if explicit:
                branches.append((f"opcion {','.join(cands)} → {kind}:{target}", DEMONSTRATED, {it.id: f"codigo:{cands[0]}"}, [], [], False,
                                 kind, target, {it.id: {f"codigo:{c}" for c in cands}}))
            else:
                branches.append((f"opcion {','.join(cands)} → {kind}:{target}", STRUCTURAL, None, ["regla_no_explicita"], hyps,
                                 self.mode == EXPERIMENTAL and bool(hyps), kind, target, {it.id: {f"codigo:{c}" for c in cands}}))
        cond_rest: Any = None
        for n in cond_navs:
            if n.condition is None:
                k, w, why, hyps, hy = STRUCTURAL, None, ["condicion_implicita"], ([n.hypothesis] if n.hypothesis else []), \
                    self.mode == EXPERIMENTAL and bool(n.hypothesis)
                if n.certainty is Certainty.EXPLICIT:
                    k, why = DEMONSTRATED, []
            else:
                k, w, why, hyps, hy = self.classify(n.condition, n.certainty, n.hypothesis, constraints)
                if k == STRUCTURAL and why == ["sin_testigo"]:
                    continue
            branches.append((f"regla {n.rule_id} → {n.kind}:{n.target}", k, w, why, hyps, hy, n.kind, n.target, {}))
        for o in flow_opq:
            branches.append((f"regla opaca {o.rule_id} aplica (destino desconocido)", STRUCTURAL, None, ["opaque"], [], False,
                             "unknown", None, {}))
        # Rama «continúa» (ninguna navegación aplica).
        cont_cons = {}
        if opt_navs:
            cont_cons = {it.id: {f"codigo:{c}" for c in cont_codes} | {"UNANSWERED"}}
        cont_class = DEMONSTRATED if (opt_navs and cont_codes and not cond_navs and not flow_opq) else STRUCTURAL
        if not opt_navs and not cond_navs and flow_opq:
            cont_class = STRUCTURAL
        branches.append(("continúa", cont_class, {it.id: f"codigo:{cont_codes[0]}"} if cont_class == DEMONSTRATED else None,
                         [] if cont_class == DEMONSTRATED else ["alternativa_estructural"], [], False, "continue", None, cont_cons))
        for label, k, w, why, hyps, hy, kind, target, cons in branches:
            n = self.add(parent=parent, item=it.id, decision="navegacion", branch=label, klass=k, witness=w,
                         hypotheses=hyps, hypothetical=hy, reasons=why)
            if n is None:
                return
            c2 = dict(constraints)
            for v, s in cons.items():
                c2[v] = s if v not in c2 else c2[v] & s
            if kind == "terminate":
                self.add(parent=n.id, item=None, decision="fin", branch=f"terminación {target}", klass=None, end="terminacion")
            elif kind == "unknown":
                self.add(parent=n.id, item=None, decision="fin", branch="destino desconocido", klass=None, end="indeterminado")
            elif kind == "goto":
                t = self.flow.index(target or "")
                if t is None:
                    self.add(parent=n.id, item=None, decision="fin", branch=f"destino inexistente {target}", klass=None, end="destino_inexistente")
                elif t <= pos:
                    self.add(parent=n.id, item=None, decision="fin", branch=f"salto hacia atrás {target}", klass=None, end="salto_hacia_atras")
                else:
                    for k2 in range(pos + 1, t):
                        c2[self.flow.items[k2].item.id] = {"NOT_APPLICABLE"}
                    self.walk(t, n.id, c2, depth + 1)
            else:
                self.walk(pos + 1, n.id, c2, depth + 1)


def build_tree(flow: FlowModel, mode: str, grid_opts: dict, *, max_nodes: int = MAX_NODES, limit: int = WITNESS_LIMIT) -> dict:
    t = _Tree(flow, mode, grid_opts, max_nodes, limit)
    root = t.add(parent=None, item=None, decision="inicio", branch="inicio", klass=None)
    for o in flow.start_opaque:
        if o.klass == FLOW:
            t.add(parent=root.id, item=None, decision="fin", branch=f"regla opaca sin posición {o.rule_id}", klass=STRUCTURAL,
                  reasons=["opaque"], end="indeterminado")
    t.walk(0, root.id, {}, 0)
    counts = {DEMONSTRATED: 0, STRUCTURAL: 0}
    for n in t.nodes:
        if n.klass in counts:
            counts[n.klass] += 1
    return {
        "disclaimer": DISCLAIMER,
        "mode": mode,
        "limits": {"max_nodes": max_nodes, "witness_limit": limit, "truncated": t.truncated},
        "counts": counts,
        "nodes": [n.__dict__ for n in t.nodes],
    }
