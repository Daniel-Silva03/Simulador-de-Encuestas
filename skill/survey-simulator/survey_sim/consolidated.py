"""Informe consolidado (E3): correspondencia + simulación diferencial, con trazabilidad.

Archivos (todos con la marca SINTÉTICO en el nombre; HIPOTÉTICO en modo
experimental):

- ``<prefijo>_consolidado.json``: informe completo; es el último en
  publicarse y lleva el SHA-256 de los demás (marca de completitud).
- ``<prefijo>_consolidado.xlsx``: hojas LEEME, Resumen, Hallazgos,
  Condiciones, Divergencias, Comparabilidad y Limitaciones.
- ``<prefijo>_consolidado.md``: resumen legible.
- ``<prefijo>_diferencial_agentes.csv``: estado de cada pareja por agente en
  ambos lados (para auditar las divergencias).

Vista minimizada: sin texto del instrumento (los identificadores de pregunta
no son texto libre).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import Counter
from survey_sim.simulate.differential import DifferentialResult, cell_state
from survey_sim.util.xlsx import workbook_bytes

LEVELS = (("error_comprobado", "Error comprobado"), ("sospecha", "Sospecha"), ("no_verificable", "No verificable"))

LIMITATIONS = [
    "Los datos de la simulación diferencial son sintéticos: no son observaciones ni estimaciones.",
    "Solo se comparan parejas declaradas con comparación habilitada, y solo donde ambos lados decidieron el recorrido.",
    "En modo estándar las reglas GIM (todas inferidas) interrumpen al agente del programa en la primera regla de flujo (v1.1, §7.5): la cobertura comparable puede ser pequeña.",
    "En modo experimental las divergencias de agentes que usaron hipótesis se marcan HIPOTÉTICO; ninguna confirma la semántica de esas reglas.",
    "Una divergencia es HIPOTÉTICA solo si algún lado usó una hipótesis hasta decidir esa pregunta; se informa la lista exacta.",
    "Un código oculto en el programa por una regla de presentación de opciones se informa como opcion_oculta, nunca como código inexistente.",
    "El árbol de recorridos y las divergencias no prueban qué hará el programa con encuestados reales.",
]


def prefix(diff: DifferentialResult) -> str:
    return "SINTETICO_HIPOTETICO" if diff.hypothetical else "SINTETICO"


def build(rep: dict, diff: DifferentialResult, *, versions: dict, hashes: dict) -> dict:
    d = diff.to_dict()
    comp = rep["comparison"]
    return {
        "survey_simulator": {"report": "consolidado", "norma": "especificación v1.0 + documento de cambios v1.1"},
        "SINTETICO": True,
        "HIPOTETICO": diff.hypothetical,
        "aviso": "Contiene resultados de simulación sintética. No son observaciones, resultados ni estimaciones reales.",
        "inputs": rep["inputs"],
        "binding": rep["binding"],
        "versions": versions,
        "config_sha256": hashes,
        "privacy": rep["privacy"],
        "correspondence": {
            "result": rep["result"],
            "identity": {k: rep["identity"][k] for k in ("verdict", "source", "backing", "observation_review",
                                                         "comparison_enabled", "matching_level")},
            "pairs": [{"design": p["design"], "program": p["program"], "state": p["state"]} for p in rep["matching"]["pairs"]],
            "only_design": rep["matching"]["only_design"], "only_program": rep["matching"]["only_program"],
            "findings": comp["findings"], "findings_by_level": comp["findings_by_level"],
            "conditions": comp["conditions"],
        },
        "differential": d,
        "limitations": rep["limitations"] + comp["limitations"] + LIMITATIONS,
    }


def agents_csv(diff: DifferentialResult) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    head = ["_sintetico"] + (["_hipotetico"] if diff.hypothetical else []) + ["_agente", "estado_diseno", "estado_programa",
                                                                               "hipotesis"]
    for d, p in diff.pairs:
        head += [f"D:{d}", f"P:{p}"]
    w.writerow(head)
    if diff.enabled:
        for da, pa in zip(diff.design_run.agents, diff.program_run.agents):
            hyp = sorted(set(da.hypotheses) | set(pa.hypotheses))
            row = ["SINTÉTICO"] + (["HIPOTÉTICO"] if diff.hypothetical else []) + [da.index, da.status, pa.status, ";".join(hyp)]
            for d, p in diff.pairs:
                row += [cell_state(da, d), cell_state(pa, p)]
            w.writerow(row)
    return buf.getvalue().encode("utf-8")


def xlsx(c: dict) -> bytes:
    hyp = c["HIPOTETICO"]
    tag = " HIPOTÉTICO" if hyp else ""
    d = c["differential"]
    corr = c["correspondence"]
    leeme = [("SINTÉTICO", c["aviso"]), ("HIPOTÉTICO", "sí: modo experimental" if hyp else "no"),
             ("Diferencial", d["disclaimer"]), ("Escenario", d["scenario"]["label"]), ("Semilla", d["seed"]),
             ("Modo", d["mode"]), ("Agentes", d["agents"])]
    resumen = [("Resultado de correspondencia", corr["result"]), ("Identidad", corr["identity"]["verdict"]),
               ("Comparación habilitada", corr["identity"]["comparison_enabled"]),
               ("Diferencial habilitado", d["enabled"]), ("Motivo", d["reason"])]
    resumen += [(f"Hallazgos: {lab}", corr["findings_by_level"][k]) for k, lab in LEVELS]
    resumen += [(f"Divergencias: {k}", v) for k, v in sorted(d["divergences_by_evidence"].items())]
    resumen += [(f"Agentes diseño: {k}", v) for k, v in d["agent_statuses"].get("design", {}).items()]
    resumen += [(f"Agentes programa: {k}", v) for k, v in d["agent_statuses"].get("program", {}).items()]
    resumen += [("Diseño", f"{c['inputs']['design']['name']} · {c['inputs']['design']['sha256']}"),
                ("Programa", f"{c['inputs']['program']['name']} · {c['inputs']['program']['sha256']}")]
    sheets = [
        (f"LEEME{tag}", ["Campo", "Valor"], leeme),
        (f"Resumen{tag}", ["Campo", "Valor"], resumen),
        (f"Hallazgos{tag}", ["Nivel", "Regla", "Categoría", "Severidad", "Heurístico", "Depende de", "Explicación", "Referencias"],
         [(f["evidence_level"], f["check_id"], f["category"], f["severity"], f["heuristic"], ", ".join(f["depends_on_unconfirmed"]),
           f["explanation"], "; ".join(f"{x['side']}:{x['element_id']}" for x in f["refs"])) for f in corr["findings"]]),
        (f"Condiciones{tag}", ["Pareja", "Qué", "Resultado", "Método", "Motivos", "Completa", "Contraejemplo"],
         [(" / ".join(x["pair"]), x["what"], x["result"], x["method"], ", ".join(x["reasons"]), x["limits"]["complete"],
           json.dumps(x["counterexample"], ensure_ascii=False)) for x in corr["conditions"]]),
        (f"Divergencias{tag}", ["Categoría", "Diseño", "Programa", "Estado diseño", "Estado programa", "Agentes",
                                "Confirmados", "Hipotéticos", "Evidencia", "Hipótesis", "Agentes de ejemplo"],
         [(x["category"], x["design"], x["program"], x["design_state"], x["program_state"], x["agents"],
           x["confirmed_agents"], x["hypothetical_agents"], x["evidence"], ", ".join(x["hypotheses"]),
           ", ".join(map(str, x["example_agents"]))) for x in d["divergences"]]),
        (f"Comparabilidad{tag}", ["Pareja", "Comparables", "Diseño sin decidir", "Programa sin decidir"],
         [(k, v["comparable"], v["design_undecided"], v["program_undecided"]) for k, v in d["comparable"].items()]),
        (f"Limitaciones{tag}", ["Limitación"], [(x,) for x in c["limitations"]]),
    ]
    return workbook_bytes(sheets)


def markdown(c: dict) -> str:
    d, corr = c["differential"], c["correspondence"]
    mark = "**SINTÉTICO" + (" · HIPOTÉTICO" if c["HIPOTETICO"] else "") + "**"
    lines = ["# Informe consolidado", "", f"{mark}: {c['aviso']}", "",
             f"- Diseño: `{c['inputs']['design']['name']}` · `{c['inputs']['design']['sha256'][:16]}…`",
             f"- Programa: `{c['inputs']['program']['name']}` · `{c['inputs']['program']['sha256'][:16]}…`", "",
             f"## Correspondencia: {corr['result']}", "",
             f"- Identidad: {corr['identity']['verdict']} · comparación habilitada: {corr['identity']['comparison_enabled']}",
             f"- Parejas: {dict(Counter(p['state'] for p in corr['pairs']))} · solo diseño: {corr['only_design']} · solo programa: {corr['only_program']}",
             "- Hallazgos: " + ", ".join(f"{lab.lower()} {corr['findings_by_level'][k]}" for k, lab in LEVELS),
             "- Condiciones: " + (", ".join(f"{k} {v}" for k, v in sorted(Counter(x['result'] for x in corr['conditions']).items())) or "ninguna"),
             "", f"## Simulación diferencial ({d['mode']}, semilla {d['seed']}, {d['agents']} agentes)", ""]
    if not d["enabled"]:
        lines.append(f"- No se ejecutó: {d['reason']}.")
    else:
        lines.append(f"- Escenario: {d['scenario']['label']}")
        lines.append(f"- Estados de los agentes: diseño {d['agent_statuses']['design']} · programa {d['agent_statuses']['program']}")
        if not d["divergences"]:
            lines.append("- Sin divergencias en los segmentos comparables.")
        for x in d["divergences"]:
            where = f"{x['design']} / {x['program']}" if x["design"] or x["program"] else "fin del recorrido"
            lines.append(f"- [{x['evidence']}{' · HIPOTÉTICO' if x['HIPOTETICO'] else ''}] {x['category']} en {where}: "
                         f"diseño {x['design_state']}, programa {x['program_state']} ({x['agents']} agentes)")
        lines.append(f"- {d['disclaimer']}")
    lines += ["", "## Limitaciones", ""] + [f"- {x}" for x in c["limitations"]] + [""]
    return "\n".join(lines)


def outputs(c: dict, diff: DifferentialResult) -> tuple[dict[str, bytes], str]:
    pre = prefix(diff)
    files = {f"{pre}_consolidado.xlsx": xlsx(c), f"{pre}_consolidado.md": markdown(c).encode("utf-8"),
             f"{pre}_diferencial_agentes.csv": agents_csv(diff)}
    marker = f"{pre}_consolidado.json"
    full = {**c, "files": {k: hashlib.sha256(v).hexdigest() for k, v in sorted(files.items())}}
    files[marker] = (json.dumps(full, ensure_ascii=False, indent=1, sort_keys=True, default=str) + "\n").encode("utf-8")
    return files, marker
