"""Exportación de la simulación (v1.0, sección 9; v1.1, §9).

Marcas obligatorias:

- SINTÉTICO en el nombre de archivo, en la columna ``_sintetico`` de cada
  registro (con la etiqueta del modo), en la hoja ``LEEME`` y en el
  encabezado de cada CSV (la primera columna se llama ``_sintetico``).
- HIPOTÉTICO, además, en modo experimental: nombre de archivo, columna
  ``_hipotetico``, cada hoja y metadatos.

Estados exportados: los cinco de la IR y dos de exportación
(``NO_SIMULADO``, ``NO_CALCULADO``), en la hoja/CSV ``estados``. La hoja
``datos`` lleva el código o valor solo para VALID y MISSING_CODE.

Sin ``include_text`` no se exportan etiquetas de opciones ni enunciados.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from typing import Any

from survey_sim.util.xlsx import workbook_bytes

from . import SIMULATOR_VERSION
from .engine import EXPERIMENTAL, NO_CALCULADO, NO_SIMULADO, SimulationResult
from .scenarios import sensitive_candidates

STATES = ("VALID", "MISSING_CODE", "UNANSWERED", "NOT_APPLICABLE", "UNKNOWN_TO_EVALUATOR", NO_SIMULADO, NO_CALCULADO)


def file_prefix(r: SimulationResult) -> str:
    stem = r.instrument.source.name.rsplit(".", 1)[0]
    parts = ["SINTETICO"] + (["HIPOTETICO"] if r.mode == EXPERIMENTAL else []) + [stem, r.mode, r.scenario.mode, f"s{r.seed}"]
    return "_".join(p.replace(" ", "-") for p in parts)


def _marks(r: SimulationResult) -> tuple[list[str], list[Any]]:
    head = ["_sintetico"] + (["_hipotetico"] if r.mode == EXPERIMENTAL else [])
    vals = [r.scenario.label] + (["HIPOTÉTICO: reglas inferidas ejecutadas como hipótesis"] if r.mode == EXPERIMENTAL else [])
    return head, vals


def tables(r: SimulationResult) -> dict[str, tuple[list[str], list[list[Any]]]]:
    head, vals = _marks(r)
    vars_ = [v for v, _ in r.columns]
    item_of = dict(r.columns)
    base = head + ["_agente", "_estado_agente"]
    datos, estados, agentes = [], [], []
    for a in r.agents:
        row_d = vals + [a.index, a.status]
        row_e = vals + [a.index, a.status]
        for v in vars_:
            c = a.cells.get(item_of[v] or v)
            st = c.state if c else NO_SIMULADO
            row_e.append(st)
            row_d.append(c.value if c is not None and st in ("VALID", "MISSING_CODE") else None)
        datos.append(row_d)
        estados.append(row_e)
        intr = a.interruption or {}
        agentes.append(vals + [a.index, a.case, a.seed, a.status, a.termination, intr.get("item"), intr.get("rule"),
                               intr.get("reason"), ";".join(intr.get("u_reasons", [])), ";".join(a.marks),
                               ";".join(a.hypotheses), len(a.path)])
    return {
        "datos": (base + vars_, datos),
        "estados": (base + vars_, estados),
        "agentes": (head + ["_agente", "caso", "semilla_agente", "estado", "terminacion", "item_interrupcion",
                            "regla_interrupcion", "motivo", "razones_U", "marcas", "hipotesis", "items_recorridos"], agentes),
    }


def summary(r: SimulationResult, tree: dict) -> dict:
    from collections import Counter

    st = Counter(a.status for a in r.agents)
    reasons = Counter((a.interruption or {}).get("reason") for a in r.agents if a.interruption)
    points = Counter((a.interruption or {}).get("rule") for a in r.agents if a.interruption)
    reached: dict[str, int] = {}
    for a in r.agents:
        for k, c in a.cells.items():
            if c.state in ("VALID", "MISSING_CODE", "UNANSWERED", "UNKNOWN_TO_EVALUATOR", NO_CALCULADO):
                reached[k] = reached.get(k, 0) + 1
    items = [f.item.id for f in r.flow.items]
    hyps = sorted({h for a in r.agents for h in a.hypotheses})
    texts = {f.item.id: f.item.text.clean for f in r.flow.items}
    return {
        "agents": len(r.agents),
        "status": dict(sorted(st.items())),
        "interruption_reasons": dict(sorted((k or "", v) for k, v in reasons.items())),
        "interruption_points": dict(sorted((k or "", v) for k, v in points.items())),
        "coverage": {"items": len(items), "reached_by_some_agent": sum(1 for i in items if reached.get(i)),
                     "never_reached": [i for i in items if not reached.get(i)]},
        "hypotheses_used": hyps,
        "rules_executed": r.rules_executed,
        "rules_not_executed": r.rules_not_executed,
        "tree_counts": tree["counts"],
        "sensitive_candidates": sensitive_candidates(texts),
        "sensitive_declared": list(r.scenario.sensitive),
    }


def metadata(r: SimulationResult, tree: dict, *, versions: dict, files: dict[str, str], include_text: bool,
             config_hashes: dict) -> dict:
    return {
        "survey_simulator": {"report": "simulacion", "simulator_version": SIMULATOR_VERSION,
                             "norma": "especificación v1.0 + documento de cambios v1.1"},
        "SINTETICO": True,
        "HIPOTETICO": r.mode == EXPERIMENTAL,
        "label": r.scenario.label,
        "aviso": "Datos sintéticos generados por simulación. No son observaciones, resultados ni estimaciones reales.",
        "instrument": {"name": r.instrument.source.name, "sha256": r.instrument.source.sha256, "role": r.instrument.role.value},
        "mode": r.mode,
        "scenario": {"mode": r.scenario.mode, "name": r.scenario.name, "assumptions": r.scenario.assumptions,
                     "sha256": r.scenario.sha256, "artificial": True},
        "seed": r.seed,
        "seed_derivation": "semilla del agente = primeros 16 hex de SHA-256('survey-simulator:<semilla>:<índice>')",
        "versions": versions,
        "config_sha256": config_hashes,
        "include_text": include_text,
        "warnings": r.warnings,
        "summary": summary(r, tree),
        "tree": {"disclaimer": tree["disclaimer"], "limits": tree["limits"], "counts": tree["counts"]},
        "states": {"ir": list(STATES[:5]), "export_only": [NO_SIMULADO, NO_CALCULADO]},
        "files": files,
    }


def csv_bytes(header: list[str], rows: list[list[Any]]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for row in rows:
        w.writerow(["" if v is None else v for v in row])
    return buf.getvalue().encode("utf-8")


def xlsx_bytes(r: SimulationResult, tree: dict, meta_preview: dict, *, include_text: bool) -> bytes:
    t = tables(r)
    exp = r.mode == EXPERIMENTAL
    leeme = [
        ("SINTÉTICO", r.scenario.label),
        ("Aviso", "Datos sintéticos generados por simulación. No son observaciones, resultados ni estimaciones reales."),
    ]
    if exp:
        leeme.append(("HIPOTÉTICO", "Modo experimental: se ejecutaron reglas inferidas como hipótesis; sus resultados no confirman la semántica."))
    leeme += [("Modo", r.mode), ("Escenario", r.scenario.mode), ("Semilla", r.seed),
              ("Instrumento", f"{r.instrument.source.name} · {r.instrument.source.sha256}")]
    leeme += [("Advertencia", w) for w in r.warnings]
    leeme.append(("Árbol de recorridos", tree["disclaimer"]))
    sfx = " HIPOTÉTICO" if exp else ""
    sheets = [("LEEME" + sfx, ["Campo", "Valor"], leeme)]
    for name in ("datos", "estados", "agentes"):
        h, rows = t[name]
        sheets.append((f"{name}{sfx}", h, rows))
    nodes = tree["nodes"]
    sheets.append((f"recorridos{sfx}", ["nodo", "padre", "item", "decision", "rama", "clase", "testigo", "hipotesis", "HIPOTÉTICO", "motivos", "fin"], [
        (n["id"], n["parent"], n["item"], n["decision"], n["branch"], n["klass"], json.dumps(n["witness"], ensure_ascii=False) if n["witness"] else None,
         ";".join(n["hypotheses"]), n["hypothetical"], ";".join(n["reasons"]), n["end"]) for n in nodes]))
    if include_text:
        rows = []
        for f in r.flow.items:
            for o in f.item.options:
                rows.append((f.item.id, o.code, o.label.clean))
        sheets.append((f"etiquetas{sfx}", ["item", "codigo", "etiqueta"], rows))
    sheets.append((f"metadatos{sfx}", ["clave", "valor"], [(k, json.dumps(v, ensure_ascii=False, default=str)) for k, v in meta_preview.items()]))
    return workbook_bytes(sheets)


def build_outputs(r: SimulationResult, tree: dict, *, versions: dict, include_text: bool, config_hashes: dict) -> dict[str, bytes]:
    """Archivos de salida. El de metadatos (último) lleva el SHA-256 de los demás."""
    pre = file_prefix(r)
    t = tables(r)
    files: dict[str, bytes] = {}
    for name in ("datos", "estados", "agentes"):
        h, rows = t[name]
        files[f"{pre}_{name}.csv"] = csv_bytes(h, rows)
    # §7.1(b): toda salida lleva sus marcas también en el contenido, no solo en el nombre.
    marked_tree = {**tree, "SINTETICO": True, "HIPOTETICO": r.mode == EXPERIMENTAL}
    files[f"{pre}_recorridos.json"] = (json.dumps(marked_tree, ensure_ascii=False, indent=1, sort_keys=True, default=str) + "\n").encode("utf-8")
    preview = metadata(r, tree, versions=versions, files={}, include_text=include_text, config_hashes=config_hashes)
    files[f"{pre}.xlsx"] = xlsx_bytes(r, tree, {k: preview[k] for k in ("label", "mode", "scenario", "seed", "versions", "warnings")},
                                      include_text=include_text)
    hashes = {k: hashlib.sha256(v).hexdigest() for k, v in sorted(files.items())}
    meta = metadata(r, tree, versions=versions, files=hashes, include_text=include_text, config_hashes=config_hashes)
    files[f"{pre}_metadatos.json"] = (json.dumps(meta, ensure_ascii=False, indent=1, sort_keys=True, default=str) + "\n").encode("utf-8")
    return files
