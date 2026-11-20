"""Vista minimizada del informe de lectura Word (AUT-1: D-07 y C-07b).

Principio: la IR es un artefacto confidencial y conserva el texto exacto como
evidencia. El informe es una vista derivada, que puede compartirse y no
reproduce texto libre salvo con ``include_text=True``.

En el informe:

- Se conserva la estructura: ubicación, índice y tipo de bloque, estilo,
  numeración, marcas de formato, dimensiones y celdas combinadas de tablas,
  roles de tabla y sus motivos.
- Un bloque reconocido enlaza con los elementos de la IR que salen de él
  (``ir_refs``); su texto ya está en la IR.
- El texto de párrafos, celdas, encabezados, pies, cuadros de texto y
  elementos no resueltos se sustituye por longitud, HMAC (si hay clave) y
  señales del léxico y la estructura (``features``). No hay detectores de
  correos ni teléfonos: esa señal ya revelaría el tipo de dato.
- Los detalles de los no resueltos los genera el lector (identificadores,
  códigos, ubicaciones) y se conservan.
"""

from __future__ import annotations

from survey_sim.ir.models import Instrument, Rule
from survey_sim.util.privacy import TextProtector

from .blocks import DocumentBlocks, ParagraphBlock
from .lexicon import Lexicon


def text_features(text: str, lex: Lexicon) -> dict:
    """Señales sin contenido: qué reconoce el léxico y recuento por clase de carácter."""
    split = lex.split(text)
    letters = sum(ch.isalpha() for ch in text)
    digits = sum(ch.isdigit() for ch in text)
    spaces = sum(ch.isspace() for ch in text)
    return {
        "empty": not text.strip(),
        "label_pattern": lex.match_label(text) is not None,
        "instruction_tags": sorted({s.kind.value for s in split.segments}),
        "navigation_keyword": lex.has_navigation_keyword(text),
        "chars": {"letters": letters, "digits": digits, "spaces": spaces, "other": len(text) - letters - digits - spaces},
    }


def _text_view(text: str, lex: Lexicon, prot: TextProtector, include_text: bool) -> dict:
    if include_text:
        return {"text": text}
    return {"text_info": {**prot.describe(text), "features": text_features(text, lex)}}


def ir_locator_index(inst: Instrument) -> dict[str, list[str]]:
    """Localizador → identificadores de los elementos de la IR que salen de él."""
    idx: dict[str, list[str]] = {}

    def add(loc: str, ref: str) -> None:
        idx.setdefault(loc, []).append(ref)

    for s in inst.sections:
        add(s.provenance.locator, f"section:{s.id}")
        for it in s.items:
            add(it.provenance.locator, f"item:{it.id}")
            if it.universe is not None:
                add(it.universe.provenance.locator, f"universe:{it.id}")
            for o in it.options:
                add(o.provenance.locator, f"option:{it.id}:{o.code}")
            if it.grid is not None:
                add(it.grid.provenance.locator, f"grid:{it.id}")
    for r in inst.rules:
        add(r.provenance.locator, f"{'rule' if isinstance(r, Rule) else 'opaque_rule'}:{r.id}")
    for t in inst.terminations:
        add(t.provenance.locator, f"termination:{t.code}")
    return idx


def _by_ancestor(idx: dict[str, list[str]]) -> dict[str, set[str]]:
    """Referencias de cada localizador y de todos sus descendientes («loc» o «loc/…»).

    Equivale a recorrer el índice con ``startswith`` para cada bloque, pero en tiempo
    lineal (V: la búsqueda anterior era cuadrática y dominaba la lectura de documentos
    grandes). El resultado es idéntico.
    """
    out: dict[str, set[str]] = {}
    for loc, refs in idx.items():
        parts = loc.split("/")
        for k in range(1, len(parts) + 1):
            out.setdefault("/".join(parts[:k]), set()).update(refs)
    return out


def _refs_for(locator: str, anc: dict[str, set[str]]) -> list[str]:
    return sorted(anc.get(locator, ()))


def blocks_view(doc: DocumentBlocks, inst: Instrument, lex: Lexicon, prot: TextProtector, include_text: bool) -> list[dict]:
    idx = _by_ancestor(ir_locator_index(inst))
    out = []
    for b in doc.blocks:
        if isinstance(b, ParagraphBlock):
            out.append({
                "index": b.index,
                "kind": "paragraph",
                "locator": b.locator,
                "style": b.style_name or b.style_id,
                "numbering": b.numbering,
                "format_marks": b.marks.as_dict(),
                "ir_refs": _refs_for(b.locator, idx),
                **_text_view(b.text, lex, prot, include_text),
            })
        else:
            out.append({
                "index": b.index,
                "kind": "table",
                "locator": b.locator,
                "rows": b.n_rows,
                "cols": b.n_cols,
                "vmerge": [[c.vmerge for c in row] for row in b.rows],
                "format_marks": [[c.marks.as_dict() for c in row] for row in b.rows],
                "ir_refs": _refs_for(b.locator, idx),
                "cells": [[_text_view(c.text, lex, prot, include_text) for c in row] for row in b.rows],
            })
    return out


def side_texts_view(doc: DocumentBlocks, lex: Lexicon, prot: TextProtector, include_text: bool) -> list[dict]:
    out = []
    for s in doc.side_texts:
        row = {"kind": s.kind, "locator": s.locator}
        if include_text:
            row["text"] = s.text
        else:
            row.update(prot.describe(s.text))
        out.append(row)
    return out


def unresolved_view(inst: Instrument, lex: Lexicon, prot: TextProtector, include_text: bool) -> list[dict]:
    """Vista de ``Instrument.unresolved``: igual que la IR con ``include_text``; sin el texto en otro caso."""
    out = []
    for u in inst.unresolved:
        d = u.model_dump(mode="json")
        if not include_text:
            text = d.pop("text")
            d["text_info"] = {**prot.describe(text), "features": text_features(text, lex)}
        out.append(d)
    return out


def origin_counts(inst: Instrument) -> dict:
    validation = sum(1 for u in inst.unresolved if u.code.startswith("validation:"))
    return {"reading": len(inst.unresolved) - validation, "validation": validation}

