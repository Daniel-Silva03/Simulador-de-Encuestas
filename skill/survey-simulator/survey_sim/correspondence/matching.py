"""Emparejamiento de preguntas (v1.0, 7.2; v1.1, §3.2–§3.4).

Cada pareja tiene un estado de la v1.1 y una clase de la v1.0:

- Estado: ``declarada`` (confirmada por el usuario en el archivo de pareos),
  ``evidencia_exacta`` (misma etiqueta o nombre de variable, con contenido
  compatible) o ``propuesta_heuristica`` (similitud de contenido). Solo una
  pareja ``declarada`` admite conclusiones de equivalencia.
- Clase: ``IDENTICA`` o ``MODIFICADA`` para las parejas; ``SOLO_DISENO``,
  ``SOLO_PROGRAMA`` y ``AMBIGUA`` para lo que no se empareja.

Misma etiqueta con contenido distinto: no se emparejan y se registra.
El emparejamiento no prueba la identidad del estudio (v1.1, §3.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from survey_sim.ir.models import Instrument, Item, ItemKind
from survey_sim.util.textnorm import jaccard, normalize_label, normalize_text, ratio

from .config import CorrespondenceConfig
from .records import DeclaredPairs

DECLARED, EXACT, HEURISTIC = "declarada", "evidencia_exacta", "propuesta_heuristica"
EXCLUDED_KINDS = (ItemKind.SYSTEM, ItemKind.PARADATA)


@dataclass(frozen=True)
class Entry:
    item: Item
    pos: int  # posición en el orden del documento
    rel: float  # posición relativa (0–1)


@dataclass
class Pair:
    design: str
    program: str
    state: str
    klass: str  # IDENTICA | MODIFICADA
    score: float
    parts: dict
    reason: str = ""

    @property
    def declared(self) -> bool:
        return self.state == DECLARED


@dataclass
class Matching:
    pairs: list[Pair]
    only_design: list[str]
    only_program: list[str]
    ambiguous: list[dict]
    same_label_different: list[dict]
    declared_invalid: list[dict]
    excluded_program: list[str]
    ratio: float
    n_design: int
    n_program: int
    declarations: Optional[dict] = None
    notes: list[str] = field(default_factory=list)

    def pair_of_design(self, did: str) -> Optional[Pair]:
        return next((p for p in self.pairs if p.design == did), None)

    def pair_of_program(self, pid: str) -> Optional[Pair]:
        return next((p for p in self.pairs if p.program == pid), None)


def entries(inst: Instrument, *, exclude_system: bool) -> list[Entry]:
    items = [i for s in inst.sections for i in s.items]
    if exclude_system:
        items = [i for i in items if i.kind not in EXCLUDED_KINDS]
    n = max(1, len(items) - 1)
    return [Entry(i, k, k / n) for k, i in enumerate(items)]


def _options(it: Item):
    opts = it.options or (it.grid.shared_options if it.grid else ())
    return opts


def content_parts(d: Item, p: Item) -> dict:
    t = ratio(normalize_text(d.text.clean), normalize_text(p.text.clean))
    do, po = _options(d), _options(p)
    if not do and not po:
        o = 1.0
    elif not do or not po:
        o = 0.0
    else:
        codes = jaccard([x.code for x in do], [x.code for x in po])
        labels = jaccard([normalize_text(x.label.clean) for x in do], [normalize_text(x.label.clean) for x in po])
        o = 0.5 * codes + 0.5 * labels
    ty = 1.0 if d.type == p.type else 0.0
    return {"text": round(t, 6), "options": round(o, 6), "type": ty}


def score(parts: dict, cfg: CorrespondenceConfig) -> float:
    return round(sum(cfg.weights[k] * parts[k] for k in ("text", "options", "type")), 6)


def klass(d: Item, p: Item) -> str:
    same_text = normalize_text(d.text.clean) == normalize_text(p.text.clean)
    do, po = _options(d), _options(p)
    same_opts = [(x.code, normalize_text(x.label.clean)) for x in do] == [(x.code, normalize_text(x.label.clean)) for x in po]
    return "IDENTICA" if same_text and same_opts and d.type == p.type else "MODIFICADA"


def match(design: Instrument, program: Instrument, cfg: CorrespondenceConfig,
          declared: Optional[DeclaredPairs] = None) -> Matching:
    de = entries(design, exclude_system=False)
    pe_all = entries(program, exclude_system=False)
    pe = [e for e in pe_all if e.item.kind not in EXCLUDED_KINDS]
    excluded = [e.item.id for e in pe_all if e.item.kind in EXCLUDED_KINDS]
    dmap = {e.item.id: e for e in de}
    pmap = {e.item.id: e for e in pe_all}
    pairs: list[Pair] = []
    used_d: set[str] = set()
    used_p: set[str] = set()
    invalid: list[dict] = []
    notes: list[str] = []

    # 1. Parejas declaradas (vigentes).
    decl_info = None
    if declared is not None:
        decl_info = {"sha256": declared.sha256, "confirmed": len(declared.pairs), "proposed_unconfirmed": declared.proposed,
                     "stale": declared.stale, "errors": declared.errors, "effective": declared.effective}
        if declared.effective:
            for d, p, motivo in declared.pairs:
                if d not in dmap or p not in pmap:
                    invalid.append({"design": d, "program": p, "reason": "elemento inexistente"})
                    continue
                if d in used_d or p in used_p:
                    invalid.append({"design": d, "program": p, "reason": "elemento ya declarado en otra pareja"})
                    continue
                parts = content_parts(dmap[d].item, pmap[p].item)
                pairs.append(Pair(d, p, DECLARED, klass(dmap[d].item, pmap[p].item), score(parts, cfg), parts, motivo))
                used_d.add(d)
                used_p.add(p)
        elif declared.stale:
            notes.append("archivo de pareos stale: no se aplica")

    # 2. Evidencia exacta: misma etiqueta normalizada, contenido compatible.
    same_label_diff: list[dict] = []
    blocked: set[tuple[str, str]] = set()
    by_label: dict[str, list[Entry]] = {}
    for e in pe:
        if e.item.id not in used_p:
            by_label.setdefault(normalize_label(e.item.label or e.item.id), []).append(e)
    ambiguous: list[dict] = []
    for e in de:
        if e.item.id in used_d:
            continue
        lab = normalize_label(e.item.label)
        if not lab:
            continue
        cands = [c for c in by_label.get(lab, []) if c.item.id not in used_p]
        if len(cands) > 1:
            ambiguous.append({"design": e.item.id, "candidates": [c.item.id for c in cands], "reason": "varias candidatas con la misma etiqueta"})
            used_d.add(e.item.id)
            continue
        if len(cands) == 1:
            c = cands[0]
            parts = content_parts(e.item, c.item)
            sc = score(parts, cfg)
            if sc >= cfg.label_content_min:
                pairs.append(Pair(e.item.id, c.item.id, EXACT, klass(e.item, c.item), sc, parts))
                used_d.add(e.item.id)
                used_p.add(c.item.id)
            else:
                same_label_diff.append({"design": e.item.id, "program": c.item.id, "score": sc, "parts": parts})
                blocked.add((e.item.id, c.item.id))

    # 3. Propuestas heurísticas por contenido, con detección de ambigüedad.
    free_d = [e for e in de if e.item.id not in used_d]
    free_p = [e for e in pe if e.item.id not in used_p]
    cands: list[tuple[float, float, int, int, Entry, Entry, dict]] = []
    per_d: dict[str, list[tuple[float, str]]] = {}
    for d in free_d:
        for p in free_p:
            if (d.item.id, p.item.id) in blocked:
                continue
            parts = content_parts(d.item, p.item)
            sc = score(parts, cfg)
            if sc >= cfg.propose_min:
                cands.append((-sc, abs(d.rel - p.rel), d.pos, p.pos, d, p, parts))
                per_d.setdefault(d.item.id, []).append((sc, p.item.id))
    amb_d = set()
    for did, lst in per_d.items():
        lst.sort(key=lambda x: (-x[0], x[1]))
        if len(lst) > 1 and lst[0][0] - lst[1][0] < cfg.ambiguity_margin:
            amb_d.add(did)
            ambiguous.append({"design": did, "candidates": [x[1] for x in lst if lst[0][0] - x[0] < cfg.ambiguity_margin],
                              "reason": "puntajes parecidos"})
    for negsc, _, _, _, d, p, parts in sorted(cands, key=lambda x: x[:4]):
        if d.item.id in used_d or p.item.id in used_p or d.item.id in amb_d:
            continue
        pairs.append(Pair(d.item.id, p.item.id, HEURISTIC, klass(d.item, p.item), -negsc, parts))
        used_d.add(d.item.id)
        used_p.add(p.item.id)
    amb_ids = {a["design"] for a in ambiguous}
    used_d |= amb_ids

    order_d = {e.item.id: e.pos for e in de}
    pairs.sort(key=lambda x: order_d[x.design])
    only_d = [e.item.id for e in de if e.item.id not in used_d]
    only_p = [e.item.id for e in pe if e.item.id not in used_p]
    n_d, n_p = len(de), len(pe)
    r = len(pairs) / max(1, min(n_d, n_p))
    return Matching(pairs, only_d, only_p, ambiguous, same_label_diff, invalid, excluded, round(r, 6), n_d, n_p,
                    decl_info, notes)
