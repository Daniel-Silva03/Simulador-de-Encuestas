"""Comparador de diseño frente a programa (v1.0, 7.2–7.3 y sección 8; v1.1, §3–§5).

Solo se ejecuta con ``comparison_enabled = true`` (resultado CORRESPONDEN).
Produce hallazgos ``scope = pair`` con nivel de evidencia calculado a partir
de la certeza de los elementos (``derive_evidence_level``), con dos límites
adicionales de la v1.1:

- §3.3: un hallazgo solo puede ser «error comprobado» si la pareja está
  ``declarada``; en otro caso se marca ``heuristic`` (máximo «sospecha»).
- §5: una comparación de condiciones «no verificable» da un hallazgo «no
  verificable».

Los hallazgos no copian texto libre del instrumento: solo identificadores,
códigos y estados.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from survey_sim.correspondence.matching import DECLARED, Matching, Pair, klass
from survey_sim.correspondence.records import Equivalences
from survey_sim.flowmodel import FlowItem, FlowModel, Nav
from survey_sim.ir.conditions import And, InSet, Lit, Or
from survey_sim.ir.models import (
    Certainty,
    EvidenceLevel,
    Finding,
    FindingCategory,
    FindingRef,
    FindingScope,
    Instrument,
    Item,
    Severity,
    derive_evidence_level,
    finding_id,
)
from survey_sim.util.textnorm import normalize_text

from .conditions import DIFFERENT, EQUIVALENT, NOT_VERIFIABLE, VarLink, compare_conditions, has_opaque

C = FindingCategory


@dataclass
class Comparison:
    findings: list[Finding] = field(default_factory=list)
    conditions: list[dict] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


def _ref(side: str, el: str, it: Optional[Item], certainty: Certainty, *, missing: bool = False) -> FindingRef:
    return FindingRef(side=side, element_id=el, provenance=it.provenance if it is not None else None,
                      certainty=certainty, missing=missing)


def _finding(check: str, cat: FindingCategory, sev: Severity, refs: list[FindingRef], explanation: str,
             *, pair: Optional[Pair], depends: tuple[str, ...] = (), suggested: Optional[str] = None,
             force: Optional[EvidenceLevel] = None, heuristic: bool = False) -> Finding:
    heur = heuristic or (pair is not None and pair.state != DECLARED)
    level = derive_evidence_level(refs, heuristic=heur, depends_on_unconfirmed=depends)
    if force is not None:
        level = force
    return Finding(id=finding_id(check, refs), check_id=check, scope=FindingScope.PAIR, evidence_level=level,
                   category=cat, severity=sev, refs=tuple(refs), heuristic=heur, depends_on_unconfirmed=depends,
                   explanation=explanation, suggested_check=suggested)


def _opts(it: Item):
    return it.options or (it.grid.shared_options if it.grid else ())


def _hyp_names(*hyps: Optional[str]) -> tuple[str, ...]:
    out = []
    for h in hyps:
        if h and h.startswith("gim:"):
            out.append(h[4:])
        elif h:
            out.append(h)
    return tuple(dict.fromkeys(out))


def _or_codes(var: str, codes: tuple[str, ...]):
    return InSet(var=var, items=tuple(Lit(value=c) for c in codes))


def _combine(conds: list[Any], cls) -> Any:
    conds = [c for c in conds if c is not None]
    if not conds:
        return None
    return conds[0] if len(conds) == 1 else cls(args=tuple(conds))


def run(design: Instrument, program: Instrument, dflow: FlowModel, pflow: FlowModel, matching: Matching, *,
        comparison_enabled: bool, eq: Equivalences, limit: int) -> Comparison:
    out = Comparison()
    if not comparison_enabled:
        out.limitations.append("comparación no habilitada: solo se muestra el emparejamiento en modo diagnóstico")
        return out
    links: dict[str, VarLink] = {}
    for k, p in enumerate(matching.pairs):
        di, pi = dflow.by_id[p.design].item, pflow.by_id[p.program].item
        links[p.design] = VarLink(f"V{k + 1:03d}", p.design, p.program, di, pi, p.declared)
    f = out.findings

    # Ítems faltantes, sobrantes, ambiguos y misma etiqueta con contenido distinto.
    for d in matching.only_design:
        it = dflow.by_id[d].item
        f.append(_finding("pair:solo_diseno", C.ITEM_FALTANTE_O_SOBRANTE, Severity.ALTA,
                          [_ref("design", d, it, it.certainty), _ref("program", f"(sin pareja de {d})", None, Certainty.INFERRED, missing=True)],
                          f"La pregunta {d} del diseño no tiene pareja en el programa (SOLO_DISENO).", pair=None,
                          suggested="Confirmar si falta en el programa o declarar la pareja."))
    for p in matching.only_program:
        it = pflow.by_id[p].item
        f.append(_finding("pair:solo_programa", C.ITEM_FALTANTE_O_SOBRANTE, Severity.ALTA,
                          [_ref("program", p, it, it.certainty), _ref("design", f"(sin pareja de {p})", None, Certainty.INFERRED, missing=True)],
                          f"La variable {p} del programa no tiene pareja en el diseño (SOLO_PROGRAMA).", pair=None,
                          suggested="Confirmar si sobra en el programa o declarar la pareja."))
    for a in matching.ambiguous:
        it = dflow.by_id[a["design"]].item
        f.append(_finding("pair:ambigua", C.ESTRUCTURA, Severity.MEDIA,
                          [_ref("design", a["design"], it, it.certainty)] +
                          [_ref("program", c, pflow.by_id[c].item, pflow.by_id[c].item.certainty) for c in a["candidates"]],
                          f"La pregunta {a['design']} tiene varias candidatas con puntaje parecido (AMBIGUA): "
                          f"{', '.join(a['candidates'])}.", pair=None, heuristic=True,
                          suggested="Declarar la pareja correcta en el archivo de pareos."))
    for s in matching.same_label_different:
        di, pi = dflow.by_id[s["design"]].item, pflow.by_id[s["program"]].item
        f.append(_finding("pair:misma_etiqueta_contenido_distinto", C.ESTRUCTURA, Severity.MEDIA,
                          [_ref("design", s["design"], di, di.certainty), _ref("program", s["program"], pi, pi.certainty)],
                          f"{s['design']} y {s['program']} tienen la misma etiqueta y contenido distinto "
                          f"(puntaje {s['score']}); no se emparejan.", pair=None, heuristic=True,
                          suggested="Revisar si es la misma pregunta; si lo es, declarar la pareja."))

    for p in matching.pairs:
        dfi, pfi = dflow.by_id[p.design], pflow.by_id[p.program]
        di, pi = dfi.item, pfi.item
        if normalize_text(di.text.clean) != normalize_text(pi.text.clean):
            f.append(_finding("pair:texto", C.TEXTO, Severity.BAJA,
                              [_ref("design", di.id, di, di.certainty), _ref("program", pi.id, pi, pi.certainty)],
                              f"El enunciado de {di.id} difiere del de {pi.id} (similitud {p.parts['text']}).", pair=p))
        if di.type != pi.type:
            f.append(_finding("pair:tipo", C.TIPO, Severity.MEDIA,
                              [_ref("design", di.id, di, di.certainty), _ref("program", pi.id, pi, pi.certainty)],
                              f"Tipo distinto: {di.id} es {di.type.value} y {pi.id} es {pi.type.value}.", pair=p))
        do, po = {o.code: o for o in _opts(di)}, {o.code: o for o in _opts(pi)}
        for code in sorted(set(do) ^ set(po)):
            side_has = "design" if code in do else "program"
            o = do.get(code) or po.get(code)
            other_item = pi if side_has == "design" else di
            f.append(_finding("pair:codigos", C.CODIGOS, Severity.ALTA,
                              [_ref(side_has, f"{(di if side_has == 'design' else pi).id}#opt:{code}", None, o.certainty),
                               _ref("program" if side_has == "design" else "design", other_item.id, other_item, other_item.certainty)],
                              f"El código {code} existe en {'el diseño' if side_has == 'design' else 'el programa'} "
                              f"({di.id} / {pi.id}) y no en el otro lado.", pair=p))
        for code in sorted(set(do) & set(po)):
            a, b = do[code], po[code]
            if normalize_text(a.label.clean) != normalize_text(b.label.clean):
                f.append(_finding("pair:opciones", C.OPCIONES, Severity.MEDIA,
                                  [_ref("design", f"{di.id}#opt:{code}", None, a.certainty),
                                   _ref("program", f"{pi.id}#opt:{code}", None, b.certainty)],
                                  f"La etiqueta del código {code} difiere entre {di.id} y {pi.id}.", pair=p))
            if a.is_missing_code != b.is_missing_code:
                f.append(_finding("pair:codigo_no_respuesta", C.CODIGOS, Severity.MEDIA,
                                  [_ref("design", f"{di.id}#opt:{code}", None, a.certainty),
                                   _ref("program", f"{pi.id}#opt:{code}", None, Certainty.INFERRED)],
                                  f"El código {code} es de no respuesta en un solo lado ({di.id} / {pi.id}). "
                                  "En el programa, False significa «sin evidencia».", pair=p))
        _compare_presentation(out, p, dfi, pfi, links, eq, limit)
        _compare_navigation(out, p, dfi, pfi, matching, links, eq, limit)
    out.limitations += [
        "Validaciones (controles) y cálculos no se comparan en esta entrega.",
        "Las incidencias GIM no producen terminaciones (v1.1, §2.2): la terminación solo se compara como no verificable.",
    ]
    return out


def _cond_finding(out: Comparison, p: Pair, cat: FindingCategory, check: str, refs: list[FindingRef],
                  res, depends: tuple[str, ...], what: str) -> None:
    out.conditions.append({"pair": [p.design, p.program], "pair_state": p.state, "check": check, "what": what,
                           "depends_on_unconfirmed": list(depends), **res.to_dict()})
    if res.result == EQUIVALENT:
        return
    if res.result == DIFFERENT:
        ce = res.counterexample
        expl = (f"{what}: las condiciones difieren. Contraejemplo {ce['assignment']}: diseño {ce['design']}, "
                f"programa {ce['program']}.")
        out.findings.append(_finding(check, cat, Severity.ALTA, refs, expl, pair=p, depends=depends,
                                     suggested="Revisar la condición en ambos documentos."))
    else:
        expl = f"{what}: no verificable ({', '.join(res.reasons) or 'sin conclusión'})."
        out.findings.append(_finding(check, cat, Severity.ALTA, refs, expl, pair=p, depends=depends,
                                     force=EvidenceLevel.NO_VERIFICABLE))


def _rule_ref(side: str, rid: str, it: Item, certainty: Certainty, cond: Any) -> FindingRef:
    c = Certainty.OPAQUE if cond is not None and has_opaque(cond) else certainty
    return FindingRef(side=side, element_id=rid, provenance=it.provenance, certainty=c)


def _compare_presentation(out, p: Pair, dfi: FlowItem, pfi: FlowItem, links, eq, limit) -> None:
    dopq = [o for o in dfi.opaque if o.klass == "flow" and "#" not in o.rule_id]
    popq = [o for o in pfi.opaque if any(k in o.rule_id for k in ("/filtros", "/habil_cnd", "/oculta_cnd"))]
    ds = _combine([r.show for r in dfi.show], And)
    ps = _combine([r.show for r in pfi.show], And)
    depends = _hyp_names(*[r.hypothesis for r in dfi.show + pfi.show])
    what = f"presentación de {p.design} / {p.program}"
    if popq:
        refs = [FindingRef(side="program", element_id=o.rule_id, provenance=pfi.item.provenance, certainty=Certainty.OPAQUE) for o in popq]
        refs.append(FindingRef(side="design", element_id=p.design, provenance=dfi.item.provenance, certainty=dfi.item.certainty))
        out.findings.append(_finding("pair:filtro_opaco", C.FILTRO, Severity.ALTA, refs,
                                     f"{what}: el programa tiene reglas de presentación opacas.", pair=p, depends=depends))
    if ds is None and ps is None:
        return
    if ds is None or ps is None:
        side = "design" if ds is not None else "program"
        fi = dfi if side == "design" else pfi
        rules = fi.show
        refs = [_rule_ref(side, r.rule_id, fi.item, r.certainty, r.show) for r in rules]
        other = pfi if side == "design" else dfi
        refs.append(FindingRef(side="program" if side == "design" else "design", element_id=other.item.id,
                               provenance=other.item.provenance, certainty=other.item.certainty))
        out.findings.append(_finding("pair:filtro_un_lado", C.FILTRO, Severity.ALTA, refs,
                                     f"{what}: solo {'el diseño' if side == 'design' else 'el programa'} tiene condición de presentación.",
                                     pair=p, depends=depends, suggested="Confirmar si falta el filtro en el otro documento."))
        return
    res = compare_conditions(ds, ps, links, item_pair_declared=p.declared, comparison_enabled=True, eq=eq, limit=limit)
    refs = [_rule_ref("design", r.rule_id, dfi.item, r.certainty, r.show) for r in dfi.show] + \
           [_rule_ref("program", r.rule_id, pfi.item, r.certainty, r.show) for r in pfi.show]
    _cond_finding(out, p, C.FILTRO, "pair:filtro", refs, res, depends, what)


def _compare_navigation(out, p: Pair, dfi: FlowItem, pfi: FlowItem, matching: Matching, links, eq, limit) -> None:
    # Diseño: destino -> condición (OR de las opciones que llevan a él, o la regla).
    dgo: dict[str, list[Nav]] = {}
    for n in dfi.nav:
        if n.kind == "goto" and n.target:
            dgo.setdefault(n.target, []).append(n)
    pgo: dict[str, list[Nav]] = {}
    for n in pfi.nav:
        if n.kind == "goto" and n.target:
            pgo.setdefault(n.target, []).append(n)
    mapped_p: set[str] = set()
    for dt, navs in sorted(dgo.items()):
        tp = matching.pair_of_design(dt)
        what = f"salto de {p.design} hacia {dt}"
        drefs = [FindingRef(side="design", element_id=n.rule_id, provenance=dfi.item.provenance, certainty=n.certainty) for n in navs]
        if tp is None:
            out.findings.append(_finding("pair:salto_destino_sin_pareja", C.SALTO, Severity.ALTA,
                                         drefs + [FindingRef(side="program", element_id=f"(pareja de {dt})", certainty=Certainty.INFERRED, missing=True)],
                                         f"{what}: el destino no tiene pareja en el programa.", pair=p))
            continue
        mapped_p.add(tp.program)
        dcond = _combine([_or_codes(p.design, n.codes) if n.codes else n.condition for n in navs], Or)
        pnavs = pgo.get(tp.program, [])
        if not pnavs:
            out.findings.append(_finding("pair:salto_solo_diseno", C.SALTO, Severity.ALTA,
                                         drefs + [FindingRef(side="program", element_id=pfi.item.id, provenance=pfi.item.provenance, certainty=pfi.item.certainty)],
                                         f"{what}: el programa no tiene un salto desde {p.program} hacia {tp.program}.", pair=p,
                                         depends=("saltos",)))
            continue
        pcond = _combine([n.condition for n in pnavs], Or)
        depends = _hyp_names(*[n.hypothesis for n in navs + pnavs])
        res = compare_conditions(dcond, pcond, links, item_pair_declared=p.declared and tp.declared,
                                 comparison_enabled=True, eq=eq, limit=limit)
        prefs = [_rule_ref("program", n.rule_id, pfi.item, n.certainty, n.condition) for n in pnavs]
        _cond_finding(out, p, C.SALTO, "pair:salto", drefs + prefs, res, depends, what)
    for pt, navs in sorted(pgo.items()):
        if pt in mapped_p:
            continue
        refs = [_rule_ref("program", n.rule_id, pfi.item, n.certainty, n.condition) for n in navs]
        refs.append(FindingRef(side="design", element_id=dfi.item.id, provenance=dfi.item.provenance, certainty=dfi.item.certainty))
        out.findings.append(_finding("pair:salto_solo_programa", C.SALTO, Severity.ALTA, refs,
                                     f"salto de {p.program} hacia {pt}: no tiene equivalente en el diseño.", pair=p,
                                     depends=("saltos",)))
    dterm = [n for n in dfi.nav if n.kind == "terminate"]
    popq = [o for o in pfi.opaque if "/saltos" in o.rule_id]
    if dterm or popq:
        refs = [FindingRef(side="design", element_id=n.rule_id, provenance=dfi.item.provenance, certainty=n.certainty) for n in dterm]
        refs += [FindingRef(side="program", element_id=o.rule_id, provenance=pfi.item.provenance, certainty=Certainty.OPAQUE) for o in popq]
        if not popq:
            refs.append(FindingRef(side="program", element_id=pfi.item.id, provenance=pfi.item.provenance, certainty=Certainty.OPAQUE))
        out.findings.append(_finding("pair:terminacion", C.TERMINACION, Severity.ALTA, refs,
                                     f"terminación en {p.design} / {p.program}: no verificable (destinos especiales e "
                                     "incidencias GIM sin semántica confirmada).", pair=p, force=EvidenceLevel.NO_VERIFICABLE))
