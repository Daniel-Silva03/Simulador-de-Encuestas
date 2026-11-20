"""Léxico configurable de convenciones de redacción (D06; sección 5, pasos 3 y 4).

El léxico se carga desde YAML y se valida con un modelo estricto. El código
no contiene ninguna convención particular: etiquetas (ENC, PROG, PRG),
tipos de respuesta (RU, RA, RM), patrones de navegación y terminaciones
salen del archivo.
"""

from __future__ import annotations

import hashlib
import pathlib
import re
from dataclasses import dataclass
from typing import Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from survey_sim.ir.models import InstructionKind, ItemType, TerminationKind

DEFAULT_LEXICON = pathlib.Path(__file__).resolve().parents[2] / "config" / "lexicon" / "default_es.yaml"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _compiles(patterns: list[str]) -> list[str]:
    for p in patterns:
        re.compile(p)
    return patterns


class NavigationPatterns(_Strict):
    continue_: list[str] = Field(default_factory=list, alias="continue")
    goto: list[str] = Field(default_factory=list)
    terminate: list[str] = Field(default_factory=list)
    show_if: list[str] = Field(default_factory=list)
    applies_if: list[str] = Field(default_factory=list)


class TableRoleConfig(_Strict):
    code_cell: str
    version_cell: str
    recode_header: str
    section_header_max_chars: int = Field(gt=0)

    @field_validator("code_cell", "version_cell", "recode_header")
    @classmethod
    def _re(cls, v: str) -> str:
        re.compile(v)
        return v


class LexiconSpec(_Strict):
    name: str
    version: str
    label_patterns: list[str] = Field(min_length=1)
    label_exclude: list[str] = Field(default_factory=list)
    numbered_paragraphs_as_items: bool = False
    instruction_tags: dict[InstructionKind, list[str]]
    response_types: dict[str, ItemType] = Field(default_factory=dict)
    missing_code_labels: list[str] = Field(default_factory=list)
    opens_text_markers: list[str] = Field(default_factory=list)
    calculated_item_markers: list[str] = Field(default_factory=list)
    label_token: str
    codes_token: str
    navigation: NavigationPatterns
    navigation_keywords: list[str] = Field(default_factory=list)
    termination_kinds: dict[str, TerminationKind] = Field(default_factory=dict)
    table_roles: TableRoleConfig

    @field_validator("label_patterns")
    @classmethod
    def _labels(cls, v: list[str]) -> list[str]:
        for p in v:
            if "(?P<label>" not in p:
                raise ValueError(f"el patrón de etiqueta necesita un grupo 'label': {p}")
            re.compile(p)
        return v

    @field_validator("missing_code_labels", "opens_text_markers", "calculated_item_markers", "navigation_keywords")
    @classmethod
    def _regexes(cls, v: list[str]) -> list[str]:
        return _compiles(v)


@dataclass(frozen=True)
class Segment:
    """Fragmento de texto tras una etiqueta de instrucción (p. ej. ``PROG: RU``)."""

    kind: InstructionKind
    tag: str
    text: str


@dataclass(frozen=True)
class SplitText:
    statement: str  # texto antes de la primera etiqueta de instrucción
    segments: tuple[Segment, ...]


