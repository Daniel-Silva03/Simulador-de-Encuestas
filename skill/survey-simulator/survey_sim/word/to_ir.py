"""Conversión del documento leído a ``Instrument(role=design)`` (sección 5, paso 5).

Usa exclusivamente los modelos de la IR (2.0.0). Lo no resuelto queda en
``Instrument.unresolved``; el informe de lectura (``ReadReport``) lo presenta
leyéndolo de la IR, sin guardar una copia propia.

Certeza:

- ``explicit``: reconocido por patrón o por estructura, sin ambigüedad.
- ``inferred``: deducido de texto libre, de varios párrafos o de una firma
  de tabla heurística.
- ``opaque``: navegación con palabras clave que ningún patrón reconoce
  (``OpaqueRule``).

El formato (negrita, resaltado, sombreado) va en ``format_marks`` y no
cambia ni la certeza ni la interpretación.
"""

from __future__ import annotations

import json
import pathlib
import re
from dataclasses import asdict, dataclass
from typing import Optional

from survey_sim.ir.conditions import Const, InSet, Lit
from survey_sim.ir.models import (
    Certainty,
    FormatMarks as IRFormatMarks,
    Grid,
    GridRow,
    Instruction,
    Instrument,
    Item,
    ItemKind,
    ItemType,
    OpaqueRule,
    Option,
    OptionRouting,
    Provenance,
    Role,
    Rule,
    RuleKind,
    Section,
    Source,
    StorageType,
    Study,
    Termination,
    Text,
    Universe,
    UnresolvedEntry,
    Variable,
)
from survey_sim.ir.serialize import ir_hash
from survey_sim.ir.validate import IRValidationError, errors, validate_instrument
from survey_sim.util.privacy import TextProtector
from survey_sim.util.validation_unresolved import with_validation_unresolved

from .blocks import DocumentBlocks, FormatMarks, ParagraphBlock, build_blocks, clean_text
from .docx_xml import open_docx
from .items import ParsedDocument, ParsedItem, assemble, merge_marks
from .lexicon import Lexicon, load_lexicon
from .report_view import blocks_view, origin_counts, side_texts_view, unresolved_view
from .routing_text import termination_code

ADAPTER = "word"
#: 0.3.0 (AUT-1.1): AUT-1 cambió la salida para documentos con duplicados (D-01)
#: y con saltos de párrafo sin condición (nota D-12). Para documentos sin esos
#: casos, la IR solo difiere de la 0.2.0 en este campo (equivalencia normalizada).
#: 0.4.0 (v1.1, §8.3, H-15): los patrones de dígitos aceptan solo [0-9] y un
#: dígito de otro sistema de escritura se registra como non_ascii_digit. Para
#: documentos solo con dígitos ASCII, la IR solo difiere en este campo.
ADAPTER_VERSION = "0.4.0"

#: D-12: marca de los saltos escritos sin condición (condición verdadera inferida).
IMPLICIT_CONDITION_NOTE = "word:implicit_condition=unconditional"

#: Decisiones pendientes que el lector Word no resuelve (se arrastran del paso 1).
PENDING_DECISIONS = (
    {
        "id": "functions_registry",
        "stage": "pasos 3-4 (adaptador GIM y parser)",
        "detail": "Funciones conocidas (CARACTERES, MENCIONES, REGEXP, VALOR) pendientes de registro e implementación segura; hoy evalúan U.",
    },
    {
        "id": "multi_value_comparisons",
        "stage": "pasos 3-4",
        "detail": "Comparaciones sobre respuestas múltiples: hoy evalúan U.",
    },
    {
        "id": "termination_kind_unrecognized",
        "stage": "paso 3 y revisión de la IR",
        "detail": "Incidencias de terminación cuyo tipo no está en el léxico: no se crean en la IR y quedan en unresolved.",
    },
)

_STORAGE = {
    ItemType.SINGLE: StorageType.SINGLE,
    ItemType.MULTIPLE: StorageType.MULTIPLE,
    ItemType.NUMERIC: StorageType.NUMERIC,
    ItemType.TEXT: StorageType.TEXT,
    ItemType.DATE: StorageType.DATE,
    ItemType.TIME: StorageType.TIME,
}


