"""Secuencia ordenada de bloques del documento (sección 5, paso 1).

Cada bloque conserva su posición (``index``), su procedencia (``locator``),
el texto crudo y limpio, el estilo, la numeración real y el formato
(negrita, resaltado y sombreado). El formato se registra sin interpretarlo
(D07).

Encabezados, pies, metadatos y cuadros de texto se registran aparte y no
entran en la secuencia del cuerpo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Union

from . import docx_xml as dx

DOC_PART = "word/document.xml"


def clean_text(s: str) -> str:
    """Texto limpio: espacios normalizados (incluidos NBSP, tabulaciones y saltos)."""
    return re.sub(r"\s+", " ", s.replace(" ", " ")).strip()


@dataclass(frozen=True)
class FormatMarks:
    bold: Optional[str]  # "all", "some", "none"; None si no hay texto
    highlights: tuple[str, ...] = ()
    shading: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        """Representación para el informe de lectura, sin significado asignado."""
        return {"bold": self.bold, "highlights": list(self.highlights), "shading": list(self.shading)}

    @property
    def empty(self) -> bool:
        """Sin ninguna marca: ni negrita, ni resaltado, ni sombreado."""
        return self.bold in (None, "none") and not self.highlights and not self.shading


def aggregate_bold(values) -> Optional[str]:
    """Negrita de un conjunto de bloques; ignora los que no tienen texto."""
    vals = {v for v in values if v is not None}
    if not vals:
        return None
    return vals.pop() if len(vals) == 1 else "some"


def _marks(fmt: dx.RunFormat, extra_shading: Optional[str] = None) -> FormatMarks:
    if fmt.text_chars == 0:
        bold = None
    elif fmt.bold_chars == 0:
        bold = "none"
    elif fmt.bold_chars >= fmt.text_chars:
        bold = "all"
    else:
        bold = "some"
    shading = set(fmt.shading)
    if extra_shading:
        shading.add(extra_shading)
    return FormatMarks(bold=bold, highlights=tuple(sorted(fmt.highlights)), shading=tuple(sorted(shading)))


@dataclass(frozen=True)
class ParagraphBlock:
    index: int
    locator: str
    raw: str
    text: str
    style_id: Optional[str]
    style_name: Optional[str]
    numbering: Optional[str]  # etiqueta visible calculada (p. ej. "1." o "•")
    numbering_format: Optional[str]
    num_id: Optional[str]
    ilvl: Optional[int]
    marks: FormatMarks
    kind: str = "paragraph"

    @property
    def display_text(self) -> str:
        """Texto como se ve: etiqueta de numeración automática + texto."""
        if self.numbering and self.numbering_format != "bullet":
            return f"{self.numbering} {self.text}".strip()
        return self.text


@dataclass(frozen=True)
class CellBlock:
    row: int
    col: int
    span: int
    vmerge: Optional[str]  # "restart", "continue" o None
    origin: tuple[int, int]  # celda que contiene el texto (para continuaciones verticales)
    locator: str
    paragraphs: tuple[ParagraphBlock, ...]
    raw: str
    text: str
    marks: FormatMarks
    nested_table: bool = False

    @property
    def is_continuation(self) -> bool:
        return self.vmerge == "continue"


@dataclass(frozen=True)
class TableBlock:
    index: int
    locator: str
    rows: tuple[tuple[CellBlock, ...], ...]  # celdas físicas por fila
    n_cols: int  # columnas lógicas (tblGrid o máximo calculado)
    kind: str = "table"

    @property
    def n_rows(self) -> int:
        return len(self.rows)

    def logical(self) -> list[list[Optional[CellBlock]]]:
        """Matriz filas × columnas lógicas. Una celda con gridSpan ocupa varias posiciones."""
        grid: list[list[Optional[CellBlock]]] = []
        for r in self.rows:
            line: list[Optional[CellBlock]] = [None] * self.n_cols
            for c in r:
                for k in range(c.col, min(c.col + c.span, self.n_cols)):
                    line[k] = c
            grid.append(line)
        return grid

    def cell_text(self, r: int, c: int, *, follow_merge: bool = True) -> str:
        cell = self.logical()[r][c]
        if cell is None:
            return ""
        if cell.is_continuation and follow_merge:
            orow, ocol = cell.origin
            origin = self.logical()[orow][ocol]
            return origin.text if origin is not None else ""
        return cell.text


Block = Union[ParagraphBlock, TableBlock]


@dataclass(frozen=True)
class SideText:
    """Texto registrado aparte: encabezados, pies y cuadros de texto."""

    kind: str  # "header", "footer" o "textbox"
    locator: str
    text: str


@dataclass(frozen=True)
class DocumentBlocks:
    sha256: str
    blocks: tuple[Block, ...]
    side_texts: tuple[SideText, ...]
    core: dict
    vmerge_cells: int = 0


class _Builder:
    def __init__(self, pkg: dx.DocxPackage):
        self.pkg = pkg
        self.styles = dx.read_styles(pkg)
        self.numbering = dx.Numbering(pkg)
        self.side: list[SideText] = []
        self.vmerge = 0

    def paragraph(self, p, index: int, locator: str) -> ParagraphBlock:
        raw, fmt, textboxes = dx.paragraph_text(p)
        for k, tb in enumerate(textboxes):
            text = clean_text(" ".join(dx.paragraph_text(pp)[0] for pp in tb.iter(dx.q("w:p"))))
            if text:
                self.side.append(SideText("textbox", f"{locator}/textbox[{k}]", text))
        style_id, num_id, ilvl = dx.paragraph_style_and_numpr(p)
        if num_id is None:
            s_num, s_ilvl = dx.style_numbering(self.styles, style_id)
            num_id = s_num
            ilvl = ilvl if ilvl is not None else s_ilvl
        label = fmt_name = None
        if num_id is not None and num_id != "0":
            res = self.numbering.next_label(num_id, ilvl or 0)
            if res is not None:
                label, fmt_name = res
        st = self.styles.get(style_id) if style_id else None
        return ParagraphBlock(
            index=index,
            locator=locator,
            raw=raw,
            text=clean_text(raw),
            style_id=style_id,
            style_name=st.name if st else None,
            numbering=label,
            numbering_format=fmt_name,
            num_id=num_id if num_id != "0" else None,
            ilvl=ilvl if num_id not in (None, "0") else None,
            marks=_marks(fmt),
        )

    def table(self, tbl, index: int, locator: str) -> TableBlock:
        n_cols = dx.table_grid_cols(tbl)
        rows: list[tuple[CellBlock, ...]] = []
        # columna lógica -> (fila, col) de la celda que inició la combinación vertical
        open_merge: dict[int, tuple[int, int]] = {}
        for r_i, tr in enumerate(tbl.findall(dx.q("w:tr"))):
            col = 0
            cells: list[CellBlock] = []
            for c_i, tc in enumerate(tr.findall(dx.q("w:tc"))):
                span, vmerge, cell_fill = dx.cell_props(tc)
                cloc = f"{locator}/r{r_i}/c{col}"
                paras = tuple(
                    self.paragraph(p, index=-1, locator=f"{cloc}/p{k}")
                    for k, p in enumerate(tc.findall(dx.q("w:p")))
                )
                nested = tc.find(dx.q("w:tbl")) is not None
                raw = "\n".join(p.raw for p in paras)
                if vmerge == "restart":
                    open_merge[col] = (r_i, col)
                    origin = (r_i, col)
                elif vmerge == "continue":
                    origin = open_merge.get(col, (r_i, col))
                    self.vmerge += 1
                else:
                    open_merge.pop(col, None)
                    origin = (r_i, col)
                fm = FormatMarks(
                    bold=aggregate_bold([p.marks.bold for p in paras]),
                    highlights=tuple(sorted({h for p in paras for h in p.marks.highlights})),
                    shading=tuple(sorted({s for p in paras for s in p.marks.shading} | ({cell_fill} if cell_fill else set()))),
                )
                cells.append(
                    CellBlock(
                        row=r_i,
                        col=col,
                        span=span,
                        vmerge=vmerge,
                        origin=origin,
                        locator=cloc,
                        paragraphs=paras,
                        raw=raw,
                        text=clean_text(" ".join(p.display_text for p in paras)),
                        marks=fm,
                        nested_table=nested,
                    )
                )
                col += span
            n_cols = max(n_cols, col)
            rows.append(tuple(cells))
        return TableBlock(index=index, locator=locator, rows=tuple(rows), n_cols=n_cols)


def build_blocks(pkg: dx.DocxPackage) -> DocumentBlocks:
    b = _Builder(pkg)
    blocks: list[Block] = []
    root = pkg.parts[DOC_PART]
    for el in dx.body_children(root):
        i = len(blocks)
        loc = f"{DOC_PART}#block{i}"
        if el.tag == dx.q("w:p"):
            blocks.append(b.paragraph(el, i, loc))
        else:
            blocks.append(b.table(el, i, loc))
    for name in sorted(pkg.parts):
        kind = "header" if "/header" in name else "footer" if "/footer" in name else None
        if kind is None:
            continue
        text = clean_text(" ".join(dx.paragraph_text(p)[0] for p in pkg.parts[name].iter(dx.q("w:p"))))
        if text:
            b.side.append(SideText(kind, name, text))
    return DocumentBlocks(
        sha256=pkg.sha256,
        blocks=tuple(blocks),
        side_texts=tuple(b.side),
        core=dx.core_properties(pkg),
        vmerge_cells=b.vmerge,
    )
