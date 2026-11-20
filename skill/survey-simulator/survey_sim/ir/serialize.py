"""Serialización JSON determinista de la IR (D11).

- Claves ordenadas alfabéticamente en todos los niveles.
- El orden de las listas se conserva: es semántico (orden de secciones,
  ítems, opciones, reglas).
- Sin espacios, UTF-8 sin escapar, salto de línea final.
- Se incluyen los valores por defecto y los ``null``, para que dos
  instrumentos iguales den siempre los mismos bytes.
- ``ir_version`` siempre está presente y se verifica al leer.

Compatibilidad (enmienda 2.0.0). Siempre se escribe 2.0.0. Se leen:

- 2.0.0: directamente.
- 1.1.0: directamente. Su esquema es idéntico al de 2.0.0 (solo cambió el
  número de versión), así que no hay migración; únicamente se actualiza el
  campo ``ir_version`` en una copia.
- 1.0.0: con ``migrate_1_0_0``. La única migración necesaria es
  ``Item.universe``: en 1.0.0 era una condición sin procedencia ni certeza
  propias; se envuelve en ``Universe`` con la procedencia y la certeza del
  ítem, que era lo que implicaba 1.0.0, y se deja una nota que lo indica.
  El resto de 1.0.0 es válido tal cual.

Ninguna lectura modifica el objeto de entrada.
"""

from __future__ import annotations

import hashlib
import json

from .models import IR_VERSION, READABLE_IR_VERSIONS, Instrument


class IRVersionError(ValueError):
    pass


def to_json_bytes(inst: Instrument) -> bytes:
    data = inst.model_dump(mode="json", exclude_none=False, exclude_defaults=False)
    data["ir_version"] = IR_VERSION
    text = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return (text + "\n").encode("utf-8")


def from_json_bytes(raw: bytes) -> Instrument:
    data = json.loads(raw.decode("utf-8"))
    version = data.get("ir_version") if isinstance(data, dict) else None
    if version not in READABLE_IR_VERSIONS:
        raise IRVersionError(f"ir_version {version!r} no soportada; se admiten {READABLE_IR_VERSIONS!r}")
    if version == "1.0.0":
        data = migrate_1_0_0(data)
    elif version == "1.1.0":
        data = _same_schema_as_current(data)
    return Instrument.model_validate(data)


def _same_schema_as_current(data: dict) -> dict:
    """1.1.0 → 2.0.0: mismo esquema; solo cambia ``ir_version`` (en una copia)."""
    out = dict(data)
    out["ir_version"] = IR_VERSION
    return out


MIGRATION_NOTE_1_0_0 = "migrated_from_ir_1.0.0:universe_hereda_procedencia_y_certeza_del_item"


def migrate_1_0_0(data: dict) -> dict:
    """Convierte un dict de IR 1.0.0 en uno de la versión actual sin perder información."""
    out = json.loads(json.dumps(data))  # copia profunda; no se toca la entrada
    out["ir_version"] = IR_VERSION
    for section in out.get("sections", []) or []:
        for item in section.get("items", []) or []:
            cond = item.get("universe")
            if cond is not None:
                item["universe"] = {
                    "condition": cond,
                    "provenance": item.get("provenance"),
                    "certainty": item.get("certainty"),
                    "notes": [MIGRATION_NOTE_1_0_0],
                }
    return out


def ir_hash(inst: Instrument) -> str:
    """SHA-256 de la serialización canónica."""
    return hashlib.sha256(to_json_bytes(inst)).hexdigest()
