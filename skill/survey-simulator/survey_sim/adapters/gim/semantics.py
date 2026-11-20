"""Registro versionado de semántica GIM (especificación v1.0, sección 6; D09, D15).

El cargador valida la estructura y aplica las reglas de rechazo de
``status: confirmed``:

- falta ``confirmation``, o falta o está vacío alguno de sus campos
  obligatorios;
- ``kind`` no es ``vendor_documentation`` ni ``responsible_person``;
- ``date`` o ``recorded_at`` no son fechas ISO válidas, o ``date`` es
  posterior a ``recorded_at``;
- ``scope`` no incluye la entrada que se confirma;
- en ``empty_value_comparison``, algún ``result`` sigue en ``unknown``.

Si una entrada trae ``confirmation`` pero no está ``confirmed``, se acepta
con un aviso. La comprobación de que ``statement`` coincide con ``meaning``
queda para la revisión humana: no se puede verificar automáticamente.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import pathlib
from dataclasses import dataclass, field
from typing import Any, Optional

import yaml

DEFAULT_SEMANTICS = pathlib.Path(__file__).resolve().parents[3] / "config" / "semantics" / "gim.yaml"

REQUIRED_ENTRIES = ("tip", "filtros", "ord", "acc", "empty_value_comparison")
CONFIRMATION_KINDS = ("vendor_documentation", "responsible_person")
COMMON_FIELDS = ("kind", "date", "scope", "statement", "recorded_by", "recorded_at")
REFERENCE_FIELDS = {
    "vendor_documentation": ("title", "version", "locator", "source"),
    "responsible_person": ("role", "organization", "channel", "record_id"),
}
CHANNELS = ("email", "acta", "ticket")
EMPTY_STATES = ("UNANSWERED", "NOT_APPLICABLE")
OPERATOR_FAMILIES = ("eq", "ne", "order", "functions")
RESULTS = ("true", "false", "unknown")


class SemanticsError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class Hypothesis:
    name: str
    meaning: Any
    status: str  # inferred | confirmed
    confirmation_required: bool
    evidence: tuple
    confirmation: Optional[dict]
    extra: dict = field(default_factory=dict)

    @property
    def confirmed(self) -> bool:
        return self.status == "confirmed"

    @property
    def tag(self) -> str:
        """Etiqueta para notas y dependencias: ``semantics:<nombre>=<estado>``."""
        return f"semantics:{self.name}={self.status}"


@dataclass(frozen=True)
class Semantics:
    version: str
    sha256: str
    entries: dict[str, Hypothesis]
    warnings: tuple[str, ...]

    def __getitem__(self, name: str) -> Hypothesis:
        return self.entries[name]

    def pending(self) -> list[str]:
        return sorted(n for n, h in self.entries.items() if not h.confirmed)


def _is_empty(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, (list, dict)) and not v)


def _iso_date(v: Any) -> Optional[_dt.date]:
    if isinstance(v, _dt.datetime):
        return None  # se exige fecha, no fecha y hora
    if isinstance(v, _dt.date):
        return v
    if isinstance(v, str):
        try:
            return _dt.date.fromisoformat(v)
        except ValueError:
            return None
    return None


def _check_confirmation(name: str, conf: Any) -> list[str]:
    errs: list[str] = []
    p = f"{name}.confirmation"
    if not isinstance(conf, dict):
        return [f"{p}: falta o no es un objeto"]
    for f in COMMON_FIELDS:
        if _is_empty(conf.get(f)):
            errs.append(f"{p}.{f}: obligatorio y no vacío")
    kind = conf.get("kind")
    if not _is_empty(kind) and kind not in CONFIRMATION_KINDS:
        errs.append(f"{p}.kind: {kind!r} no es {' ni '.join(CONFIRMATION_KINDS)}")
    ref = conf.get("reference")
    if kind in REFERENCE_FIELDS:
        if not isinstance(ref, dict):
            errs.append(f"{p}.reference: obligatorio para {kind}")
        else:
            for f in REFERENCE_FIELDS[kind]:
                if _is_empty(ref.get(f)):
                    errs.append(f"{p}.reference.{f}: obligatorio y no vacío para {kind}")
            if kind == "responsible_person" and not _is_empty(ref.get("channel")) and ref.get("channel") not in CHANNELS:
                errs.append(f"{p}.reference.channel: debe ser {', '.join(CHANNELS)}")
    d, r = conf.get("date"), conf.get("recorded_at")
    dd, rr = _iso_date(d), _iso_date(r)
    if not _is_empty(d) and dd is None:
        errs.append(f"{p}.date: no es una fecha ISO (AAAA-MM-DD)")
    if not _is_empty(r) and rr is None:
        errs.append(f"{p}.recorded_at: no es una fecha ISO (AAAA-MM-DD)")
    if dd and rr and dd > rr:
        errs.append(f"{p}.date: posterior a recorded_at")
    scope = conf.get("scope")
    if not _is_empty(scope) and (not isinstance(scope, list) or name not in scope):
        errs.append(f"{p}.scope: no incluye {name}")
    return errs


def _check_empty_value(name: str, h: dict) -> list[str]:
    errs: list[str] = []
    entries = h.get("entries")
    if not isinstance(entries, dict):
        return [f"{name}.entries: falta"]
    for st in EMPTY_STATES:
        fam = entries.get(st)
        if not isinstance(fam, dict):
            errs.append(f"{name}.entries.{st}: falta")
            continue
        for op in OPERATOR_FAMILIES:
            v = fam.get(op)
            if v not in RESULTS:
                errs.append(f"{name}.entries.{st}.{op}: debe ser {', '.join(RESULTS)}")
    return errs


def parse_semantics(data: Any, sha256: str = "") -> Semantics:
    errs: list[str] = []
    warnings: list[str] = []
    if not isinstance(data, dict):
        raise SemanticsError(["el registro debe ser un objeto"])
    version = data.get("version")
    if _is_empty(version):
        errs.append("version: obligatoria")
    raw_entries = data.get("entries")
    if not isinstance(raw_entries, dict):
        raise SemanticsError(errs + ["entries: obligatorio"])
    for name in REQUIRED_ENTRIES:
        if name not in raw_entries:
            errs.append(f"{name}: entrada obligatoria ausente")
    entries: dict[str, Hypothesis] = {}
    for name, h in raw_entries.items():
        if not isinstance(h, dict):
            errs.append(f"{name}: debe ser un objeto")
            continue
        status = h.get("status")
        if status not in ("inferred", "confirmed"):
            errs.append(f"{name}.status: debe ser inferred o confirmed")
        if "meaning" not in h or _is_empty(h.get("meaning")):
            errs.append(f"{name}.meaning: obligatorio")
        if not isinstance(h.get("confirmation_required"), bool):
            errs.append(f"{name}.confirmation_required: debe ser booleano")
        ev = h.get("evidence", [])
        if not isinstance(ev, list):
            errs.append(f"{name}.evidence: debe ser una lista")
            ev = []
        if name == "empty_value_comparison":
            errs += _check_empty_value(name, h)
        conf = h.get("confirmation")
        if status == "confirmed":
            errs += _check_confirmation(name, conf)
            if name == "empty_value_comparison" and isinstance(h.get("entries"), dict):
                if any(
                    isinstance(fam, dict) and fam.get(op) == "unknown"
                    for fam in h["entries"].values()
                    for op in OPERATOR_FAMILIES
                ):
                    errs.append(f"{name}: no puede estar confirmed con result unknown")
        elif conf is not None:
            warnings.append(f"{name}: tiene confirmation pero status no es confirmed")
        extra = {k: v for k, v in h.items() if k not in ("meaning", "status", "confirmation_required", "evidence", "confirmation")}
        entries[name] = Hypothesis(
            name=name,
            meaning=h.get("meaning"),
            status=status if status in ("inferred", "confirmed") else "inferred",
            confirmation_required=bool(h.get("confirmation_required")),
            evidence=tuple(ev),
            confirmation=conf if isinstance(conf, dict) else None,
            extra=extra,
        )
    if errs:
        raise SemanticsError(errs)
    return Semantics(version=str(version), sha256=sha256, entries=entries, warnings=tuple(warnings))


def load_semantics(path: Optional[str | pathlib.Path] = None) -> Semantics:
    p = pathlib.Path(path) if path else DEFAULT_SEMANTICS
    raw = p.read_bytes()
    # yaml.safe_load con fechas: BaseLoader evitaría conversiones, pero
    # safe_load es suficiente y no construye objetos arbitrarios.
    data = yaml.safe_load(raw.decode("utf-8"))
    return parse_semantics(data, hashlib.sha256(raw).hexdigest())
