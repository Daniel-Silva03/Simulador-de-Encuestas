"""Orquestación de la correspondencia (E1) y sus informes.

Lee el Word y el programa GIM con sus lectores congelados, calcula el
``binding`` vigente, carga los registros humanos, empareja, determina la
identidad y, solo con ``comparison_enabled``, compara. Los informes (JSON,
Excel y Markdown) son vistas minimizadas: sin texto libre del instrumento
salvo con ``include_text``.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any, Optional

from survey_sim.adapters.gim import read_gim_bytes
from survey_sim.adapters.gim.expr_parser import PARSER_VERSION
from survey_sim.adapters.gim.semantics import Semantics, load_semantics
from survey_sim.adapters.gim.to_ir import ADAPTER_VERSION as GIM_VERSION
from survey_sim.compare import checks
from survey_sim.flowmodel import build as build_flow
from survey_sim.ir.models import Instrument
from survey_sim.util.privacy import NO_KEY, TextProtector
from survey_sim.util.xlsx import workbook_bytes
from survey_sim.word.lexicon import Lexicon, load_lexicon
from survey_sim.word.to_ir import ADAPTER_VERSION as WORD_VERSION
from survey_sim.word.to_ir import read_docx_bytes

from . import CORRESPONDENCE_VERSION, RULESET_VERSION
from .config import CorrespondenceConfig, load_config
from .identity import DOCS, Identity, determine, signal_view
from .matching import Matching, match
from .records import load_equivalences, load_pairs, load_review_log

LEVEL_SHEETS = (("error_comprobado", "Error comprobado"), ("sospecha", "Sospecha"), ("no_verificable", "No verificable"))


def make_binding(design: Instrument, program: Instrument, cfg: CorrespondenceConfig, sem: Semantics, lex: Lexicon) -> dict:
    return {
        "inputs": {"design_sha256": design.source.sha256, "program_sha256": program.source.sha256},
        "config": {"correspondence_yaml_sha256": cfg.sha256, "semantics_sha256": sem.sha256,
                   "lexicon_sha256": lex.sha256, "ruleset_version": RULESET_VERSION},
        "versions": {"word_adapter": WORD_VERSION, "gim_adapter": GIM_VERSION, "expr_parser": PARSER_VERSION,
                     "correspondence": CORRESPONDENCE_VERSION},
    }


@dataclass
class CorrespondenceResult:
    design: Instrument
    program: Instrument
    binding: dict
    identity: Identity
    matching: Matching
    comparison: checks.Comparison
    inputs_meta: dict
    pairs_meta: Optional[dict]
    equivalences_meta: Optional[dict]


def read_inputs(design_bytes: bytes, design_name: str, program_bytes: bytes, program_name: str, *,
                lexicon: Lexicon, semantics: Semantics) -> tuple[Instrument, Instrument]:
    d = read_docx_bytes(design_bytes, design_name, lexicon).instrument
    p = read_gim_bytes(program_bytes, program_name, semantics).instrument
    return d, p


def correspond(design: Instrument, program: Instrument, *, cfg: CorrespondenceConfig, semantics: Semantics,
               lexicon: Lexicon, review_log=None, pairs_path=None, equivalences_path=None,
               unreadable: frozenset = frozenset()) -> CorrespondenceResult:
    binding = make_binding(design, program, cfg, semantics, lexicon)
    records = load_review_log(review_log, binding)
    pairs = load_pairs(pairs_path, binding)
    eq = load_equivalences(equivalences_path, binding)
    m = match(design, program, cfg, pairs)
    ident = determine(design, program, cfg, matching_ratio=m.ratio, records=records, unreadable=unreadable)
    dflow, pflow = build_flow(design), build_flow(program)
    comp = checks.run(design, program, dflow, pflow, m, comparison_enabled=ident.comparison_enabled, eq=eq,
                      limit=cfg.enumeration_limit)
    eq_meta = None
    if equivalences_path is not None:
        eq_meta = {"sha256": eq.sha256, "variables": [f"{s}:{v}" for (s, v) in sorted(eq.maps)], "stale": eq.stale,
                   "errors": eq.errors, "effective": eq.effective}
    return CorrespondenceResult(design, program, binding, ident, m, comp,
                                {d: {"name": i.source.name, "sha256": i.source.sha256} for d, i in (("design", design), ("program", program))},
                                m.declarations, eq_meta)


# --------------------------------------------------------------------------
# Vistas e informes
# --------------------------------------------------------------------------


def _shower(protector: TextProtector, include_text: bool):
    def show(v: Any) -> Any:
        if v is None:
            return None
        return v if include_text else {"text": protector.describe(str(v))}
    return show


def report_dict(r: CorrespondenceResult, *, include_text: bool = False, protector: TextProtector = NO_KEY) -> dict:
    show = _shower(protector, include_text)
    idn = r.identity
    findings = [f.model_dump(mode="json") for f in r.comparison.findings]
    by_level = Counter(f["evidence_level"] for f in findings)
    pairs = [{"design": p.design, "program": p.program, "state": p.state, "class": p.klass, "score": p.score,
              "parts": p.parts, "conclusive_equivalence_allowed": p.declared} for p in r.matching.pairs]
    return {
        "survey_simulator": {"report": "correspondencia", "ruleset_version": RULESET_VERSION,
                             "correspondence_version": CORRESPONDENCE_VERSION,
                             "norma": "especificación v1.0 + documento de cambios v1.1"},
        "inputs": r.inputs_meta,
        "binding": r.binding,
        "privacy": protector.report_block(include_text),
        "result": idn.result,
        "identity": {
            "verdict": idn.verdict, "source": idn.source, "row": idn.row, "backing": idn.backing,
            "observation_review": idn.observation_review, "comparison_enabled": idn.comparison_enabled,
            "matching_level": idn.matching_level, "sustaining": idn.sustaining,
            "signals": [signal_view(s, show) for s in idn.signals],
            "blocking": idn.blocking, "advisory": idn.advisory,
            "unconfirmed_assignments": idn.unconfirmed_assignments,
            "resolution_rules": idn.resolution_rules, "composite_patterns_applied": idn.composite_applied,
            "declaration": idn.declaration, "signals_contradicted_by_declaration": idn.contradicted_by_declaration,
            "verifications": idn.verifications, "review_log": idn.records,
            "extraction_findings": idn.extraction_findings, "warnings": idn.warnings,
            "note": "La evidencia de parejas de preguntas nunca prueba la identidad ni habilita la comparación (v1.1, §3.1).",
        },
        "matching": {
            "mode": "comparación" if idn.comparison_enabled else "diagnóstico",
            "ratio": r.matching.ratio, "n_design": r.matching.n_design, "n_program": r.matching.n_program,
            "pairs": pairs, "only_design": r.matching.only_design, "only_program": r.matching.only_program,
            "ambiguous": r.matching.ambiguous, "same_label_different_content": r.matching.same_label_different,
            "declared_invalid": r.matching.declared_invalid, "excluded_program_system_paradata": r.matching.excluded_program,
            "declared_pairs_file": r.pairs_meta, "notes": r.matching.notes,
        },
        "equivalences_file": r.equivalences_meta,
        "comparison": {
            "enabled": idn.comparison_enabled,
            "findings": findings,
            "findings_by_level": {k: by_level.get(k, 0) for k, _ in LEVEL_SHEETS},
            "conditions": r.comparison.conditions,
            "limitations": r.comparison.limitations,
        },
        "limitations": LIMITATIONS,
    }


LIMITATIONS = [
    "Todas las asignaciones de identificadores y todas las hipótesis GIM están en inferred: sin declaración humana vigente no se habilita la comparación.",
    "Las reglas GIM dependen de semántica no confirmada (D15): sus hallazgos son, como máximo, sospecha.",
    "«Equivalente» exige pareja declarada y comparación habilitada (v1.1, §3.2 y §5.1); con valores vacíos sin política confirmada la enumeración da U y el resultado es no verificable.",
    "Los umbrales de emparejamiento están calibrados con pares sintéticos (v1.0, 7.2).",
    "Las salidas no contienen texto del instrumento salvo con --include-text.",
]


def report_xlsx(rep: dict) -> bytes:
    idn = rep["identity"]
    resumen = [
        ("Resultado", rep["result"]), ("Veredicto", idn["verdict"]), ("Fuente", idn["source"]), ("Fila aplicada", idn["row"]),
        ("Respaldo (backing)", idn["backing"]), ("Revisión de observaciones", idn["observation_review"]),
        ("Comparación habilitada", idn["comparison_enabled"]), ("Emparejamiento", idn["matching_level"]),
        ("Diseño", f"{rep['inputs']['design']['name']} · {rep['inputs']['design']['sha256']}"),
        ("Programa", f"{rep['inputs']['program']['name']} · {rep['inputs']['program']['sha256']}"),
        ("Versión de reglas", rep["survey_simulator"]["ruleset_version"]),
        ("Privacidad", json.dumps(rep["privacy"], ensure_ascii=False)),
    ] + [(f"Hallazgos: {lab}", rep["comparison"]["findings_by_level"][k]) for k, lab in LEVEL_SHEETS] + \
        [("Aviso", w) for w in idn["warnings"]]
    sheets = [("Resumen", ["Campo", "Valor"], resumen)]
    sheets.append(("Identidad", ["Tipo", "Estado", "Certeza observación", "Alcance", "Asignación", "Clave"], [
        (s["type"], s["state"], s["observation"]["certainty"], json.dumps(s["observation"]["scope_basis"]),
         json.dumps(s["assignment"]), s["key"]) for s in idn["signals"]]))
    sheets.append(("Bloqueos", ["Id", "Señal", "Documento", "Ubicación", "Motivo", "Revisión", "Estado", "Informativo"], [
        (b["id"], b["signal"], b["document"], b["location"], b["reason"], b["review"], b.get("status"), b["informative"])
        for b in idn["blocking"]]))
    m = rep["matching"]
    rows = [(p["design"], p["program"], p["state"], p["class"], p["score"]) for p in m["pairs"]]
    rows += [(d, "", "", "SOLO_DISENO", "") for d in m["only_design"]]
    rows += [("", p, "", "SOLO_PROGRAMA", "") for p in m["only_program"]]
    rows += [(a["design"], ", ".join(a["candidates"]), "", "AMBIGUA", "") for a in m["ambiguous"]]
    sheets.append(("Pareos", ["Diseño", "Programa", "Estado (v1.1)", "Clase (v1.0)", "Puntaje"], rows))
    sheets.append(("Condiciones", ["Pareja", "Qué", "Resultado", "Método", "Motivos", "Dominio", "Evaluadas", "Completa", "Límite", "Contraejemplo"], [
        (" / ".join(c["pair"]), c["what"], c["result"], c["method"], ", ".join(c["reasons"]), c["limits"]["domain_size"],
         c["limits"]["evaluated"], c["limits"]["complete"], c["limits"]["enumeration_limit"], json.dumps(c["counterexample"], ensure_ascii=False))
        for c in rep["comparison"]["conditions"]]))
    for k, lab in LEVEL_SHEETS:
        sheets.append((lab, ["Id", "Regla", "Categoría", "Severidad", "Heurístico", "Depende de", "Explicación", "Referencias", "Sugerencia"], [
            (f["id"][:16], f["check_id"], f["category"], f["severity"], f["heuristic"], ", ".join(f["depends_on_unconfirmed"]),
             f["explanation"], "; ".join(f"{x['side']}:{x['element_id']}({x['certainty']})" for x in f["refs"]), f["suggested_check"])
            for f in rep["comparison"]["findings"] if f["evidence_level"] == k]))
    sheets.append(("Limitaciones", ["Limitación"], [(x,) for x in rep["limitations"] + rep["comparison"]["limitations"]]))
    return workbook_bytes(sheets)


def report_md(rep: dict) -> str:
    idn = rep["identity"]
    lines = [
        "# Informe de correspondencia", "",
        f"- Diseño: `{rep['inputs']['design']['name']}` · SHA-256 `{rep['inputs']['design']['sha256']}`",
        f"- Programa: `{rep['inputs']['program']['name']}` · SHA-256 `{rep['inputs']['program']['sha256']}`",
        f"- Versión de reglas: {rep['survey_simulator']['ruleset_version']}", "",
        f"## Resultado: {rep['result']}", "",
        f"- Veredicto: **{idn['verdict']}** (fuente: {idn['source']}; fila: {idn['row']})",
        f"- Respaldo: {idn['backing']} · revisión de observaciones: {idn['observation_review']}",
        f"- Comparación habilitada: {idn['comparison_enabled']} · emparejamiento {idn['matching_level']} ({rep['matching']['ratio']})",
    ]
    for w in idn["warnings"]:
        lines.append(f"- Aviso: {w}")
    open_b = [b for b in idn["blocking"] if b.get("status") == "open" and not b["informative"]]
    if open_b:
        lines += ["", "## Revisión necesaria", ""]
        lines += [f"- `{b['id']}` · {b['signal']} · {b['document']} · {b['reason']} → {b['review']}" for b in open_b]
    if idn["unconfirmed_assignments"]:
        lines += ["", "Asignaciones usadas **no confirmadas**: " + ", ".join(sorted({u['level'] for u in idn['unconfirmed_assignments']}))]
    m = rep["matching"]
    lines += ["", f"## Emparejamiento ({m['mode']})", "",
              f"- Parejas: {len(m['pairs'])} · solo diseño: {len(m['only_design'])} · solo programa: {len(m['only_program'])} · ambiguas: {len(m['ambiguous'])}"]
    st = Counter(p["state"] for p in m["pairs"])
    lines.append("- Estados: " + ", ".join(f"{k}: {v}" for k, v in sorted(st.items())))
    c = rep["comparison"]
    lines += ["", "## Hallazgos", ""]
    if c["enabled"]:
        lines += [f"- {lab}: {c['findings_by_level'][k]}" for k, lab in LEVEL_SHEETS]
    else:
        lines.append("- No hay comparación: la identidad no la habilita (v1.0, 7.3).")
    lines += ["", "## Limitaciones", ""] + [f"- {x}" for x in rep["limitations"]]
    lines += ["", "_La evidencia de parejas de preguntas nunca prueba la identidad del estudio._", ""]
    return "\n".join(lines)
