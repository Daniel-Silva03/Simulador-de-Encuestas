"""Conversión del programa GIM leído a ``Instrument(role=program)`` (IR 2.0.0).

Reglas de esta etapa:

- Ninguna expresión se evalúa ni se ejecuta. Cada condición pasa por el
  parser del paso 4 (``parse_condition``): lo reconocido entra como AST de
  la IR y lo no reconocido como ``Opaque`` con su texto exacto y motivo.
  Las notas del elemento registran el estado del parser, su versión y sus
  diagnósticos (código y posición dentro del texto de la condición).
- Que una condición quede ``parsed`` no confirma la semántica de la regla
  ni del campo GIM que la contiene: la certeza sigue en ``inferred``.
- ``valdefs[].xpr`` solo se diagnostica con ``parse_value``: ``Rule.raw``
  conserva el texto y no se crea ningún valor evaluable.
- Toda regla depende de semántica no confirmada (registro
  ``semantics/gim.yaml`` o campos sin entrada en el registro), así que su
  certeza es ``inferred`` como mínimo (D15). Ninguna regla es ``explicit``.
- Si un elemento no puede representarse sin inventar algo (condición
  vacía, valor de ``acc`` desconocido, cualquier destino especial ``@…@``),
  queda como ``OpaqueRule`` con el elemento original en ``raw``. Ningún
  destino especial se convierte en terminación por su nombre.
- ``Option.anchored``, ``is_missing_code`` e ``is_exclusive`` son booleanos
  en la IR: ``False`` no distingue «no» de «desconocido». El lector solo
  pone ``True`` con evidencia (``fijo=1`` + hipótesis ``ord``) y anota en
  cada ítem con opciones que ``False`` significa «sin evidencia».
- Lo que la IR no puede representar se conserva en el informe y se
  documenta como limitación.
- Los identificadores de ítems y variables los asigna un registro global
  (``ids.py``, AUT-2): se reservan antes todos los nombres originales de
  variable, después se asignan las variables y por último las grillas,
  comprobando cada candidato. Toda asignación que tuvo que saltar un
  candidato ocupado queda en ``unresolved`` como ``identifier_collision``.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Optional

from survey_sim.ir.conditions import Opaque
from survey_sim.ir.models import (
    Certainty,
    DisplayRotation,
    ExternalDep,
    ExternalDepKind,
    Finding,
    FindingCategory,
    FindingRef,
    FindingScope,
    Grid,
    GridRow,
    Instruction,
    InstructionKind,
    Instrument,
    Item,
    ItemKind,
    ItemType,
    OpaqueRule,
    Option,
    Provenance,
    Quota,
    Role,
    Rule,
    RuleAction,
    RuleKind,
    Section,
    Severity,
    Source,
    StorageType,
    Study,
    Text,
    UnresolvedEntry,
    Variable,
    derive_evidence_level,
    finding_id,
)
from survey_sim.ir.serialize import ir_hash
from survey_sim.ir.validate import IRValidationError, errors, validate_instrument
from survey_sim.util.privacy import NO_KEY, TextProtector
from survey_sim.util.validation_unresolved import with_validation_unresolved

from .container import ReaderLimits
from .expr_parser import PARSER_VERSION, parse_condition, parse_value
from .ids import Assignment, IdRegistry, describe
from .reader import SPECIAL_TARGET, Obj, RawProgram, Var
from .semantics import Semantics, load_semantics

ADAPTER = "gim"
# 0.2.0 (AUT-2): registro global de identificadores (H-02). Las entradas que
# antes se leían sin error conservan identificadores y reglas; las que
# abortaban por colisión ahora se leen, y toda asignación que salta un
# candidato ocupado se registra como identifier_collision.
# 0.3.0 (v1.1, §8): H-08 (intro y svarenlacecod solo por presencia, longitud y
# HMAC con clave local) y H-09 (avisos del validador en unresolved como
# validation:<código>). Lo demás no cambia (equivalencia normalizada).
ADAPTER_VERSION = "0.3.0"
#: Campos sin semántica documentada que pueden llevar texto libre (v1.1, §8.1).
PRIVATE_NOTE_FIELDS = ("svarenlacecod", "intro")

_STORAGE = {
    ItemType.SINGLE: StorageType.SINGLE,
    ItemType.MULTIPLE: StorageType.MULTIPLE,
    ItemType.NUMERIC: StorageType.NUMERIC,
    ItemType.TEXT: StorageType.TEXT,
    ItemType.DATE: StorageType.DATE,
    ItemType.TIME: StorageType.TIME,
}

#: Campos cuya semántica no está en el registro: se usan como en la tabla de
#: la sección 6, siempre con certeza inferred y con esta marca en las notas.
UNREGISTERED = {
    "saltos": "semantics:saltos=unregistered",
    "valdefs": "semantics:valdefs=unregistered",
    "habil_cnd": "semantics:habil_cnd=unregistered",
    "oculta_cnd": "semantics:oculta_cnd=unregistered",
    "tabitems": "semantics:tabitems_rows=unregistered",
}

PENDING_DECISIONS = (
    {"id": "expression_grammar", "stage": "confirmación", "detail": "El parser (paso 4) solo reconoce la sintaxis observada; la precedencia de NO frente a Y, el operador O, la coma en conjuntos y las funciones siguen opacos. La equivalencia entre códigos de texto y literales enteros está pendiente."},
    {"id": "semantics_registry_hypotheses", "stage": "confirmación del CNC o del proveedor", "detail": "tip, filtros, ord, acc y empty_value_comparison siguen inferred; toda regla dependiente queda inferred."},
    {"id": "unregistered_semantics", "stage": "decisión de especificación", "detail": "saltos, valdefs, habil_cnd, oculta_cnd y la correspondencia filas-variables de tabitems se leen según la tabla de la sección 6, pero no tienen entrada en semantics/gim.yaml. Propuesta: añadirlas al registro."},
    {"id": "special_targets", "stage": "confirmación documental", "detail": "Los destinos especiales de saltos (@RECHAZAR@ y cualquier otro @…@) no tienen significado confirmado. Se conservan como OpaqueRule con el elemento original; no se crea ninguna terminación por el nombre del destino. Para tratarlos como terminación hace falta una entrada confirmada en el registro."},
    {"id": "option_flags_unknown", "stage": "revisión de la IR", "detail": "Option.anchored, is_missing_code e is_exclusive no distinguen falso de desconocido. El lector solo marca True con evidencia; False significa «sin evidencia», nunca «confirmado que no»."},
    {"id": "termination_kind_unrecognized", "stage": "paso 3 y revisión", "detail": "incidencias[] no indica el tipo de terminación (rechazo, no aplica, revisita, completa) de forma documentada; no se crean Termination y cada incidencia queda en unresolved."},
    {"id": "functions_registry", "stage": "pasos 4-5", "detail": "Funciones conocidas pendientes de registro e implementación segura; hoy evalúan U."},
    {"id": "multi_value_comparisons", "stage": "pasos 4-5", "detail": "Comparaciones sobre respuestas múltiples: hoy evalúan U."},
    {"id": "unconfirmed_flags", "stage": "confirmación", "detail": "exc, oculta (sin condición), ln1/ln2, met, nece, gen y guiones se conservan en notas o en el informe sin interpretarse; intro y svarenlacecod, solo por presencia, longitud y HMAC con clave (v1.1, §8.1)."},
)

NOT_REPRESENTABLE = (
    {"structure": "textos condicionales (txts[].refers, reftextosvar)", "reason": "la IR no tiene textos que cambien según una condición", "where": "report.conditional_texts", "proposal": "Text con variantes condicionales (condición Opaque + texto) o una regla de visualización de texto"},
    {"structure": "codcomp decodificado", "reason": "son datos binarios del compilador de GIM; no deben entrar en la IR", "where": "report.codcomp", "proposal": "ninguna; se mantiene fuera por diseño (D10)"},
    {"structure": "flujo[], arbol, arbolflujo", "reason": "duplican la jerarquía de paginas; la especificación los usa solo para verificar", "where": "report.cross_checks", "proposal": "ninguna"},
    {"structure": "versiones[]", "reason": "historial del layout; solo se contrasta la última versión", "where": "report.versions", "proposal": "ninguna"},
    {"structure": "SQL de cuotas (sql_cuota, sql_tab_aux)", "reason": "Quota solo guarda descripción y condición; el SQL nunca se ejecuta", "where": "report.sql", "proposal": "campo Quota.sql_raw opcional si se quiere en la IR"},
    {"structure": "tipo de terminación de incidencias[]", "reason": "Termination.kind es obligatorio y el tipo no está documentado", "where": "unresolved + report.incidences", "proposal": "decisión pendiente ya registrada; o TerminationKind.unknown"},
    {"structure": "codvars[] (listas de códigos con nombre)", "reason": "no hay referencia documentada desde las variables", "where": "report.code_lists", "proposal": "confirmar cómo se enlazan con las variables"},
    {"structure": "Option.anchored / is_missing_code / is_exclusive", "reason": "booleanos que no distinguen 'falso' de 'desconocido'; False solo significa que no hay evidencia (la semántica de ord sigue inferred)", "where": "notas del ítem gim:option_flags_false_means_no_evidence y notas gim:fijo / gim:exc de cada opción", "proposal": "valores de tres estados (true / false / unknown) o certeza propia por bandera"},
    {"structure": "destinos especiales de saltos (@RECHAZAR@, @…@)", "reason": "su significado no está confirmado; un RuleKind daría una afirmación semántica", "where": "OpaqueRule con reason unconfirmed_special_target", "proposal": "entrada en semantics/gim.yaml; con confirmación, regla terminate"},
)

_TAG = re.compile(r"<[^>]*>")

OPTION_FLAGS_NOTE = "gim:option_flags_false_means_no_evidence=anchored,is_missing_code,is_exclusive"


def clean_html(s: str) -> str:
    """Texto limpio para lectura: sin etiquetas HTML, con entidades resueltas y espacios normalizados."""
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", s))).strip()


def _canon(o: Any) -> str:
    return json.dumps(o, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class GimReadReport:
    ir_sha256: str
    source: dict
    semantics: dict
    limits: dict
    container: dict
    counts: dict
    classification: dict
    hierarchy: list
    expressions: list
    expression_counts: dict
    sql: list
    codcomp: dict
    external_references: dict
    conditional_texts: list
    incidences: list
    code_lists: list
    cross_checks: dict
    versions: list
    raw_extra: list
    dropped_personal_metadata: list
    not_mapped: dict
    not_representable: list
    pending_decisions: list
    validation_warnings: list

    def to_dict(self, instrument: Instrument) -> dict:
        if ir_hash(instrument) != self.ir_sha256:
            raise ValueError("el informe no corresponde a esta IR")
        d = asdict(self)
        d["unresolved"] = [u.model_dump(mode="json") for u in instrument.unresolved]
        return d

    def to_json_bytes(self, instrument: Instrument) -> bytes:
        return (json.dumps(self.to_dict(instrument), sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str) + "\n").encode("utf-8")


@dataclass(frozen=True)
class ProgramReadResult:
    instrument: Instrument
    report: GimReadReport

    @property
    def unresolved(self) -> tuple[UnresolvedEntry, ...]:
        return self.instrument.unresolved

    def report_json_bytes(self) -> bytes:
        return self.report.to_json_bytes(self.instrument)


class _Builder:
    def __init__(self, raw: RawProgram, sem: Semantics, protector: TextProtector = NO_KEY):
        self.raw = raw
        self.sem = sem
        self.protector = protector
        self.prov = lambda loc: Provenance(source=raw.name, locator=loc)  # noqa: E731
        self.unresolved: list[UnresolvedEntry] = [
            UnresolvedEntry(code=i.code, text=i.text, provenance=self.prov(i.path), detail=i.detail) for i in raw.issues
        ]
        self.rules: list = []
        self.var_ids: dict[int, str] = {}  # id(Var) -> id en la IR
        self.var_assign: dict[int, Assignment] = {}  # id(Var) -> asignación
        self.grid_assign: dict[int, Assignment] = {}  # id(Preg) -> asignación
        self.parse_stats: dict[str, int] = {}
        self.ext_mentions = {}
        for m in raw.function_mentions:
            if m.external:
                self.ext_mentions.setdefault(m.path, []).append(m.name)

    def private_note(self, key: str, value: Any) -> str:
        """H-08: presencia, longitud y HMAC (solo con clave). Nunca el contenido ni SHA-256 sin clave."""
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
        d = self.protector.describe(text)
        note = f"gim:{key}=presente;len={d['length']}"
        if "hmac_sha256" in d:
            note += f";hmac_sha256={d['hmac_sha256']}"
        return note

    def unres(self, code: str, path: str, text: str = "", detail: str = "") -> None:
        self.unresolved.append(UnresolvedEntry(code=code, text=text, provenance=self.prov(path), detail=detail))

    def trad(self, seq: Any, path: str) -> Optional[str]:
        """Texto en el idioma por defecto; si falta, el primero disponible (anotado)."""
        if not isinstance(seq, list):
            return None
        valid = [t for t in seq if isinstance(t, dict) and isinstance(t.get("trad"), str)]
        if not valid:
            return None
        lang = self.raw.default_language
        for t in valid:
            if t.get("id") == lang:
                return t["trad"]
        self.unres("text_language_fallback", path, valid[0]["trad"][:80], f"no hay texto en el idioma por defecto {lang}; se usa el primero disponible")
        return valid[0]["trad"]

    def condition(self, raw_cnd: str, path: str) -> tuple[Any, tuple[str, ...]]:
        """Condición de la IR y notas del parser. ``raw_cnd`` nunca está vacío aquí.

        El resultado nunca es una constante inventada: un texto inválido o no
        soportado queda como ``Opaque`` con el texto completo.
        """
        r = parse_condition(raw_cnd, path)
        notes = [f"gim:parse_status={r.status}", f"gim:parser={PARSER_VERSION}"]
        notes += [f"gim:parse_diag={d.code}@{d.start}-{d.end}" for d in r.diagnostics]
        self.parse_stats[r.status] = self.parse_stats.get(r.status, 0) + 1
        return r.condition, tuple(notes)

    # Ítems --------------------------------------------------------------------

    def item_type(self, v: Var) -> tuple[ItemType, list[str]]:
        tip = v.obj.get("tip")
        h = self.sem["tip"]
        notes = [h.tag]
        if tip is None:
            self.unres("missing_field", v.path, v.nom, "variable sin tip")
            return ItemType.UNKNOWN, notes
        meaning = h.meaning if isinstance(h.meaning, dict) else {}
        name = meaning.get(str(tip))
        try:
            return ItemType(name), notes + [f"gim:tip={tip}"]
        except ValueError:
            self.unres("tip_value_unrecognized", f"{v.path}.tip", str(tip), "valor de tip sin entrada en el registro de semántica")
            return ItemType.UNKNOWN, notes + [f"gim:tip={tip}"]

    def options(self, cods: list[Obj], owner: str, *, linked: bool = False) -> tuple[Option, ...]:
        out = []
        seen: set[str] = set()
        ord_h = self.sem["ord"]
        for c in cods:
            code = c.get("def")
            if code is None or code in seen:
                continue  # ya registrado en unresolved por el lector
            seen.add(code)
            label = self.trad(c.get("noms"), f"{c.path}.noms")
            notes: list[str] = ["gim:opcion_de_varenlazadas"] if linked else []
            # Opciones tomadas de varenlazadas: el enlace por nomvar es una inferencia.
            certainty = Certainty.INFERRED if linked else Certainty.EXPLICIT
            display_if = None
            cnd = c.get("cnd")
            if isinstance(cnd, str) and cnd.strip():
                display_if, pnotes = self.condition(cnd, f"{c.path}.cnd")
                notes += pnotes
                certainty = Certainty.INFERRED
            anchored = False
            if c.get("fijo") == 1 and isinstance(ord_h.meaning, dict) and ord_h.meaning.get("anchored_option_when_fijo") == 1:
                anchored = True
                certainty = Certainty.INFERRED
                notes.append(ord_h.tag)
            for k in ("fijo", "exc", "varab"):
                if k in c.f:
                    notes.append(f"gim:{k}={c.f[k]}")
            if label is None:
                self.unres("missing_field", c.path, code, "opción sin texto (noms)")
            out.append(
                Option(
                    provenance=self.prov(c.path),
                    certainty=certainty,
                    code=code,
                    label=Text(raw=label or "", clean=clean_html(label or "")),
                    anchored=anchored,
                    display_if=display_if,
                    notes=tuple(notes),
                )
            )
        return tuple(out)

    def assign_ids(self) -> None:
        """Asigna todos los identificadores antes de construir los ítems.

        Mismo recorrido que ``build_items`` (páginas, preguntas, variables).
        """
        raw = self.raw
        reg = IdRegistry(v.nom for page in raw.pages for preg in page.pregs for v in preg.vars)
        for page in raw.pages:
            for preg in page.pregs:
                for v in preg.vars:
                    a = reg.assign_variable(v.nom)
                    self.var_assign[id(v)] = a
                    self.var_ids[id(v)] = a.assigned
        for page in raw.pages:
            for preg in page.pregs:
                if preg.tabitems is not None:
                    t = preg.tabitems
                    self.grid_assign[id(preg)] = reg.assign_grid(t.get("nom") or f"TAB{preg.num}")

    def collision(self, a: Assignment, path: str) -> None:
        if a.rejected:
            self.unres("identifier_collision", path, a.base, describe(a))

    def build_items(self) -> tuple[tuple[Section, ...], list[Variable]]:
        raw = self.raw
        self.assign_ids()
        camposid = {c.get("nomvar") for c in raw.camposid if c.get("nomvar")}
        varenl = {o.get("nomvar"): cods for o, cods in raw.varenlazadas if o.get("nomvar")}
        sections: list[Section] = []
        variables: list[Variable] = []
        page_seen: dict[int, int] = {}
        for page in raw.pages:
            items: list[Item] = []
            for preg in page.pregs:
                preg_text_raw = "\n".join(
                    t for t in (self.trad(tx.get("txts"), f"{tx.path}.txts") for tx in preg.texts) if t
                )
                for v in preg.vars:
                    a = self.var_assign[id(v)]
                    n = a.occurrence
                    iid = a.assigned
                    self.collision(a, v.path)
                    itype, notes = self.item_type(v)
                    var_text = self.trad(v.obj.get("txtext"), f"{v.path}.txtext")
                    instructions = []
                    if var_text:
                        text_raw = var_text
                        if preg_text_raw:
                            instructions.append(Instruction(kind=InstructionKind.DISPLAY, text=clean_html(preg_text_raw) or preg_text_raw))
                    else:
                        text_raw = preg_text_raw
                        if preg_text_raw:
                            notes.append("gim:texto_de_la_pregunta")
                    if v.nom in camposid:
                        kind = ItemKind.PARADATA
                        notes.append("gim:camposid")
                    elif preg.num is None:
                        kind = ItemKind.SYSTEM
                        notes.append("gim:pregunta_sin_numero")
                    else:
                        kind = ItemKind.SUBSTANTIVE
                    cods = v.cods
                    linked = False
                    if not cods and v.nom in varenl:
                        cods = varenl[v.nom]
                        linked = True
                        notes.append("gim:opciones_de_varenlazadas")
                    rotation = None
                    ordv = v.obj.get("ord")
                    if ordv is not None:
                        notes.append(f"gim:ord={ordv}")
                        h = self.sem["ord"]
                        if isinstance(h.meaning, dict) and ordv == h.meaning.get("rotate_options_when_ord"):
                            rotation = DisplayRotation(rotate=True, certainty=Certainty.INFERRED)
                            notes.append(h.tag)
                    for k in ("oculta", "ln1", "ln2", "gen", "met", "nece"):
                        if k in v.obj.f:
                            notes.append(f"gim:{k}={v.obj.f[k]}")
                    for k in PRIVATE_NOTE_FIELDS:
                        if k in v.obj.f:
                            notes.append(self.private_note(k, v.obj.f[k]))
                    if n > 1:
                        notes.append("gim:nom_repetido")
                    if cods:
                        # False en anchored / is_missing_code / is_exclusive = sin evidencia, no «confirmado que no».
                        notes.append(OPTION_FLAGS_NOTE)
                    items.append(
                        Item(
                            provenance=self.prov(v.path),
                            certainty=Certainty.INFERRED,
                            id=iid,
                            label=v.nom,
                            text=Text(raw=text_raw, clean=clean_html(text_raw)),
                            instructions=tuple(instructions),
                            type=itype,
                            options=self.options(cods, iid, linked=linked),
                            display_rotation=rotation,
                            kind=kind,
                            notes=tuple(dict.fromkeys(notes)),
                        )
                    )
                    variables.append(
                        Variable(
                            provenance=self.prov(v.path),
                            certainty=Certainty.INFERRED,
                            name=iid,
                            item_id=iid,
                            storage_type=_STORAGE.get(itype, StorageType.UNKNOWN),
                            notes=(self.sem["tip"].tag,),
                        )
                    )
                    self.var_rules(v, iid)
                if preg.tabitems is not None:
                    g = self.grid_item(preg)
                    if g is not None:
                        items.append(g)
            k = page_seen.get(page.id, 0) + 1
            page_seen[page.id] = k
            sid = f"PAG{page.id}" if k == 1 else f"PAG{page.id}#{k}"
            sections.append(
                Section(
                    provenance=self.prov(page.obj.path),
                    certainty=Certainty.EXPLICIT,
                    id=sid,
                    title=page.obj.get("nom"),
                    items=tuple(items),
                    pages=(str(page.id),),
                )
            )
        return tuple(sections), variables

    def grid_item(self, preg) -> Optional[Item]:
        t = preg.tabitems
        nom = t.get("nom") or f"TAB{preg.num}"
        a = self.grid_assign[id(preg)]
        gid = a.assigned
        row_vars = [self.var_ids[id(v)] for v in preg.vars]
        titles = [self.trad(r.get("txt"), f"{r.path}.txt") or "" for r in preg.row_titles]
        rows: list[GridRow] = []
        if row_vars and len(titles) == len(row_vars):
            rows = [GridRow(item_id=iv, label=Text(raw=tt, clean=clean_html(tt))) for iv, tt in zip(row_vars, titles)]
        else:
            self.unres("grid_rows_mismatch", t.path, nom, f"{len(titles)} títulos de fila y {len(row_vars)} variables; no se emparejan sin evidencia")
            rows = [GridRow(item_id=iv, label=Text(raw="", clean="")) for iv in row_vars]
        if not rows:
            self.unres("grid_without_rows", t.path, nom, "tabitems sin variables")
            return None
        self.collision(a, t.path)
        shared: tuple[Option, ...] = ()
        if len(preg.subtablas) == 1:
            shared = self.options(preg.subtablas[0][1], gid)
        elif len(preg.subtablas) > 1:
            self.unres("multiple_subtables", t.path, nom, f"{len(preg.subtablas)} subtablas; no se eligen opciones compartidas")
        title = self.trad(t.get("txt"), f"{t.path}.txt") or ""
        return Item(
            provenance=self.prov(t.path),
            certainty=Certainty.INFERRED,
            id=gid,
            label=nom,
            text=Text(raw=title, clean=clean_html(title)),
            type=ItemType.GRID,
            grid=Grid(provenance=self.prov(t.path), certainty=Certainty.INFERRED, rows=tuple(rows), shared_options=shared, orientation="rows",
                      notes=(UNREGISTERED["tabitems"],)),
            kind=ItemKind.SUBSTANTIVE,
            notes=("gim:tabitems", UNREGISTERED["tabitems"]) + ((OPTION_FLAGS_NOTE,) if shared else ()),
        )

    # Reglas -------------------------------------------------------------------

    def opaque_rule(self, rid: str, o: Obj, reason: str, extra_notes: tuple = ()) -> None:
        self.rules.append(OpaqueRule(provenance=self.prov(o.path), certainty=Certainty.OPAQUE, id=rid, raw=_canon(o.f), reason=reason, notes=extra_notes))

    def cond_or_none(self, o: Obj, key: str = "cnd") -> Optional[str]:
        v = o.get(key)
        return v if isinstance(v, str) and v.strip() else None

    def var_rules(self, v: Var, iid: str) -> None:
        for i, f in enumerate(v.filtros):
            rid = f"{iid}/filtros[{i}]"
            cnd = self.cond_or_none(f)
            if cnd is None:
                self.opaque_rule(rid, f, "empty_condition:filtros")
                continue
            notes = [self.sem["filtros"].tag, f"gim:filtros.meaning={self.sem['filtros'].meaning}"]
            if f.get("nomvar") is not None:
                notes.append(f"gim:nomvar={f.get('nomvar')}")
            if "guiones" in f.f:
                notes.append(f"gim:guiones={f.f['guiones']}")
            when, pnotes = self.condition(cnd, f"{f.path}.cnd")
            self.rules.append(Rule(provenance=self.prov(f.path), certainty=Certainty.INFERRED, id=rid, kind=RuleKind.FILTER,
                                   when=when, target=iid, raw=cnd, message=f.get("des"), notes=tuple(notes) + pnotes))
        for i, s in enumerate(v.saltos):
            rid = f"{iid}/saltos[{i}]"
            cnd = self.cond_or_none(s)
            dest = s.get("nomvar")
            if cnd is None:
                self.opaque_rule(rid, s, "empty_condition:saltos")
                continue
            if dest is None:
                self.opaque_rule(rid, s, "missing_target:saltos")
                continue
            notes = [UNREGISTERED["saltos"], f"gim:after={iid}"]
            if s.get("inc") is not None:
                notes.append(f"gim:inc={s.get('inc')}")
            if "guiones" in s.f:
                notes.append(f"gim:guiones={s.f['guiones']}")
            if SPECIAL_TARGET.match(dest):
                # Ningún destino especial se interpreta por su nombre (tampoco @RECHAZAR@).
                self.opaque_rule(rid, s, f"unconfirmed_special_target:{dest}", tuple(notes + [f"gim:special_target={dest}"]))
                continue
            when, pnotes = self.condition(cnd, f"{s.path}.cnd")
            self.rules.append(Rule(provenance=self.prov(s.path), certainty=Certainty.INFERRED, id=rid, kind=RuleKind.SKIP,
                                   when=when, target=dest, raw=cnd, notes=tuple(notes) + pnotes))
        acc_h = self.sem["acc"]
        for i, c in enumerate(v.controles):
            rid = f"{iid}/controles[{i}]"
            cnd = self.cond_or_none(c)
            acc = c.get("acc")
            meaning = acc_h.meaning if isinstance(acc_h.meaning, dict) else {}
            action_name = meaning.get(str(acc)) if acc is not None else None
            if cnd is None:
                self.opaque_rule(rid, c, "empty_condition:controles")
                continue
            if action_name not in ("block", "warn"):
                self.opaque_rule(rid, c, f"acc_value_unrecognized:{acc}", (acc_h.tag,))
                continue
            when, pnotes = self.condition(cnd, f"{c.path}.cnd")
            self.rules.append(Rule(provenance=self.prov(c.path), certainty=Certainty.INFERRED, id=rid, kind=RuleKind.VALIDATION,
                                   when=when, action=RuleAction(action_name),
                                   message=self.trad(c.get("mens"), f"{c.path}.mens"), target=iid, raw=cnd,
                                   notes=(acc_h.tag, f"gim:acc={acc}") + pnotes))
        for i, d in enumerate(v.valdefs):
            rid = f"{iid}/valdefs[{i}]"
            cnd = self.cond_or_none(d)
            xpr = d.get("xpr")
            refer = self.trad(d.get("refer"), f"{d.path}.refer") if d.get("refer") else None
            notes = [UNREGISTERED["valdefs"]]
            if cnd is None:
                notes.append("gim:cnd_vacia")
            if xpr is None and refer is not None:
                notes.append("gim:valor_de_refer")
            ext = sorted(set(self.ext_mentions.get(f"{d.path}.xpr", [])))
            if ext:
                notes.append("gim:xpr_menciona_funciones_externas=" + ",".join(ext))
            raw_value = xpr if xpr is not None else refer
            if raw_value is None:
                self.opaque_rule(rid, d, "valdefs_without_value")
                continue
            if isinstance(xpr, str) and xpr.strip():
                # Solo diagnóstico: el valor por defecto sigue siendo texto en Rule.raw.
                pv = parse_value(xpr, f"{d.path}.xpr")
                notes.append(f"gim:xpr_parse_status={pv.status}")
                notes += [f"gim:xpr_parse_diag={x.code}@{x.start}-{x.end}" for x in pv.diagnostics]
            when, pnotes = self.condition(cnd, f"{d.path}.cnd") if cnd else (None, ())
            self.rules.append(Rule(provenance=self.prov(d.path), certainty=Certainty.INFERRED, id=rid, kind=RuleKind.COMPUTATION,
                                   when=when, target=iid, raw=raw_value, notes=tuple(notes) + pnotes))
        for key, kind in (("habil_cnd", RuleKind.ENABLE), ("oculta_cnd", RuleKind.HIDE)):
            if key in v.obj.f:
                rid = f"{iid}/{key}"
                cnd = self.cond_or_none(v.obj, key)
                if cnd is None:
                    self.rules.append(OpaqueRule(provenance=self.prov(f"{v.path}.{key}"), certainty=Certainty.OPAQUE, id=rid,
                                                 raw=_canon({key: v.obj.f[key]}), reason=f"empty_condition:{key}"))
                    continue
                when, pnotes = self.condition(cnd, f"{v.path}.{key}")
                self.rules.append(Rule(provenance=self.prov(f"{v.path}.{key}"), certainty=Certainty.INFERRED, id=rid, kind=kind,
                                       when=when, target=iid, raw=cnd, notes=(UNREGISTERED[key],) + pnotes))

    # Resto --------------------------------------------------------------------

    def external_deps(self) -> tuple[ExternalDep, ...]:
        found: dict[tuple, dict] = {}

        def add(kind: ExternalDepKind, name: str, fields: tuple[str, ...], path: str, note: str) -> None:
            key = (kind, name)
            if key not in found:
                found[key] = {"fields": list(fields), "path": path, "notes": [note]}
            else:
                found[key]["fields"] += [f for f in fields if f not in found[key]["fields"]]
                found[key]["notes"].append(f"{note}@{path}")

        for c in self.raw.camposid:
            nomvar, func = c.get("nomvar"), c.get("func")
            if not nomvar:
                continue
            kind = ExternalDepKind.GPS if isinstance(func, str) and func.upper().startswith("GPS(") else ExternalDepKind.DEVICE
            add(kind, nomvar, (func,) if func else (), c.path, f"gim:camposid.func={func}")
        for g in self.raw.varsgps:
            if g.get("nomvar"):
                add(ExternalDepKind.GPS, g.get("nomvar"), (), g.path, "gim:varsgps")
        for t in self.raw.tabaux:
            if t.get("nomvar"):
                add(ExternalDepKind.LOOKUP_TABLE, f"tabaux:{t.get('nomvar')}", (t.get("campo"),) if t.get("campo") else (), t.path, "gim:tabaux")
        for t, campos in self.raw.tablasenlaz:
            if t.get("nomtabla"):
                add(ExternalDepKind.LOOKUP_TABLE, t.get("nomtabla"), tuple(c.get("nom") for c in campos if c.get("nom")), t.path, "gim:tablasenlaz")
        return tuple(
            ExternalDep(provenance=self.prov(v["path"]), certainty=Certainty.INFERRED, kind=k, name=n, fields=tuple(v["fields"]),
                        notes=tuple(v["notes"]) + ("gim:clasificacion_por_nombre_de_estructura",))
            for (k, n), v in found.items()
        )

    def quotas(self) -> tuple[Quota, ...]:
        out = []
        seen: set[str] = set()
        for lst, items in self.raw.cuotas.items():
            for q in items:
                qid = f"{lst}:{q.get('id')}"
                if qid in seen:
                    continue
                seen.add(qid)
                notes = [f"gim:{k}={q.f[k]}" for k in ("grp", "sys_cuota", "tp_cuota") if k in q.f]
                if "sql_cuota" in q.f:
                    notes.append("gim:sql_cuota_presente_no_ejecutado")
                if "sql_tab_aux" in q.f:
                    notes.append("gim:sql_tab_aux_presente_no_ejecutado")
                out.append(Quota(provenance=self.prov(q.path), certainty=Certainty.EXPLICIT, id=qid, description=q.get("desc_cuota"),
                                 condition_raw=q.get("cond_cuota"), notes=tuple(notes)))
        return tuple(out)

    def parse_findings(self) -> tuple[Finding, ...]:
        out = []
        for i, e in enumerate(self.raw.errores):
            des = e.get("des")
            if not des:
                continue
            refs = (FindingRef(side="program", element_id=f"errores[{i}]", provenance=self.prov(e.path), certainty=Certainty.EXPLICIT),)
            dep = ("gim:errores_semantics",)
            level = derive_evidence_level(refs, depends_on_unconfirmed=dep)
            payload = dict(check_id="gim:errores", scope=FindingScope.PROGRAM, category=FindingCategory.ESTRUCTURA,
                           severity=Severity.MEDIA, refs=refs, depends_on_unconfirmed=dep,
                           explanation=f"La herramienta GIM registra este error: {des}",
                           suggested_check="Revisar el mensaje en el editor GIM; su significado no está documentado.")
            out.append(Finding(id=finding_id("gim:errores", refs), evidence_level=level, **payload))
        return tuple(out)

    def incidences(self) -> list[dict]:
        rows = []
        for inc in self.raw.incidencias:
            label = self.trad(inc.get("txts"), f"{inc.path}.txts") or ""
            rows.append({"path": inc.path, "id": inc.get("id"), "tipo": inc.get("tipo"), "label": label,
                         "flags": {k: inc.f[k] for k in ("capi", "cati", "cawi", "dentro", "fuera") if k in inc.f}})
            self.unres("termination_kind_unrecognized", inc.path, label,
                       f"incidencia {inc.get('id')}: decisión pendiente; el tipo de terminación no está documentado")
        return rows


def to_ir(raw: RawProgram, semantics: Optional[Semantics] = None, *, hmac_key: Optional[bytes] = None) -> ProgramReadResult:
    sem = semantics or load_semantics()
    b = _Builder(raw, sem, TextProtector(hmac_key))
    sections, variables = b.build_items()
    incid = b.incidences()
    est = raw.estudio
    study = Study(ids=(est.get("guid"),) if est and est.get("guid") else (), title=est.get("nom") if est else None)
    langs = tuple(dict.fromkeys(
        (l.get("idiauto") or l.get("descripcion") or str(l.get("id"))) for l in raw.languages
        if (l.get("idiauto") or l.get("descripcion") or l.get("id") is not None)
    ))
    inst = Instrument(
        role=Role.PROGRAM,
        source=Source(name=raw.name, sha256=raw.container.sha256, format="gim-json", adapter=ADAPTER, adapter_version=ADAPTER_VERSION),
        study=study,
        languages=langs,
        sections=sections,
        variables=tuple(variables),
        rules=tuple(b.rules),
        terminations=(),
        external_deps=b.external_deps(),
        quotas=b.quotas(),
        parse_findings=b.parse_findings(),
        unresolved=tuple(b.unresolved),
    )
    issues = validate_instrument(inst)
    errs = errors(issues)
    if errs:
        raise IRValidationError(errs)
    warnings = [asdict(i) for i in issues if i.level == "warning"]
    # H-09: avisos pertinentes del validador como validation:<código> (mismo criterio que Word, D-09).
    # El lector GIM no registra antes esas referencias, así que no hay equivalentes que omitir.
    inst = with_validation_unresolved(inst, issues)
    report = _report(raw, sem, inst, warnings, incid, b.parse_stats)
    return ProgramReadResult(inst, report)


def _report(raw: RawProgram, sem: Semantics, inst: Instrument, warnings: list, incid: list, parse_stats: dict) -> GimReadReport:
    from collections import Counter

    items = [i for s in inst.sections for i in s.items]
    rules = [r for r in inst.rules if isinstance(r, Rule)]
    opaque_rules = [r for r in inst.rules if isinstance(r, OpaqueRule)]
    cert = Counter()
    for kind, elems in (("sections", inst.sections), ("items", items), ("variables", inst.variables), ("rules", rules),
                        ("opaque_rules", opaque_rules), ("external_deps", inst.external_deps), ("quotas", inst.quotas)):
        for e in elems:
            cert[f"{kind}:{e.certainty.value}"] += 1
    opts = [o for i in items for o in i.options]
    for o in opts:
        cert[f"options:{o.certainty.value}"] += 1
    cc = Counter(info.status for _, info in raw.codcomp)
    ec = Counter(e.section for e in raw.expressions if e.raw.strip())
    ec_empty = Counter(e.section for e in raw.expressions if not e.raw.strip())
    unique = len({e.raw for e in raw.expressions if e.raw.strip()})
    hierarchy = [
        {"path": p.obj.path, "id": p.id, "nom": p.obj.get("nom"),
         "pregs": [{"path": q.obj.path, "preg": q.num, "tabitems": q.tabitems.get("nom") if q.tabitems else None,
                    "vars": [{"nom": v.nom, "path": v.path, "n_cods": len(v.cods),
                              "rules": {k: len(getattr(v, k)) for k in ("filtros", "saltos", "controles", "valdefs")},
                              "habil_cnd": "habil_cnd" in v.obj.f, "oculta_cnd": "oculta_cnd" in v.obj.f}
                             for v in q.vars]} for q in p.pregs]}
        for p in raw.pages
    ]
    cond_texts = [
        {"path": o.path, "cnd": o.get("cnd"), "xpr": o.get("xpr"), "refer": o.get("refer")}
        for p in raw.pages for q in p.pregs for o in (q.cond_texts + [c for v in q.vars for c in v.cond_texts])
    ]
    ext_names = sorted({m.name for m in raw.function_mentions if m.external})
    return GimReadReport(
        ir_sha256=ir_hash(inst),
        source={"name": raw.name, "sha256": raw.container.sha256, "size": raw.container.size},
        semantics={"version": sem.version, "sha256": sem.sha256, "pending": sem.pending(), "warnings": list(sem.warnings),
                   "statuses": {n: h.status for n, h in sorted(sem.entries.items())}},
        limits=asdict(raw.limits),
        container={"had_bom": raw.container.had_bom, "max_depth": raw.container.max_depth_seen, "nodes": raw.container.nodes,
                   "duplicate_keys": [d.key for d in raw.container.duplicate_keys]},
        counts={
            "pages": len(raw.pages), "questions": sum(len(p.pregs) for p in raw.pages), "variables": len(raw.all_vars()),
            "grids": sum(1 for i in items if i.grid is not None), "options": len(opts), "rules": len(rules),
            "opaque_rules": len(opaque_rules),
            "conditions": sum(1 for r in rules if r.when is not None) + sum(1 for o in opts if o.display_if is not None),
            "conditions_by_parse_status": dict(sorted(parse_stats.items())),
            "opaque_conditions": sum(1 for c in [r.when for r in rules] + [o.display_if for o in opts] if isinstance(c, Opaque)),
            "incidences": len(raw.incidencias), "quotas": len(inst.quotas), "external_deps": len(inst.external_deps),
            "parse_findings": len(inst.parse_findings), "pool_conditions": len(raw.pools.get("condiciones", [])),
            "unresolved": len(inst.unresolved),
        },
        classification={
            "by_certainty": dict(sorted(cert.items())),
            "interpreted": "explicit: estructura leída tal cual (secciones, opciones sin condición, cuotas como metadatos)",
            "inferred": "depende del registro de semántica o de campos sin registrar (ítems, variables, reglas, dependencias)",
            "opaque": "reglas opacas y nodos Opaque dentro de las condiciones (sintaxis no respaldada por la gramática del paso 4)",
            "unresolved": "Instrument.unresolved",
        },
        hierarchy=hierarchy,
        expressions=[asdict(e) for e in raw.expressions],
        expression_counts={"non_empty_by_section": dict(sorted(ec.items())), "empty_by_section": dict(sorted(ec_empty.items())),
                           "total_non_empty": sum(ec.values()), "unique_non_empty": unique},
        sql=[asdict(e) for e in raw.sql],
        codcomp={"by_status": dict(sorted(cc.items())), "items": [{"path": p, **asdict(i)} for p, i in raw.codcomp]},
        external_references={"function_mentions": [asdict(m) for m in raw.function_mentions], "external_function_names": ext_names,
                             "deps": [d.model_dump(mode="json") for d in inst.external_deps]},
        conditional_texts=cond_texts,
        incidences=incid,
        code_lists=[{"path": o.path, "nom": o.get("nom"), "codes": [c.get("def") for c in cods]} for o, cods in raw.codvars],
        cross_checks={"flujo_entries": len(raw.flujo), "arbol_nodes": len(raw.arbol), "arbolflujo_nodes": len(raw.arbolflujo_nodes),
                      "pools": {k: len(v) for k, v in sorted(raw.pools.items())}},
        versions=[{"path": v.path, "id": v.get("id"), "guid": v.get("guid"), "n_vars": len(vv)} for v, vv in raw.versiones],
        raw_extra=raw.raw_extra,
        dropped_personal_metadata=raw.dropped_personal,
        not_mapped=raw.not_mapped,
        not_representable=list(NOT_REPRESENTABLE),
        pending_decisions=list(PENDING_DECISIONS),
        validation_warnings=warnings,
    )
