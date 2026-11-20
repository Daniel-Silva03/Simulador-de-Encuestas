"""Lector estructural GIM (especificación v1.0, sección 6, capa 1).

Convierte el JSON en objetos con ruta de origen y sin interpretar su
semántica:

- recorre ``paginas[] → pregs[] → vars[]`` y conserva la jerarquía;
- comprueba claves y tipos con el esquema observado (``schema.py``);
- de las claves desconocidas conserva solo metadatos estructurales en
  ``raw_extra`` (ruta, clave, tipo, tamaño y huella de la forma), nunca su
  valor; las claves con apariencia de dato personal, en cualquier nivel
  dentro de un valor desconocido, se anotan en ``dropped_personal`` solo
  con su ruta;
- los avisos sobre tipos inesperados registran el tipo, nunca el valor;
- inventaría cada expresión con su texto exacto y cada ``codcomp`` (que se
  decodifica solo como datos);
- comprueba referencias cruzadas y duplicados.

Las expresiones nunca se evalúan. El parser de expresiones es el paso 4.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from . import schema as SC
from .codcomp import CodcompInfo, inspect_codcomp
from .container import Container, GimContainerError, ReaderLimits, load_container

_TYPES = {"int": int, "str": str, "bool": bool, "list": list, "dict": dict}
_PERSONAL_KEY = re.compile(r"(autor|author|usuario|user|e-?mail|correo|creador|creado_?por|modificado_?por|telefono|tel[eé]fono|phone|owner|propietario)", re.I)

#: Nombres de función que la especificación clasifica como dependencias
#: externas. Solo se usan para inventariar menciones en el texto de las
#: expresiones; no se interpretan las expresiones (paso 4).
EXTERNAL_FUNCTION_PATTERNS = (
    re.compile(r"^TABAUX$"), re.compile(r"^TABLE$"), re.compile(r"^GPS$"), re.compile(r"^NUMALEATORIO$"),
    re.compile(r"^GUID\w*$"), re.compile(r"^ID_\w+$"), re.compile(r"^COORDINADOR$"), re.compile(r"^USUARIO$"),
    re.compile(r"^VERSION$"),
)
_FUNC_TOKEN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
SPECIAL_TARGET = re.compile(r"^@[^@]+@$")


@dataclass(frozen=True)
class Issue:
    code: str
    path: str
    text: str
    detail: str = ""


@dataclass
class Obj:
    """Objeto GIM validado: solo claves conocidas con el tipo esperado."""

    path: str
    f: dict

    def get(self, k: str, default: Any = None) -> Any:
        return self.f.get(k, default)


@dataclass(frozen=True)
class Expression:
    path: str
    field: str
    section: str  # paginas | flujo | cuotas | cuotasocultas | cuotascpl | otros
    raw: str


@dataclass(frozen=True)
class FunctionMention:
    path: str
    name: str
    external: bool


@dataclass
class Var:
    obj: Obj
    page_id: int
    preg_num: Optional[int]
    cods: list[Obj] = field(default_factory=list)
    filtros: list[Obj] = field(default_factory=list)
    saltos: list[Obj] = field(default_factory=list)
    controles: list[Obj] = field(default_factory=list)
    valdefs: list[Obj] = field(default_factory=list)
    cond_texts: list[Obj] = field(default_factory=list)
    unmapped_keys: tuple[str, ...] = ()

    @property
    def nom(self) -> str:
        return self.obj.f["nom"]

    @property
    def path(self) -> str:
        return self.obj.path


@dataclass
class Preg:
    obj: Obj
    texts: list[Obj] = field(default_factory=list)
    cond_texts: list[Obj] = field(default_factory=list)
    tabitems: Optional[Obj] = None
    subtablas: list[tuple[Obj, list[Obj]]] = field(default_factory=list)
    row_titles: list[Obj] = field(default_factory=list)
    vars: list[Var] = field(default_factory=list)

    @property
    def num(self) -> Optional[int]:
        return self.obj.get("preg")


@dataclass
class Page:
    obj: Obj
    pregs: list[Preg] = field(default_factory=list)

    @property
    def id(self) -> int:
        return self.obj.f["id"]


@dataclass
class RawProgram:
    name: str
    container: Container
    limits: ReaderLimits
    estudio: Optional[Obj]
    languages: list[Obj]
    default_language: Optional[int]
    pages: list[Page]
    incidencias: list[Obj]
    camposid: list[Obj]
    tabaux: list[Obj]
    tablasenlaz: list[tuple[Obj, list[Obj]]]
    varenlazadas: list[tuple[Obj, list[Obj]]]
    codvars: list[tuple[Obj, list[Obj]]]
    varsgps: list[Obj]
    cuotas: dict[str, list[Obj]]
    errores: list[Obj]
    versiones: list[tuple[Obj, list[Obj]]]
    flujo: list[tuple[Obj, list[Obj]]]
    arbol: list[Obj]
    arbolflujo_nodes: list[Obj]
    pools: dict[str, list]
    expressions: list[Expression]
    sql: list[Expression]
    codcomp: list[tuple[str, CodcompInfo]]
    function_mentions: list[FunctionMention]
    issues: list[Issue]
    raw_extra: list[dict]
    dropped_personal: list[str]
    not_mapped: dict[str, int]
    duplicate_vars: dict[str, int]

    def all_vars(self) -> list[Var]:
        return [v for p in self.pages for q in p.pregs for v in q.vars]


class _Reader:
    def __init__(self, c: Container, name: str, limits: ReaderLimits):
        self.c = c
        self.name = name
        self.limits = limits
        self.issues: list[Issue] = []
        self.raw_extra: list[dict] = []
        self.dropped: list[str] = []
        self.expressions: list[Expression] = []
        self.sql: list[Expression] = []
        self.codcomp: list[tuple[str, CodcompInfo]] = []
        self.mentions: list[FunctionMention] = []

    # Utilidades ----------------------------------------------------------------

    def issue(self, code: str, path: str, text: str = "", detail: str = "") -> None:
        self.issues.append(Issue(code, path, text, detail))

    def check(self, value: Any, spec: dict, path: str, section: str) -> Optional[Obj]:
        """Valida un objeto contra su esquema. Devuelve None si no es un objeto."""
        if value is None:
            self.issue("null_value", path, "null", "se esperaba un objeto")
            return None
        if not isinstance(value, dict):
            self.issue("unexpected_type", path, _type_tag(value), f"se esperaba objeto, llegó {type(value).__name__}")
            return None
        out: dict = {}
        for k, v in value.items():
            p = f"{path}.{k}"
            if k not in spec:
                self.unknown(k, v, p)
                continue
            want = spec[k]
            if v is None:
                self.issue("null_value", p, "null", f"se esperaba {want}")
                continue
            if want == SC.C:
                ok = isinstance(v, (str, int)) and not isinstance(v, bool)
            elif want == SC.I:
                ok = isinstance(v, int) and not isinstance(v, bool)
            else:
                ok = isinstance(v, _TYPES[want])
            if not ok:
                self.issue("unexpected_type", p, _type_tag(v), f"se esperaba {want}, llegó {type(v).__name__}")
                continue
            out[k] = v
            if k in SC.EXPRESSION_FIELDS and isinstance(v, str):
                self.expression(p, k, section, v)
            elif k in SC.SQL_FIELDS and isinstance(v, str):
                self.sql.append(Expression(p, k, section, v))
            elif k in SC.CODCOMP_FIELDS:
                self.codcomp.append((p, inspect_codcomp(v, max_decoded=self.limits.max_codcomp_decoded_bytes, max_ratio=self.limits.max_codcomp_ratio)))
        return Obj(path, out)

    def unknown(self, key: str, value: Any, path: str) -> None:
        """Clave fuera del esquema: se registra su estructura, nunca su valor.

        Dentro del valor se buscan, en todos los niveles y sin recursión,
        claves con apariencia de dato personal; solo se anota su ruta.
        """
        if _PERSONAL_KEY.search(key):
            self.dropped.append(path)
            self.issue("personal_metadata_dropped", path, key, "clave desconocida con apariencia de dato personal; valor descartado")
            return
        nested = _personal_paths(value, path)
        for dp in nested:
            self.dropped.append(dp)
            self.issue("personal_metadata_dropped", dp, dp.rsplit(".", 1)[-1], "clave con apariencia de dato personal dentro de un valor desconocido; valor descartado")
        self.raw_extra.append({
            "path": path,
            "key": key,
            "type": type(value).__name__,
            "size": _size(value),
            "shape_sha256": _shape_hash(value),
            "personal_keys_dropped": len(nested),
        })
        self.issue("unknown_key", path, key, "clave fuera del esquema observado; se registra su estructura, no su valor")

    def expression(self, path: str, fieldname: str, section: str, raw: str) -> None:
        self.expressions.append(Expression(path, fieldname, section, raw))
        for m in _FUNC_TOKEN.finditer(raw):
            name = m.group(1)
            ext = any(p.match(name.upper()) for p in EXTERNAL_FUNCTION_PATTERNS)
            self.mentions.append(FunctionMention(path, name, ext))

    def items(self, parent: Obj | dict | None, key: str, spec: dict, section: str, path: Optional[str] = None) -> list[Obj]:
        if parent is None:
            return []
        f = parent.f if isinstance(parent, Obj) else parent
        base = path if path is not None else (parent.path if isinstance(parent, Obj) else "$")
        seq = f.get(key)
        if seq is None:
            return []
        out = []
        for i, x in enumerate(seq):
            o = self.check(x, spec, f"{base}.{key}[{i}]", section)
            if o is not None:
                out.append(o)
        return out

    def top_list(self, key: str, spec: dict, section: str) -> list[Obj]:
        data = self.c.data
        if key not in data:
            return []
        v = data[key]
        if not isinstance(v, list):
            self.issue("unexpected_type", f"$.{key}", _type_tag(v), f"se esperaba lista, llegó {type(v).__name__}")
            return []
        return self.items({key: v}, key, spec, section, "$")

    # Recorrido ------------------------------------------------------------------

    def run(self) -> RawProgram:
        d = self.c.data
        for k, v in d.items():
            if k in SC.TOP_MAPPED or k in SC.TOP_KNOWN_NOT_MAPPED:
                want = {**SC.TOP_MAPPED, **SC.TOP_KNOWN_NOT_MAPPED}[k]
                if v is not None and not isinstance(v, _TYPES[want]):
                    self.issue("unexpected_type", f"$.{k}", _type_tag(v), f"se esperaba {want}")
                continue
            self.unknown(k, v, f"$.{k}")
        if not isinstance(d.get("paginas"), list):
            raise GimContainerError("missing_paginas", "el programa no tiene la lista paginas[]")
        not_mapped = {k: _size(d[k]) for k in SC.TOP_KNOWN_NOT_MAPPED if k in d}

        estudio = self.check(d["estudio"], SC.ESTUDIO, "$.estudio", "otros") if "estudio" in d else None
        langs = self.top_list("idiomas", SC.IDIOMA, "otros")
        default = next((l.get("id") for l in langs if l.get("defecto") == 1), None)
        if default is None and langs:
            default = langs[0].get("id")

        pages = self.read_pages(d["paginas"])
        incid = self.top_list("incidencias", SC.INCIDENCIA, "otros")
        camposid = self.top_list("camposid", SC.CAMPOSID, "otros")
        for c in camposid:
            if "func" in c.f:  # referencia a función externa, se conserva como texto
                self.expression(f"{c.path}.func", "func", "camposid", c.f["func"])
        tabaux = self.top_list("tabaux", SC.TABAUX, "otros")
        tablasenlaz = [(t, self.items(t, "campos", SC.CAMPO, "otros")) for t in self.top_list("tablasenlaz", SC.TABLASENLAZ, "otros")]
        varenl = [(v, self.items(v, "cods", SC.COD, "otros")) for v in self.top_list("varenlazadas", SC.VARENLAZ, "otros")]
        codvars = [(v, self.items(v, "cods", SC.COD, "otros")) for v in self.top_list("codvars", SC.CODVAR, "otros")]
        varsgps = self.top_list("varsgps", SC.VARSGPS, "otros")
        cuotas = {k: self.top_list(k, SC.CUOTA, k) for k in ("cuotas", "cuotasocultas", "cuotascpl")}
        errores = self.top_list("errores", SC.ERROR, "otros")
        versiones = [(v, self.items(v, "vars", SC.VERSION_VAR, "otros")) for v in self.top_list("versiones", SC.VERSION, "otros")]
        flujo = []
        for fo in self.top_list("flujo", SC.FLUJO, "flujo"):
            for k, spec in (("filtros", SC.FILTRO), ("saltos", SC.SALTO), ("controles", SC.CONTROL), ("valdefs", SC.VALDEF)):
                self.items(fo, k, spec, "flujo")
            flujo.append((fo, self.items(fo, "vars", SC.FLUJO_VAR, "flujo")))
        arbol = self.top_list("arbol", SC.ARBOL, "otros")
        arbolflujo_nodes = self.read_arbolflujo(d.get("arbolflujo"))
        pools = {}
        for k in ("condiciones", "codcomp", "filtrosentrev"):
            v = d.get(k)
            if isinstance(v, list):
                pools[k] = v
                for i, x in enumerate(v):
                    p = f"$.{k}[{i}]"
                    if k == "condiciones" and isinstance(x, str):
                        self.expression(p, "condiciones", "pool", x)
                    elif k == "codcomp":
                        self.codcomp.append((p, inspect_codcomp(x, max_decoded=self.limits.max_codcomp_decoded_bytes, max_ratio=self.limits.max_codcomp_ratio)))

        raw = RawProgram(
            name=self.name, container=self.c, limits=self.limits, estudio=estudio, languages=langs,
            default_language=default, pages=pages, incidencias=incid, camposid=camposid, tabaux=tabaux,
            tablasenlaz=tablasenlaz, varenlazadas=varenl, codvars=codvars, varsgps=varsgps, cuotas=cuotas,
            errores=errores, versiones=versiones, flujo=flujo, arbol=arbol, arbolflujo_nodes=arbolflujo_nodes,
            pools=pools, expressions=self.expressions, sql=self.sql, codcomp=self.codcomp,
            function_mentions=self.mentions, issues=self.issues, raw_extra=self.raw_extra,
            dropped_personal=self.dropped, not_mapped=not_mapped, duplicate_vars={},
        )
        self.cross_checks(raw)
        for dk in self.c.duplicate_keys:
            self.issue("duplicate_json_key", "$", dk.key, "clave repetida en un objeto JSON; se conserva la primera aparición")
        return raw

    def read_pages(self, seq: list) -> list[Page]:
        lim = self.limits
        if len(seq) > lim.max_pages:
            raise GimContainerError("max_pages", f"{len(seq)} páginas; el límite es {lim.max_pages}")
        pages: list[Page] = []
        n_pregs = n_vars = 0
        for pi, praw in enumerate(seq):
            po = self.check(praw, SC.PAGINA, f"$.paginas[{pi}]", "paginas")
            if po is None:
                continue
            if "id" not in po.f:
                self.issue("missing_field", po.path, "", "página sin id; se omite")
                continue
            page = Page(po)
            pregs_raw = po.get("pregs", [])
            n_pregs += len(pregs_raw)
            if n_pregs > lim.max_questions:
                raise GimContainerError("max_questions", f"más de {lim.max_questions} preguntas")
            for qi, qraw in enumerate(pregs_raw):
                qo = self.check(qraw, SC.PREG, f"{po.path}.pregs[{qi}]", "paginas")
                if qo is None:
                    continue
                preg = Preg(qo)
                preg.texts = self.items(qo, "txts", SC.PREG_TXT, "paginas")
                for t in preg.texts:
                    self.items(t, "txts", SC.TRAD, "paginas")
                    preg.cond_texts += self.items(t, "refers", SC.COND_TEXT, "paginas")
                ti = qo.get("tabitems")
                if ti is not None:
                    preg.tabitems = self.check(ti, SC.TABITEMS, f"{qo.path}.tabitems", "paginas")
                    if preg.tabitems is not None:
                        for st in self.items(preg.tabitems, "subtablas", SC.SUBTABLA, "paginas"):
                            preg.subtablas.append((st, self.items(st, "cods", SC.COD, "paginas")))
                        preg.row_titles = self.items(preg.tabitems, "txtti", SC.TXTTI, "paginas")
                vars_raw = qo.get("vars", [])
                n_vars += len(vars_raw)
                if n_vars > lim.max_variables:
                    raise GimContainerError("max_variables", f"más de {lim.max_variables} variables")
                for vi, vraw in enumerate(vars_raw):
                    vo = self.check(vraw, SC.VAR, f"{qo.path}.vars[{vi}]", "paginas")
                    if vo is None:
                        continue
                    if not isinstance(vo.get("nom"), str) or not vo.get("nom"):
                        self.issue("missing_field", vo.path, "", "variable sin nom; se omite")
                        continue
                    v = Var(vo, page.id, preg.num)
                    v.cods = self.items(vo, "cods", SC.COD, "paginas")
                    v.filtros = self.items(vo, "filtros", SC.FILTRO, "paginas")
                    v.saltos = self.items(vo, "saltos", SC.SALTO, "paginas")
                    v.controles = self.items(vo, "controles", SC.CONTROL, "paginas")
                    v.valdefs = self.items(vo, "valdefs", SC.VALDEF, "paginas")
                    v.cond_texts = self.items(vo, "reftextosvar", SC.COND_TEXT, "paginas")
                    tv = vo.get("textosvar")
                    if tv is not None:
                        tvo = self.check(tv, SC.TEXTOSVAR, f"{vo.path}.textosvar", "paginas")
                        self.items(tvo, "txtvar", SC.TXTVAR, "paginas")
                    preg.vars.append(v)
                page.pregs.append(preg)
            pages.append(page)
        return pages

    def read_arbolflujo(self, v: Any) -> list[Obj]:
        """``arbolflujo`` es una lista de listas de listas de nodos; se recorre sin recursión."""
        if v is None:
            return []
        out: list[Obj] = []
        stack: list[tuple[Any, str]] = [(v, "$.arbolflujo")]
        while stack:
            x, p = stack.pop(0)
            if isinstance(x, list):
                stack[0:0] = [(y, f"{p}[{i}]") for i, y in enumerate(x)]
            else:
                o = self.check(x, SC.ARBOLFLUJO, p, "otros")
                if o is not None:
                    out.append(o)
        return out

    # Referencias cruzadas -------------------------------------------------------

    def cross_checks(self, raw: RawProgram) -> None:
        vars_ = raw.all_vars()
        names: dict[str, int] = {}
        for v in vars_:
            names[v.nom] = names.get(v.nom, 0) + 1
        raw.duplicate_vars = {k: n for k, n in names.items() if n > 1}
        for k, n in sorted(raw.duplicate_vars.items()):
            self.issue("duplicate_variable", "$.paginas", k, f"{n} variables con el mismo nom")
        known = set(names)

        ids = [p.id for p in raw.pages]
        for dup in sorted({i for i in ids if ids.count(i) > 1}):
            self.issue("duplicate_page_id", "$.paginas", str(dup), "id de página repetido")
        inc_ids = [i.get("id") for i in raw.incidencias if i.get("id") is not None]
        for dup in sorted({i for i in inc_ids if inc_ids.count(i) > 1}):
            self.issue("duplicate_incidence_id", "$.incidencias", str(dup), "id de incidencia repetido")
        for k, lst in raw.cuotas.items():
            qids = [q.get("id") for q in lst if q.get("id") is not None]
            for dup in sorted({i for i in qids if qids.count(i) > 1}):
                self.issue("duplicate_quota_id", f"$.{k}", str(dup), "id de cuota repetido")

        for v in vars_:
            seen: set[str] = set()
            for c in v.cods:
                d = c.get("def")
                if d is None:
                    self.issue("missing_field", c.path, "", "código sin def")
                elif d in seen:
                    self.issue("duplicate_option_code", c.path, d, f"código repetido en {v.nom}")
                seen.add(d) if d is not None else None
            for f in v.filtros:
                t = f.get("nomvar")
                if t is not None and t not in known:
                    self.issue("reference_not_found", f"{f.path}.nomvar", t, "filtros: variable no encontrada")
            for s in v.saltos:
                t = s.get("nomvar")
                if t is None:
                    self.issue("missing_field", s.path, "", "salto sin nomvar")
                elif SPECIAL_TARGET.match(t):
                    pass
                elif t not in known:
                    self.issue("reference_not_found", f"{s.path}.nomvar", t, "saltos: destino no encontrado")
                inc = s.get("inc")
                if inc is not None and inc not in inc_ids:
                    self.issue("reference_not_found", f"{s.path}.inc", str(inc), "saltos: incidencia no encontrada")

        for coll, key in ((raw.camposid, "camposid"), (raw.tabaux, "tabaux"), (raw.varsgps, "varsgps")):
            for o in coll:
                t = o.get("nomvar")
                if t is not None and t not in known:
                    self.issue("reference_not_found", f"{o.path}.nomvar", t, f"{key}: variable no encontrada")
        for o, _ in raw.varenlazadas:
            t = o.get("nomvar")
            if t is not None and t not in known:
                self.issue("reference_not_found", f"{o.path}.nomvar", t, "varenlazadas: variable no encontrada")

        pregs = {(p.id, q.num) for p in raw.pages for q in p.pregs}
        page_ids = set(ids)
        for a in raw.arbol:
            pg, pr = a.get("pag"), a.get("preg")
            if pg is not None and pg not in page_ids:
                self.issue("reference_not_found", a.path, str(pg), "arbol: página no encontrada")
            elif pg is not None and pr is not None and (pg, pr) not in pregs:
                self.issue("reference_not_found", a.path, f"{pg}/{pr}", "arbol: pregunta no encontrada")

        # flujo: solo verificación cruzada con paginas.
        flujo_names = set()
        for fo, fvars in raw.flujo:
            if fo.get("tipo") == "V" and fo.get("nom"):
                flujo_names.add(fo.get("nom"))
            flujo_names |= {fv.get("nom") for fv in fvars if fv.get("nom")}
        if raw.flujo:
            for n in sorted(flujo_names - known):
                self.issue("flujo_mismatch", "$.flujo", n, "variable en flujo que no está en paginas")
            for n in sorted(known - flujo_names):
                self.issue("flujo_mismatch", "$.paginas", n, "variable en paginas que no está en flujo")
        pool = raw.pools.get("codcomp")
        for fo, _ in raw.flujo:
            for k in ("filtros", "saltos", "controles", "valdefs"):
                for i, x in enumerate(fo.f.get(k, []) or []):
                    if isinstance(x, dict) and isinstance(x.get("codcomp"), int) and not isinstance(x.get("codcomp"), bool):
                        idx = x["codcomp"]
                        if pool is None or not (0 <= idx < len(pool)):
                            self.issue("codcomp_index_out_of_range", f"{fo.path}.{k}[{i}].codcomp", str(idx),
                                       "índice fuera del depósito $.codcomp (la lectura como índice es una inferencia)")
        if "condiciones" in raw.pools and "codcomp" in raw.pools and len(raw.pools["condiciones"]) != len(raw.pools["codcomp"]):
            self.issue("pool_length_mismatch", "$", f"{len(raw.pools['condiciones'])}/{len(raw.pools['codcomp'])}",
                       "condiciones y codcomp tienen longitudes distintas")

        # versiones: se contrasta la última versión con las variables de paginas.
        if raw.versiones:
            last, lvars = max(raw.versiones, key=lambda t: (t[0].get("id") is not None, t[0].get("id") or 0))
            by_name = {v.nom: v for v in vars_}
            vnames = {lv.get("nom") for lv in lvars if lv.get("nom")}
            for n in sorted(vnames - known):
                self.issue("version_layout_mismatch", last.path, n, "variable de la última versión que no está en paginas")
            for n in sorted(known - vnames):
                self.issue("version_layout_mismatch", last.path, n, "variable de paginas que no está en la última versión")
            for lv in lvars:
                n = lv.get("nom")
                if n in by_name and lv.get("tip") is not None and by_name[n].obj.get("tip") != lv.get("tip"):
                    self.issue("version_layout_mismatch", lv.path, n, f"tip distinto: versión {lv.get('tip')} / paginas {by_name[n].obj.get('tip')}")


def _type_tag(v: Any) -> str:
    """Descripción de un valor inesperado sin copiar su contenido."""
    return f"<{type(v).__name__}>"


def _personal_paths(value: Any, base: str) -> list[str]:
    """Rutas de claves con apariencia de dato personal, en cualquier nivel, sin recursión."""
    out: list[str] = []
    stack: list[tuple[Any, str]] = [(value, base)]
    while stack:
        x, p = stack.pop()
        if isinstance(x, dict):
            for k, v in x.items():
                kp = f"{p}.{k}"
                if isinstance(k, str) and _PERSONAL_KEY.search(k):
                    out.append(kp)
                else:
                    stack.append((v, kp))
        elif isinstance(x, list):
            stack.extend((v, f"{p}[{i}]") for i, v in enumerate(x))
    return sorted(out)


def _shape(value: Any) -> Any:
    """Forma de un valor: claves (sin las personales) y tipos; ningún escalar."""
    if isinstance(value, dict):
        return {k: _shape(v) for k, v in sorted(value.items()) if not _PERSONAL_KEY.search(k)}
    if isinstance(value, list):
        return [_shape(v) for v in value]
    return type(value).__name__


def _shape_hash(value: Any) -> str:
    """Huella de la estructura (no del contenido): permite detectar cambios sin guardar valores."""
    return hashlib.sha256(json.dumps(_shape(value), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _size(v: Any) -> int:
    return len(v) if isinstance(v, (list, dict)) else 1


def read_program(data: bytes, name: str, limits: ReaderLimits = ReaderLimits()) -> RawProgram:
    c = load_container(data, limits)
    return _Reader(c, name, limits).run()