class Lexicon:
    """Léxico compilado y listo para usar."""

    def __init__(self, spec: LexiconSpec, sha256: str):
        self.spec = spec
        self.sha256 = sha256
        flags = re.IGNORECASE
        self._labels = [re.compile(p) for p in spec.label_patterns]
        self._exclude = {x.upper() for x in spec.label_exclude}
        tags = [(kind, t) for kind, ts in spec.instruction_tags.items() for t in ts]
        self._tag_kind = {t.upper(): kind for kind, t in tags}
        alternation = "|".join(sorted((re.escape(t) for _, t in tags), key=len, reverse=True))
        self._tag_re = re.compile(rf"(?<![A-Za-zÁÉÍÓÚÑáéíóúñ])({alternation})\s*:", flags) if tags else None
        rt = "|".join(sorted((re.escape(k) for k in spec.response_types), key=len, reverse=True))
        self._rtype_re = re.compile(rf"(?<![A-Za-z0-9.])({rt})(?![A-Za-z0-9])") if rt else None
        self._missing = [re.compile(p, flags) for p in spec.missing_code_labels]
        self._opens = [re.compile(p, flags) for p in spec.opens_text_markers]
        self._calc = [re.compile(p, flags) for p in spec.calculated_item_markers]
        self._nav_kw = [re.compile(p, flags) for p in spec.navigation_keywords]
        nav = spec.navigation

        def comp(ps: list[str]) -> list[re.Pattern]:
            return [
                re.compile(p.replace("{label}", f"(?:{spec.label_token})").replace("{codes}", f"(?:{spec.codes_token})"), flags)
                for p in ps
            ]

        self.nav = {
            "continue": comp(nav.continue_),
            "goto": comp(nav.goto),
            "terminate": comp(nav.terminate),
            "show_if": comp(nav.show_if),
            "applies_if": comp(nav.applies_if),
        }
        self.code_cell = re.compile(spec.table_roles.code_cell)
        self.version_cell = re.compile(spec.table_roles.version_cell)
        self.recode_header = re.compile(spec.table_roles.recode_header, flags)

    # Etiquetas -------------------------------------------------------------

    def match_label(self, text: str) -> Optional[tuple[str, str]]:
        """Devuelve (etiqueta, resto del texto) si el párrafo empieza con una etiqueta."""
        for rx in self._labels:
            m = rx.match(text)
            if m:
                label = m.group("label")
                if label.upper() in self._exclude:
                    continue
                return label, text[m.end():].strip()
        return None

    # Instrucciones ---------------------------------------------------------

    def split(self, text: str) -> SplitText:
        if self._tag_re is None:
            return SplitText(text.strip(), ())
        matches = list(self._tag_re.finditer(text))
        if not matches:
            return SplitText(text.strip(), ())
        segments = []
        for i, m in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            tag = m.group(1).upper()
            segments.append(Segment(self._tag_kind[tag], tag, text[m.end():end].strip()))
        return SplitText(text[: matches[0].start()].strip(), tuple(segments))

    def response_types(self, text: str) -> list[ItemType]:
        if self._rtype_re is None:
            return []
        keys = {k.upper(): v for k, v in self.spec.response_types.items()}
        return [keys[m.group(1).upper()] for m in self._rtype_re.finditer(text)]

    def strip_response_types(self, text: str) -> str:
        if self._rtype_re is None:
            return text
        return re.sub(r"\s+", " ", self._rtype_re.sub("", text)).strip(" .")

    # Opciones y otros marcadores ---------------------------------------------

    def is_missing_label(self, label: str) -> bool:
        return any(rx.search(label) for rx in self._missing)

    def opens_text(self, text: str) -> bool:
        return any(rx.search(text) for rx in self._opens)

    def is_calculated(self, text: str) -> bool:
        return any(rx.search(text) for rx in self._calc)

    def has_navigation_keyword(self, text: str) -> bool:
        return any(rx.search(text) for rx in self._nav_kw)

    def termination_kind(self, label: str) -> Optional[TerminationKind]:
        norm = re.sub(r"\s+", " ", label).strip().upper()
        for k, v in self.spec.termination_kinds.items():
            if re.sub(r"\s+", " ", k).strip().upper() == norm:
                return v
        return None


def load_lexicon(path: Optional[pathlib.Path | str] = None) -> Lexicon:
    p = pathlib.Path(path) if path else DEFAULT_LEXICON
    raw = p.read_bytes()
    data = yaml.safe_load(raw)
    return Lexicon(LexiconSpec.model_validate(data), hashlib.sha256(raw).hexdigest())
