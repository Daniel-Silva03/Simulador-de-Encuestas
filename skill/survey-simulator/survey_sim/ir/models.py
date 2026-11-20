"""Modelos de la representación intermedia (IR).

Especificación v1.0, sección 4 (entidades) y sección 8 (``Finding``).

Todos los elementos heredan ``provenance``, ``certainty``, ``notes`` y
``format_marks`` (D02, D07). Los modelos son inmutables y rechazan campos no
declarados.

Enmienda técnica de la IR, versión mayor 2.0.0 (ver
``docs/ir_enmienda_2.0.0.md``). Es mayor porque ``Item.universe`` cambia su
tipo público de ``Condition`` a ``Universe``:

1. ``Item.universe`` pasa a ser ``Universe`` (condición + procedencia + certeza).
2. ``OpaqueRule``: regla no interpretable, en una unión discriminada con ``Rule``.
3. ``FormatMarks``: marcas de formato estructuradas, sin significado semántico.
4. ``Instrument.unresolved``: elementos no resueltos dentro de la propia IR.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Discriminator, Field, StrictBool, StrictInt, StrictStr, Tag, field_validator, model_validator

from .conditions import Condition
from .values import Scalar

IR_VERSION = "2.0.0"

#: Versiones que ``from_json_bytes`` sabe leer. 1.0.0 se migra; 1.1.0 tiene
#: el mismo esquema que 2.0.0 y se lee directamente.
READABLE_IR_VERSIONS = ("1.0.0", "1.1.0", "2.0.0")

_SHA256 = r"^[0-9a-f]{64}$"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------
# Procedencia y certeza (D02)
# --------------------------------------------------------------------------


class Certainty(str, Enum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"
    OPAQUE = "opaque"


class Provenance(_Model):
    """De dónde salió un elemento: archivo y ubicación dentro de él."""

    source: StrictStr = Field(min_length=1, description="Nombre del insumo")
    locator: StrictStr = Field(
        min_length=1,
        description="Ruta JSON, o párrafo/tabla/fila del .docx",
    )


class FormatMarks(_Model):
    """Marcas de formato del documento fuente (D07).

    Se conservan como evidencia para la revisión humana. No tienen
    significado semántico: no cambian la certeza ni la interpretación de
    ningún elemento, y ningún módulo debe tomar decisiones a partir de ellas.
    """

    bold: Optional[Literal["all", "some", "none"]] = None
    highlights: tuple[StrictStr, ...] = ()
    shading: tuple[StrictStr, ...] = ()

    @field_validator("highlights", "shading")
    @classmethod
    def _canonical(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        # Orden canónico y sin repetidos: la serialización es determinista.
        return tuple(sorted(set(v)))


class IRElement(_Model):
    provenance: Provenance
    certainty: Certainty
    notes: tuple[StrictStr, ...] = ()
    format_marks: Optional[FormatMarks] = None


# --------------------------------------------------------------------------
# Enumeraciones de la sección 4
# --------------------------------------------------------------------------


class Role(str, Enum):
    DESIGN = "design"
    PROGRAM = "program"


class ItemType(str, Enum):
    SINGLE = "single"
    MULTIPLE = "multiple"
    NUMERIC = "numeric"
    TEXT = "text"
    DATE = "date"
    TIME = "time"
    SCALE = "scale"
    GRID = "grid"
    RANKING = "ranking"
    INFORMATIVE = "informative"
    MEDIA = "media"
    GEO = "geo"
    UNKNOWN = "unknown"


class ItemKind(str, Enum):
    SUBSTANTIVE = "substantive"
    SYSTEM = "system"
    PARADATA = "paradata"
    CALCULATED = "calculated"
    INFORMATIVE = "informative"


class InstructionKind(str, Enum):
    INTERVIEWER = "interviewer"
    PROGRAMMER = "programmer"
    DISPLAY = "display"


class RuleKind(str, Enum):
    FILTER = "filter"
    SKIP = "skip"
    VALIDATION = "validation"
    COMPUTATION = "computation"
    ENABLE = "enable"
    HIDE = "hide"
    TERMINATE = "terminate"
    RECODE = "recode"


class RuleAction(str, Enum):
    BLOCK = "block"
    WARN = "warn"


class TerminationKind(str, Enum):
    RECHAZO = "rechazo"
    NO_APLICA = "no_aplica"
    REVISITA = "revisita"
    COMPLETA = "completa"


class ExternalDepKind(str, Enum):
    LOOKUP_TABLE = "lookup_table"
    GPS = "gps"
    DEVICE = "device"
    RANDOM = "random"
    QUOTA = "quota"


class StorageType(str, Enum):
    SINGLE = "single"
    MULTIPLE = "multiple"
    NUMERIC = "numeric"
    TEXT = "text"
    DATE = "date"
    TIME = "time"
    UNKNOWN = "unknown"


# --------------------------------------------------------------------------
# Piezas
# --------------------------------------------------------------------------


class Text(_Model):
    """Texto crudo (tal como viene, con HTML si lo hay) y limpio."""

    raw: StrictStr
    clean: StrictStr


class Instruction(_Model):
    kind: InstructionKind
    text: StrictStr = Field(min_length=1)


class Constraints(_Model):
    min_value: Optional[Scalar] = None
    max_value: Optional[Scalar] = None
    max_length: Optional[StrictInt] = Field(default=None, ge=0)
    min_selections: Optional[StrictInt] = Field(default=None, ge=0)
    max_selections: Optional[StrictInt] = Field(default=None, ge=0)


class DisplayRotation(_Model):
    """Rotación de opciones. Su certeza la decide el adaptador (p. ej. ``ord`` es inferred)."""

    rotate: StrictBool
    certainty: Certainty


class OptionRouting(_Model):
    action: Literal["continue", "goto", "terminate", "unknown"]
    target: Optional[StrictStr] = None
    raw: Optional[StrictStr] = None


class Option(IRElement):
    code: StrictStr = Field(min_length=1)
    label: Text
    is_missing_code: StrictBool = False
    is_exclusive: StrictBool = False
    opens_text: StrictBool = False
    anchored: StrictBool = False
    display_if: Optional[Condition] = None
    routing: Optional[OptionRouting] = None


class GridRow(_Model):
    item_id: StrictStr = Field(min_length=1)
    label: Text


class Grid(IRElement):
    rows: tuple[GridRow, ...] = Field(min_length=1)
    shared_options: tuple[Option, ...] = ()
    orientation: Literal["rows", "columns", "unknown"] = "unknown"


class Universe(IRElement):
    """Universo de una pregunta: a quién aplica.

    Tiene procedencia y certeza propias, independientes de las del ítem:
    una pregunta puede leerse de forma explícita y su condición de
    aplicación, inferirse de texto libre en otro párrafo. No es una regla de
    habilitación (``Rule(kind=enable)``).
    """

    condition: Condition
    raw: Optional[StrictStr] = None


class Item(IRElement):
    id: StrictStr = Field(min_length=1)
    label: Optional[StrictStr] = None
    text: Text
    instructions: tuple[Instruction, ...] = ()
    type: ItemType
    options: tuple[Option, ...] = ()
    constraints: Optional[Constraints] = None
    universe: Optional[Universe] = None
    display_rotation: Optional[DisplayRotation] = None
    grid: Optional[Grid] = None
    kind: ItemKind


class Section(IRElement):
    id: StrictStr = Field(min_length=1)
    title: Optional[StrictStr] = None
    items: tuple[Item, ...] = ()
    pages: Optional[tuple[StrictStr, ...]] = None  # solo en el programa


class Variable(IRElement):
    name: StrictStr = Field(min_length=1)
    item_id: Optional[StrictStr] = None
    storage_type: StorageType
    length: Optional[StrictInt] = Field(default=None, ge=0)
    expands_to: tuple[StrictStr, ...] = ()


class Rule(IRElement):
    """Regla reconocida. Su condición puede contener nodos ``Opaque``."""

    form: Literal["recognized"] = "recognized"
    id: StrictStr = Field(min_length=1)
    kind: RuleKind
    when: Optional[Condition] = None
    target: Optional[StrictStr] = None
    action: Optional[RuleAction] = None
    message: Optional[StrictStr] = None
    raw: Optional[StrictStr] = None


class OpaqueRule(IRElement):
    """Regla de navegación o lógica que no pudo interpretarse.

    Conserva el texto original, la procedencia y el motivo. No tiene tipo,
    condición, destino ni acción: no se inventan. Su certeza es siempre
    ``opaque`` y nunca se ejecuta ni se evalúa como una regla reconocida.
    """

    form: Literal["opaque"] = "opaque"
    id: StrictStr = Field(min_length=1)
    raw: StrictStr = Field(min_length=1)
    reason: StrictStr = Field(min_length=1)

    @model_validator(mode="after")
    def _always_opaque(self) -> "OpaqueRule":
        if self.certainty is not Certainty.OPAQUE:
            raise ValueError("una OpaqueRule siempre tiene certeza opaque")
        return self


def _rule_form(v: object) -> str:
    # Las reglas de IR 1.0.0 no traen "form": son reglas reconocidas.
    if isinstance(v, dict):
        return v.get("form", "recognized")
    return getattr(v, "form", "recognized")


RuleEntry = Annotated[
    Union[Annotated[Rule, Tag("recognized")], Annotated[OpaqueRule, Tag("opaque")]],
    Discriminator(_rule_form),
]


class Termination(IRElement):
    code: StrictStr = Field(min_length=1)
    label: StrictStr
    kind: TerminationKind


class ExternalDep(IRElement):
    kind: ExternalDepKind
    name: StrictStr = Field(min_length=1)
    fields: tuple[StrictStr, ...] = ()


class Quota(IRElement):
    """Cuota como metadato. Su SQL o condición nunca se ejecuta."""

    id: StrictStr = Field(min_length=1)
    description: Optional[StrictStr] = None
    condition_raw: Optional[StrictStr] = None


# --------------------------------------------------------------------------
# Hallazgos (sección 8)
# --------------------------------------------------------------------------


class EvidenceLevel(str, Enum):
    ERROR_COMPROBADO = "error_comprobado"
    SOSPECHA = "sospecha"
    NO_VERIFICABLE = "no_verificable"


class FindingScope(str, Enum):
    DESIGN = "design"
    PROGRAM = "program"
    PAIR = "pair"


class FindingCategory(str, Enum):
    TEXTO = "texto"
    OPCIONES = "opciones"
    CODIGOS = "codigos"
    FILTRO = "filtro"
    SALTO = "salto"
    VALIDACION = "validacion"
    TIPO = "tipo"
    ITEM_FALTANTE_O_SOBRANTE = "item_faltante_o_sobrante"
    TERMINACION = "terminacion"
    ESTRUCTURA = "estructura"


class Severity(str, Enum):
    ALTA = "alta"
    MEDIA = "media"
    BAJA = "baja"


class FindingRef(_Model):
    """Elemento que sustenta un hallazgo, con su certeza.

    ``external`` marca un elemento que depende de algo externo (tabla, GPS…).
    ``missing`` marca un elemento que se esperaba y no existe.
    """

    side: Literal["design", "program"]
    element_id: StrictStr = Field(min_length=1)
    provenance: Optional[Provenance] = None
    certainty: Certainty
    external: StrictBool = False
    missing: StrictBool = False


class Finding(_Model):
    id: StrictStr = Field(pattern=_SHA256)
    check_id: StrictStr = Field(min_length=1, description="Regla de verificación que lo produjo")
    scope: FindingScope
    evidence_level: EvidenceLevel
    category: FindingCategory
    severity: Severity
    refs: tuple[FindingRef, ...] = Field(min_length=1)
    heuristic: StrictBool = False
    depends_on_unconfirmed: tuple[StrictStr, ...] = Field(
        default=(),
        description="Semánticas no confirmadas de las que depende (D15)",
    )
    explanation: StrictStr = Field(min_length=1)
    suggested_check: Optional[StrictStr] = None


def derive_evidence_level(
    refs: tuple[FindingRef, ...] | list[FindingRef],
    *,
    heuristic: bool = False,
    depends_on_unconfirmed: tuple[str, ...] | list[str] = (),
) -> EvidenceLevel:
    """Nivel de evidencia según la sección 8: lo decide la certeza de los elementos."""
    if any(r.certainty is Certainty.OPAQUE or r.external or r.missing for r in refs):
        return EvidenceLevel.NO_VERIFICABLE
    if heuristic or depends_on_unconfirmed or any(r.certainty is Certainty.INFERRED for r in refs):
        return EvidenceLevel.SOSPECHA
    return EvidenceLevel.ERROR_COMPROBADO


def finding_id(check_id: str, refs: tuple[FindingRef, ...] | list[FindingRef]) -> str:
    """Id estable: hash de la regla de verificación y de los elementos que toca."""
    payload = json.dumps(
        {"check": check_id, "refs": sorted([r.side, r.element_id] for r in refs)},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Instrumento
# --------------------------------------------------------------------------


class Source(_Model):
    name: StrictStr = Field(min_length=1)
    sha256: StrictStr = Field(pattern=_SHA256)
    format: StrictStr = Field(min_length=1)
    adapter: StrictStr = Field(min_length=1)
    adapter_version: StrictStr = Field(min_length=1)


class UnresolvedEntry(_Model):
    """Algo del insumo que no pudo resolverse, con su evidencia."""

    code: StrictStr = Field(min_length=1)
    text: StrictStr
    provenance: Provenance
    detail: StrictStr = ""


class Study(_Model):
    ids: tuple[StrictStr, ...] = ()
    title: Optional[StrictStr] = None
    date: Optional[StrictStr] = None


class Instrument(_Model):
    ir_version: Literal["2.0.0"] = IR_VERSION
    role: Role
    source: Source
    study: Study = Field(default_factory=Study)
    languages: tuple[StrictStr, ...] = ()
    sections: tuple[Section, ...] = ()
    variables: tuple[Variable, ...] = ()
    rules: tuple[RuleEntry, ...] = ()
    terminations: tuple[Termination, ...] = ()
    external_deps: tuple[ExternalDep, ...] = ()
    quotas: tuple[Quota, ...] = ()
    parse_findings: tuple[Finding, ...] = ()
    unresolved: tuple[UnresolvedEntry, ...] = ()
