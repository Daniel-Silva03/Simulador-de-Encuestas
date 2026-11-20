"""Estados de un valor (especificación v1.0, sección 4.1).

Cada variable tiene exactamente uno de cinco estados en cada momento de la
evaluación. El estado se guarda aparte del valor y nunca se codifica con
números mágicos.

Invariantes:

- ``Valid`` y ``MissingCode`` llevan un valor; los otros tres estados no.
- ``Valid`` admite un escalar o una lista no vacía de escalares (respuesta
  múltiple). Una lista vacía no es una respuesta: es ``Unanswered``.
- ``MissingCode`` lleva siempre un único código escalar (el código marcado
  como ``is_missing_code`` en la opción).
- ``UnknownToEvaluator`` registra por qué el valor no es conocible.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr, field_validator

Scalar = Union[StrictInt, StrictFloat, StrictStr]


class ValueStateKind(str, Enum):
    VALID = "VALID"
    MISSING_CODE = "MISSING_CODE"
    UNANSWERED = "UNANSWERED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN_TO_EVALUATOR = "UNKNOWN_TO_EVALUATOR"


class _State(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Valid(_State):
    """Respuesta válida: el ítem se presentó y se respondió con un valor sustantivo."""

    state: Literal["VALID"] = "VALID"
    value: Union[Scalar, tuple[Scalar, ...]]

    @field_validator("value")
    @classmethod
    def _non_empty_multi(cls, v):
        if isinstance(v, tuple) and len(v) == 0:
            raise ValueError("una respuesta múltiple VALID no puede estar vacía; use Unanswered")
        if isinstance(v, str) and v == "":
            raise ValueError("una cadena vacía no es una respuesta VALID; use Unanswered")
        return v

    @property
    def is_multi(self) -> bool:
        return isinstance(self.value, tuple)


class MissingCode(_State):
    """NS/NR: el ítem se presentó y se registró un código marcado como no-respuesta."""

    state: Literal["MISSING_CODE"] = "MISSING_CODE"
    value: Scalar

    @field_validator("value")
    @classmethod
    def _non_empty(cls, v):
        if isinstance(v, str) and v == "":
            raise ValueError("MISSING_CODE requiere un código no vacío")
        return v


class Unanswered(_State):
    """Sin responder: el ítem se presentó pero quedó vacío."""

    state: Literal["UNANSWERED"] = "UNANSWERED"


class NotApplicable(_State):
    """No aplicable: el ítem no se presentó porque un filtro, salto o terminación lo excluyó."""

    state: Literal["NOT_APPLICABLE"] = "NOT_APPLICABLE"


class UnknownToEvaluator(_State):
    """Existe o existirá un valor válido, pero el evaluador no puede conocerlo."""

    state: Literal["UNKNOWN_TO_EVALUATOR"] = "UNKNOWN_TO_EVALUATOR"
    reason: StrictStr = Field(min_length=1)


ValueState = Annotated[
    Union[Valid, MissingCode, Unanswered, NotApplicable, UnknownToEvaluator],
    Field(discriminator="state"),
]

#: Estados vacíos cuya comparación depende de una política declarada (4.3, 4.5).
EMPTY_STATES = (ValueStateKind.UNANSWERED, ValueStateKind.NOT_APPLICABLE)


def kind_of(state: object) -> ValueStateKind:
    """Devuelve el tipo de estado de una instancia de estado."""
    return ValueStateKind(state.state)  # type: ignore[attr-defined]
