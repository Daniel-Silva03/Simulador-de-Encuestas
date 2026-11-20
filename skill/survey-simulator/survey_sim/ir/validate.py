"""Validaciones estructurales y semánticas de la IR.

La validación estructural (tipos, campos obligatorios, campos no declarados,
valores de enumeraciones) la hace Pydantic al construir los modelos. Este
módulo añade las reglas que dependen de varios elementos a la vez.

Niveles:

- ``error``: la IR es incoherente y no debe usarse.
- ``warning``: la IR es usable, pero algo merece revisión. Por ejemplo, un
  destino de salto que no existe no es un error de la IR: es un hallazgo
  del programa que las verificaciones internas (paso 5) convertirán en
  ``Finding``. Aquí solo se señala.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Literal, Optional

from .conditions import Opaque, depth, iter_nodes, referenced_vars
from .models import (
    Certainty,
    Finding,
    Instrument,
    ItemType,
    OpaqueRule,
    Role,
    RuleAction,
    RuleKind,
    derive_evidence_level,
    finding_id,
)

#: Límites defensivos frente a entradas hostiles (sección 10.2).
MAX_CONDITION_DEPTH = 64
MAX_CONDITION_NODES = 5000

#: Reglas cuya condición es obligatoria.
_RULES_REQUIRING_WHEN = {
    RuleKind.FILTER,
    RuleKind.SKIP,
    RuleKind.VALIDATION,
    RuleKind.ENABLE,
    RuleKind.HIDE,
}


@dataclass(frozen=True)
class Issue:
    level: Literal["error", "warning"]
    code: str
    path: str
    message: str


class IRValidationError(ValueError):
    def __init__(self, issues: list[Issue]):
        self.issues = issues
        lines = "; ".join(f"{i.code} @ {i.path}: {i.message}" for i in issues)
        super().__init__(f"IR inválida ({len(issues)} errores): {lines}")


# --------------------------------------------------------------------------
# Condiciones
# --------------------------------------------------------------------------


def validate_condition(condition: object, path: str = "condition") -> list[Issue]:
    issues: list[Issue] = []
    d = depth(condition)
    if d > MAX_CONDITION_DEPTH:
        issues.append(Issue("error", "condition_too_deep", path, f"profundidad {d} > {MAX_CONDITION_DEPTH}"))
        return issues
    n = sum(1 for _ in iter_nodes(condition))
    if n > MAX_CONDITION_NODES:
        issues.append(Issue("error", "condition_too_large", path, f"{n} nodos > {MAX_CONDITION_NODES}"))
    return issues


def _has_opaque(condition: object) -> bool:
    return any(isinstance(n, Opaque) for n in iter_nodes(condition))


def _check_condition_certainty(cond, certainty: Certainty, path: str) -> list[Issue]:
    """Un elemento con un nodo Opaque no puede declararse ``explicit`` (D02)."""
    if cond is not None and certainty is Certainty.EXPLICIT and _has_opaque(cond):
        return [Issue("error", "explicit_with_opaque", path, "contiene Opaque pero su certeza es explicit")]
    return []


# --------------------------------------------------------------------------
# Hallazgos
# --------------------------------------------------------------------------


def validate_finding(f: Finding, path: str = "finding") -> list[Issue]:
    issues: list[Issue] = []
    expected = derive_evidence_level(f.refs, heuristic=f.heuristic, depends_on_unconfirmed=f.depends_on_unconfirmed)
    if f.evidence_level is not expected:
        issues.append(
            Issue(
                "error",
                "evidence_level_mismatch",
                path,
                f"evidence_level={f.evidence_level.value}, pero la certeza de sus elementos da {expected.value}",
            )
        )
    fid = finding_id(f.check_id, f.refs)
    if f.id != fid:
        issues.append(Issue("error", "finding_id_mismatch", path, "id no coincide con el hash de check_id y refs"))
    return issues


# --------------------------------------------------------------------------
# Instrumento
# --------------------------------------------------------------------------


def _duplicates(values: Iterable[str]) -> list[str]:
    return sorted(k for k, c in Counter(values).items() if c > 1)


def validate_instrument(inst: Instrument) -> list[Issue]:
    issues: list[Issue] = []

    def err(code: str, path: str, msg: str) -> None:
        issues.append(Issue("error", code, path, msg))

    def warn(code: str, path: str, msg: str) -> None:
        issues.append(Issue("warning", code, path, msg))

    items = [(si, ii, it) for si, s in enumerate(inst.sections) for ii, it in enumerate(s.items)]

    # Unicidad --------------------------------------------------------------
    for name, values in (
        ("section_id", [s.id for s in inst.sections]),
        ("item_id", [it.id for _, _, it in items]),
        ("variable_name", [v.name for v in inst.variables]),
        ("rule_id", [r.id for r in inst.rules]),
        ("termination_code", [t.code for t in inst.terminations]),
        ("quota_id", [q.id for q in inst.quotas]),
        ("external_dep", [f"{d.kind.value}:{d.name}" for d in inst.external_deps]),
    ):
        for dup in _duplicates(values):
            err(f"duplicate_{name}", name, f"valor repetido: {dup}")

    item_ids = {it.id for _, _, it in items}
    var_names = {v.name for v in inst.variables}
    term_codes = {t.code for t in inst.terminations}
    known_refs = item_ids | var_names

    # Secciones e ítems -----------------------------------------------------
    for si, s in enumerate(inst.sections):
        if s.pages is not None and inst.role is not Role.PROGRAM:
            err("pages_only_in_program", f"sections[{si}]", "pages solo existe en la IR del programa")

    for si, ii, it in items:
        p = f"sections[{si}].items[{ii}]"
        for dup in _duplicates(o.code for o in it.options):
            err("duplicate_option_code", f"{p}.options", f"código repetido: {dup}")
        if it.grid is not None:
            if it.type is not ItemType.GRID:
                err("grid_type_mismatch", p, "tiene grid pero type no es grid")
            for dup in _duplicates(o.code for o in it.grid.shared_options):
                err("duplicate_option_code", f"{p}.grid.shared_options", f"código repetido: {dup}")
            for ri, row in enumerate(it.grid.rows):
                if row.item_id not in item_ids:
                    warn("unresolved_grid_row", f"{p}.grid.rows[{ri}]", f"ítem no encontrado: {row.item_id}")
        if it.universe is not None:
            # La certeza que cuenta es la del universo, no la del ítem (2.0.0).
            u = it.universe.condition
            issues += validate_condition(u, f"{p}.universe.condition")
            issues += _check_condition_certainty(u, it.universe.certainty, f"{p}.universe.condition")
            for v in sorted(referenced_vars(u) - known_refs):
                warn("unresolved_condition_var", f"{p}.universe", f"variable no encontrada: {v}")
        for oi, o in enumerate(it.options):
            if o.display_if is not None:
                op = f"{p}.options[{oi}].display_if"
                issues += validate_condition(o.display_if, op)
                issues += _check_condition_certainty(o.display_if, o.certainty, op)
                for v in sorted(referenced_vars(o.display_if) - known_refs):
                    warn("unresolved_condition_var", op, f"variable no encontrada: {v}")

    # Variables -------------------------------------------------------------
    for vi, v in enumerate(inst.variables):
        if v.item_id is not None and v.item_id not in item_ids:
            warn("unresolved_variable_item", f"variables[{vi}]", f"ítem no encontrado: {v.item_id}")

    # Reglas ----------------------------------------------------------------
    for ri, r in enumerate(inst.rules):
        p = f"rules[{ri}]"
        if isinstance(r, OpaqueRule):
            # Sin condición, destino ni acción que comprobar: solo se conserva.
            continue
        if r.kind in _RULES_REQUIRING_WHEN and r.when is None:
            err("rule_missing_condition", p, f"una regla {r.kind.value} necesita condición")
        if r.kind is RuleKind.VALIDATION and r.action is None:
            err("validation_missing_action", p, "una validación necesita action (block/warn)")
        if r.kind is not RuleKind.VALIDATION and r.action is not None:
            err("action_only_for_validation", p, "action solo aplica a validaciones")
        if r.kind is RuleKind.COMPUTATION and r.target is None:
            err("computation_missing_target", p, "un cálculo necesita la variable destino (target)")
        if r.when is not None:
            issues += validate_condition(r.when, f"{p}.when")
            issues += _check_condition_certainty(r.when, r.certainty, f"{p}.when")
            for v in sorted(referenced_vars(r.when) - known_refs):
                warn("unresolved_condition_var", f"{p}.when", f"variable no encontrada: {v}")
        if r.target is not None and r.target not in known_refs | term_codes:
            warn("unresolved_rule_target", p, f"destino no encontrado: {r.target}")

    # Hallazgos de lectura --------------------------------------------------
    for fi, f in enumerate(inst.parse_findings):
        issues += validate_finding(f, f"parse_findings[{fi}]")

    return issues


def errors(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.level == "error"]


def validate_or_raise(inst: Instrument) -> list[Issue]:
    """Valida y lanza ``IRValidationError`` si hay errores. Devuelve los avisos."""
    issues = validate_instrument(inst)
    errs = errors(issues)
    if errs:
        raise IRValidationError(errs)
    return [i for i in issues if i.level == "warning"]