@dataclass(frozen=True)
class ReadReport:
    """Informe de lectura: vista derivada y minimizada de la IR (AUT-1, P-2).

    La IR es la única fuente de lo no resuelto. ``unresolved_view`` es su
    vista: idéntica a ``Instrument.unresolved`` con ``include_text``; sin el
    texto libre (longitud, HMAC si hay clave y señales) en otro caso. El
    informe queda ligado a su IR por ``ir_sha256`` y se niega a presentarse
    con otra.
    """

    ir_sha256: str
    source: dict
    lexicon: dict
    privacy: dict
    counts: dict
    blocks: list
    table_roles: list
    version_tables: list
    side_texts: list
    validation_warnings: list
    pending_decisions: list
    unresolved_view: list

    def to_dict(self, instrument: Instrument) -> dict:
        if ir_hash(instrument) != self.ir_sha256:
            raise ValueError("el informe no corresponde a esta IR")
        d = asdict(self)
        d["unresolved"] = d.pop("unresolved_view")
        return d

    def to_json_bytes(self, instrument: Instrument) -> bytes:
        text = json.dumps(self.to_dict(instrument), sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
        return (text + "\n").encode("utf-8")


@dataclass(frozen=True)
class WordReadResult:
    instrument: Instrument
    report: ReadReport

    @property
    def unresolved(self) -> tuple[UnresolvedEntry, ...]:
        return self.instrument.unresolved

    def report_json_bytes(self) -> bytes:
        return self.report.to_json_bytes(self.instrument)


# --------------------------------------------------------------------------


def _fm(marks: list[FormatMarks]) -> Optional[IRFormatMarks]:
    """Marcas de formato estructuradas; ``None`` si no hay ninguna marca."""
    m = merge_marks(marks)
    if m.empty:
        return None
    return IRFormatMarks(bold=m.bold, highlights=m.highlights, shading=m.shading)


def _item_type(it: ParsedItem) -> tuple[ItemType, bool]:
    """(tipo, es_explícito)."""
    if it.grid is not None:
        return ItemType.GRID, False
    types = list(dict.fromkeys(it.response_types))
    if len(types) == 1:
        return types[0], True
    if len(types) > 1:
        return ItemType.UNKNOWN, False
    if it.options:
        return ItemType.SINGLE, False
    if it.open_boxes:
        return ItemType.TEXT, False
    return ItemType.UNKNOWN, False


def _build(parsed: ParsedDocument, doc: DocumentBlocks, name: str) -> tuple[Instrument, list[dict]]:
    prov = lambda loc: Provenance(source=name, locator=loc)  # noqa: E731
    unresolved = [UnresolvedEntry(code=u.code, text=u.text, provenance=prov(u.locator), detail=u.detail) for u in parsed.unresolved]
    unresolved += _non_ascii_digits(doc, prov)
    items_by_section: dict[str, list[Item]] = {s.id: [] for s in parsed.sections}
    variables: list[Variable] = []
    rules: list = []

    # Universos: condiciones de aplicación leídas del texto, por ítem destino.
    item_ids = {it.id for it in parsed.items}
    universes: dict[str, Universe] = {}
    for c in parsed.conditions:
        if c.target not in item_ids:
            continue  # ya está en unresolved como routing_target_not_found
        if c.target in universes:
            unresolved.append(
                UnresolvedEntry(
                    code="multiple_universe_conditions",
                    text=c.raw,
                    provenance=prov(c.locator),
                    detail=f"{c.target} ya tiene un universo; no se combinan condiciones sin regla documentada",
                )
            )
            continue
        universes[c.target] = Universe(
            provenance=prov(c.locator),
            certainty=Certainty.INFERRED,
            condition=InSet(var=c.source, items=tuple(Lit(value=x) for x in c.codes)),
            raw=c.raw,
            notes=(f"word:{c.kind}", f"word:place={c.place}"),
        )

    for it in parsed.items:
        itype, type_explicit = _item_type(it)
        weak = list(it.weak)
        if it.continued:
            weak.append("enunciado en varios párrafos")
        if not type_explicit and itype is not ItemType.UNKNOWN:
            weak.append("tipo inferido de la tabla")
        certainty = Certainty.INFERRED if weak else Certainty.EXPLICIT
        options = tuple(
            Option(
                provenance=prov(o.locator),
                certainty=Certainty.EXPLICIT,
                code=o.code,
                label=Text(raw=o.label_raw, clean=o.label_clean),
                is_missing_code=o.is_missing_code,
                opens_text=o.opens_text,
                routing=(
                    OptionRouting(action=o.routing.action, target=o.routing.target, raw=o.routing.raw)
                    if o.routing
                    else None
                ),
                notes=tuple(f"instruction:{x}" for x in o.extra_instructions),
                format_marks=_fm([o.marks]),
            )
            for o in it.options
        )
        options = _dedupe_options(options, unresolved)
        grid = None
        row_items: list[Item] = []
        if it.grid is not None:
            g = it.grid
            shared = _dedupe_options(tuple(
                Option(
                    provenance=prov(f"{g.locator}/r0/c{col}"),
                    certainty=Certainty.EXPLICIT,
                    code=code,
                    label=Text(raw=label, clean=label),
                )
                for col, code, label in g.header
                if code is not None
            ), unresolved)
            rows = []
            for k, (rtext, rloc, rmarks) in enumerate(g.rows, 1):
                rid = f"{it.id}.r{k}"
                rows.append(GridRow(item_id=rid, label=Text(raw=rtext, clean=rtext)))
                row_items.append(
                    Item(
                        provenance=prov(rloc),
                        certainty=Certainty.INFERRED,
                        id=rid,
                        label=None,
                        text=Text(raw=rtext, clean=rtext),
                        type=ItemType.SINGLE,
                        options=shared,
                        kind=ItemKind.SUBSTANTIVE,
                        notes=("word:grid_row",),
                        format_marks=_fm([rmarks]),
                    )
                )
                variables.append(
                    Variable(provenance=prov(rloc), certainty=Certainty.INFERRED, name=rid, item_id=rid, storage_type=StorageType.SINGLE)
                )
            grid = Grid(provenance=prov(g.locator), certainty=g.certainty, rows=tuple(rows), shared_options=shared, orientation="rows")
        instructions = tuple(Instruction(kind=i.kind, text=i.text) for i in it.instructions if i.text)
        item = Item(
            provenance=prov(it.locator),
            certainty=certainty,
            id=it.id,
            label=it.label,
            text=Text(raw="\n".join(it.raw_parts), clean=clean_text(" ".join(x for x in it.statement_parts if x))),
            instructions=instructions,
            type=itype,
            options=options,
            universe=universes.get(it.id),
            grid=grid,
            kind=ItemKind.CALCULATED if it.calculated else ItemKind.SUBSTANTIVE,
            notes=tuple(f"word:{w}" for w in dict.fromkeys(weak)),
            format_marks=_fm(it.marks),
        )
        items_by_section[it.section_id].append(item)
        items_by_section[it.section_id].extend(row_items)
        if it.grid is None:
            variables.append(
                Variable(
                    provenance=prov(it.locator),
                    certainty=Certainty.INFERRED,
                    name=it.id,
                    item_id=it.id,
                    storage_type=_STORAGE.get(itype, StorageType.UNKNOWN),
                )
            )
        if it.recode is not None:
            text, loc = it.recode
            rules.append(
                Rule(
                    provenance=prov(loc),
                    certainty=Certainty.INFERRED,
                    id=f"W-R{len(rules) + 1}",
                    kind=RuleKind.RECODE,
                    target=it.id,
                    raw=text,
                    notes=("word:recode_matrix",),
                )
            )

    for r in parsed.rule_texts:
        if r.kind == "goto":
            rules.append(
                Rule(
                    provenance=prov(r.locator),
                    certainty=Certainty.INFERRED,
                    id=f"W-R{len(rules) + 1}",
                    kind=RuleKind.SKIP,
                    # D-12: el texto indica un salto sin condición escrita; la condición
                    # verdadera es una interpretación (inferred), no una condición del documento.
                    when=Const(value=True),
                    target=r.target,
                    raw=r.raw,
                    notes=(f"word:place={r.place}", f"word:after_item={r.after_item}", IMPLICIT_CONDITION_NOTE),
                )
            )
        else:
            rules.append(
                Rule(
                    provenance=prov(r.locator),
                    certainty=Certainty.INFERRED,
                    id=f"W-R{len(rules) + 1}",
                    kind=RuleKind.TERMINATE,
                    target=termination_code(r.termination_label) if r.termination_label else None,
                    raw=r.raw,
                    notes=(f"word:place={r.place}", f"word:after_item={r.after_item}"),
                )
            )

    # Navegación no reconocida: regla opaca, sin destino, condición ni acción.
    for k, o in enumerate(parsed.opaque_navigation, 1):
        rid = f"W-O{k}"
        rules.append(
            OpaqueRule(
                provenance=prov(o.locator),
                certainty=Certainty.OPAQUE,
                id=rid,
                raw=o.text,
                reason="unrecognized_navigation",
                notes=(f"word:place={o.place}",),
            )
        )
        for i, u in enumerate(unresolved):
            if u.code == "unparsed_navigation" and u.provenance.locator == o.locator and u.text == o.text and "regla opaca" not in u.detail:
                unresolved[i] = u.model_copy(update={"detail": f"{u.detail}; regla opaca {rid}"})
                break

    terminations = tuple(
        Termination(provenance=prov(t.locator), certainty=Certainty.EXPLICIT, code=t.code, label=t.label, kind=t.kind)
        for t in parsed.terminations.values()
        if t.kind is not None
    )
    sections = tuple(
        Section(provenance=prov(s.locator), certainty=s.certainty, id=s.id, title=s.title, items=tuple(items_by_section[s.id]))
        for s in parsed.sections
    )
    core = doc.core
    inst = Instrument(
        role=Role.DESIGN,
        source=Source(name=name, sha256=doc.sha256, format="docx", adapter=ADAPTER, adapter_version=ADAPTER_VERSION),
        study=Study(title=core.get("title"), date=core.get("created")),
        sections=sections,
        variables=tuple(variables),
        rules=tuple(rules),
        terminations=terminations,
        unresolved=tuple(unresolved),
    )
    issues = validate_instrument(inst)
    errs = errors(issues)
    if errs:
        raise IRValidationError(errs)
    # D-09: avisos pertinentes del validador a unresolved, sin duplicar lo que el lector ya registró.
    inst = with_validation_unresolved(inst, issues, _reader_equivalents(parsed))
    warnings = [asdict(i) for i in issues if i.level == "warning"]
    return inst, warnings


_REF_DETAIL = re.compile(r"^(?:destino|pregunta) (\S+) no existe$")


def _non_ascii_digits(doc: DocumentBlocks, prov) -> list[UnresolvedEntry]:
    """H-15: textos con dígitos decimales que no son 0-9 (otros sistemas de escritura).

    Los patrones del lector solo aceptan [0-9]: estos dígitos no se leen como
    códigos ni etiquetas. Se registra un elemento por párrafo o celda, en orden
    del documento. El texto queda en la IR (confidencial); los informes lo
    minimizan como el resto de ``unresolved``.
    """
    out: list[UnresolvedEntry] = []

    def check(locator: str, text: str) -> None:
        bad = sorted({c for c in text if c.isdecimal() and not ("0" <= c <= "9")})
        if bad:
            cps = ", ".join(f"U+{ord(c):04X}" for c in bad)
            out.append(UnresolvedEntry(code="non_ascii_digit", text=text, provenance=prov(locator),
                                       detail=f"dígitos no ASCII ({cps}); solo se aceptan 0-9"))

    for b in doc.blocks:
        if isinstance(b, ParagraphBlock):
            check(b.locator, b.text)
        else:
            for row in b.rows:
                for c in row:
                    if not c.is_continuation:
                        check(c.locator, c.text)
    return out


def _reader_equivalents(parsed: ParsedDocument) -> set[tuple[str, str]]:
    """(localizador, nombre) de las referencias inexistentes que el lector ya registró."""
    out: set[tuple[str, str]] = set()
    for u in parsed.unresolved:
        if u.code == "routing_target_not_found":
            m = _REF_DETAIL.match(u.detail)
            if m:
                out.add((u.locator, m.group(1)))
    return out


def _dedupe_options(options: tuple[Option, ...], unresolved: list[UnresolvedEntry]) -> tuple[Option, ...]:
    """Red de seguridad (D-01): si aún quedara un código repetido, se conserva el primero.

    El lector ya evita los repetidos; esta comprobación garantiza que la
    lectura nunca aborte por ellos.
    """
    seen: dict[str, str] = {}
    out = []
    for o in options:
        if o.code in seen:
            unresolved.append(UnresolvedEntry(code="internal_duplicate", text=o.label.raw, provenance=o.provenance,
                                              detail=f"código {o.code} repetido; se conserva el de {seen[o.code]}"))
            continue
        seen[o.code] = o.provenance.locator
        out.append(o)
    return tuple(out)


def read_docx_bytes(
    data: bytes,
    name: str,
    lexicon: Optional[Lexicon] = None,
    *,
    include_text: bool = False,
    hmac_key: Optional[bytes] = None,
) -> WordReadResult:
    """Lee un .docx. El informe no incluye texto libre salvo con ``include_text=True``.

    ``hmac_key`` (32 bytes, fuera del repositorio) activa el HMAC de los textos
    en el informe. La IR no depende de la clave.
    """
    prot = TextProtector(hmac_key)
    lex = lexicon or load_lexicon()
    pkg = open_docx(data)
    doc = build_blocks(pkg)
    parsed = assemble(doc, lex)
    inst, warnings = _build(parsed, doc, name)
    report = ReadReport(
        ir_sha256=ir_hash(inst),
        source={"name": name, "sha256": doc.sha256},
        lexicon={"name": lex.spec.name, "version": lex.spec.version, "sha256": lex.sha256},
        privacy=prot.report_block(include_text),
        counts={
            "blocks": len(doc.blocks),
            "paragraphs": sum(1 for b in doc.blocks if isinstance(b, ParagraphBlock)),
            "tables": sum(1 for b in doc.blocks if not isinstance(b, ParagraphBlock)),
            "vmerge_continuation_cells": doc.vmerge_cells,
            "sections": len(inst.sections),
            "items": sum(len(s.items) for s in inst.sections),
            "universes": sum(1 for s in inst.sections for i in s.items if i.universe is not None),
            "rules": sum(1 for r in inst.rules if isinstance(r, Rule)),
            "opaque_rules": sum(1 for r in inst.rules if isinstance(r, OpaqueRule)),
            "terminations": len(inst.terminations),
            "unresolved": len(inst.unresolved),
            "unresolved_by_origin": origin_counts(inst),
        },
        blocks=blocks_view(doc, inst, lex, prot, include_text),
        table_roles=[asdict(r) for r in parsed.table_roles],
        version_tables=[{"locator": loc, "labels": list(v)} for loc, v in parsed.version_tables],
        side_texts=side_texts_view(doc, lex, prot, include_text),
        validation_warnings=warnings,
        pending_decisions=list(PENDING_DECISIONS),
        unresolved_view=unresolved_view(inst, lex, prot, include_text),
    )
    return WordReadResult(inst, report)


def read_docx(
    path: str | pathlib.Path,
    lexicon: Optional[Lexicon] = None,
    *,
    include_text: bool = False,
    hmac_key: Optional[bytes] = None,
) -> WordReadResult:
    """Lee un .docx sin modificarlo: abre en solo lectura y trabaja sobre los bytes."""
    p = pathlib.Path(path)
    with p.open("rb") as fh:
        data = fh.read()
    return read_docx_bytes(data, p.name, lexicon, include_text=include_text, hmac_key=hmac_key)
