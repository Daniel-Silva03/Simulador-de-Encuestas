"""Lectura segura del XML de un ``.docx`` (especificación v1.0, sección 5, paso 1).

Se lee el paquete como datos:

- Solo se abren las partes conocidas (``document.xml``, ``styles.xml``,
  ``numbering.xml``, encabezados, pies y ``docProps/core.xml``).
- No se siguen relaciones externas ni se resuelven entidades; el parser no
  accede a la red.
- Hay límites de tamaño por parte, de tamaño total y de razón de
  compresión, para no aceptar paquetes desproporcionados.
- El archivo nunca se modifica: se trabaja sobre los bytes leídos.

Este módulo no interpreta: devuelve texto, estilos, numeración, formato y
estructura de tablas tal como están en el XML.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import Optional

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {
    "w": W,
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
}


def q(tag: str) -> str:
    prefix, local = tag.split(":")
    return f"{{{NS[prefix]}}}{local}"


# --------------------------------------------------------------------------
# Límites y apertura segura
# --------------------------------------------------------------------------

MAX_PART_BYTES = 30 * 1024 * 1024
MAX_TOTAL_BYTES = 80 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200


class DocxError(ValueError):
    """El archivo no es un .docx legible o excede los límites de seguridad."""


_PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    load_dtd=False,
    dtd_validation=False,
    huge_tree=False,
    remove_comments=True,
    remove_pis=True,
)

_KNOWN_PARTS = re.compile(
    r"^(word/document\.xml|word/styles\.xml|word/numbering\.xml|word/header[0-9]*\.xml|word/footer[0-9]*\.xml|docProps/core\.xml)$"
)


@dataclass(frozen=True)
class DocxPackage:
    sha256: str
    size: int
    parts: dict  # nombre de parte -> árbol lxml


def open_docx(data: bytes) -> DocxPackage:
    """Abre un .docx desde bytes y devuelve solo las partes conocidas ya parseadas."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise DocxError("no es un archivo .docx (ZIP) válido") from exc
    total = 0
    parts: dict = {}
    with zf:
        for info in zf.infolist():
            if not _KNOWN_PARTS.match(info.filename):
                continue
            if info.file_size > MAX_PART_BYTES:
                raise DocxError(f"la parte {info.filename} excede el tamaño máximo")
            if info.compress_size and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
                raise DocxError(f"la parte {info.filename} tiene una razón de compresión anómala")
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise DocxError("el paquete excede el tamaño total máximo")
            with zf.open(info) as fh:
                raw = fh.read(MAX_PART_BYTES + 1)
            if len(raw) > MAX_PART_BYTES:
                raise DocxError(f"la parte {info.filename} excede el tamaño máximo")
            try:
                parts[info.filename] = etree.fromstring(raw, parser=_PARSER)
            except etree.XMLSyntaxError as exc:
                raise DocxError(f"XML inválido en {info.filename}") from exc
    if "word/document.xml" not in parts:
        raise DocxError("el paquete no contiene word/document.xml")
    return DocxPackage(sha256=hashlib.sha256(data).hexdigest(), size=len(data), parts=parts)


# --------------------------------------------------------------------------
# Estilos
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class StyleInfo:
    style_id: str
    name: Optional[str]
    based_on: Optional[str]
    num_id: Optional[str]
    ilvl: Optional[int]


def read_styles(pkg: DocxPackage) -> dict[str, StyleInfo]:
    root = pkg.parts.get("word/styles.xml")
    out: dict[str, StyleInfo] = {}
    if root is None:
        return out
    for st in root.iter(q("w:style")):
        sid = st.get(q("w:styleId"))
        if not sid:
            continue
        name = st.find(q("w:name"))
        based = st.find(q("w:basedOn"))
        num_id = ilvl = None
        numpr = st.find(f"{q('w:pPr')}/{q('w:numPr')}")
        if numpr is not None:
            n = numpr.find(q("w:numId"))
            lv = numpr.find(q("w:ilvl"))
            num_id = n.get(q("w:val")) if n is not None else None
            ilvl = int(lv.get(q("w:val"))) if lv is not None else None
        out[sid] = StyleInfo(
            style_id=sid,
            name=name.get(q("w:val")) if name is not None else None,
            based_on=based.get(q("w:val")) if based is not None else None,
            num_id=num_id,
            ilvl=ilvl,
        )
    return out


def style_numbering(styles: dict[str, StyleInfo], style_id: Optional[str]) -> tuple[Optional[str], Optional[int]]:
    """Numeración heredada de un estilo (sigue ``basedOn``)."""
    seen = set()
    while style_id and style_id not in seen:
        seen.add(style_id)
        st = styles.get(style_id)
        if st is None:
            break
        if st.num_id is not None:
            return st.num_id, st.ilvl
        style_id = st.based_on
    return None, None


