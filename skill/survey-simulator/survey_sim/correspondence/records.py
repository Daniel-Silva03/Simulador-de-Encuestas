"""Registros humanos de la correspondencia y su vigencia.

- ``review_log.jsonl`` (v1.0, 7.1): declaraciones de identidad
  (``identity_declaration``) y verificaciones puntuales
  (``observation_verification``). Log de solo adición: nunca se edita ni se
  borra; un registro nuevo sustituye a otro con ``supersedes``.
- Archivo de pareos (v1.1, §3.4) y archivo de equivalencias (v1.1, §4).

Todos llevan un ``binding`` con los SHA-256 de los insumos y de la
configuración, la versión de las reglas y (V-3) las versiones de los lectores,
del parser y del módulo de correspondencia. Si cualquier valor cambia, el
registro queda ``stale``: se conserva para auditoría y deja de surtir efecto.
"""

from __future__ import annotations

import datetime as _dt
import json
import pathlib
from dataclasses import dataclass, field
from typing import Any, Optional

import yaml

DECLARATION = "identity_declaration"
VERIFICATION = "observation_verification"
PAIRS = "pareos"
EQUIVALENCES = "equivalencias"
RESULTS = ("confirmed", "contradicted", "inconclusive")


class RecordError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _flat(d: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(d, dict):
        for k in sorted(d):
            out.update(_flat(d[k], f"{prefix}{k}."))
    else:
        out[prefix[:-1]] = d
    return out


def binding_changes(record_binding: Any, current: dict) -> list[str]:
    """Claves de ``binding`` que no coinciden con la ejecución actual (vacío = vigente)."""
    cur = _flat(current)
    rec = _flat(record_binding) if isinstance(record_binding, dict) else {}
    return sorted(k for k in cur if rec.get(k) != cur[k])


def _iso(v: Any) -> bool:
    if isinstance(v, _dt.date) and not isinstance(v, _dt.datetime):
        return True
    if isinstance(v, str):
        try:
            _dt.date.fromisoformat(v)
            return True
        except ValueError:
            return False
    return False


def _empty(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, (list, dict)) and not v)


@dataclass
class Record:
    data: dict
    line: int
    errors: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    superseded_by: Optional[str] = None

    @property
    def id(self) -> str:
        return str(self.data.get("id", ""))

    @property
    def type(self) -> str:
        return str(self.data.get("record_type", ""))

    @property
    def valid(self) -> bool:
        return not self.errors

    @property
    def effective(self) -> bool:
        """Válido, vigente y no sustituido."""
        return self.valid and not self.stale and self.superseded_by is None

    def status(self) -> str:
        if self.errors:
            return "invalid"
        if self.superseded_by:
            return "superseded"
        return "stale" if self.stale else "vigente"

    def summary(self) -> dict:
        d = {"id": self.id, "record_type": self.type, "line": self.line, "status": self.status()}
        if self.errors:
            d["errors"] = self.errors
        if self.stale:
            d["binding_changed"] = self.stale
        if self.superseded_by:
            d["superseded_by"] = self.superseded_by
        return d


def validate_record(d: Any) -> list[str]:
    if not isinstance(d, dict):
        return ["el registro debe ser un objeto JSON"]
    rt = d.get("record_type")
    errs: list[str] = []
    common = ("id", "date", "binding")
    if rt == DECLARATION:
        req = common + ("verdict", "reason", "declared_by_role")
    elif rt == VERIFICATION:
        req = common + ("resolves", "signal", "document", "location", "result", "observed_value", "reviewer_role", "input_ref")
    else:
        return [f"record_type desconocido: {rt!r}"]
    for f in req:
        if _empty(d.get(f)):
            errs.append(f"{f}: obligatorio")
    if not _empty(d.get("date")) and not _iso(d.get("date")):
        errs.append("date: no es una fecha ISO (AAAA-MM-DD)")
    if not _empty(d.get("binding")) and not isinstance(d.get("binding"), dict):
        errs.append("binding: debe ser un objeto")
    if rt == DECLARATION:
        if d.get("verdict") not in ("MISMO", "DISTINTO"):
            errs.append("verdict: MISMO o DISTINTO")
        ack = d.get("acknowledges", [])
        if not isinstance(ack, list) or not all(isinstance(x, str) for x in ack):
            errs.append("acknowledges: lista de ids de bloqueo")
    else:
        if d.get("document") not in ("design", "program"):
            errs.append("document: design o program")
        if d.get("result") not in RESULTS:
            errs.append(f"result: {', '.join(RESULTS)}")
        if not isinstance(d.get("resolves"), list) or not all(isinstance(x, str) for x in d.get("resolves") or []):
            errs.append("resolves: lista de ids de bloqueo")
        ref = d.get("input_ref")
        if not isinstance(ref, dict) or _empty(ref.get("name")) or _empty(ref.get("sha256")):
            errs.append("input_ref: nombre y sha256 del insumo revisado")
    return errs


