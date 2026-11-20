"""Escenarios de respuesta (v1.0, sección 9; v1.1, §7.6).

Tres modos de elegir respuestas, cada uno con su etiqueta obligatoria:

- ``diversidad``: uniforme sobre las opciones que se muestran. Es un
  **escenario artificial uniforme**, no una estimación poblacional.
- ``escenario``: distribuciones que el usuario declara ítem por ítem en YAML.
  Nunca las infiere el skill. Lo no declarado solo se responde si el archivo
  declara expresamente ``por_defecto: uniforme``.
- ``funcional``: guiones de respuestas por caso (prueba de rutas).

Los ítems marcados ``sensitive_real_person`` solo usan la uniforme en
``diversidad`` y exigen distribución declarada en ``escenario`` (v1.0, 9).
"""

from __future__ import annotations

import hashlib
import pathlib
from dataclasses import dataclass, field
from typing import Any, Optional

import yaml

DIVERSITY, SCENARIO, FUNCTIONAL = "diversidad", "escenario", "funcional"
LABELS = {
    DIVERSITY: "SINTÉTICO – PRUEBA DE PROCESAMIENTO (escenario artificial uniforme)",
    SCENARIO: "SINTÉTICO – ESCENARIO HIPOTÉTICO: {supuestos}",
    FUNCTIONAL: "SINTÉTICO – PRUEBA FUNCIONAL",
}

#: Pistas para proponer ítems sensibles (se confirman con el usuario; nunca se marcan solos).
SENSITIVE_HINTS = ("presidente", "presidenta", "gobernador", "gobernadora", "alcalde", "alcaldesa", "candidato",
                   "candidata", "senador", "senadora", "concejal", "ministro", "ministra", "congresista", "diputado")


class ScenarioError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass
class Scenario:
    mode: str
    name: str
    assumptions: str
    sha256: Optional[str] = None
    default_uniform: bool = True
    distributions: dict[str, dict] = field(default_factory=dict)  # item -> {"codes": {c: w}} | {"rango": [a, b]}
    cases: list[dict] = field(default_factory=list)  # funcional
    sensitive: tuple[str, ...] = ()
    binding: Optional[dict] = None

    @property
    def label(self) -> str:
        return LABELS[self.mode].format(supuestos=self.assumptions or self.name)


def uniform(sensitive: tuple[str, ...] = ()) -> Scenario:
    return Scenario(DIVERSITY, "escenario artificial uniforme",
                    "distribución uniforme sobre las opciones mostradas; no es una estimación poblacional",
                    sensitive=tuple(sensitive))


def _load(path: str | pathlib.Path) -> tuple[Any, str]:
    raw = pathlib.Path(path).read_bytes()
    return yaml.safe_load(raw), hashlib.sha256(raw).hexdigest()


def load_scenario(path: str | pathlib.Path) -> Scenario:
    data, sha = _load(path)
    errs: list[str] = []
    if not isinstance(data, dict) or data.get("record_type") not in ("escenario", "guion"):
        raise ScenarioError(["record_type: escenario o guion"])
    sens = data.get("sensibles") or []
    if not isinstance(sens, list) or not all(isinstance(x, str) for x in sens):
        errs.append("sensibles: lista de ids de ítem")
        sens = []
    if data["record_type"] == "guion":
        cases = []
        for i, c in enumerate(data.get("casos") or []):
            if not isinstance(c, dict) or not isinstance(c.get("respuestas"), dict):
                errs.append(f"casos[{i}]: nombre y respuestas")
                continue
            resp = {}
            for k, v in c["respuestas"].items():
                if isinstance(v, list):
                    if not v or not all(isinstance(x, (str, int, float)) and not isinstance(x, bool) for x in v):
                        errs.append(f"casos[{i}].respuestas.{k}: lista de códigos no vacía")
                    resp[str(k)] = tuple(str(x) for x in v)
                elif isinstance(v, (str, int, float)) and not isinstance(v, bool):
                    resp[str(k)] = str(v)
                else:
                    errs.append(f"casos[{i}].respuestas.{k}: código o lista de códigos")
            cases.append({"nombre": str(c.get("nombre") or f"caso_{i + 1}"), "respuestas": resp})
        if not cases:
            errs.append("casos: al menos uno")
        if errs:
            raise ScenarioError(errs)
        return Scenario(FUNCTIONAL, str(data.get("nombre") or "guion"), str(data.get("supuestos") or ""), sha,
                        False, {}, cases, tuple(sens), data.get("binding"))
    name = data.get("nombre")
    sup = data.get("supuestos")
    if not isinstance(name, str) or not name.strip():
        errs.append("nombre: obligatorio")
    if not isinstance(sup, str) or not sup.strip():
        errs.append("supuestos: obligatorio (se rotulan en las salidas)")
    pd = data.get("por_defecto")
    if pd not in (None, "uniforme"):
        errs.append("por_defecto: solo 'uniforme' o ausente")
    dists: dict[str, dict] = {}
    for item, spec in (data.get("items") or {}).items():
        if isinstance(spec, dict) and "rango" in spec:
            r = spec["rango"]
            if not (isinstance(r, list) and len(r) == 2 and all(isinstance(x, int) and not isinstance(x, bool) for x in r) and r[0] <= r[1]):
                errs.append(f"items.{item}.rango: [mínimo, máximo] enteros")
                continue
            dists[str(item)] = {"rango": [r[0], r[1]]}
        elif isinstance(spec, dict) and spec:
            codes = {}
            for c, w in spec.items():
                if not isinstance(w, (int, float)) or isinstance(w, bool) or w < 0:
                    errs.append(f"items.{item}.{c}: peso no negativo")
                    continue
                codes[str(c)] = float(w)
            if not codes or sum(codes.values()) <= 0:
                errs.append(f"items.{item}: pesos con suma positiva")
                continue
            dists[str(item)] = {"codes": codes}
        else:
            errs.append(f"items.{item}: pesos por código o rango")
    for s in sens:
        if s not in dists:
            errs.append(f"sensibles: {s} exige una distribución declarada")
    if errs:
        raise ScenarioError(errs)
    return Scenario(SCENARIO, name, sup, sha, pd == "uniforme", dists, [], tuple(sens), data.get("binding"))


def sensitive_candidates(texts: dict[str, str]) -> list[str]:
    """Ítems cuyo texto contiene pistas de cargos políticos. Solo propuesta: se confirma con el usuario."""
    from survey_sim.util.textnorm import tokens

    out = []
    for iid, t in texts.items():
        tk = set(tokens(t))
        if tk & set(SENSITIVE_HINTS):
            out.append(iid)
    return out
