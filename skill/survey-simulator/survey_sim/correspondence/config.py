"""Carga y validación de ``config/correspondence.yaml``.

Las asignaciones de niveles, los patrones compuestos y las reglas de
resolución son hipótesis con estado. ``confirmed`` solo se acepta con la
estructura ``confirmation`` completa (misma validación que el registro de
semántica GIM, sección 6 de la v1.0).
"""

from __future__ import annotations

import hashlib
import pathlib
import re
from dataclasses import dataclass, field
from typing import Any, Optional

import yaml

from survey_sim.adapters.gim.semantics import _check_confirmation

DEFAULT_CONFIG = pathlib.Path(__file__).resolve().parents[2] / "config" / "correspondence.yaml"

IDENTIFIER_TYPES = ("study_code", "instrument_id", "wave_label", "questionnaire_version", "phase_label")
LEVELS = ("study", "instrument", "wave_or_version")
FIELDS = ("source.name", "study.title", "study.ids")
STATUSES = ("inferred", "confirmed")
VERDICTS = ("MISMO", "DISTINTO")


class CorrespondenceConfigError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class Hypothesis:
    """Entrada con estado: nivel de identificador, patrón compuesto o regla."""

    name: str
    status: str

    @property
    def confirmed(self) -> bool:
        return self.status == "confirmed"


@dataclass(frozen=True)
class Level(Hypothesis):
    types: tuple[str, ...] = ()


@dataclass(frozen=True)
class Location:
    field: str
    pattern: Optional[str] = None

    @property
    def key(self) -> str:
        return f"{self.field}" + (f"~{self.pattern}" if self.pattern else "")


@dataclass(frozen=True)
class CompositePattern(Hypothesis):
    applies_to: str = ""
    regex: Any = None  # re.Pattern


@dataclass(frozen=True)
class ResolutionRule(Hypothesis):
    coinciden: tuple[str, ...] = ()
    contradicen: tuple[str, ...] = ()
    verdict: str = ""


@dataclass(frozen=True)
class CorrespondenceConfig:
    sha256: str
    levels: dict[str, Level]
    locations: dict[str, dict[str, tuple[Location, ...]]]
    patterns: dict[str, Any]
    composite_patterns: tuple[CompositePattern, ...]
    resolution_rules: tuple[ResolutionRule, ...]
    title_remove_words: frozenset[str]
    title_coincide_min: float
    weights: dict[str, float]
    label_content_min: float
    propose_min: float
    ambiguity_margin: float
    high_ratio: float
    enumeration_limit: int
    level_of_type: dict[str, str] = field(default_factory=dict)


def _status(name: str, entry: dict, errs: list[str]) -> str:
    st = entry.get("status")
    if st not in STATUSES:
        errs.append(f"{name}.status: debe ser inferred o confirmed")
        return "inferred"
    if not isinstance(entry.get("confirmation_required"), bool):
        errs.append(f"{name}.confirmation_required: debe ser booleano")
    if st == "confirmed":
        errs.extend(_check_confirmation(name, entry.get("confirmation")))
    return st


def _num(d: dict, key: str, path: str, errs: list[str], lo: float = 0.0, hi: float = 1.0) -> float:
    v = d.get(key)
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not (lo <= v <= hi):
        errs.append(f"{path}.{key}: número entre {lo} y {hi}")
        return lo
    return float(v)


