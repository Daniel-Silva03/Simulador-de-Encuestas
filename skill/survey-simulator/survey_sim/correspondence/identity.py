"""Identidad del estudio (especificación v1.0, 7.1 y 7.3; v1.1, §3.1).

Determina ``verdict`` (MISMO, DISTINTO, NO_DETERMINADA), ``backing``,
``observation_review`` con ``blocking[]`` y ``advisory[]``,
``comparison_enabled`` y el resultado combinado. Las reglas son las de la
v1.0 sin cambios; la v1.1 solo añade que ninguna evidencia de pareja de
preguntas prueba la identidad ni habilita la comparación.

Los valores observados (códigos, títulos) se guardan aquí en claro porque son
la evidencia; las salidas los minimizan (``report_view``).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Optional

from survey_sim.ir.models import Instrument
from survey_sim.util.textnorm import jaccard, normalize_text

from .config import IDENTIFIER_TYPES, CorrespondenceConfig, Location
from .records import DECLARATION, VERIFICATION, Record

MISMO, DISTINTO, NO_DET = "MISMO", "DISTINTO", "NO_DETERMINADA"
DOCS = ("design", "program")

REVIEW_FOR = {
    "inferred_observation": "verify_value_manually",
    "opaque_observation": "provide_readable_source",
    "not_observable": "provide_readable_source",
    "heuristic_absence": "verify_absence_manually",
    "incomplete_search_scope": "confirm_search_scope",
}

RESULTS = {
    "CORRESPONDEN": "Empareja y compara (comparison_enabled = true)",
    "CORRESPONDENCIA PENDIENTE DE REVISIÓN": "No compara: hay bloqueos sin resolver",
    "CORRESPONDENCIA PROVISIONAL": "No compara automáticamente: MISMO con respaldo inferred",
    "NO CORRESPONDEN": "No compara",
    "INDETERMINADO": "No compara: identidad no determinada",
}


# --------------------------------------------------------------------------
# Observación
# --------------------------------------------------------------------------


@dataclass
class Found:
    value: str
    location: str
    via_pattern: bool
    composite: Optional[tuple[str, str]] = None  # (id del patrón, status)


@dataclass
class DocObservation:
    doc: str
    searched: list[dict] = field(default_factory=list)
    found: list[Found] = field(default_factory=list)
    absence: list[dict] = field(default_factory=list)

    @property
    def coverage(self) -> str:
        if not self.searched:
            return "complete"
        read = [s for s in self.searched if s["read"]]
        if len(read) == len(self.searched):
            return "complete"
        return "partial" if read else "none"

    @property
    def side(self) -> str:
        if self.found:
            return "found"
        return "absent_observed" if self.coverage == "complete" else "unobservable"

    def certainty(self, used: Optional[list[Found]] = None) -> str:
        """Certeza de la observación en este documento."""
        if self.coverage != "complete":
            return "opaque"
        if used is not None:
            return "inferred" if any(f.via_pattern for f in used) else "explicit"
        # Ausencia: inferred si la búsqueda usó algún patrón (heurística).
        return "inferred" if any(s.get("pattern") for s in self.searched) else "explicit"


def _field_values(inst: Instrument, fld: str) -> list[str]:
    if fld == "source.name":
        return [inst.source.name]
    if fld == "study.title":
        return [inst.study.title] if inst.study.title else []
    if fld == "study.ids":
        return [x for x in inst.study.ids if x]
    raise ValueError(fld)


def observe(inst: Instrument, doc: str, locs: tuple[Location, ...], cfg: CorrespondenceConfig,
            unreadable: frozenset = frozenset()) -> DocObservation:
    """Busca un tipo de identificador en las ubicaciones declaradas de un documento.

    ``unreadable`` (pares ``(doc, ubicación)``) simula ubicaciones ilegibles; la
    IR no tiene hoy ubicaciones ilegibles, pero la regla de estado las contempla.
    """
    ob = DocObservation(doc)
    for loc in locs:
        entry = {"location": loc.key, "field": loc.field, "pattern": loc.pattern, "read": True}
        if (doc, loc.key) in unreadable:
            entry.update(read=False, read_error="ubicación ilegible")
            ob.searched.append(entry)
            continue
        ob.searched.append(entry)
        vals = _field_values(inst, loc.field)
        if not vals:
            ob.absence.append({"location": loc.key, "reason": "field_empty"})
            continue
        hits: list[str] = []
        if loc.pattern:
            rx = cfg.patterns[loc.pattern]
            for v in vals:
                for m in rx.finditer(v):
                    hits.append(m.group(1) if m.groups() else m.group(0))
            if not hits:
                ob.absence.append({"location": loc.key, "reason": "pattern_not_found"})
        else:
            hits = vals
        for h in hits:
            if not any(_norm_id(f.value) == _norm_id(h) for f in ob.found):
                ob.found.append(Found(h, loc.key, bool(loc.pattern)))
    return ob


def _norm_id(v: str) -> str:
    return normalize_text(v).replace(" ", "")


def _decompose(obs: dict[str, dict[str, DocObservation]], cfg: CorrespondenceConfig) -> list[dict]:
    """Aplica los patrones compuestos declarados. Sin patrón, el código se compara entero.

    Un código que encaja en un patrón se sustituye por sus partes, y cada
    parte se compara con su tipo. Las partes conservan el patrón y su estado.
    """
    applied = []
    for cp in cfg.composite_patterns:
        if cp.regex is None:
            continue
        for doc in DOCS:
            src = obs[cp.applies_to][doc]
            kept: list[Found] = []
            additions: dict[str, list[Found]] = {}
            for f in src.found:
                m = cp.regex.fullmatch(f.value) if f.composite is None else None
                if not m:
                    kept.append(f)
                    continue
                applied.append({"pattern": cp.name, "status": cp.status, "doc": doc, "location": f.location})
                for t, part in m.groupdict().items():
                    if part:
                        additions.setdefault(t, []).append(
                            Found(part, f"{f.location}#{cp.name}.{t}", f.via_pattern, (cp.name, cp.status)))
            src.found = kept
            for t, lst in additions.items():
                tgt = obs[t][doc]
                for a in lst:
                    if not any(_norm_id(x.value) == _norm_id(a.value) for x in tgt.found):
                        tgt.found.append(a)
    return applied


# --------------------------------------------------------------------------
# Señales
# --------------------------------------------------------------------------


@dataclass
class Signal:
    type: str
    state: str  # coincide | contradice | ausente | no_observable
    obs: dict[str, DocObservation]
    used: dict[str, list[Found]]
    level: Optional[str]
    assignment: Optional[dict]
    scope_basis: Optional[dict]
    key: str

    @property
    def is_identifier(self) -> bool:
        return self.type in IDENTIFIER_TYPES

    @property
    def is_pair(self) -> bool:
        return self.state in ("coincide", "contradice")

    def doc_certainty(self, doc: str) -> str:
        o = self.obs[doc]
        return o.certainty(self.used.get(doc) if self.is_pair else (None if o.side != "found" else o.found))

    @property
    def certainty(self) -> str:
        docs = DOCS if self.is_pair else [d for d in DOCS if self.obs[d].side != "found"] or list(DOCS)
        cs = [self.doc_certainty(d) for d in docs]
        if any(self.obs[d].coverage != "complete" for d in DOCS):
            return "opaque"
        return "opaque" if "opaque" in cs else ("inferred" if "inferred" in cs else "explicit")

    def assignment_confirmed(self) -> bool:
        if not self.assignment:
            return False
        if self.assignment["status"] != "confirmed":
            return False
        pat = self.assignment.get("pattern")
        return pat is None or pat["status"] == "confirmed"


def _title_norm(t: str, cfg: CorrespondenceConfig) -> tuple[str, ...]:
    return tuple(w for w in normalize_text(t).split() if not w.isdigit() and not any(c.isdigit() for c in w)
                 and w not in cfg.title_remove_words)


def build_signals(design: Instrument, program: Instrument, cfg: CorrespondenceConfig,
                  unreadable: frozenset = frozenset()) -> tuple[list[Signal], list[dict]]:
    insts = {"design": design, "program": program}
    obs = {t: {d: observe(insts[d], d, cfg.locations[t][d], cfg, unreadable) for d in DOCS} for t in IDENTIFIER_TYPES}
    applied = _decompose(obs, cfg)
    signals: list[Signal] = []
    for t in IDENTIFIER_TYPES:
        lv = cfg.level_of_type[t]
        level = cfg.levels[lv]
        scope = {"ref": f"identifier_levels.{lv}", "status": level.status}
        dobs, pobs = obs[t]["design"], obs[t]["program"]
        if dobs.side == "found" and pobs.side == "found":
            for dv in dobs.found:
                for pv in pobs.found:
                    comp = dv.composite or pv.composite
                    if dv.composite and pv.composite and pv.composite[1] != "confirmed":
                        comp = pv.composite
                    st = "coincide" if _norm_id(dv.value) == _norm_id(pv.value) else "contradice"
                    assignment = {"level": lv, "status": level.status, "ref": f"identifier_levels.{lv}",
                                  "pattern": {"id": comp[0], "status": comp[1]} if comp else None}
                    signals.append(Signal(t, st, obs[t], {"design": [dv], "program": [pv]}, lv, assignment, scope,
                                          f"{t}|{dv.location}|{pv.location}"))
        else:
            st = "ausente" if "absent_observed" in (dobs.side, pobs.side) else "no_observable"
            signals.append(Signal(t, st, obs[t], {}, lv, None, scope, f"{t}|-"))
    # Título y metadatos: nunca tiene asignación.
    tob = {}
    for d in DOCS:
        o = DocObservation(d)
        o.searched.append({"location": "study.title", "field": "study.title", "pattern": None, "read": True})
        title = insts[d].study.title
        if title:
            o.found.append(Found(title, "study.title", False))
        else:
            o.absence.append({"location": "study.title", "reason": "field_empty"})
        tob[d] = o
    if tob["design"].found and tob["program"].found:
        a = _title_norm(tob["design"].found[0].value, cfg)
        b = _title_norm(tob["program"].found[0].value, cfg)
        st = "coincide" if jaccard(a, b) >= cfg.title_coincide_min else "contradice"
        signals.append(Signal("title_metadata", st, tob, {d: tob[d].found for d in DOCS}, None, None, None, "title_metadata"))
    else:
        signals.append(Signal("title_metadata", "ausente", tob, {}, None, None, None, "title_metadata"))
    return signals, applied


# --------------------------------------------------------------------------
# Veredicto
# --------------------------------------------------------------------------


@dataclass
class Identity:
    verdict: str
    source: str  # table | resolution_rule | human_declaration
    row: str
    backing: str
    observation_review: str
    comparison_enabled: bool
    result: str
    signals: list[Signal]
    sustaining: list[str]
    blocking: list[dict]
    advisory: list[dict]
    matching_level: str
    resolution_rules: list[dict]
    composite_applied: list[dict]
    declaration: Optional[dict]
    contradicted_by_declaration: list[str]
    verifications: list[dict]
    records: list[dict]
    unconfirmed_assignments: list[dict]
    warnings: list[str]
    extraction_findings: list[dict]


def _aggregate(pairs: list[Signal]) -> str:
    co = any(s.state == "coincide" for s in pairs)
    ct = any(s.state == "contradice" for s in pairs)
    if co and ct:
        return "mixtos"
    if co:
        return "coinciden"
    if ct:
        return "contradicen"
    return "ausentes"


def _table(signals: list[Signal], high: bool, cfg: CorrespondenceConfig) -> tuple[str, str, list[Signal], list[dict]]:
    pairs = [s for s in signals if s.is_identifier and s.is_pair]
    title = next(s for s in signals if s.type == "title_metadata")
    agg = _aggregate(pairs)
    levels = {s.level for s in pairs}
    rules_report = []
    if agg == "mixtos":
        for r in cfg.resolution_rules:
            applies = all(any(s.type == t and s.state == "coincide" for s in pairs) for t in r.coinciden) and \
                all(any(s.type == t and s.state == "contradice" for s in pairs) for t in r.contradicen)
            rules_report.append({"id": r.name, "status": r.status, "matches": applies,
                                 "applied": applies and r.confirmed, "verdict": r.verdict})
        chosen = next((r for r in rules_report if r["applied"]), None)
        if chosen:
            return chosen["verdict"], f"regla:{chosen['id']}", pairs, rules_report
        return NO_DET, "mixtos", pairs, rules_report
    for r in cfg.resolution_rules:
        rules_report.append({"id": r.name, "status": r.status, "matches": False, "applied": False, "verdict": r.verdict})
    if agg == "coinciden" and levels & {"study", "instrument"}:
        return MISMO, "coinciden_estudio_o_instrumento", [s for s in pairs if s.state == "coincide"], rules_report
    if agg == "contradicen" and any(s.level == "study" for s in pairs):
        sus = [s for s in pairs if s.level == "study" and s.state == "contradice"]
        return (NO_DET, "contradicen_estudio_emparejamiento_alto", sus, rules_report) if high else \
            (DISTINTO, "contradicen_estudio_emparejamiento_bajo", sus, rules_report)
    if agg == "contradicen" and "instrument" in levels:
        return NO_DET, "contradicen_instrumento", pairs, rules_report
    # Ausentes, o solo comparables en ola o versión: decide título y metadatos.
    if title.state == "contradice":
        return (NO_DET, "ausentes_titulo_contradice_alto", pairs + [title], rules_report) if high else \
            (DISTINTO, "ausentes_titulo_contradice_bajo", [title], rules_report)
    return NO_DET, "ausentes_titulo_compatible", pairs + [title], rules_report


def _bid(sig: Signal, doc: str, location: str, reason: str) -> str:
    payload = json.dumps([sig.key, doc, location, reason], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _blocks_for(sig: Signal, informative: bool = False) -> list[dict]:
    out = []

    def add(doc: str, location: str, reason: str) -> None:
        out.append({"id": _bid(sig, doc, location, reason), "signal": sig.type, "signal_key": sig.key, "document": doc,
                    "location": location, "reason": reason, "review": REVIEW_FOR[reason], "informative": informative})

    if sig.is_pair:
        for d in DOCS:
            o = sig.obs[d]
            for s in o.searched:
                if not s["read"]:
                    add(d, s["location"], "opaque_observation")
            c = sig.doc_certainty(d)
            if c == "inferred":
                for f in sig.used.get(d, []):
                    add(d, f.location, "inferred_observation")
    elif sig.state == "no_observable":
        for d in DOCS:
            for s in sig.obs[d].searched:
                if not s["read"]:
                    add(d, s["location"], "not_observable")
    elif sig.state == "ausente" and sig.is_identifier:
        for d in DOCS:
            o = sig.obs[d]
            if o.side != "absent_observed":
                continue
            if o.certainty() == "inferred":
                add(d, "+".join(s["location"] for s in o.searched if s.get("pattern")), "heuristic_absence")
            if sig.scope_basis and sig.scope_basis["status"] != "confirmed":
                add(d, "document", "incomplete_search_scope")
    return out


def _decisive(verdict: str, signals: list[Signal], sustaining: list[Signal]) -> tuple[list[dict], set[str]]:
    blocks: list[dict] = []
    keys: set[str] = set()
    ids = [s for s in signals if s.is_identifier]
    for s in sustaining:
        blocks += _blocks_for(s)
        keys.add(s.key)
    for s in ids:
        if s.state == "no_observable":
            blocks += _blocks_for(s)
            keys.add(s.key)
    for s in ids:
        if s.state == "ausente":
            heuristic = any(s.obs[d].side == "absent_observed" and s.obs[d].certainty() == "inferred" for d in DOCS)
            scope_inf = bool(s.scope_basis and s.scope_basis["status"] != "confirmed")
            if heuristic or scope_inf:
                blocks += _blocks_for(s, informative=verdict != MISMO)
                keys.add(s.key)
    seen, uniq = set(), []
    for b in blocks:
        if b["id"] not in seen:
            seen.add(b["id"])
            uniq.append(b)
    return uniq, keys


def determine(design: Instrument, program: Instrument, cfg: CorrespondenceConfig, *, matching_ratio: float,
              records: list[Record], unreadable: frozenset = frozenset()) -> Identity:
    signals, applied = build_signals(design, program, cfg, unreadable)
    high = matching_ratio >= cfg.high_ratio
    verdict, row, sustaining, rules_report = _table(signals, high, cfg)
    source = "resolution_rule" if row.startswith("regla:") else "table"
    warnings: list[str] = []

    decl_recs = [r for r in records if r.type == DECLARATION and r.effective]
    declaration = decl_recs[-1].data if decl_recs else None
    contradicted: list[str] = []
    if declaration:
        verdict = declaration["verdict"]
        source = "human_declaration"
        bad = "contradice" if verdict == MISMO else "coincide"
        contradicted = [s.key for s in signals if s.state == bad]

    pairs_sus = [s for s in sustaining if s.is_identifier and s.is_pair]
    if declaration:
        backing = "human_declaration"
    elif pairs_sus and all(s.assignment_confirmed() for s in pairs_sus):
        backing = "confirmed_assignments"
    else:
        backing = "inferred"

    # Observaciones decisivas y bloqueos (no cambian backing ni certezas).
    if declaration:
        # Con declaración, las observaciones decisivas son las del veredicto declarado.
        want = "coincide" if verdict == MISMO else "contradice"
        sustaining = [s for s in signals if s.is_identifier and s.is_pair and s.state == want]
    blocking, decisive_keys = _decisive(verdict, signals, sustaining)
    advisory = []
    for s in signals:
        if s.key in decisive_keys:
            continue
        c = s.certainty
        if c in ("inferred", "opaque") and (s.is_pair or s.type == "title_metadata"):
            advisory.append({"signal": s.type, "signal_key": s.key, "certainty": c})

    verifs = [r for r in records if r.type == VERIFICATION]
    extraction_findings = []
    resolved: dict[str, str] = {}
    for r in verifs:
        if not r.effective:
            continue
        d = r.data
        for b in blocking:
            if b["id"] in d["resolves"] and b["signal"] == d["signal"] and b["document"] == d["document"] \
                    and b["location"] == d["location"]:
                if d["result"] == "confirmed":
                    resolved[b["id"]] = r.id
                elif d["result"] == "contradicted":
                    extraction_findings.append({"verification": r.id, "blocking": b["id"], "signal": b["signal"],
                                                "document": b["document"], "location": b["location"]})
    active = [b for b in blocking if not b["informative"]]
    ack = set(declaration.get("acknowledges", [])) if declaration else set()
    for b in blocking:
        if b["id"] in resolved:
            b["status"] = "resolved_by_verification"
            b["resolved_by"] = resolved[b["id"]]
        elif declaration and b["id"] in ack:
            b["status"] = "acknowledged"
            b["acknowledged"] = {"declaration": declaration["id"], "date": str(declaration["date"]), "reason": declaration["reason"]}
        else:
            b["status"] = "open"

    if not active:
        review = "clear"
    elif backing == "human_declaration":
        review = "resolved_by_human_declaration" if all(b["id"] in ack for b in active) else "review_required"
    elif all(b["id"] in resolved for b in active):
        review = "resolved_by_verification"
    else:
        review = "review_required"

    enabled = verdict == MISMO and (
        (backing == "confirmed_assignments" and review in ("clear", "resolved_by_verification"))
        or (backing == "human_declaration" and review in ("clear", "resolved_by_human_declaration"))
    )
    if verdict == MISMO:
        if enabled:
            result = "CORRESPONDEN"
        elif backing == "inferred":
            result = "CORRESPONDENCIA PROVISIONAL"
        else:
            result = "CORRESPONDENCIA PENDIENTE DE REVISIÓN"
        if not high:
            warnings.append("cambios sustanciales: emparejamiento de preguntas bajo")
    elif verdict == DISTINTO:
        result = "NO CORRESPONDEN"
        if active:
            warnings.append("el veredicto depende de observaciones inciertas; ver blocking[]")
    else:
        result = "INDETERMINADO"
    if row == "contradicen_estudio_emparejamiento_alto":
        warnings.append("posible otra ola del mismo estudio")

    unconfirmed = []
    for s in signals:
        if s.assignment and not s.assignment_confirmed():
            unconfirmed.append({"signal_key": s.key, "level": s.assignment["level"], "status": s.assignment["status"],
                                "pattern": s.assignment.get("pattern"), "label": "no confirmada"})

    return Identity(
        verdict=verdict, source=source, row=row, backing=backing, observation_review=review,
        comparison_enabled=enabled, result=result, signals=signals, sustaining=[s.key for s in sustaining],
        blocking=blocking, advisory=advisory, matching_level="alto" if high else "bajo",
        resolution_rules=rules_report, composite_applied=applied,
        declaration={k: declaration[k] for k in ("id", "verdict", "date", "reason", "declared_by_role") if k in declaration}
        if declaration else None,
        contradicted_by_declaration=contradicted,
        verifications=[{"id": r.id, "result": r.data.get("result"), "status": r.status()} for r in verifs],
        records=[r.summary() for r in records],
        unconfirmed_assignments=unconfirmed, warnings=warnings, extraction_findings=extraction_findings,
    )


def signal_view(s: Signal, show: Any) -> dict:
    """Vista de una señal para informes. ``show(texto)`` minimiza los valores observados."""
    obs = {}
    for d in DOCS:
        o = s.obs[d]
        obs[d] = {
            "searched": o.searched,
            "found": [{"value": show(f.value), "location": f.location} for f in o.found] if o.found else None,
            "absence_evidence": o.absence,
            "coverage": o.coverage,
        }
    return {
        "type": s.type, "key": s.key, "state": s.state,
        "observation": {"per_document": obs, "certainty": s.certainty, "scope_basis": s.scope_basis},
        "assignment": s.assignment,
        "pair": {d: show(s.used[d][0].value) for d in DOCS} if s.is_pair else None,
    }
