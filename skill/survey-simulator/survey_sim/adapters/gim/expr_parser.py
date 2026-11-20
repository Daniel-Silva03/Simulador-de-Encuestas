"""Parser de expresiones GIM (paso 4): texto → AST cerrado de la IR 2.0.0.

Principios:

- **Solo sintaxis respaldada.** La gramática reconocida es la parte de la
  sección 6 de la especificación que está observada en el corpus (ver
  ``docs/paso4_parser_expresiones.md``). Lo no observado, lo marcado «por
  confirmar» y lo que exigiría suponer una precedencia queda como
  ``Opaque`` con un motivo.
- **Nunca lanza.** Cualquier error léxico, sintáctico o de límites produce
  un ``Opaque`` con el texto original exacto. Un ``Opaque`` evalúa U.
- **Sin precedencias inventadas.** ``Y`` es conmutativo y asociativo en la
  lógica de Kleene, así que una cadena de ``Y`` se representa como un único
  ``And``. ``NO`` no tiene precedencia documentada frente a ``Y``: si su
  alcance cambia el resultado (``NO A Y B``), la cadena queda opaca. ``O``
  no está observado: siempre opaco.
- **Funciones siempre opacas.** El registro de semántica no tiene ninguna
  función autorizada. No se implementa ninguna función, externa o no.
- **Nada se ejecuta.** El parser solo construye objetos de datos.

El resultado no lleva certeza propia en la IR; ``ParseResult.certainty``
indica la que debe tener el elemento que lo use: ``inferred`` si se
reconoció alguna estructura (la gramática sale de la observación, no de la
documentación del proveedor) y ``opaque`` si la raíz es ``Opaque``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from survey_sim.ir.conditions import And, Compare, CompareOp, Const, InSet, Lit, Not, Opaque, Range, VarRef, iter_nodes
from survey_sim.ir.models import Certainty
from survey_sim.ir.validate import validate_condition

from .expr_lexer import LexError, Token, tokenize
from .reader import EXTERNAL_FUNCTION_PATTERNS

PARSER_VERSION = "0.1.0"


@dataclass(frozen=True)
class ParserLimits:
    max_chars: int = 10_000
    max_tokens: int = 2_000
    max_paren_depth: int = 32


@dataclass(frozen=True)
class Diagnostic:
    code: str
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class ParseResult:
    raw: str
    path: Optional[str]
    mode: str  # condition | value
    status: str  # parsed | partial | unsupported | invalid | empty
    condition: Optional[object] = None  # Condition de la IR (modo condition)
    operand: Optional[object] = None  # VarRef | Lit (modo value)
    diagnostics: tuple[Diagnostic, ...] = ()
    constructs: tuple[str, ...] = ()
    certainty: Optional[Certainty] = None

    @property
    def opaque_reasons(self) -> tuple[str, ...]:
        if self.condition is None:
            return ()
        return tuple(n.reason for n in iter_nodes(self.condition) if isinstance(n, Opaque))


class _Syntax(Exception):
    def __init__(self, code: str, pos: int):
        super().__init__(code)
        self.code = code
        self.pos = pos


@dataclass
class _V:
    """Operando o término sin convertir todavía."""

    kind: str  # var | num | str | true | false | decimal | func | arith | group
    start: int
    end: int
    value: object = None
    node: object = None  # para group
    parenthesized: bool = False


@dataclass
class _C:
    node: object
    start: int
    end: int
    no_bare: bool = False  # NO cuyo alcance no está delimitado por paréntesis
    parenthesized: bool = False


_RELOPS = {"=": CompareOp.EQ, "<>": CompareOp.NE, "<": CompareOp.LT, "<=": CompareOp.LE, ">": CompareOp.GT, ">=": CompareOp.GE}


def _is_external(name: str) -> bool:
    return any(p.match(name.upper()) for p in EXTERNAL_FUNCTION_PATTERNS)


@dataclass
class _Parser:
    raw: str
    toks: list[Token]
    limits: ParserLimits
    pos: int = 0
    depth: int = 0
    diags: list[Diagnostic] = field(default_factory=list)

    # Utilidades ------------------------------------------------------------

    def peek(self, k: int = 0) -> Optional[Token]:
        i = self.pos + k
        return self.toks[i] if i < len(self.toks) else None

    def take(self) -> Token:
        t = self.peek()
        if t is None:
            raise _Syntax("unexpected_end", len(self.raw))
        self.pos += 1
        return t

    def is_(self, kind: str, text: Optional[str] = None) -> bool:
        t = self.peek()
        return t is not None and t.kind == kind and (text is None or t.text == text)

    def opaque(self, start: int, end: int, reason: str) -> Opaque:
        text = self.raw[start:end]
        self.diags.append(Diagnostic(reason, start, end, text))
        return Opaque(raw=text, reason=reason)

    def skip_balanced(self) -> int:
        """Salta desde un ``(`` hasta su ``)`` y devuelve la posición final (en caracteres)."""
        level = 0
        while True:
            t = self.take()
            if t.kind == "LPAREN":
                level += 1
                if level + self.depth > self.limits.max_paren_depth:
                    raise _Syntax("limit:max_paren_depth", t.start)
            elif t.kind == "RPAREN":
                level -= 1
                if level == 0:
                    return t.end

    # Gramática ----------------------------------------------------------------

    def chain(self) -> _C:
        first = self.unary()
        items, ops = [first], []
        while self.is_("KW", "Y") or self.is_("KW", "O"):
            ops.append(self.take().text)
            items.append(self.unary())
        if not ops:
            return first
        start, end = items[0].start, items[-1].end
        if "O" in ops:
            reason = "operator_O_unconfirmed" if "Y" not in ops else "mixed_Y_O_precedence_undocumented"
            return _C(self.opaque(start, end, reason), start, end)
        if any(it.no_bare for it in items[:-1]):
            return _C(self.opaque(start, end, "ambiguous_NO_scope_before_Y"), start, end)
        return _C(And(args=tuple(it.node for it in items)), start, end)

    def unary(self) -> _C:
        if self.is_("KW", "NO"):
            t = self.take()
            inner = self.unary()
            bare = inner.no_bare or not inner.parenthesized
            return _C(Not(arg=inner.node), t.start, inner.end, no_bare=bare)
        return self.comparison()

    def comparison(self) -> _C:
        left = self.sum()
        if self.is_("OP"):
            op = self.take()
            if op.text == "=" and self.is_("LPAREN"):
                return self.set_comparison(left)
            right = self.sum()
            if self.is_("OP"):
                raise _Syntax("chained_comparison", self.peek().start)
            start, end = left.start, right.end
            lo, ro = self.operand(left), self.operand(right)
            if isinstance(lo, str) or isinstance(ro, str):
                return _C(self.opaque(start, end, lo if isinstance(lo, str) else ro), start, end)
            return _C(Compare(left=lo, op=_RELOPS[op.text], right=ro), start, end)
        # Sin operador relacional.
        if left.kind == "group":
            return _C(left.node, left.start, left.end, parenthesized=True)
        if left.kind == "true":
            return _C(Const(value=True), left.start, left.end)
        reasons = {
            "var": "bare_variable_condition",
            "func": f"function_not_authorized:{left.value}" + (":external" if left.kind == "func" and _is_external(str(left.value)) else ""),
            "false": "literal_FALSE_unobserved",
        }
        return _C(self.opaque(left.start, left.end, reasons.get(left.kind, "bare_value_condition")), left.start, left.end)

    def set_comparison(self, left: _V) -> _C:
        save = self.pos
        self.take()  # "("
        items: list = []
        fail = None
        t = self.take()
        if t.kind == "NUM":
            items.append(Lit(value=int(t.text)))
        elif t.kind == "RANGE":
            lo, hi = (int(x) for x in t.text.split("_"))
            if lo > hi:
                fail = "invalid_range_lo_greater_than_hi"
            else:
                items.append(Range(lo=lo, hi=hi))
        elif t.kind == "DECIMAL":
            fail = "decimal_literal_unobserved"
        else:
            fail = "set_item_not_integer"
        if fail is None:
            nxt = self.take()
            if nxt.kind == "COMMA":
                fail = "set_comma_unconfirmed_list_or_decimal"
            elif nxt.kind != "RPAREN":
                fail = "set_syntax_unsupported"
        if fail is not None:
            self.pos = save
            end = self.skip_balanced()
            return _C(self.opaque(left.start, end, fail), left.start, end)
        end = self.toks[self.pos - 1].end
        if left.kind != "var":
            return _C(self.opaque(left.start, end, "set_left_operand_not_variable"), left.start, end)
        return _C(InSet(var=str(left.value), items=tuple(items)), left.start, end)

    def operand(self, v: _V):
        """VarRef / Lit, o el motivo por el que el término no es un operando representable."""
        if v.kind == "var":
            return VarRef(name=str(v.value))
        if v.kind == "num":
            return Lit(value=v.value)
        if v.kind == "str":
            return Lit(value=v.value)
        return {
            "func": f"function_not_authorized:{v.value}" + (":external" if v.kind == "func" and _is_external(str(v.value)) else ""),
            "arith": "arithmetic_not_representable_in_ir",
            "decimal": "decimal_literal_unobserved",
            "true": "boolean_operand_unsupported",
            "false": "literal_FALSE_unobserved",
            "group": "parenthesized_operand_unsupported",
        }[v.kind]

    def sum(self) -> _V:
        first = self.term()
        if not self.is_("ARITH"):
            return first
        end = first.end
        while self.is_("ARITH"):
            self.take()
            end = self.term().end
        return _V("arith", first.start, end)

    def term(self) -> _V:
        t = self.take()
        if t.kind == "NUM":
            return _V("num", t.start, t.end, int(t.text))
        if t.kind == "DECIMAL":
            return _V("decimal", t.start, t.end)
        if t.kind == "STRING":
            return _V("str", t.start, t.end, t.text[1:-1])
        if t.kind == "KW" and t.text == "TRUE":
            return _V("true", t.start, t.end)
        if t.kind == "KW" and t.text == "FALSE":
            return _V("false", t.start, t.end)
        if t.kind == "IDENT":
            if self.is_("LPAREN"):
                end = self.skip_balanced()  # los argumentos no se analizan: la función es opaca
                return _V("func", t.start, end, t.text)
            return _V("var", t.start, t.end, t.text)
        if t.kind == "LPAREN":
            self.depth += 1
            if self.depth > self.limits.max_paren_depth:
                raise _Syntax("limit:max_paren_depth", t.start)
            inner = self.chain()
            close = self.take()
            if close.kind != "RPAREN":
                raise _Syntax("expected_closing_parenthesis", close.start)
            self.depth -= 1
            return _V("group", t.start, close.end, node=inner.node, parenthesized=True)
        if t.kind == "RANGE":
            raise _Syntax("range_outside_set", t.start)
        raise _Syntax(f"unexpected_token:{t.kind}:{t.text}", t.start)


def _whole_opaque(raw: str, reason: str, path: Optional[str], mode: str, status: str, pos: int = 0) -> ParseResult:
    d = Diagnostic(reason, pos, len(raw), raw)
    return ParseResult(raw=raw, path=path, mode=mode, status=status, condition=Opaque(raw=raw, reason=reason) if mode == "condition" else None,
                       diagnostics=(d,), certainty=Certainty.OPAQUE)


def parse_condition(raw: str, path: Optional[str] = None, limits: ParserLimits = ParserLimits()) -> ParseResult:
    """Convierte una condición GIM en una condición de la IR. Nunca lanza."""
    if not isinstance(raw, str):
        return ParseResult(raw=repr(type(raw).__name__), path=path, mode="condition", status="invalid",
                           diagnostics=(Diagnostic("not_a_string", 0, 0, ""),))
    if not raw.strip():
        return ParseResult(raw=raw, path=path, mode="condition", status="empty")
    if len(raw) > limits.max_chars:
        return _whole_opaque(raw, "limit:max_chars", path, "condition", "unsupported")
    try:
        toks = tokenize(raw, limits.max_tokens)
    except LexError as e:
        return _whole_opaque(raw, f"lexical_error:{e.code}@{e.pos}", path, "condition", "unsupported" if e.code.startswith("limit") else "invalid", e.pos)
    p = _Parser(raw, toks, limits)
    try:
        res = p.chain()
        if p.peek() is not None:
            raise _Syntax(f"trailing_tokens:{p.peek().text}", p.peek().start)
    except _Syntax as e:
        status = "unsupported" if e.code.startswith("limit") else "invalid"
        return _whole_opaque(raw, f"syntax_error:{e.code}@{e.pos}", path, "condition", status, e.pos)
    except RecursionError:  # defensa adicional; la profundidad está limitada
        return _whole_opaque(raw, "limit:recursion", path, "condition", "unsupported")

    node = res.node
    if isinstance(node, Opaque):
        # La raíz es opaca: se conserva el texto completo, espacios incluidos.
        reason = node.reason
        return ParseResult(raw=raw, path=path, mode="condition", status="unsupported", condition=Opaque(raw=raw, reason=reason),
                           diagnostics=tuple(p.diags), certainty=Certainty.OPAQUE)
    if [i for i in validate_condition(node) if i.level == "error"]:
        return _whole_opaque(raw, "limit:ir_condition_size", path, "condition", "unsupported")
    has_opaque = any(isinstance(n, Opaque) for n in iter_nodes(node))
    return ParseResult(raw=raw, path=path, mode="condition", status="partial" if has_opaque else "parsed", condition=node,
                       diagnostics=tuple(p.diags), constructs=constructs_of(node), certainty=Certainty.INFERRED)


def constructs_of(node) -> tuple[str, ...]:
    """Construcciones presentes en el AST final (no las absorbidas por un Opaque)."""
    out: set[str] = set()
    for n in iter_nodes(node):
        if isinstance(n, And):
            out.add("Y")
        elif isinstance(n, Not):
            out.add("NO")
        elif isinstance(n, Compare):
            out.add(f"CMP{n.op.value}")
            for side in (n.left, n.right):
                out.add("VAR" if isinstance(side, VarRef) else ("STRING" if isinstance(side.value, str) else "NUM"))
        elif isinstance(n, InSet):
            out.update({"SET", "VAR"})
            out.update("RANGE" if isinstance(i, Range) else "NUM" for i in n.items)
        elif isinstance(n, Const):
            out.add("TRUE")
    return tuple(sorted(out))


def parse_value(raw: str, path: Optional[str] = None, limits: ParserLimits = ParserLimits()) -> ParseResult:
    """Expresión de valor (por ejemplo ``valdefs[].xpr``): solo un literal o una variable.

    Cualquier otra cosa (aritmética, funciones, decimales) queda sin operando
    y con su diagnóstico: la IR no tiene nodos aritméticos y las funciones no
    están autorizadas.
    """
    if not isinstance(raw, str) or not raw.strip():
        return ParseResult(raw=raw if isinstance(raw, str) else "", path=path, mode="value", status="empty")
    if len(raw) > limits.max_chars:
        return _whole_opaque(raw, "limit:max_chars", path, "value", "unsupported")
    try:
        toks = tokenize(raw, limits.max_tokens)
        p = _Parser(raw, toks, limits)
        v = p.sum()
        if p.peek() is not None:
            raise _Syntax(f"trailing_tokens:{p.peek().text}", p.peek().start)
    except LexError as e:
        return _whole_opaque(raw, f"lexical_error:{e.code}@{e.pos}", path, "value", "invalid", e.pos)
    except _Syntax as e:
        return _whole_opaque(raw, f"syntax_error:{e.code}@{e.pos}", path, "value", "invalid", e.pos)
    except RecursionError:
        return _whole_opaque(raw, "limit:recursion", path, "value", "unsupported")
    op = p.operand(v) if v.kind != "group" else "parenthesized_operand_unsupported"
    if isinstance(op, str):
        d = Diagnostic(op, 0, len(raw), raw)
        return ParseResult(raw=raw, path=path, mode="value", status="unsupported", diagnostics=(d,), certainty=Certainty.OPAQUE)
    kind = "VAR" if isinstance(op, VarRef) else ("STRING" if isinstance(op.value, str) else "NUM")
    return ParseResult(raw=raw, path=path, mode="value", status="parsed", operand=op, constructs=(kind,), certainty=Certainty.INFERRED)

