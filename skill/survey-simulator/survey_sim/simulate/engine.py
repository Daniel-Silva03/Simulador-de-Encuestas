"""Motor de simulación (v1.1, §6–§7).

Reglas de ejecución:

- Modo ``estandar``: solo se ejecutan reglas explícitas.
- Modo ``experimental`` (expreso): también las inferidas, como hipótesis;
  cada agente lleva la lista de hipótesis usadas y las salidas, la marca
  HIPOTÉTICO.
- Las reglas opacas no se ejecutan nunca.
- **Bifurcación indeterminada** (§7.2): si una regla de flujo (filtro, salto,
  habilitación, ocultación, terminación, presentación de opciones) no se puede
  ejecutar en el modo vigente o su evaluación da U, el agente se interrumpe
  (``interrumpido_indeterminado``). Se conservan las respuestas anteriores y
  las posteriores se exportan como ``no_simulado``. No se elige rama ni se
  continúa como si la regla no existiera.
- Validación no ejecutable: la respuesta se conserva con la marca
  ``validacion_no_evaluada:<id>``. Cálculo no ejecutable o sin expresión
  estructurada: la variable se exporta como ``no_calculado``.

El motor no ejecuta expresiones del archivo: solo evalúa el AST cerrado de la
IR con el evaluador de Kleene. Toda la aleatoriedad sale de una semilla
declarada; cada agente recibe una semilla derivada con SHA-256.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from typing import Any, Optional

from survey_sim.compare.conditions import has_opaque
from survey_sim.correspondence.records import Equivalences, NO_EQUIVALENCES
from survey_sim.flowmodel import COMPUTATION, FLOW, VALIDATION, FlowItem, FlowModel, Nav, build
from survey_sim.ir.conditions import Truth, evaluate
from survey_sim.ir.models import Certainty, Instrument, Item, ItemKind, ItemType, Option
from survey_sim.ir.values import MissingCode, NotApplicable, Unanswered, UnknownToEvaluator, Valid

from . import SIMULATOR_VERSION
from .scenarios import DIVERSITY, FUNCTIONAL, SCENARIO, Scenario

STANDARD, EXPERIMENTAL = "estandar", "experimental"
COMPLETED, TERMINATED, INTERRUPTED = "completado", "terminado", "interrumpido_indeterminado"
# Estados de exportación adicionales (v1.1, §9.2): no son estados de la IR.
NO_SIMULADO, NO_CALCULADO = "NO_SIMULADO", "NO_CALCULADO"
RESAMPLE_LIMIT = 20
NUMERIC_DEFAULT = (0, 99)


class SimulationError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass
class Cell:
    state: str  # VALID | MISSING_CODE | UNANSWERED | NOT_APPLICABLE | UNKNOWN_TO_EVALUATOR | NO_SIMULADO | NO_CALCULADO
    value: Any = None


@dataclass
class AgentResult:
    index: int
    seed: int
    status: str
    cells: dict[str, Cell]
    interruption: Optional[dict] = None
    termination: Optional[str] = None
    marks: list[str] = field(default_factory=list)
    hypotheses: list[str] = field(default_factory=list)
    path: list[str] = field(default_factory=list)
    case: Optional[str] = None
    # Hipótesis usadas hasta decidir cada ítem (acumuladas, en orden de uso). Permite
    # marcar como hipotético exactamente lo que depende de una hipótesis (§7.8).
    hyp_at: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass
class SimulationResult:
    instrument: Instrument
    mode: str
    scenario: Scenario
    seed: int
    agents: list[AgentResult]
    columns: list[tuple[str, Optional[str]]]  # (variable, item_id)
    flow: FlowModel
    warnings: list[str]
    rules_executed: dict[str, int]
    rules_not_executed: dict[str, int]


def agent_seed(seed: int, index: int) -> int:
    h = hashlib.sha256(f"survey-simulator:{seed}:{index}".encode("ascii")).hexdigest()
    return int(h[:16], 16)


def executable(certainty: Certainty, mode: str, opaque: bool = False) -> bool:
    if opaque or certainty is Certainty.OPAQUE:
        return False
    if certainty is Certainty.EXPLICIT:
        return True
    return mode == EXPERIMENTAL


class _Interrupt(Exception):
    def __init__(self, item: str, rule: str, reason: str, u_reasons: tuple[str, ...] = ()):
        self.info = {"item": item, "rule": rule, "reason": reason, "u_reasons": list(u_reasons)}


def _grid_options(inst: Instrument) -> dict[str, tuple[Option, ...]]:
    out: dict[str, tuple[Option, ...]] = {}
    for s in inst.sections:
        for it in s.items:
            if it.grid is not None and it.grid.shared_options:
                for r in it.grid.rows:
                    out[r.item_id] = it.grid.shared_options
    return out


class _Agent:
    def __init__(self, sim: "Simulator", index: int, case: Optional[dict]):
        self.sim = sim
        self.index = index
        self.seed = agent_seed(sim.seed, index)
        self.rng = random.Random(self.seed)
        self.env: dict[str, Any] = {}
        self.cells: dict[str, Cell] = {}
        self.marks: list[str] = []
        self.hyps: list[str] = []
        self.hyp_at: dict[str, tuple[str, ...]] = {}
        self.path: list[str] = []
        self.case = case

    # Utilidades ------------------------------------------------------------

    def _use(self, certainty: Certainty, hyp: Optional[str], rule_id: str) -> None:
        if certainty is not Certainty.EXPLICIT and hyp and hyp not in self.hyps:
            self.hyps.append(hyp)
        self.sim.executed[rule_id] = self.sim.executed.get(rule_id, 0) + 1

    def _skip(self, rule_id: str) -> None:
        self.sim.not_executed[rule_id] = self.sim.not_executed.get(rule_id, 0) + 1

    def _eval(self, cond: Any, item: str, rule: str, certainty: Certainty, hyp: Optional[str]) -> bool:
        # §6.3: una condición que contiene un nodo Opaque (o una función externa) es opaca: nunca se ejecuta.
        opaque = has_opaque(cond)
        if not executable(certainty, self.sim.mode, opaque):
            self._skip(rule)
            raise _Interrupt(item, rule, "opaca" if opaque or certainty is Certainty.OPAQUE else "inferida_no_autorizada")
        r = evaluate(cond, self.env)
        if r.truth is Truth.UNKNOWN:
            self._skip(rule)
            raise _Interrupt(item, rule, "U", r.reasons)
        self._use(certainty, hyp, rule)
        return r.truth is Truth.TRUE

    def _set(self, it: Item, state: Any, cell: Cell) -> None:
        self.env[it.id] = state
        self.cells[it.id] = cell
        self.hyp_at[it.id] = tuple(self.hyps)

    def _eval_value(self, it: Item, code: Any) -> Any:
        """Valor para el evaluador: el código, o el literal declarado para esa variable (v1.1, §4)."""
        m = self.sim.eq.for_var(self.sim.role, it.id)
        return m.get(code, code) if isinstance(code, str) else code

    # Respuestas -------------------------------------------------------------

    def _options(self, fi: FlowItem) -> list[Option]:
        it = fi.item
        opts = list(it.options or self.sim.grid_opts.get(it.id, ()))
        shown = []
        disp = {c: (cond, cert, hyp) for c, cond, cert, hyp in fi.option_display}
        for o in opts:
            if o.code in disp:
                cond, cert, hyp = disp[o.code]
                if not self._eval(cond, it.id, f"{it.id}#opt_display:{o.code}", cert, hyp):
                    continue
            shown.append(o)
        return shown

    def _pick_weighted(self, opts: list[Option], weights: dict[str, float]) -> Option:
        cand = [(o, weights.get(o.code, 0.0)) for o in opts]
        total = sum(w for _, w in cand)
        if total <= 0:
            raise _Interrupt(cand[0][0].code if cand else "?", "escenario", "escenario_sin_peso_para_opciones_mostradas")
        x = self.rng.random() * total
        acc = 0.0
        for o, w in cand:
            acc += w
            if x < acc:
                return o
        return cand[-1][0]

    def _answer(self, fi: FlowItem) -> None:
        it = fi.item
        sc = self.sim.scenario
        if it.kind in (ItemKind.SYSTEM, ItemKind.PARADATA):
            self._set(it, UnknownToEvaluator(reason="sistema_o_paradato"), Cell("UNKNOWN_TO_EVALUATOR"))
            self.marks.append(f"sistema_o_paradato:{it.id}")
            return
        if it.type in (ItemType.GRID, ItemType.INFORMATIVE) or it.kind is ItemKind.INFORMATIVE:
            return
        if it.kind is ItemKind.CALCULATED:
            self._set(it, UnknownToEvaluator(reason="calculo_no_ejecutable"), Cell(NO_CALCULADO))
            return
        opts = self._options(fi)
        if sc.mode == FUNCTIONAL:
            resp = self.case["respuestas"].get(it.id) if self.case else None
            if resp is None:
                self._set(it, Unanswered(), Cell("UNANSWERED"))
                self.marks.append(f"sin_respuesta_en_guion:{it.id}")
                return
            codes = resp if isinstance(resp, tuple) else (resp,)
            byc = {o.code: o for o in opts}
            if opts and any(c not in byc for c in codes):
                # Distinguir un código que no existe de uno que existe pero está oculto por una
                # regla de presentación de opciones: solo el primero es «inexistente».
                all_codes = {o.code for o in (it.options or self.sim.grid_opts.get(it.id, ()))}
                missing = [c for c in codes if c not in byc]
                reason = "codigo_del_guion_inexistente" if any(c not in all_codes for c in missing) else "codigo_del_guion_oculto"
                raise _Interrupt(it.id, "guion", reason)
            if it.type is ItemType.MULTIPLE:
                self._set(it, Valid(value=tuple(self._eval_value(it, c) for c in codes)), Cell("VALID", ";".join(codes)))
            elif opts and byc[codes[0]].is_missing_code:
                self._set(it, MissingCode(value=self._eval_value(it, codes[0])), Cell("MISSING_CODE", codes[0]))
            else:
                v = codes[0]
                self._set(it, Valid(value=self._eval_value(it, v)), Cell("VALID", v))
            return
        dist = sc.distributions.get(it.id) if sc.mode == SCENARIO else None
        if sc.mode == SCENARIO and dist is None and not sc.default_uniform:
            raise _Interrupt(it.id, "escenario", "item_sin_distribucion_declarada")
        if opts:
            if it.type is ItemType.MULTIPLE:
                self._answer_multiple(it, opts, dist)
                return
            o = self._pick_weighted(opts, dist["codes"]) if dist and "codes" in dist else opts[self.rng.randrange(len(opts))]
            v = self._eval_value(it, o.code)
            self._set(it, MissingCode(value=v) if o.is_missing_code else Valid(value=v),
                      Cell("MISSING_CODE" if o.is_missing_code else "VALID", o.code))
            return
        if it.type in (ItemType.SINGLE, ItemType.SCALE, ItemType.MULTIPLE, ItemType.RANKING):
            self._set(it, UnknownToEvaluator(reason="sin_opciones"), Cell(NO_SIMULADO))
            self.marks.append(f"sin_opciones:{it.id}")
            return
        if it.type is ItemType.NUMERIC:
            lo, hi = NUMERIC_DEFAULT
            if dist and "rango" in dist:
                lo, hi = dist["rango"]
            elif it.constraints and isinstance(it.constraints.min_value, int) and isinstance(it.constraints.max_value, int):
                lo, hi = it.constraints.min_value, it.constraints.max_value
            else:
                self.marks.append(f"rango_artificial:{it.id}")
            v = self.rng.randint(lo, hi)
            self._set(it, Valid(value=v), Cell("VALID", v))
            return
        if it.type is ItemType.DATE:
            v = f"2000-01-{1 + self.rng.randrange(28):02d}"
        elif it.type is ItemType.TIME:
            v = f"{self.rng.randrange(24):02d}:{self.rng.randrange(60):02d}"
        else:  # texto, geo, media, desconocido: valores que se ven ficticios (v1.0, 9)
            v = f"SINT-{self.index:06d}-{self.rng.randrange(1000000):06d}"
        self._set(it, Valid(value=v), Cell("VALID", v))

    def _answer_multiple(self, it: Item, opts: list[Option], dist: Optional[dict]) -> None:
        excl = [o for o in opts if o.is_exclusive or o.is_missing_code]
        normal = [o for o in opts if o not in excl]
        cons = it.constraints
        lo = max(1, cons.min_selections or 1) if cons else 1
        hi = min(len(normal), cons.max_selections) if cons and cons.max_selections else len(normal)
        if excl and (not normal or self.rng.random() < len(excl) / (len(opts) + 1)):
            o = excl[self.rng.randrange(len(excl))]
            v = self._eval_value(it, o.code)
            self._set(it, MissingCode(value=v) if o.is_missing_code else Valid(value=(v,)),
                      Cell("MISSING_CODE" if o.is_missing_code else "VALID", o.code))
            return
        if not normal or hi < lo:
            self._set(it, UnknownToEvaluator(reason="restricciones_inconsistentes"), Cell(NO_SIMULADO))
            self.marks.append(f"restricciones_inconsistentes:{it.id}")
            return
        k = self.rng.randint(lo, hi)
        chosen = sorted(self.rng.sample(range(len(normal)), k))
        codes = [normal[i].code for i in chosen]
        self._set(it, Valid(value=tuple(self._eval_value(it, c) for c in codes)), Cell("VALID", ";".join(codes)))

    # Recorrido --------------------------------------------------------------

    def _shown(self, fi: FlowItem) -> bool:
        it = fi.item
        for o in fi.opaque:
            if o.klass == FLOW and any(k in o.rule_id for k in ("/filtros", "/habil_cnd", "/oculta_cnd")):
                self._skip(o.rule_id)
                raise _Interrupt(it.id, o.rule_id, "opaca")
        shown = True
        for r in fi.show:
            if r.show is None:
                self._skip(r.rule_id)
                raise _Interrupt(it.id, r.rule_id, "sin_condicion")
            if not self._eval(r.show, it.id, r.rule_id, r.certainty, r.hypothesis):
                shown = False
        return shown

    def _validate(self, fi: FlowItem) -> bool:
        """True si todas las validaciones ejecutables se cumplen."""
        ok = True
        for c in fi.checks:
            if c.condition is None or not executable(c.certainty, self.sim.mode):
                self._skip(c.rule_id)
                if f"validacion_no_evaluada:{c.rule_id}" not in self.marks:
                    self.marks.append(f"validacion_no_evaluada:{c.rule_id}")
                continue
            r = evaluate(c.condition, self.env)
            if r.truth is Truth.UNKNOWN:
                self._skip(c.rule_id)
                if f"validacion_no_evaluada:{c.rule_id}" not in self.marks:
                    self.marks.append(f"validacion_no_evaluada:{c.rule_id}")
                continue
            self._use(c.certainty, c.hypothesis, c.rule_id)
            # Hipótesis de los controles: la condición verdadera señala el error.
            if r.truth is Truth.TRUE:
                if c.action == "block":
                    ok = False
                else:
                    self.marks.append(f"validacion_advertencia:{c.rule_id}")
        for o in fi.opaque:
            if o.klass == VALIDATION:
                self._skip(o.rule_id)
                if f"validacion_no_evaluada:{o.rule_id}" not in self.marks:
                    self.marks.append(f"validacion_no_evaluada:{o.rule_id}")
        return ok

    def _calcs(self, fi: FlowItem) -> None:
        for c in fi.calcs:
            applies = True
            if c.condition is not None and executable(c.certainty, self.sim.mode):
                r = evaluate(c.condition, self.env)
                if r.truth is Truth.FALSE:
                    applies = False
                    self._use(c.certainty, c.hypothesis, c.rule_id)
            if applies:
                # La IR no contiene una expresión de cálculo estructurada: no se ejecuta.
                self._skip(c.rule_id)
                tgt = self.sim.flow.by_id.get(c.target)
                if tgt is not None:
                    self._set(tgt.item, UnknownToEvaluator(reason=f"no_calculado:{c.rule_id}"), Cell(NO_CALCULADO))
                self.marks.append(f"calculo_no_ejecutado:{c.rule_id}")
        for o in fi.opaque:
            if o.klass == COMPUTATION:
                self._skip(o.rule_id)
                self._set(fi.item, UnknownToEvaluator(reason=f"no_calculado:{o.rule_id}"), Cell(NO_CALCULADO))
                self.marks.append(f"calculo_no_ejecutado:{o.rule_id}")

    def _navigate(self, fi: FlowItem) -> Optional[Nav]:
        it = fi.item
        for o in fi.opaque:
            if o.klass == FLOW and not any(k in o.rule_id for k in ("/filtros", "/habil_cnd", "/oculta_cnd")):
                self._skip(o.rule_id)
                raise _Interrupt(it.id, o.rule_id, "opaca")
        cell = self.cells.get(it.id)
        chosen = set()
        if cell is not None and cell.state in ("VALID", "MISSING_CODE") and isinstance(cell.value, str):
            chosen = set(cell.value.split(";"))
        opt_navs = [n for n in fi.nav if n.codes]
        hits = [n for n in opt_navs if set(n.codes) & chosen]
        for n in hits:
            if not executable(n.certainty, self.sim.mode):
                self._skip(n.rule_id)
                raise _Interrupt(it.id, n.rule_id, "inferida_no_autorizada")
        if len({(n.kind, n.target) for n in hits}) > 1:
            raise _Interrupt(it.id, hits[0].rule_id, "ruteo_multiple_conflictivo")
        if hits:
            n = hits[0]
            self._use(n.certainty, n.hypothesis, n.rule_id)
            if n.kind == "unknown":
                raise _Interrupt(it.id, n.rule_id, "ruteo_desconocido")
            return n
        for n in fi.nav:
            if n.codes:
                continue
            if n.condition is None and not n.implicit:
                self._skip(n.rule_id)
                raise _Interrupt(it.id, n.rule_id, "sin_condicion")
            if n.condition is None:
                # Condición implícita (D-12): es una interpretación, nunca explícita.
                if not executable(n.certainty, self.sim.mode):
                    self._skip(n.rule_id)
                    raise _Interrupt(it.id, n.rule_id, "inferida_no_autorizada")
                self._use(n.certainty, n.hypothesis, n.rule_id)
                return n
            if self._eval(n.condition, it.id, n.rule_id, n.certainty, n.hypothesis):
                return n
        return None

    def run(self) -> AgentResult:
        flow = self.sim.flow
        items = flow.items
        status, interruption, term = COMPLETED, None, None
        pos = 0
        try:
            for o in flow.start_opaque:
                if o.klass == FLOW:
                    self._skip(o.rule_id)
                    raise _Interrupt("(inicio)", o.rule_id, "opaca_sin_posicion")
            while pos < len(items):
                fi = items[pos]
                it = fi.item
                self.path.append(it.id)
                if not self._shown(fi):
                    self._set(it, NotApplicable(), Cell("NOT_APPLICABLE"))
                    pos += 1
                    continue
                for _ in range(RESAMPLE_LIMIT):
                    self._answer(fi)
                    if self._validate(fi):
                        break
                else:
                    raise _Interrupt(it.id, "validacion", "validacion_bloqueante_no_satisfecha")
                self._calcs(fi)
                n = self._navigate(fi)
                if n is None:
                    pos += 1
                    continue
                if n.kind == "terminate":
                    status, term = TERMINATED, n.target
                    pos += 1
                    while pos < len(items):
                        self._set(items[pos].item, NotApplicable(), Cell("NOT_APPLICABLE"))
                        pos += 1
                    break
                tgt = flow.index(n.target or "")
                if tgt is None:
                    raise _Interrupt(it.id, n.rule_id, "destino_inexistente")
                if tgt <= pos:
                    raise _Interrupt(it.id, n.rule_id, "salto_hacia_atras")
                for k in range(pos + 1, tgt):
                    self._set(items[k].item, NotApplicable(), Cell("NOT_APPLICABLE"))
                pos = tgt
        except _Interrupt as e:
            status, interruption = INTERRUPTED, e.info
            for f in items:
                if f.item.id not in self.cells:
                    self.cells[f.item.id] = Cell(NO_SIMULADO)
        for f in items:
            self.cells.setdefault(f.item.id, Cell(NO_SIMULADO) if status == INTERRUPTED else Cell("NOT_APPLICABLE"))
        return AgentResult(self.index, self.seed, status, self.cells, interruption, term, self.marks, self.hyps, self.path,
                           self.case["nombre"] if self.case else None, self.hyp_at)


class Simulator:
    def __init__(self, inst: Instrument, *, mode: str, scenario: Scenario, seed: int, eq: Equivalences = NO_EQUIVALENCES):
        if mode not in (STANDARD, EXPERIMENTAL):
            raise SimulationError([f"modo desconocido: {mode}"])
        self.inst = inst
        self.role = inst.role.value
        self.mode = mode
        self.scenario = scenario
        self.seed = seed
        self.eq = eq
        self.flow = build(inst)
        self.grid_opts = _grid_options(inst)
        self.executed: dict[str, int] = {}
        self.not_executed: dict[str, int] = {}
        ids = set(self.flow.by_id)
        unknown = sorted(set(scenario.distributions) - ids) + sorted(set(scenario.sensitive) - ids)
        if unknown:
            raise SimulationError([f"ítems del escenario que no existen en el instrumento: {', '.join(unknown)}"])

    def run(self, n_agents: int) -> SimulationResult:
        if self.scenario.mode == FUNCTIONAL:
            agents = [_Agent(self, i + 1, c).run() for i, c in enumerate(self.scenario.cases)]
        else:
            if n_agents < 1:
                raise SimulationError(["número de agentes: al menos 1"])
            agents = [_Agent(self, i + 1, None).run() for i in range(n_agents)]
        cols = [(v.name, v.item_id) for v in self.inst.variables]
        warnings = []
        if self.role == "program" and self.mode == STANDARD:
            warnings.append("Limitación §7.5: mientras ninguna hipótesis GIM esté confirmada, la simulación de un programa "
                            "GIM en modo estándar se interrumpe en la primera regla de flujo GIM.")
        if self.mode == EXPERIMENTAL:
            warnings.append("HIPOTÉTICO: se ejecutaron reglas inferidas como hipótesis; ningún resultado confirma su semántica.")
        if self.flow.unplaced:
            warnings.append("reglas sin posición en el flujo: " + ", ".join(self.flow.unplaced))
        return SimulationResult(self.inst, self.mode, self.scenario, self.seed, agents, cols, self.flow, warnings,
                                dict(sorted(self.executed.items())), dict(sorted(self.not_executed.items())))


__all__ = ["Simulator", "SimulationResult", "AgentResult", "STANDARD", "EXPERIMENTAL", "SIMULATOR_VERSION"]