# --------------------------------------------------------------------------
# Numeración automática (numbering.xml)
# --------------------------------------------------------------------------


@dataclass
class _Level:
    start: int = 1
    fmt: str = "decimal"
    text: str = "%1."


def _roman(n: int) -> str:
    vals = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"), (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]
    out = ""
    for v, s in vals:
        while n >= v:
            out += s
            n -= v
    return out


def _letters(n: int) -> str:
    out = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        out = chr(97 + r) + out
    return out


def _format(n: int, fmt: str) -> str:
    if fmt == "decimal":
        return str(n)
    if fmt == "decimalZero":
        return f"{n:02d}"
    if fmt == "lowerLetter":
        return _letters(n)
    if fmt == "upperLetter":
        return _letters(n).upper()
    if fmt == "lowerRoman":
        return _roman(n)
    if fmt == "upperRoman":
        return _roman(n).upper()
    if fmt == "none":
        return ""
    return str(n)  # formatos no soportados: se conserva el número


class Numbering:
    """Calcula las etiquetas visibles de las listas automáticas, en orden de documento."""

    def __init__(self, pkg: DocxPackage):
        self._levels: dict[str, dict[int, _Level]] = {}
        self._counters: dict[str, dict[int, int]] = {}
        root = pkg.parts.get("word/numbering.xml")
        if root is None:
            return
        abstract: dict[str, dict[int, _Level]] = {}
        for an in root.iter(q("w:abstractNum")):
            aid = an.get(q("w:abstractNumId"))
            lv: dict[int, _Level] = {}
            for l in an.iter(q("w:lvl")):
                il = int(l.get(q("w:ilvl"), "0"))
                start = l.find(q("w:start"))
                fmt = l.find(q("w:numFmt"))
                txt = l.find(q("w:lvlText"))
                lv[il] = _Level(
                    start=int(start.get(q("w:val"))) if start is not None else 1,
                    fmt=fmt.get(q("w:val")) if fmt is not None else "decimal",
                    text=txt.get(q("w:val")) if txt is not None else "",
                )
            abstract[aid] = lv
        for num in root.iter(q("w:num")):
            nid = num.get(q("w:numId"))
            a = num.find(q("w:abstractNumId"))
            if a is None:
                continue
            levels = {k: _Level(v.start, v.fmt, v.text) for k, v in abstract.get(a.get(q("w:val")), {}).items()}
            for ov in num.iter(q("w:lvlOverride")):
                il = int(ov.get(q("w:ilvl"), "0"))
                so = ov.find(q("w:startOverride"))
                if so is not None and il in levels:
                    levels[il].start = int(so.get(q("w:val")))
            self._levels[nid] = levels

    def next_label(self, num_id: str, ilvl: int) -> Optional[tuple[str, str]]:
        """Avanza el contador y devuelve (etiqueta, formato). None si no hay definición."""
        levels = self._levels.get(num_id)
        if not levels or ilvl not in levels:
            return None
        ctr = self._counters.setdefault(num_id, {})
        ctr[ilvl] = ctr.get(ilvl, levels[ilvl].start - 1) + 1
        for deeper in [k for k in ctr if k > ilvl]:
            del ctr[deeper]
        lvl = levels[ilvl]
        if lvl.fmt == "bullet":
            return lvl.text, "bullet"

        def repl(m: re.Match) -> str:
            k = int(m.group(1)) - 1
            n = ctr.get(k, levels.get(k, _Level()).start)
            return _format(n, levels.get(k, lvl).fmt)

        return re.sub(r"%([0-9])", repl, lvl.text), lvl.fmt


# --------------------------------------------------------------------------
# Texto y formato de párrafos
# --------------------------------------------------------------------------

_SKIP = {q("w:instrText"), q("w:delText"), q("w:del"), q("w:txbxContent"), q("w:rPr"), q("w:pPr")}


@dataclass
class RunFormat:
    bold_chars: int = 0
    text_chars: int = 0
    highlights: set = field(default_factory=set)
    shading: set = field(default_factory=set)


def _run_props(r) -> tuple[bool, Optional[str], Optional[str]]:
    rpr = r.find(q("w:rPr"))
    if rpr is None:
        return False, None, None
    b = rpr.find(q("w:b"))
    bold = b is not None and b.get(q("w:val"), "true") not in ("0", "false", "off")
    hl = rpr.find(q("w:highlight"))
    sh = rpr.find(q("w:shd"))
    fill = sh.get(q("w:fill")) if sh is not None else None
    return bold, (hl.get(q("w:val")) if hl is not None else None), (fill if fill and fill.lower() != "auto" else None)


