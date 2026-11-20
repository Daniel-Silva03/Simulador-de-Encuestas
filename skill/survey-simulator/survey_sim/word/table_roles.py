"""Clasificación de tablas por rol (sección 5, paso 2).

Cada tabla se clasifica por su forma (filas, columnas, celdas vacías), por
si la primera columna son códigos, por si tiene fila de encabezado y por el
párrafo que la precede. Las firmas siguen la tabla de la sección 5.

- Si una sola firma coincide, el rol es ``explicit`` (salvo ``grid``, que
  es una heurística y queda ``inferred``).
- Si coinciden varias, se elige la primera por prioridad, la certeza es
  ``inferred`` y se registran las alternativas en ``ambiguous_with``.
- Si ninguna coincide, el rol es ``other`` y la tabla se conserva entera.

El formato (resaltado, sombreado) no interviene en la clasificación.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from survey_sim.ir.models import Certainty

from .blocks import TableBlock
from .lexicon import Lexicon

Role = Literal["options", "open_box", "section_header", "recode_matrix", "version_table", "grid", "other"]

_PRIORITY: tuple[Role, ...] = ("version_table", "section_header", "open_box", "recode_matrix", "options", "grid")


@dataclass(frozen=True)
class RoleResult:
    role: Role
    certainty: Certainty
    reasons: tuple[str, ...]
    ambiguous_with: tuple[Role, ...] = ()


def _texts(t: TableBlock) -> list[list[str]]:
    grid = t.logical()
    out = []
    for r, line in enumerate(grid):
        row = []
        for c in range(t.n_cols):
            row.append(t.cell_text(r, c) if line[c] is not None else "")
        out.append(row)
    return out


def _is_upper_heading(s: str) -> bool:
    letters = [ch for ch in s if ch.isalpha()]
    return bool(letters) and all(ch.isupper() for ch in letters) and "?" not in s


def classify(t: TableBlock, lex: Lexicon, *, after_question: bool) -> RoleResult:
    texts = _texts(t)
    n_rows, n_cols = t.n_rows, t.n_cols
    cells = [x for row in texts for x in row]
    is_code = lambda s: bool(lex.code_cell.match(s))  # noqa: E731
    matches: dict[Role, str] = {}

    if n_rows and n_cols and all(not x for x in cells):
        matches["open_box"] = f"{n_rows}x{n_cols} sin texto"

    if n_rows == 1 and n_cols == 1 and cells[0] and len(cells[0]) <= lex.spec.table_roles.section_header_max_chars and _is_upper_heading(cells[0]):
        matches["section_header"] = "1x1 con texto corto en mayúsculas"

    nonempty = [x for x in cells if x]
    if n_rows == 1 and nonempty and len(nonempty) == len(cells) and all(lex.version_cell.match(x) for x in nonempty):
        matches["version_table"] = "una fila de rótulos de versión"

    if n_rows >= 2 and n_cols >= 2:
        header = texts[0]
        body = texts[1:]
        if sum(1 for h in header if lex.recode_header.search(h)) >= 2 and all(is_code(r[0]) and is_code(r[1]) for r in body):
            matches["recode_matrix"] = "encabezado con códigos de origen y destino"

    if n_cols in (2, 3) and n_rows >= 1:
        first_col_codes = all(is_code(r[0]) for r in texts)
        labels_ok = all(r[1] for r in texts)
        continuation_codes = any(
            (t.logical()[ri][0] is not None and t.logical()[ri][0].is_continuation) for ri in range(n_rows)
        )
        if first_col_codes and labels_ok and not continuation_codes:
            matches["options"] = f"{n_cols} columnas, primera con códigos"

    if n_rows >= 2 and n_cols >= 3:
        header = texts[0][1:]
        body = texts[1:]
        body_labels = all(r[0] and not is_code(r[0]) for r in body)
        header_ok = all(header)
        inner = [x for r in body for x in r[1:]]
        mostly_empty = inner and sum(1 for x in inner if not x) / len(inner) >= 0.5
        if body_labels and header_ok and mostly_empty and "recode_matrix" not in matches:
            matches["grid"] = "filas de ítems × columnas de escala"

    if not matches:
        return RoleResult("other", Certainty.INFERRED, ("ninguna firma coincide",))

    ordered = [r for r in _PRIORITY if r in matches]
    role = ordered[0]
    reasons = [matches[role]]
    certainty = Certainty.EXPLICIT
    if role == "grid":
        certainty = Certainty.INFERRED
        reasons.append("rol heurístico")
    if role == "open_box" and not (n_rows == 1 and n_cols == 1 and after_question):
        certainty = Certainty.INFERRED
        reasons.append("no es 1x1 o no sigue a una pregunta")
    if len(ordered) > 1:
        certainty = Certainty.INFERRED
        reasons.append("varias firmas coinciden")
    return RoleResult(role, certainty, tuple(reasons), tuple(ordered[1:]))
