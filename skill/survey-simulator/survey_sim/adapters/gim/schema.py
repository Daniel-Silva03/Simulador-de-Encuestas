"""Esquema estructural observado del formato GIM JSON.

Derivado de la estructura del archivo de referencia (solo nombres de claves
y tipos; ningún contenido). Sirve para dos cosas: reconocer qué claves
existen en cada nivel y comprobar su tipo. Una clave fuera de este esquema
no se descarta: se conserva en ``raw_extra`` del informe y genera un
elemento no resuelto informativo.

Tipos: ``int``, ``str``, ``bool``, ``list``, ``dict``. ``code`` admite
``str`` o ``int`` (``codcomp`` aparece de las dos formas).
"""

from __future__ import annotations

I, S, B, L, D, C = "int", "str", "bool", "list", "dict", "code"

#: Claves de primer nivel que el lector recorre.
TOP_MAPPED = {
    "paginas": L, "flujo": L, "arbol": L, "arbolflujo": L, "codcomp": L, "condiciones": L,
    "codvars": L, "cuotas": L, "cuotasocultas": L, "cuotascpl": L, "errores": L, "estudio": D,
    "idiomas": L, "incidencias": L, "camposid": L, "tabaux": L, "tablasenlaz": L,
    "varenlazadas": L, "varsgps": L, "versiones": L, "filtrosentrev": L,
}

#: Claves de primer nivel conocidas que no se trasladan a la IR (presentación,
#: configuración de pantalla, textos auxiliares). Solo se cuentan.
TOP_KNOWN_NOT_MAPPED = {"config": D, "observaciones": L, "pagaa": L, "protocolo": L, "txtaa": L}

TRAD = {"id": I, "trad": S}

ESTUDIO = {
    "guid": S, "nom": S, "version": I, "version_quest": S, "colorcampo": I, "colorfondo": I,
    "colorfondotrans": I, "colorfuente": I, "colorfuente2": I, "colorinact": I, "colorpre": I,
    "cuotas": I, "existcombobusquedarwd": I, "existgrabfoto": I, "genvar": I, "gps": I,
    "identif": I, "longx": I, "longy": I, "maximizatabla": I, "nmaxinci": I, "nmaxobs": I,
    "nuevapag": I, "numflujos": I, "numpags": I, "zoom": I,
}
IDIOMA = {"activo": I, "defecto": I, "descripcion": S, "id": I, "idiauto": S}

PAGINA = {"id": I, "nom": S, "gnpag": I, "pregs": L}
PREG = {
    "preg": I, "ant": I, "color": I, "imggal": S, "imgs": L, "lx": I, "ly": I,
    "tabitems": D, "tipo": I, "txts": L, "vars": L,
}
PREG_TXT = {
    "clase": S, "id": I, "lx": I, "ly": I, "pag": I, "pre": I, "refers": L, "tex": I, "txts": L,
}
COND_TEXT = {"cnd": S, "codcomp": C, "id": I, "refer": L, "xpr": S}

VAR = {
    "botones": I, "calimg": I, "clase": S, "cods": L, "codsemi": S, "controles": L, "cuadro": I,
    "cuotas": I, "filtros": L, "fvarid": I, "gen": I, "grabimg": I, "habil_cnd": S,
    "habil_codcomp": C, "id": I, "identif": I, "integrada": I, "intro": S, "ln1": I, "ln2": I,
    "lx": I, "ly": I, "met": I, "moscod": I, "moscodpos": I, "nece": I, "nom": S, "noscuro": I,
    "numdiv": I, "oculta": I, "oculta_cnd": S, "oculta_codcomp": C, "ord": I, "pag": I,
    "preg": I, "recal": I, "reftextosvar": L, "saltos": L, "sbmet": I, "svarenlacecod": S,
    "tam": I, "tamimg": I, "tamximg": I, "tamyimg": I, "textosvar": D, "tip": I, "trj": I,
    "trjx": I, "trjy": I, "txtext": L, "valdefs": L, "valp": S, "varabiertas": S, "vari": I,
    "varsemi": S, "x": I, "y": I, "zoom": I,
}
COD = {"cnd": S, "codcomp": C, "def": S, "exc": I, "fijo": I, "noms": L, "varab": S}
FILTRO = {"cnd": S, "codcomp": C, "des": S, "guiones": I, "nomvar": S}
SALTO = {"cnd": S, "codcomp": C, "guiones": I, "inc": I, "nomvar": S}
CONTROL = {"acc": I, "cnd": S, "codcomp": C, "mens": L}
VALDEF = {"cnd": S, "codcomp": C, "refer": L, "xpr": S, "xpr_codcomp": C}
TEXTOSVAR = {
    "columnas": I, "filas": I, "porcentro": I, "porfilcentro": I, "porizquierda": I,
    "porsuperior": I, "txtvar": L,
}
TXTVAR = {"clase": S, "pos": I, "texto": L}

