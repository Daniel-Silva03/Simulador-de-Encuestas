"""Identificación de preguntas, opciones, instrucciones y navegación (sección 5, pasos 3 y 4).

Recorre los bloques en el orden del documento y produce una estructura
intermedia (``ParsedDocument``) que todavía no es IR. ``to_ir`` la
convierte después en ``Instrument(role=design)``.

Reglas:

- Un párrafo es pregunta solo si su etiqueta coincide con un patrón del
  léxico. No se inventan preguntas, opciones, destinos ni reglas.
- El enunciado puede continuar en los párrafos siguientes, hasta la primera
  tabla de la pregunta o la siguiente etiqueta. Esa continuación es
  ``inferred``.
- La navegación se busca en los cuatro lugares de la sección 5 y se marca
  con su lugar de origen.
- Lo que no encaja se conserva en ``unresolved`` con su evidencia.
- Duplicados (AUT-1, D-01): dentro de una pregunta se conserva la primera
  aparición de cada código, en el orden del documento; las repeticiones van
  a ``unresolved`` (``duplicate_option_code``, ``duplicate_grid_code``). Una
  segunda tabla de opciones de la misma pregunta no se fusiona: queda
  completa en ``unresolved`` (``multiple_option_tables``). Un documento con
  duplicados nunca aborta la lectura.
- El formato (resaltado, sombreado) se conserva y nunca cambia la
  interpretación.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, Optional

from survey_sim.ir.models import Certainty, InstructionKind, ItemKind, ItemType, TerminationKind

from .blocks import CellBlock, DocumentBlocks, FormatMarks, ParagraphBlock, TableBlock, aggregate_bold, clean_text
from .lexicon import Lexicon, Segment
from .routing_text import NavMatch, find_navigation, has_unparsed_navigation, termination_code
from .table_roles import RoleResult, classify

Place = Literal["options_column", "option_label", "paragraph", "statement"]

#: Certeza de la navegación según su lugar (sección 5).
PLACE_CERTAINTY: dict[str, Certainty] = {
    "options_column": Certainty.EXPLICIT,
    "option_label": Certainty.EXPLICIT,
    "paragraph": Certainty.INFERRED,
    "statement": Certainty.INFERRED,
}


@dataclass(frozen=True)
class Unresolved:
    code: str
    locator: str
    text: str
    detail: str = ""


@dataclass(frozen=True)
class ParsedInstruction:
    kind: InstructionKind
    tag: str
    text: str
    locator: str


@dataclass(frozen=True)
class ParsedRouting:
    action: Literal["continue", "goto", "terminate", "unknown"]
    place: Place
    raw: str
    locator: str
    target: Optional[str] = None
    termination_label: Optional[str] = None


@dataclass
class ParsedOption:
    code: str
    label_raw: str
    label_clean: str
    locator: str
    is_missing_code: bool
    opens_text: bool
    marks: FormatMarks
    routing: Optional[ParsedRouting] = None
    extra_instructions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ParsedCondition:
    """Condición de aplicación reconocida en texto libre."""

    kind: Literal["show_if", "applies_if"]
    place: Place
    source: str
    codes: tuple[str, ...]
    target: str
    raw: str
    locator: str


@dataclass(frozen=True)
class OpaqueNavigation:
    """Texto con palabras de navegación que ningún patrón reconoce."""

    place: Place
    text: str
    locator: str


@dataclass(frozen=True)
class ParsedRuleText:
    """Salto o terminación fuera de una tabla de opciones."""

    kind: Literal["goto", "terminate"]
    place: Place
    raw: str
    locator: str
    after_item: Optional[str]
    target: Optional[str] = None
    termination_label: Optional[str] = None


@dataclass
class ParsedGrid:
    locator: str
    header: list[tuple[int, Optional[str], str]]  # (columna, código si aparece en el documento, texto)
    rows: list[tuple[str, str, FormatMarks]]  # (texto de la fila, locator, formato)
    certainty: Certainty


@dataclass
class ParsedItem:
    id: str
    label: str
    locator: str
    section_id: str
    raw_parts: list[str]
    statement_parts: list[str]
    marks: list[FormatMarks]
    instructions: list[ParsedInstruction] = field(default_factory=list)
    response_types: list[ItemType] = field(default_factory=list)
    options: list[ParsedOption] = field(default_factory=list)
    open_boxes: list[str] = field(default_factory=list)
    grid: Optional[ParsedGrid] = None
    recode: Optional[tuple[str, str]] = None  # (texto de la matriz, locator)
    calculated: bool = False
    continued: bool = False
    tables_seen: bool = False
    weak: list[str] = field(default_factory=list)  # motivos para certeza inferred


@dataclass
class ParsedSection:
    id: str
    title: Optional[str]
    locator: str
    certainty: Certainty


@dataclass(frozen=True)
class TableRoleRecord:
    index: int
    locator: str
    role: str
    certainty: Certainty
    reasons: tuple[str, ...]
    ambiguous_with: tuple[str, ...]
    n_rows: int
    n_cols: int


@dataclass(frozen=True)
class TerminationRecord:
    code: str
    label: str
    kind: Optional[TerminationKind]
    locator: str


@dataclass
class ParsedDocument:
    sections: list[ParsedSection]
    items: list[ParsedItem]
    conditions: list[ParsedCondition]
    rule_texts: list[ParsedRuleText]
    terminations: dict[str, TerminationRecord]
    table_roles: list[TableRoleRecord]
    version_tables: list[tuple[str, tuple[str, ...]]]
    unresolved: list[Unresolved]
    opaque_navigation: list[OpaqueNavigation]


class _Assembler:
    def __init__(self, doc: DocumentBlocks, lex: Lexicon):
        self.doc = doc
        self.lex = lex
        self.sections: list[ParsedSection] = []
        self.items: list[ParsedItem] = []
        self.conditions: list[ParsedCondition] = []
        self.rule_texts: list[ParsedRuleText] = []
        self.terminations: dict[str, TerminationRecord] = {}
        self.roles: list[TableRoleRecord] = []
        self.versions: list[tuple[str, tuple[str, ...]]] = []
        self.unresolved: list[Unresolved] = []
        self.opaque_nav: list[OpaqueNavigation] = []
        self.current: Optional[ParsedItem] = None
        self.label_counts: dict[str, int] = {}

    # Utilidades -------------------------------------------------------------

    def unresolved_(self, code: str, locator: str, text: str, detail: str = "") -> None:
        self.unresolved.append(Unresolved(code, locator, text, detail))

    def section_id(self) -> str:
        if not self.sections:
            self.sections.append(ParsedSection("S0", None, "word/document.xml#start", Certainty.INFERRED))
        return self.sections[-1].id

    def termination(self, label: str, locator: str) -> str:
        code = termination_code(label)
        if code not in self.terminations:
            kind = self.lex.termination_kind(label)
            self.terminations[code] = TerminationRecord(code, label, kind, locator)
            if kind is None:
                self.unresolved_(
                    "termination_kind_unrecognized",
                    locator,
                    label,
                    "decisión pendiente: incidencia de terminación sin tipo reconocido",
                )
        return code

    # Navegación ----------------------------------------------------------------

    def handle_navigation(self, text: str, place: Place, locator: str, *, item: Optional[ParsedItem]) -> list[NavMatch]:
        matches = find_navigation(text, self.lex)
        for m in matches:
            if m.kind == "applies_if":
                if item is None:
                    self.unresolved_("condition_without_item", locator, m.raw)
                else:
                    self.conditions.append(ParsedCondition("applies_if", place, m.source, m.codes, item.id, m.raw, locator))
            elif m.kind == "show_if":
                self.conditions.append(ParsedCondition("show_if", place, m.source, m.codes, m.target, m.raw, locator))
            elif m.kind == "terminate":
                label = m.termination
                if label:
                    self.termination(label, locator)
                if place in ("paragraph", "statement"):
                    self.rule_texts.append(ParsedRuleText("terminate", place, m.raw, locator, item.id if item else None, termination_label=label))
            elif m.kind == "goto":
                if place in ("paragraph", "statement"):
                    self.rule_texts.append(ParsedRuleText("goto", place, m.raw, locator, item.id if item else None, target=m.target))
            # "continue" fuera de una tabla no genera reglas: es el flujo normal.
        if has_unparsed_navigation(text, matches, self.lex):
            self.unresolved_("unparsed_navigation", locator, text, "palabras de navegación sin patrón reconocido")
            self.opaque_nav.append(OpaqueNavigation(place, text, locator))
        return matches

    def handle_segments(self, segments: tuple[Segment, ...], place: Place, locator: str, item: Optional[ParsedItem]) -> None:
        for seg in segments:
            if item is not None:
                item.instructions.append(ParsedInstruction(seg.kind, seg.tag, seg.text, locator))
                if seg.kind is InstructionKind.PROGRAMMER:
                    item.response_types.extend(self.lex.response_types(seg.text))
                if self.lex.is_calculated(seg.text):
                    item.calculated = True
            if seg.kind is InstructionKind.PROGRAMMER:
                self.handle_navigation(seg.text, place, locator, item=item)

    # Párrafos ------------------------------------------------------------------

    def paragraph(self, p: ParagraphBlock) -> None:
        text = p.display_text if self.lex.spec.numbered_paragraphs_as_items else p.text
        if not text:
            return
        lab = self.lex.match_label(text)
        if lab is not None:
            self.new_item(p, *lab)
            return
        split = self.lex.split(text)
        cur = self.current
        if not split.statement and split.segments:
            # Párrafo solo con instrucciones: navegación independiente.
            if cur is None:
                self.unresolved_("instruction_without_item", p.locator, p.text)
            else:
                cur.weak.append("instrucciones en párrafo independiente")
                cur.marks.append(p.marks)
            self.handle_segments(split.segments, "paragraph", p.locator, cur)
            return
        if cur is not None and not cur.tables_seen:
            cur.raw_parts.append(p.raw)
            cur.statement_parts.append(split.statement)
            cur.marks.append(p.marks)
            cur.continued = True
            self.handle_segments(split.segments, "statement", p.locator, cur)
            if self.lex.is_calculated(split.statement):
                cur.calculated = True
            return
        self.unresolved_("unrecognized_paragraph", p.locator, p.text, "texto sin etiqueta de pregunta ni pregunta abierta")

    def new_item(self, p: ParagraphBlock, label: str, rest: str) -> None:
        n = self.label_counts.get(label, 0) + 1
        self.label_counts[label] = n
        item_id = label if n == 1 else f"{label}#{n}"
        if n > 1:
            self.unresolved_("duplicate_label", p.locator, p.text, f"la etiqueta {label} ya apareció; id asignado {item_id}")
        split = self.lex.split(rest)
        item = ParsedItem(
            id=item_id,
            label=label,
            locator=p.locator,
            section_id=self.section_id(),
            raw_parts=[p.raw],
            statement_parts=[split.statement],
            marks=[p.marks],
        )
        if n > 1:
            item.weak.append("etiqueta repetida")
        self.items.append(item)
        self.current = item
        if self.lex.is_calculated(split.statement):
            item.calculated = True
        self.handle_segments(split.segments, "statement", p.locator, item)

    # Tablas --------------------------------------------------------------------

    def table(self, t: TableBlock, prev_block_is_item: bool) -> None:
        res = classify(t, self.lex, after_question=prev_block_is_item)
        self.roles.append(
            TableRoleRecord(t.index, t.locator, res.role, res.certainty, res.reasons, res.ambiguous_with, t.n_rows, t.n_cols)
        )
        if res.ambiguous_with:
            self.unresolved_("ambiguous_table_role", t.locator, _table_text(t), f"elegido {res.role}; también coincide {', '.join(res.ambiguous_with)}")
        cur = self.current
        role = res.role
        if role == "section_header":
            title = t.cell_text(0, 0)
            n = sum(1 for s in self.sections if s.id != "S0") + 1
            self.sections.append(ParsedSection(f"S{n}", title, t.locator, res.certainty))
            self.current = None
            return
        if role == "version_table":
            self.versions.append((t.locator, tuple(t.cell_text(0, c) for c in range(t.n_cols))))
            return
        if role == "other":
            self.unresolved_("table_role_other", t.locator, _table_text(t), "; ".join(res.reasons))
            return
        if cur is None:
            self.unresolved_(f"{role}_without_item", t.locator, _table_text(t))
            return
        if res.certainty is Certainty.INFERRED:
            cur.weak.append(f"rol de tabla {role} inferido")
        cur.tables_seen = True
        if role == "open_box":
            cur.open_boxes.append(t.locator)
        elif role == "options":
            if cur.options:
                # D-01: la segunda tabla no se fusiona; queda completa como no resuelta.
                self.unresolved_("multiple_option_tables", t.locator, _table_text(t),
                                 f"segunda tabla de opciones para {cur.id}; no se fusiona con la primera")
                return
            self.parse_options(t, cur)
        elif role == "recode_matrix":
            lines = [" | ".join(t.cell_text(r, c) for c in range(t.n_cols)) for r in range(t.n_rows)]
            cur.recode = ("\n".join(lines), t.locator)
        elif role == "grid":
            self.parse_grid(t, cur, res)

    def parse_options(self, t: TableBlock, item: ParsedItem) -> None:
        grid = t.logical()
        seen = {o.code: o.locator for o in item.options}
        for r in range(t.n_rows):
            code = t.cell_text(r, 0)
            label_cell: CellBlock = grid[r][1]
            row_loc = f"{t.locator}/r{r}"
            if code in seen:
                # D-01: se conserva la primera aparición; la fila repetida no entra en la IR
                # y no se analiza su navegación (no registra terminaciones ni rutas).
                row_text = " | ".join(t.cell_text(r, c) for c in range(t.n_cols))
                self.unresolved_("duplicate_option_code", row_loc, row_text, f"código {code} ya definido en {seen[code]}")
                continue
            seen[code] = row_loc
            split = self.lex.split(label_cell.text)
            label_clean = split.statement
            opt = ParsedOption(
                code=code,
                label_raw=label_cell.raw,
                label_clean=label_clean,
                locator=row_loc,
                is_missing_code=self.lex.is_missing_label(label_clean),
                opens_text=self.lex.opens_text(label_cell.text),
                marks=_merge_marks([c.marks for c in t.rows[r]]),
            )
            # Navegación dentro de la etiqueta de la opción.
            label_route = None
            for seg in split.segments:
                consumed = False
                if seg.kind is InstructionKind.PROGRAMMER:
                    ms = find_navigation(seg.text, self.lex)
                    route = self.route_from(ms, seg.text, "option_label", label_cell.locator)
                    if route is not None:
                        label_route = route
                        consumed = True
                    elif has_unparsed_navigation(seg.text, ms, self.lex):
                        self.unresolved_("unparsed_navigation", label_cell.locator, seg.text, "palabras de navegación sin patrón reconocido")
                        self.opaque_nav.append(OpaqueNavigation("option_label", seg.text, label_cell.locator))
                if not consumed:
                    opt.extra_instructions.append(f"{seg.tag}: {seg.text}")
            # Navegación en la tercera columna.
            col_route = None
            if t.n_cols == 3 and grid[r][2] is not None:
                rtext = t.cell_text(r, 2)
                if rtext:
                    rs = self.lex.split(rtext)
                    body = " ".join(s.text for s in rs.segments) if rs.segments else rs.statement
                    ms = find_navigation(body, self.lex)
                    col_route = self.route_from(ms, body, "options_column", grid[r][2].locator)
                    if col_route is None:
                        col_route = ParsedRouting("unknown", "options_column", rtext, grid[r][2].locator)
                        self.unresolved_("unparsed_navigation", grid[r][2].locator, rtext, "texto de navegación no reconocido")
            if label_route and col_route:
                self.unresolved_("routing_conflict", row_loc, f"{label_cell.text} || {t.cell_text(r, 2)}", "navegación en la etiqueta y en la tercera columna")
                opt.routing = col_route
            else:
                opt.routing = col_route or label_route
            item.options.append(opt)

    def route_from(self, ms: list[NavMatch], text: str, place: Place, locator: str) -> Optional[ParsedRouting]:
        """Una sola instrucción de navegación clara por opción; si no, no hay ruta."""
        relevant = [m for m in ms if m.kind in ("continue", "goto", "terminate")]
        if len(relevant) != 1 or has_unparsed_navigation(text, ms, self.lex) or any(m.kind in ("show_if", "applies_if") for m in ms):
            return None
        m = relevant[0]
        if m.kind == "terminate":
            label = m.termination
            if label:
                self.termination(label, locator)
            return ParsedRouting("terminate", place, m.raw, locator, target=termination_code(label) if label else None, termination_label=label)
        if m.kind == "goto":
            return ParsedRouting("goto", place, m.raw, locator, target=m.target)
        return ParsedRouting("continue", place, m.raw, locator)

    def parse_grid(self, t: TableBlock, item: ParsedItem, res: RoleResult) -> None:
        header: list[tuple[int, Optional[str], str]] = []
        seen: dict[str, str] = {}
        for c in range(1, t.n_cols):
            h = t.cell_text(0, c)
            m = re.match(r"^([0-9]{1,4})\s*[.\-)]?\s+(.+)$", h)  # H-15: solo dígitos ASCII
            code, label = (m.group(1), m.group(2)) if m else (None, h)
            loc = f"{t.locator}/r0/c{c}"
            if code is not None and code in seen:
                # D-01: columna con código repetido; se conserva la primera.
                self.unresolved_("duplicate_grid_code", loc, h, f"código {code} ya definido en {seen[code]}")
                continue
            if code is not None:
                seen[code] = loc
            header.append((c, code, label))
        rows = [(t.cell_text(r, 0), f"{t.locator}/r{r}", _merge_marks([c.marks for c in t.rows[r]])) for r in range(1, t.n_rows)]
        item.grid = ParsedGrid(t.locator, header, rows, res.certainty)
        if any(code is None for _, code, _ in header):
            self.unresolved_("grid_without_codes", t.locator, _table_text(t), "la escala no trae códigos; no se inventan")

    # Recorrido ---------------------------------------------------------------

    def run(self) -> ParsedDocument:
        prev_is_item = False
        for b in self.doc.blocks:
            if isinstance(b, ParagraphBlock):
                before = self.current
                self.paragraph(b)
                prev_is_item = self.current is not None and (self.current is not before or not self.current.tables_seen) and bool(b.text)
            else:
                self.table(b, prev_is_item)
                prev_is_item = False
        self.post_checks()
        return ParsedDocument(
            sections=self.sections,
            items=self.items,
            conditions=self.conditions,
            rule_texts=self.rule_texts,
            terminations=self.terminations,
            table_roles=self.roles,
            version_tables=self.versions,
            unresolved=self.unresolved,
            opaque_navigation=self.opaque_nav,
        )

    def post_checks(self) -> None:
        labels = {i.label for i in self.items} | {i.id for i in self.items}
        for it in self.items:
            for o in it.options:
                if o.routing and o.routing.action == "goto" and o.routing.target not in labels:
                    self.unresolved_("routing_target_not_found", o.routing.locator, o.routing.raw, f"destino {o.routing.target} no existe")
            types = set(it.response_types)
            if len(types) > 1:
                self.unresolved_("response_type_conflict", it.locator, " ".join(it.raw_parts), f"tipos: {sorted(t.value for t in types)}")
        for c in self.conditions:
            for ref in (c.source, c.target):
                if ref not in labels:
                    self.unresolved_("routing_target_not_found", c.locator, c.raw, f"pregunta {ref} no existe")
        for r in self.rule_texts:
            if r.kind == "goto" and r.target not in labels:
                self.unresolved_("routing_target_not_found", r.locator, r.raw, f"destino {r.target} no existe")


def _merge_marks(ms: list[FormatMarks]) -> FormatMarks:
    """Une marcas de varios bloques. Solo describe el formato; no lo interpreta."""
    return merge_marks(ms)


def merge_marks(ms: list[FormatMarks]) -> FormatMarks:
    return FormatMarks(
        bold=aggregate_bold([m.bold for m in ms]),
        highlights=tuple(sorted({h for m in ms for h in m.highlights})),
        shading=tuple(sorted({s for m in ms for s in m.shading})),
    )


def _table_text(t: TableBlock) -> str:
    return " / ".join(" | ".join(t.cell_text(r, c) for c in range(t.n_cols)) for r in range(t.n_rows))


def assemble(doc: DocumentBlocks, lex: Lexicon) -> ParsedDocument:
    return _Assembler(doc, lex).run()