def load_review_log(path: Optional[str | pathlib.Path], current_binding: dict) -> list[Record]:
    """Lee el log (si existe) y marca cada registro: inválido, stale, sustituido o vigente."""
    if path is None:
        return []
    p = pathlib.Path(path)
    if not p.exists():
        return []
    out: list[Record] = []
    for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError as e:
            out.append(Record({}, n, [f"JSON inválido (columna {e.colno})"]))
            continue
        r = Record(d if isinstance(d, dict) else {}, n, validate_record(d))
        if r.valid:
            r.stale = binding_changes(d.get("binding"), current_binding)
        out.append(r)
    by_id = {r.id: r for r in out if r.valid}
    for r in out:
        sup = r.data.get("supersedes")
        if r.valid and isinstance(sup, str) and sup in by_id:
            by_id[sup].superseded_by = r.id
    return out


def append_record(path: str | pathlib.Path, record: dict) -> None:
    """Añade un registro al log. Nunca reescribe las líneas existentes."""
    errs = validate_record(record)
    if errs:
        raise RecordError(errs)
    p = pathlib.Path(path)
    if p.exists():
        ids = {json.loads(x).get("id") for x in p.read_text(encoding="utf-8").splitlines() if x.strip().startswith("{")}
        if record["id"] in ids:
            raise RecordError([f"id ya usado en el log: {record['id']}"])
    line = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


# --------------------------------------------------------------------------
# Pareos y equivalencias (YAML)
# --------------------------------------------------------------------------


@dataclass
class DeclaredPairs:
    sha256: str
    pairs: list[tuple[str, str, str]]  # (diseño, programa, motivo)
    stale: list[str]
    errors: list[str]
    proposed: int = 0

    @property
    def effective(self) -> bool:
        return not self.errors and not self.stale


@dataclass
class Equivalences:
    sha256: str
    # (lado, variable) -> {texto: literal}
    maps: dict[tuple[str, str], dict[str, Any]]
    stale: list[str]
    errors: list[str]

    @property
    def effective(self) -> bool:
        return not self.errors and not self.stale

    def for_var(self, side: str, var: str) -> dict[str, Any]:
        return self.maps.get((side, var), {}) if self.effective else {}


NO_EQUIVALENCES = Equivalences("", {}, [], [])


def _load_yaml(path: str | pathlib.Path) -> tuple[Any, str]:
    import hashlib

    raw = pathlib.Path(path).read_bytes()
    return yaml.safe_load(raw), hashlib.sha256(raw).hexdigest()


def load_pairs(path: Optional[str | pathlib.Path], current_binding: dict) -> Optional[DeclaredPairs]:
    if path is None:
        return None
    data, sha = _load_yaml(path)
    errs: list[str] = []
    if not isinstance(data, dict) or data.get("record_type") != PAIRS:
        return DeclaredPairs(sha, [], [], ["record_type: pareos"])
    pairs, proposed = [], 0
    for i, x in enumerate(data.get("pares") or []):
        if not isinstance(x, dict) or not isinstance(x.get("design"), str) or not isinstance(x.get("program"), str):
            errs.append(f"pares[{i}]: design y program obligatorios")
            continue
        if x.get("confirmado") is True:
            pairs.append((x["design"], x["program"], str(x.get("motivo") or "")))
        else:
            proposed += 1
    return DeclaredPairs(sha, pairs, binding_changes(data.get("binding"), current_binding), errs, proposed)


def load_equivalences(path: Optional[str | pathlib.Path], current_binding: dict) -> Equivalences:
    if path is None:
        return NO_EQUIVALENCES
    data, sha = _load_yaml(path)
    errs: list[str] = []
    maps: dict[tuple[str, str], dict[str, Any]] = {}
    if not isinstance(data, dict) or data.get("record_type") != EQUIVALENCES:
        return Equivalences(sha, {}, [], ["record_type: equivalencias"])
    for i, v in enumerate(data.get("variables") or []):
        if not isinstance(v, dict) or v.get("lado") not in ("design", "program") or not isinstance(v.get("variable"), str):
            errs.append(f"variables[{i}]: lado (design|program) y variable obligatorios")
            continue
        m: dict[str, Any] = {}
        for j, par in enumerate(v.get("pares") or []):
            t, lit = (par or {}).get("texto"), (par or {}).get("literal")
            # Sin coerción general: texto es una cadena y literal un número.
            if not isinstance(t, str) or not isinstance(lit, (int, float)) or isinstance(lit, bool):
                errs.append(f"variables[{i}].pares[{j}]: texto (cadena) y literal (número)")
                continue
            m[t] = lit
        if not m:
            errs.append(f"variables[{i}]: sin pares válidos")
        maps[(v["lado"], v["variable"])] = m
    return Equivalences(sha, maps, binding_changes(data.get("binding"), current_binding), errs)