TABITEMS = {
    "clasetic": S, "clasetip": S, "clasetit": S, "clasetiv": S, "color": I, "ftic": I, "ftip": I,
    "ftit": I, "ftiv": I, "nom": S, "orden": I, "padd": I, "pag": I, "plongx": I, "plongy": I,
    "porcender": I, "porcentaje": I, "pposx": I, "pposy": I, "preg": I, "subtablas": L,
    "txt": L, "txt_lx": I, "txt_ly": I, "txt_x": I, "txt_y": I, "txtext": L, "txtpie_ly": I,
    "txtti": L,
}
SUBTABLA = {
    "clasetic": S, "cods": L, "espacio": I, "fstic": I, "id": I, "necesaria": I, "ordencods": I,
    "tamalfa": I, "tipotabla": I, "tipovar": I, "txt_col": L, "valintro": S,
}
TXTTI = {"id": I, "txt": L}

INCIDENCIA = {"capi": I, "cati": I, "cawi": I, "dentro": I, "fuera": I, "id": I, "tipo": I, "txts": L}
CAMPOSID = {"activo": B, "func": S, "id": I, "nomvar": S}
TABAUX = {"campo": S, "nomvar": S, "tamcampgra": I, "tipovar": I}
TABLASENLAZ = {"campos": L, "nomtabla": S}
CAMPO = {"nom": S}
VARENLAZ = {"cods": L, "nomvar": S}
CODVAR = {"cods": L, "nom": S}
VARSGPS = {"nomvar": S}
CUOTA = {
    "codcomp": C, "cond_cuota": S, "desc_cuota": S, "grp": I, "id": I, "sql_cuota": S,
    "sql_tab_aux": S, "sys_cuota": I, "tp_cuota": I,
}
ERROR = {"des": S}
VERSION = {"guid": S, "id": I, "vars": L}
VERSION_VAR = {"gen": I, "ln1": I, "nom": S, "tip": I}

FLUJO = {
    "controles": L, "cuotas": I, "filtros": L, "flujo": I, "grupo": I, "identif": I, "intro": S,
    "ln1": I, "met": I, "nece": I, "nom": S, "oculta": I, "oculta_cnd": S, "oculta_codcomp": C,
    "pag": I, "preg": I, "recal": I, "saltos": L, "tam": I, "tip": I, "tipo": S, "txtext": L,
    "valdefs": L, "valp": S, "vars": L,
}
FLUJO_VAR = {
    "codsemi": S, "intro": S, "ln1": I, "met": I, "nece": I, "nom": S, "tam": I, "tip": I,
    "txtext": L, "varsemi": S,
}
ARBOL = {"id": I, "pag": I, "preg": I, "tip": S}
ARBOLFLUJO = {"id": I, "tip": S}

#: Campos que contienen expresiones GIM (se conservan como texto exacto).
EXPRESSION_FIELDS = ("cnd", "xpr", "habil_cnd", "oculta_cnd", "cond_cuota")
#: Campos con SQL: se conservan como texto y nunca se ejecutan.
SQL_FIELDS = ("sql_cuota", "sql_tab_aux")
#: Campos con datos codificados (base64+zlib, índice o texto).
CODCOMP_FIELDS = ("codcomp", "habil_codcomp", "oculta_codcomp", "xpr_codcomp")