def paragraph_text(p) -> tuple[str, RunFormat, list]:
    """Texto visible de un párrafo, formato de sus runs y cuadros de texto que contiene.

    Incluye inserciones (``w:ins``), excluye texto borrado (``w:del``) y
    códigos de campo (``w:instrText``). Los cuadros de texto se devuelven
    aparte, sin mezclarlos con el texto del párrafo.
    """
    parts: list[str] = []
    fmt = RunFormat()
    textboxes = []

    def walk(el, run=None):
        for ch in el:
            tag = ch.tag
            if not isinstance(tag, str):
                continue
            if tag.endswith("}Fallback"):  # mc:Fallback duplica el contenido de mc:Choice
                continue
            if tag == q("w:txbxContent"):
                textboxes.append(ch)
                continue
            if tag in _SKIP:
                continue
            if tag == q("w:r"):
                walk(ch, run=ch)
                continue
            if tag == q("w:t"):
                t = ch.text or ""
                parts.append(t)
                if run is not None:
                    bold, hl, sh = _run_props(run)
                    n = len(t.strip())
                    fmt.text_chars += n
                    if bold:
                        fmt.bold_chars += n
                    if hl and t.strip():
                        fmt.highlights.add(hl)
                    if sh and t.strip():
                        fmt.shading.add(sh)
            elif tag == q("w:tab"):
                parts.append("\t")
            elif tag in (q("w:br"), q("w:cr")):
                parts.append("\n")
            elif tag == q("w:noBreakHyphen"):
                parts.append("-")
            else:
                walk(ch, run)

    walk(p)
    ppr = p.find(q("w:pPr"))
    if ppr is not None:
        sh = ppr.find(q("w:shd"))
        if sh is not None and sh.get(q("w:fill")) and sh.get(q("w:fill")).lower() != "auto":
            fmt.shading.add(sh.get(q("w:fill")))
    return "".join(parts), fmt, textboxes


def paragraph_style_and_numpr(p) -> tuple[Optional[str], Optional[str], Optional[int]]:
    ppr = p.find(q("w:pPr"))
    if ppr is None:
        return None, None, None
    ps = ppr.find(q("w:pStyle"))
    style = ps.get(q("w:val")) if ps is not None else None
    numpr = ppr.find(q("w:numPr"))
    if numpr is None:
        return style, None, None
    n = numpr.find(q("w:numId"))
    lv = numpr.find(q("w:ilvl"))
    return style, (n.get(q("w:val")) if n is not None else None), (int(lv.get(q("w:val"))) if lv is not None else None)


def cell_props(tc) -> tuple[int, Optional[str], Optional[str]]:
    """(gridSpan, vMerge, sombreado) de una celda. vMerge: 'restart', 'continue' o None."""
    tcpr = tc.find(q("w:tcPr"))
    if tcpr is None:
        return 1, None, None
    gs = tcpr.find(q("w:gridSpan"))
    span = int(gs.get(q("w:val"))) if gs is not None else 1
    vm = tcpr.find(q("w:vMerge"))
    vmerge = None
    if vm is not None:
        vmerge = "restart" if vm.get(q("w:val")) == "restart" else "continue"
    sh = tcpr.find(q("w:shd"))
    fill = sh.get(q("w:fill")) if sh is not None else None
    return span, vmerge, (fill if fill and fill.lower() != "auto" else None)


def table_grid_cols(tbl) -> int:
    grid = tbl.find(q("w:tblGrid"))
    return len(grid.findall(q("w:gridCol"))) if grid is not None else 0


def body_children(root):
    """Hijos de bloque del cuerpo, en orden, descendiendo en controles de contenido."""
    body = root.find(q("w:body"))
    if body is None:
        return

    def walk(el):
        for ch in el:
            if ch.tag in (q("w:p"), q("w:tbl")):
                yield ch
            elif ch.tag in (q("w:sdt"),):
                content = ch.find(q("w:sdtContent"))
                if content is not None:
                    yield from walk(content)
            elif ch.tag == q("w:customXml"):
                yield from walk(ch)

    yield from walk(body)


def core_properties(pkg: DocxPackage) -> dict:
    """Metadatos permitidos de ``docProps/core.xml``.

    Solo título, asunto y fechas. Autor y último editor no se leen nunca
    (sección 10.2: las salidas no copian metadatos personales).
    """
    root = pkg.parts.get("docProps/core.xml")
    out: dict = {}
    if root is None:
        return out
    for key, tag in (("title", "dc:title"), ("subject", "dc:subject"), ("created", "dcterms:created"), ("modified", "dcterms:modified")):
        el = root.find(q(tag))
        if el is not None and (el.text or "").strip():
            out[key] = el.text.strip()
    return out