def parse_config(data: Any, sha256: str = "") -> CorrespondenceConfig:
    errs: list[str] = []
    if not isinstance(data, dict):
        raise CorrespondenceConfigError(["el registro debe ser un objeto"])
    lv_raw = data.get("identifier_levels")
    levels: dict[str, Level] = {}
    if not isinstance(lv_raw, dict):
        errs.append("identifier_levels: obligatorio")
        lv_raw = {}
    for lv in LEVELS:
        e = lv_raw.get(lv)
        if not isinstance(e, dict):
            errs.append(f"identifier_levels.{lv}: obligatorio")
            continue
        st = _status(lv, e, errs)
        types = e.get("types")
        if not isinstance(types, list) or not types or any(t not in IDENTIFIER_TYPES for t in types):
            errs.append(f"identifier_levels.{lv}.types: lista de tipos conocidos")
            types = []
        levels[lv] = Level(name=lv, status=st, types=tuple(types))
    level_of_type = {t: lv for lv, e in levels.items() for t in e.types}
    for t in IDENTIFIER_TYPES:
        if t not in level_of_type:
            errs.append(f"identifier_levels: el tipo {t} no tiene nivel")

    pats_raw = data.get("patterns") or {}
    patterns: dict[str, Any] = {}
    if not isinstance(pats_raw, dict):
        errs.append("patterns: debe ser un objeto")
        pats_raw = {}
    for k, v in pats_raw.items():
        try:
            patterns[str(k)] = re.compile(str(v))
        except re.error:
            errs.append(f"patterns.{k}: expresión regular inválida")

    loc_raw = data.get("locations")
    locations: dict[str, dict[str, tuple[Location, ...]]] = {}
    if not isinstance(loc_raw, dict):
        errs.append("locations: obligatorio")
        loc_raw = {}
    for t in IDENTIFIER_TYPES:
        per = loc_raw.get(t)
        if not isinstance(per, dict):
            errs.append(f"locations.{t}: obligatorio")
            continue
        locations[t] = {}
        for doc in ("design", "program"):
            lst = per.get(doc)
            if not isinstance(lst, list):
                errs.append(f"locations.{t}.{doc}: lista obligatoria")
                continue
            out = []
            for i, x in enumerate(lst):
                if not isinstance(x, dict) or x.get("field") not in FIELDS:
                    errs.append(f"locations.{t}.{doc}[{i}].field: debe ser {', '.join(FIELDS)}")
                    continue
                p = x.get("pattern")
                if p is not None and p not in patterns:
                    errs.append(f"locations.{t}.{doc}[{i}].pattern: patrón desconocido")
                    continue
                out.append(Location(x["field"], p))
            locations[t][doc] = tuple(out)

    composites = []
    for i, c in enumerate(data.get("composite_patterns") or []):
        name = c.get("id") if isinstance(c, dict) else None
        if not isinstance(c, dict) or not name:
            errs.append(f"composite_patterns[{i}]: objeto con id")
            continue
        st = _status(str(name), c, errs)
        if c.get("applies_to") not in IDENTIFIER_TYPES:
            errs.append(f"composite_patterns[{i}].applies_to: tipo desconocido")
        try:
            rx = re.compile(str(c.get("regex")))
            bad = [g for g in rx.groupindex if g not in IDENTIFIER_TYPES]
            if bad or not rx.groupindex:
                errs.append(f"composite_patterns[{i}].regex: los grupos con nombre deben ser tipos de identificador")
        except re.error:
            errs.append(f"composite_patterns[{i}].regex: expresión regular inválida")
            rx = None
        composites.append(CompositePattern(name=str(name), status=st, applies_to=str(c.get("applies_to")), regex=rx))

    rules = []
    for i, r in enumerate(data.get("resolution_rules") or []):
        name = r.get("id") if isinstance(r, dict) else None
        if not isinstance(r, dict) or not name:
            errs.append(f"resolution_rules[{i}]: objeto con id")
            continue
        st = _status(str(name), r, errs)
        when = r.get("when") or {}
        co, ct = tuple(when.get("coinciden") or ()), tuple(when.get("contradicen") or ())
        if any(t not in IDENTIFIER_TYPES for t in co + ct) or not (co or ct):
            errs.append(f"resolution_rules[{i}].when: tipos de identificador conocidos")
        if r.get("verdict") not in VERDICTS:
            errs.append(f"resolution_rules[{i}].verdict: MISMO o DISTINTO")
        rules.append(ResolutionRule(name=str(name), status=st, coinciden=co, contradicen=ct, verdict=str(r.get("verdict"))))

    title = data.get("title") or {}
    m = data.get("matching") or {}
    w = m.get("weights") or {}
    weights = {k: _num(w, k, "matching.weights", errs) for k in ("text", "options", "type")}
    if abs(sum(weights.values()) - 1.0) > 1e-9:
        errs.append("matching.weights: deben sumar 1")
    comp = data.get("comparison") or {}
    lim = comp.get("enumeration_limit")
    if not isinstance(lim, int) or isinstance(lim, bool) or lim < 1:
        errs.append("comparison.enumeration_limit: entero positivo")
        lim = 1
    cfg = CorrespondenceConfig(
        sha256=sha256,
        levels=levels,
        locations=locations,
        patterns=patterns,
        composite_patterns=tuple(composites),
        resolution_rules=tuple(rules),
        title_remove_words=frozenset(str(x).lower() for x in (title.get("remove_words") or [])),
        title_coincide_min=_num(title, "coincide_min", "title", errs),
        weights=weights,
        label_content_min=_num(m, "label_content_min", "matching", errs),
        propose_min=_num(m, "propose_min", "matching", errs),
        ambiguity_margin=_num(m, "ambiguity_margin", "matching", errs),
        high_ratio=_num(m, "high_ratio", "matching", errs),
        enumeration_limit=lim,
        level_of_type=level_of_type,
    )
    if errs:
        raise CorrespondenceConfigError(errs)
    return cfg


def load_config(path: Optional[str | pathlib.Path] = None) -> CorrespondenceConfig:
    p = pathlib.Path(path) if path else DEFAULT_CONFIG
    raw = p.read_bytes()
    return parse_config(yaml.safe_load(raw), hashlib.sha256(raw).hexdigest())
