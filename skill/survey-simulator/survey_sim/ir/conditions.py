"""AST cerrado de condiciones y evaluador puro de tres valores.

Especificación v1.0, secciones 4 (AST), 4.1 (estados), 4.2 (Kleene),
4.3 (comparaciones según el estado) y 4.4 (efecto de U).

Principios:

- La IR nunca contiene código ejecutable (D10). Los nodos son datos.
- El evaluador es una función pura: no tiene efectos, no lee archivos y no
  ejecuta expresiones, bytecode, SQL ni código de los documentos.
- Lógica fuerte de Kleene: V, F y U. Un ``Opaque`` y una función externa
  valen siempre U. Un U nunca se resuelve: se devuelve con sus motivos.
- Comparaciones con ``UNANSWERED`` o ``NOT_APPLICABLE`` dan U salvo entrada
  declarada en una política (4.5). En este paso no se incluye ninguna
  política GIM: la política por defecto no tiene entradas, así que da U.
- La semántica de las funciones (``CARACTERES``, ``MENCIONES``, ``REGEXP``…)
  no está confirmada para ninguna plataforma. Este módulo no la implementa:
  una función solo se evalúa si se registra explícitamente en un
  ``FunctionRegistry``. Sin registro, el resultado es U.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Annotated, Callable, Literal, Mapping, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator

from .values import (
    MissingCode,
    NotApplicable,
    Scalar,
    Unanswered,
    UnknownToEvaluator,
    Valid,
    ValueStateKind,
)

# --------------------------------------------------------------------------
# AST
# --------------------------------------------------------------------------


class _Node(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CompareOp(str, Enum):
    EQ = "="
    NE = "<>"
    LT = "<"
    LE = "<="
    GT = ">"
    GE = ">="


# Operandos -----------------------------------------------------------------


class VarRef(_Node):
    kind: Literal["var"] = "var"
    name: StrictStr = Field(min_length=1)


class Lit(_Node):
    kind: Literal["lit"] = "lit"
    value: Scalar


class FuncValue(_Node):
    """Llamada a función que devuelve un valor (por ejemplo, ``CARACTERES(X)``)."""

    kind: Literal["func"] = "func"
    name: StrictStr = Field(min_length=1)
    args: tuple["Operand", ...] = ()
    external: StrictBool = False


Operand = Annotated[Union[VarRef, Lit, FuncValue], Field(discriminator="kind")]


class Range(_Node):
    """Rango cerrado de enteros: ``(1_3)`` equivale a ``Range(lo=1, hi=3)``."""

    kind: Literal["range"] = "range"
    lo: StrictInt
    hi: StrictInt

    @model_validator(mode="after")
    def _ordered(self):
        if self.lo > self.hi:
            raise ValueError(f"rango inválido: lo={self.lo} > hi={self.hi}")
        return self


SetItem = Annotated[Union[Lit, Range], Field(discriminator="kind")]


# Condiciones ---------------------------------------------------------------


class And(_Node):
    node: Literal["and"] = "and"
    args: tuple["Condition", ...] = Field(min_length=2)


class Or(_Node):
    node: Literal["or"] = "or"
    args: tuple["Condition", ...] = Field(min_length=2)


class Not(_Node):
    node: Literal["not"] = "not"
    arg: "Condition"


class Compare(_Node):
    node: Literal["compare"] = "compare"
    left: Operand
    op: CompareOp
    right: Operand


class InSet(_Node):
    node: Literal["in_set"] = "in_set"
    var: StrictStr = Field(min_length=1)
    items: tuple[SetItem, ...] = Field(min_length=1)


class IsAnswered(_Node):
    node: Literal["is_answered"] = "is_answered"
    var: StrictStr = Field(min_length=1)


class IsSubstantive(_Node):
    node: Literal["is_substantive"] = "is_substantive"
    var: StrictStr = Field(min_length=1)


class Func(_Node):
    """Función predicado (por ejemplo, ``REGEXP(SECTOR;"^[0-9]*$")``)."""

    node: Literal["func"] = "func"
    name: StrictStr = Field(min_length=1)
    args: tuple[Operand, ...] = ()
    external: StrictBool = False


class Const(_Node):
    node: Literal["const"] = "const"
    value: StrictBool


class Opaque(_Node):
    """Expresión no reconocida. Conserva el texto original; siempre vale U."""

    node: Literal["opaque"] = "opaque"
    raw: StrictStr = Field(min_length=1)
    reason: StrictStr = Field(min_length=1)


Condition = Annotated[
    Union[And, Or, Not, Compare, InSet, IsAnswered, IsSubstantive, Func, Const, Opaque],
    Field(discriminator="node"),
]

for _m in (FuncValue, And, Or, Not):
    _m.model_rebuild()


class ConditionRoot(_Node):
    """Envoltorio para validar una condición suelta desde JSON."""

    condition: Condition


# --------------------------------------------------------------------------
# Política de valores vacíos (4.5): solo la estructura, sin entradas GIM
# --------------------------------------------------------------------------


class PolicyEntry(_Node):
    """Entrada de política para un estado vacío y una familia de operadores.

    En el paso 1 solo se admite ``status: inferred``. La confirmación de una
    semántica requiere la estructura ``confirmation`` de la sección 6, que se
    valida en el registro de semántica (paso 3). Así no se puede declarar una
    entrada confirmada sin esa validación.
    """

    result: Literal["true", "false", "unknown"] = "unknown"
    status: Literal["inferred"] = "inferred"
    confirmation_required: Literal[True] = True


class StatePolicy(_Node):
    eq: Optional[PolicyEntry] = None
    ne: Optional[PolicyEntry] = None
    order: Optional[PolicyEntry] = None
    functions: Mapping[str, PolicyEntry] = Field(default_factory=dict)


class EmptyValuePolicy(_Node):
    """Política por estado vacío. Por defecto no tiene entradas: todo da U."""

    UNANSWERED: StatePolicy = Field(default_factory=StatePolicy)
    NOT_APPLICABLE: StatePolicy = Field(default_factory=StatePolicy)


NO_POLICY = EmptyValuePolicy()


# --------------------------------------------------------------------------
# Registro de funciones (vacío por defecto)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FunctionSpec:
    """Implementación pura de una función, registrada por código del skill.

    ``kind='value'`` devuelve un escalar; ``kind='predicate'`` devuelve bool.
    Las implementaciones nunca provienen de los documentos analizados.
    """

    kind: Literal["value", "predicate"]
    impl: Callable[..., object]


FunctionRegistry = Mapping[str, FunctionSpec]
NO_FUNCTIONS: FunctionRegistry = {}


# --------------------------------------------------------------------------
# Evaluador
# --------------------------------------------------------------------------


class Truth(str, Enum):
    TRUE = "V"
    FALSE = "F"
    UNKNOWN = "U"


@dataclass(frozen=True)
class Evaluation:
    """Resultado de evaluar una condición.

    ``reasons`` explica cada U (vacío si el resultado es V o F).
    ``inferred_policy_used`` indica que el resultado usó una entrada de
    política no confirmada; la regla que lo use hereda ``certainty=inferred``
    (D15).
    """

    truth: Truth
    reasons: tuple[str, ...] = ()
    inferred_policy_used: bool = False


# Resultados intermedios de operandos ---------------------------------------


@dataclass(frozen=True)
class _Concrete:
    value: object  # Scalar o tuple de Scalar


@dataclass(frozen=True)
class _Empty:
    state: ValueStateKind  # UNANSWERED o NOT_APPLICABLE
    via_function: Optional[str] = None


@dataclass(frozen=True)
class _Unknown:
    reason: str


_OperandResult = Union[_Concrete, _Empty, _Unknown]


def _is_number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _u(*reasons: str, inferred: bool = False) -> Evaluation:
    return Evaluation(Truth.UNKNOWN, tuple(reasons), inferred)


def _tv(b: bool, inferred: bool = False) -> Evaluation:
    return Evaluation(Truth.TRUE if b else Truth.FALSE, (), inferred)


@dataclass(frozen=True)
class _Ctx:
    env: Mapping[str, object]
    policy: EmptyValuePolicy
    functions: FunctionRegistry


def evaluate(
    condition: Condition,
    env: Mapping[str, object],
    *,
    policy: EmptyValuePolicy = NO_POLICY,
    functions: FunctionRegistry = NO_FUNCTIONS,
) -> Evaluation:
    """Evalúa una condición sobre un entorno ``nombre -> estado de valor``.

    Función pura: no modifica sus argumentos ni tiene efectos.
    """
    return _eval(condition, _Ctx(env, policy, functions))


def _eval(c: object, ctx: _Ctx) -> Evaluation:
    if isinstance(c, Const):
        return _tv(c.value)
    if isinstance(c, Opaque):
        return _u(f"opaque:{c.reason}")
    if isinstance(c, Not):
        r = _eval(c.arg, ctx)
        if r.truth is Truth.UNKNOWN:
            return r
        return Evaluation(Truth.FALSE if r.truth is Truth.TRUE else Truth.TRUE, (), r.inferred_policy_used)
    if isinstance(c, And):
        return _kleene(c.args, ctx, dominant=Truth.FALSE, neutral=Truth.TRUE)
    if isinstance(c, Or):
        return _kleene(c.args, ctx, dominant=Truth.TRUE, neutral=Truth.FALSE)
    if isinstance(c, IsAnswered):
        return _is_answered(c.var, ctx)
    if isinstance(c, IsSubstantive):
        return _is_substantive(c.var, ctx)
    if isinstance(c, Compare):
        return _compare(c, ctx)
    if isinstance(c, InSet):
        return _in_set(c, ctx)
    if isinstance(c, Func):
        return _predicate(c, ctx)
    raise TypeError(f"nodo de condición no soportado: {type(c).__name__}")


def _kleene(args, ctx: _Ctx, *, dominant: Truth, neutral: Truth) -> Evaluation:
    """And (dominante F) y Or (dominante V), n-arios, sin cortocircuito.

    Se evalúan todos los operandos para propagar de forma conservadora el
    uso de políticas no confirmadas (D15).
    """
    results = [_eval(a, ctx) for a in args]
    inferred = any(r.inferred_policy_used for r in results)
    if any(r.truth is dominant for r in results):
        return Evaluation(dominant, (), inferred)
    if all(r.truth is neutral for r in results):
        return Evaluation(neutral, (), inferred)
    reasons: list[str] = []
    for r in results:
        if r.truth is Truth.UNKNOWN:
            reasons.extend(r.reasons)
    return Evaluation(Truth.UNKNOWN, tuple(dict.fromkeys(reasons)), inferred)


def _lookup(name: str, ctx: _Ctx) -> _OperandResult:
    if name not in ctx.env:
        return _Unknown(f"variable_not_in_environment:{name}")
    s = ctx.env[name]
    if isinstance(s, (Valid, MissingCode)):
        return _Concrete(s.value)
    if isinstance(s, Unanswered):
        return _Empty(ValueStateKind.UNANSWERED)
    if isinstance(s, NotApplicable):
        return _Empty(ValueStateKind.NOT_APPLICABLE)
    if isinstance(s, UnknownToEvaluator):
        return _Unknown(f"unknown_to_evaluator:{name}:{s.reason}")
    raise TypeError(f"estado de valor no soportado para {name}: {type(s).__name__}")


def _operand(o: object, ctx: _Ctx) -> _OperandResult:
    if isinstance(o, Lit):
        return _Concrete(o.value)
    if isinstance(o, VarRef):
        return _lookup(o.name, ctx)
    if isinstance(o, FuncValue):
        return _func_value(o, ctx)
    raise TypeError(f"operando no soportado: {type(o).__name__}")


def _func_args(name: str, external: bool, args, ctx: _Ctx):
    """Evalúa argumentos de función. Devuelve (valores, resultado_no_concreto)."""
    if external:
        return None, _Unknown(f"external_function:{name}")
    vals = [_operand(a, ctx) for a in args]
    for v in vals:
        if isinstance(v, _Empty):
            return None, _Empty(v.state, via_function=name)
    for v in vals:
        if isinstance(v, _Unknown):
            return None, v
    spec = ctx.functions.get(name)
    if spec is None:
        return None, _Unknown(f"function_not_registered:{name}")
    return (spec, tuple(v.value for v in vals)), None  # type: ignore[union-attr]


def _func_value(f: FuncValue, ctx: _Ctx) -> _OperandResult:
    ok, other = _func_args(f.name, f.external, f.args, ctx)
    if other is not None:
        return other
    spec, values = ok
    if spec.kind != "value":
        return _Unknown(f"function_kind_mismatch:{f.name}")
    try:
        out = spec.impl(*values)
    except Exception:  # una implementación registrada no debe romper la evaluación
        return _Unknown(f"function_error:{f.name}")
    if not (_is_number(out) or isinstance(out, str)):
        return _Unknown(f"function_non_scalar_result:{f.name}")
    return _Concrete(out)


def _predicate(f: Func, ctx: _Ctx) -> Evaluation:
    ok, other = _func_args(f.name, f.external, f.args, ctx)
    if isinstance(other, _Unknown):
        return _u(other.reason)
    if isinstance(other, _Empty):
        return _policy(other.state, ("functions", f.name), ctx)
    spec, values = ok
    if spec.kind != "predicate":
        return _u(f"function_kind_mismatch:{f.name}")
    try:
        out = spec.impl(*values)
    except Exception:  # una implementación registrada no debe romper la evaluación
        return _u(f"function_error:{f.name}")
    if not isinstance(out, bool):
        return _u(f"function_non_boolean_result:{f.name}")
    return _tv(out)


def _family(op: CompareOp) -> str:
    if op is CompareOp.EQ:
        return "eq"
    if op is CompareOp.NE:
        return "ne"
    return "order"


def _policy(state: ValueStateKind, family, ctx: _Ctx) -> Evaluation:
    """Aplica la política declarada para un estado vacío. Sin entrada: U."""
    sp: StatePolicy = getattr(ctx.policy, state.value)
    if isinstance(family, tuple):  # ("functions", nombre)
        entry = sp.functions.get(family[1])
        label = f"functions:{family[1]}"
    else:
        entry = getattr(sp, family)
        label = family
    if entry is None:
        return _u(f"empty_value_no_policy:{state.value}:{label}")
    if entry.result == "unknown":
        return _u(f"empty_value_policy_unknown:{state.value}:{label}")
    # status solo puede ser "inferred" en este paso: el resultado hereda inferred.
    return _tv(entry.result == "true", inferred=True)


def _scalar_eq(a: object, b: object) -> Optional[bool]:
    """Igualdad entre escalares del mismo tipo. None si los tipos no son comparables."""
    if _is_number(a) and _is_number(b):
        return a == b
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    return None


def _compare(c: Compare, ctx: _Ctx) -> Evaluation:
    left = _operand(c.left, ctx)
    right = _operand(c.right, ctx)
    for side in (left, right):
        if isinstance(side, _Unknown):
            return _u(side.reason)
    if isinstance(left, _Empty) and isinstance(right, _Empty):
        return _u("two_empty_operands")
    empty = left if isinstance(left, _Empty) else right if isinstance(right, _Empty) else None
    if empty is not None:
        family = ("functions", empty.via_function) if empty.via_function else _family(c.op)
        return _policy(empty.state, family, ctx)
    a, b = left.value, right.value  # type: ignore[union-attr]
    if isinstance(a, tuple) or isinstance(b, tuple):
        return _u("multi_value_comparison_undefined")
    if c.op in (CompareOp.EQ, CompareOp.NE):
        eq = _scalar_eq(a, b)
        if eq is None:
            return _u("type_mismatch")
        return _tv(eq if c.op is CompareOp.EQ else not eq)
    if not (_is_number(a) and _is_number(b)):
        return _u("order_requires_numeric")
    return _tv(
        {
            CompareOp.LT: a < b,
            CompareOp.LE: a <= b,
            CompareOp.GT: a > b,
            CompareOp.GE: a >= b,
        }[c.op]
    )


def _in_set(c: InSet, ctx: _Ctx) -> Evaluation:
    v = _lookup(c.var, ctx)
    if isinstance(v, _Unknown):
        return _u(v.reason)
    if isinstance(v, _Empty):
        return _policy(v.state, "eq", ctx)
    value = v.value
    if isinstance(value, tuple):
        return _u("multi_value_comparison_undefined")
    results: list[Evaluation] = []
    for item in c.items:
        if isinstance(item, Range):
            if not _is_number(value):
                results.append(_u("type_mismatch"))
            else:
                results.append(_tv(item.lo <= value <= item.hi))
        else:
            eq = _scalar_eq(value, item.value)
            results.append(_u("type_mismatch") if eq is None else _tv(eq))
    if any(r.truth is Truth.TRUE for r in results):
        return _tv(True)
    if all(r.truth is Truth.FALSE for r in results):
        return _tv(False)
    return _u(*dict.fromkeys(x for r in results for x in r.reasons))


def _is_answered(var: str, ctx: _Ctx) -> Evaluation:
    if var not in ctx.env:
        return _u(f"variable_not_in_environment:{var}")
    s = ctx.env[var]
    if isinstance(s, (Valid, MissingCode)):
        return _tv(True)
    if isinstance(s, (Unanswered, NotApplicable)):
        return _tv(False)
    return _u(f"unknown_to_evaluator:{var}:{s.reason}")  # type: ignore[union-attr]


def _is_substantive(var: str, ctx: _Ctx) -> Evaluation:
    if var not in ctx.env:
        return _u(f"variable_not_in_environment:{var}")
    s = ctx.env[var]
    if isinstance(s, Valid):
        return _tv(True)
    if isinstance(s, (MissingCode, Unanswered, NotApplicable)):
        return _tv(False)
    return _u(f"unknown_to_evaluator:{var}:{s.reason}")  # type: ignore[union-attr]


# --------------------------------------------------------------------------
# Utilidades de recorrido (usadas por validate.py)
# --------------------------------------------------------------------------


def iter_nodes(c: object):
    """Recorre en preorden condiciones y operandos de un AST."""
    yield c
    if isinstance(c, (And, Or)):
        for a in c.args:
            yield from iter_nodes(a)
    elif isinstance(c, Not):
        yield from iter_nodes(c.arg)
    elif isinstance(c, Compare):
        yield from iter_nodes(c.left)
        yield from iter_nodes(c.right)
    elif isinstance(c, (Func, FuncValue)):
        for a in c.args:
            yield from iter_nodes(a)
    elif isinstance(c, InSet):
        yield from c.items


def depth(c: object) -> int:
    if isinstance(c, (And, Or)):
        return 1 + max(depth(a) for a in c.args)
    if isinstance(c, Not):
        return 1 + depth(c.arg)
    if isinstance(c, Compare):
        return 1 + max(depth(c.left), depth(c.right))
    if isinstance(c, (Func, FuncValue)):
        return 1 + (max((depth(a) for a in c.args), default=0))
    return 1


def referenced_vars(c: object) -> set[str]:
    out: set[str] = set()
    for n in iter_nodes(c):
        if isinstance(n, VarRef):
            out.add(n.name)
        elif isinstance(n, (InSet, IsAnswered, IsSubstantive)):
            out.add(n.var)
    return out
