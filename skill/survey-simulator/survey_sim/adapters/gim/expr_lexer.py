"""Analizador léxico de expresiones GIM (paso 4).

Convierte el texto en tokens con su posición exacta. No interpreta nada,
no evalúa y nunca lanza excepciones hacia fuera del parser: un carácter no
reconocido produce ``LexError`` y el parser deja la expresión como
``Opaque``.

Tokens (ver ``docs/paso4_parser_expresiones.md``):

- ``NUM``: dígitos ASCII ``[0-9]`` (``77``). Se usa ``[0-9]`` y no la clase ``d`` con barra invertida,
  que en Python acepta cualquier dígito Unicode. Los dígitos no ASCII
  (arábigo-índicos, de ancho completo…) producen un error léxico y no se
  normalizan.
- ``RANGE``: ``NUM_NUM`` (``1_6``), solo válido dentro de un conjunto.
- ``DECIMAL``: ``1.5``. Se reconoce para poder rechazarlo con un
  diagnóstico claro: los decimales no están observados. ``1,5`` no se trata
  como decimal: la coma es ambigua (decimal o separador de lista) y se
  conserva como ``COMMA``.
- ``STRING``: entre comillas dobles, sin comillas ni barras internas.
- ``IDENT``: ``[A-Za-z_][A-Za-z0-9_]*``.
- ``KW``: ``Y``, ``O``, ``NO``, ``TRUE``, ``FALSE``, solo en mayúsculas y
  como palabra completa.
- ``OP``: ``<=`` ``>=`` ``<>`` ``=`` ``<`` ``>``.
- ``ARITH``: ``+`` ``-`` ``*`` ``/``.
- ``LPAREN``, ``RPAREN``, ``SEMI`` (``;``), ``COMMA`` (``,``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

KEYWORDS = ("Y", "O", "NO", "TRUE", "FALSE")


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    start: int
    end: int


class LexError(Exception):
    def __init__(self, code: str, pos: int):
        super().__init__(f"{code}@{pos}")
        self.code = code
        self.pos = pos


_SPEC = [
    ("WS", r"[ \t\r\n]+"),
    ("DECIMAL", r"[0-9]+\.[0-9]+"),
    ("RANGE", r"[0-9]+_[0-9]+"),
    ("NUM", r"[0-9]+"),
    ("STRING", r'"[^"\\\n]*"'),
    ("IDENT", r"[A-Za-z_][A-Za-z0-9_]*"),
    ("OP", r"<=|>=|<>|=|<|>"),
    ("ARITH", r"[+\-*/]"),
    ("LPAREN", r"\("),
    ("RPAREN", r"\)"),
    ("SEMI", r";"),
    ("COMMA", r","),
]
_MASTER = re.compile("|".join(f"(?P<{n}>{p})" for n, p in _SPEC))


def tokenize(text: str, max_tokens: int) -> list[Token]:
    out: list[Token] = []
    pos = 0
    n = len(text)
    while pos < n:
        m = _MASTER.match(text, pos)
        if m is None:
            ch = text[pos]
            if ch == '"':
                raise LexError("unsupported_or_unterminated_string", pos)
            raise LexError(f"unexpected_character:U+{ord(ch):04X}", pos)
        kind = m.lastgroup
        if kind != "WS":
            t = m.group()
            if kind == "IDENT" and t in KEYWORDS:
                kind = "KW"
            out.append(Token(kind, t, m.start(), m.end()))
            if len(out) > max_tokens:
                raise LexError("limit:max_tokens", m.start())
        pos = m.end()
    return out
