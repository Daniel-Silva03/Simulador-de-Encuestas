"""Punto de entrada de la Skill: ``python -m survey_sim <subcomando>``.

Subcomandos:

- ``comprobar``: comprueba las dependencias (no instala nada).
- ``leer-word``, ``leer-gim``: IR e informe de lectura de un instrumento.
- ``corresponder``: identidad, emparejamiento y comparación de un cuestionario
  Word y un programa GIM, con informes JSON, Excel y Markdown y plantillas
  de pareos y equivalencias para que el usuario las confirme.
- ``declarar-identidad``: añade una declaración humana de identidad al
  registro (log de solo adición).
- ``simular``: simulación sintética de un instrumento (base CSV/Excel,
  árbol de recorridos posibles y metadatos).
- ``consolidar``: correspondencia + simulación diferencial diseño ↔ programa
  sobre parejas declaradas, en un informe consolidado (E3).

Códigos de salida (D-A3-1): 0 éxito; 1 error inesperado (no se captura y
conserva la traza); 2 entrada; 3 configuración o registros del usuario;
4 IR inválida; 5 salida.

Privacidad: los informes son vistas minimizadas; el texto del instrumento
solo aparece con ``--incluir-texto``. Los mensajes de error no copian
contenido de los archivos. Ningún subcomando accede a la red.

Salida coherente: cada subcomando escribe un conjunto de archivos cuyo
**último** archivo es la marca de que el conjunto está completo (el informe
o los metadatos, que llevan el SHA-256 de los demás). Se escriben primero en
temporales, se retira la marca anterior, se publican los demás y la marca al
final. Si algo falla se borran los temporales y lo publicado sin marca.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import pathlib
import sys
import tempfile
from typing import Any, Callable, Optional

EXIT_OK, EXIT_INPUT, EXIT_CONFIG, EXIT_IR, EXIT_OUTPUT = 0, 2, 3, 4, 5
DEPENDENCIES = ("pydantic", "lxml", "yaml", "openpyxl")


class CliError(Exception):
    """Error esperado: se informa en una línea, sin traza, con su código."""

    def __init__(self, kind: str, where: object, reason: str, code: int):
        super().__init__(reason)
        self.kind, self.where, self.reason, self.code = kind, where, reason, code


# --------------------------------------------------------------------------
# Mensajes sin contenido de los archivos
# --------------------------------------------------------------------------


def _os_reason(e: OSError) -> str:
    return e.strerror or type(e).__name__


def _config_reason(e: BaseException) -> str:
    import pydantic
    import yaml

    if isinstance(e, OSError):
        return _os_reason(e)
    if isinstance(e, yaml.YAMLError):
        m = getattr(e, "problem_mark", None)
        return f"YAML inválido (línea {m.line + 1}, columna {m.column + 1})" if m is not None else "YAML inválido"
    if isinstance(e, pydantic.ValidationError):
        types = sorted({err["type"] for err in e.errors()})
        return f"esquema inválido: {e.error_count()} errores (tipos: {', '.join(types)}); no se muestran valores de la configuración"
    found = getattr(e, "errors", None)
    count = f"{len(found)} errores de validación; " if isinstance(found, list) and found else ""
    return f"{type(e).__name__}: configuración no válida ({count}no se muestran valores de la configuración)"


def _record_reason(e: BaseException) -> str:
    """Errores de registros del usuario: solo nombres de campo, nunca valores."""
    found = getattr(e, "errors", None) or []
    fields = sorted({str(x).split(":")[0].split("[")[0] for x in found})
    return f"{type(e).__name__}: {len(found)} errores en los campos {', '.join(fields)}" if fields else _config_reason(e)


def _ir_reason(e: Any) -> str:
    codes = sorted({i.code for i in e.issues})
    return f"la IR no es válida: {len(e.issues)} errores ({', '.join(codes)}); probable defecto del lector; no se muestran valores"


# --------------------------------------------------------------------------
# Publicación coherente de un conjunto de archivos
# --------------------------------------------------------------------------

_unlink = os.unlink
_replace = os.replace


def _write_tmp(out: pathlib.Path, data: bytes) -> pathlib.Path:
    fd, name = tempfile.mkstemp(dir=out, prefix=".survey_sim.", suffix=".tmp")
    done = False
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        done = True
    finally:
        if not done and os.path.lexists(name):
            os.unlink(name)
    return pathlib.Path(name)


def publish(out: pathlib.Path, files: dict[str, bytes], marker: str) -> None:
    """Publica ``files``; ``marker`` (uno de ellos) se publica el último.

    Tras un fallo solo puede quedar el conjunto anterior intacto (fallo al
    escribir los temporales) o ningún ``marker``: nunca una marca que no
    corresponda a los demás archivos.
    """
    assert marker in files
    out.mkdir(parents=True, exist_ok=True)
    order = [n for n in sorted(files) if n != marker] + [marker]
    tmps: dict[str, pathlib.Path] = {}
    published: list[pathlib.Path] = []
    committed = False
    try:
        for n in order:
            tmps[n] = _write_tmp(out, files[n])
        for n in [marker] + order[:-1]:  # la marca anterior, primero
            try:
                _unlink(out / n)
            except FileNotFoundError:
                pass
        for n in order:
            _replace(tmps[n], out / n)
            if n != marker:
                published.append(out / n)
        committed = True
    finally:
        for t in tmps.values():
            if os.path.lexists(t):
                try:
                    os.unlink(t)
                except OSError:
                    pass
        if not committed:
            for p in published:
                try:
                    os.unlink(p)
                except OSError:
                    pass


def _publish_or_fail(out: pathlib.Path, files: dict[str, bytes], marker: str) -> None:
    try:
        publish(out, files, marker)
    except OSError as e:
        raise CliError("salida", out, f"{_os_reason(e)}; no queda ningún conjunto incompleto con su marca de completitud", EXIT_OUTPUT)


# --------------------------------------------------------------------------
# Lectura de insumos y configuración
# --------------------------------------------------------------------------


def _read_bytes(path: pathlib.Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as e:
        raise CliError("entrada", path, _os_reason(e), EXIT_INPUT)


def _where(path: pathlib.Path, data: bytes) -> str:
    return f"{path} (sha256 {hashlib.sha256(data).hexdigest()[:16]}…)"


def _config(loader: Callable, path: Optional[pathlib.Path], what: str):
    import yaml

    try:
        return loader(path)
    except (OSError, ValueError, yaml.YAMLError) as e:
        raise CliError("configuración", path or f"{what} por defecto", _config_reason(e), EXIT_CONFIG)


def _lexicon(path):
    from survey_sim.word.lexicon import load_lexicon
    return _config(load_lexicon, path, "léxico")


def _semantics(path):
    from survey_sim.adapters.gim.semantics import load_semantics
    return _config(load_semantics, path, "registro de semántica")


def _corr_config(path):
    from survey_sim.correspondence.config import load_config
    return _config(load_config, path, "configuración de correspondencia")


def _protector(key_path: Optional[pathlib.Path]):
    from survey_sim.util.privacy import NO_KEY, PrivacyError, TextProtector, load_key_file

    if key_path is None:
        return NO_KEY
    try:
        return TextProtector(load_key_file(key_path))
    except (OSError, PrivacyError) as e:
        # Nunca se muestra el contenido de la clave.
        reason = _os_reason(e) if isinstance(e, OSError) else f"{type(e).__name__}: clave no válida (se exigen 32 bytes)"
        raise CliError("configuración", key_path, reason, EXIT_CONFIG)


def _read_word(path: pathlib.Path, lexicon, *, include_text=False, hmac_key=None):
    from survey_sim.ir.validate import IRValidationError
    from survey_sim.word.docx_xml import DocxError
    from survey_sim.word.to_ir import read_docx_bytes

    data = _read_bytes(path)
    try:
        return read_docx_bytes(data, path.name, lexicon, include_text=include_text, hmac_key=hmac_key), data
    except DocxError as e:
        raise CliError("entrada", _where(path, data), str(e), EXIT_INPUT)
    except IRValidationError as e:
        raise CliError("ir", _where(path, data), _ir_reason(e), EXIT_IR)


def _read_gim(path: pathlib.Path, semantics, hmac_key=None):
    from survey_sim.adapters import base
    from survey_sim.adapters.gim import read_gim_bytes
    from survey_sim.adapters.gim.container import GimContainerError
    from survey_sim.ir.validate import IRValidationError

    data = _read_bytes(path)
    try:
        if base.choose(data).name != "gim":
            raise base.UnsupportedProgram("formato de programa no soportado")
        return read_gim_bytes(data, path.name, semantics, hmac_key=hmac_key), data
    except (base.UnsupportedProgram, GimContainerError) as e:
        raise CliError("entrada", _where(path, data), str(e), EXIT_INPUT)
    except IRValidationError as e:
        raise CliError("ir", _where(path, data), _ir_reason(e), EXIT_IR)


def _key_bytes(path: Optional[pathlib.Path]) -> Optional[bytes]:
    if path is None:
        return None
    _protector(path)  # valida
    from survey_sim.util.privacy import load_key_file
    return load_key_file(path)


def _versions() -> dict:
    from survey_sim.adapters.gim.expr_parser import PARSER_VERSION
    from survey_sim.adapters.gim.to_ir import ADAPTER_VERSION as GIM_VERSION
    from survey_sim.correspondence import CORRESPONDENCE_VERSION, RULESET_VERSION
    from survey_sim.simulate import SIMULATOR_VERSION
    from survey_sim.word.to_ir import ADAPTER_VERSION as WORD_VERSION

    return {"word_adapter": WORD_VERSION, "gim_adapter": GIM_VERSION, "expr_parser": PARSER_VERSION,
            "correspondence": CORRESPONDENCE_VERSION, "ruleset": RULESET_VERSION, "simulator": SIMULATOR_VERSION}


def _dump(d: Any) -> bytes:
    return (json.dumps(d, ensure_ascii=False, indent=1, sort_keys=True, default=str) + "\n").encode("utf-8")


# --------------------------------------------------------------------------
# Subcomandos
# --------------------------------------------------------------------------


def cmd_comprobar(a) -> int:
    import importlib

    missing, found = [], {}
    for mod in DEPENDENCIES:
        try:
            m = importlib.import_module(mod)
            found[mod] = getattr(m, "__version__", getattr(m, "VERSION", "?"))
        except ImportError:
            missing.append(mod)
    if missing:
        raise CliError("configuración", "entorno", "faltan dependencias: " + ", ".join(missing) +
                       ". La Skill no instala nada desde internet; instálelas en el entorno antes de usarla", EXIT_CONFIG)
    print("dependencias: " + ", ".join(f"{k} {v}" for k, v in found.items()) + f"; python {sys.version.split()[0]}")
    print("versiones: " + ", ".join(f"{k}={v}" for k, v in _versions().items()))
    return EXIT_OK


def cmd_leer_word(a) -> int:
    from survey_sim.ir.serialize import to_json_bytes

    res, _ = _read_word(a.docx, _lexicon(a.lexico), include_text=a.incluir_texto, hmac_key=_key_bytes(a.clave_hmac))
    stem = a.docx.stem
    _publish_or_fail(a.salida, {f"{stem}.ir.json": to_json_bytes(res.instrument), f"{stem}.report.json": res.report_json_bytes()},
                     f"{stem}.report.json")
    c = res.report.counts
    print(f"bloques={c['blocks']} secciones={c['sections']} ítems={c['items']} reglas={c['rules']} no_resueltos={c['unresolved']}")
    print(f"salida: {a.salida}/{stem}.ir.json (IR confidencial) y {stem}.report.json (vista minimizada)")
    return EXIT_OK


def cmd_leer_gim(a) -> int:
    from survey_sim.ir.serialize import to_json_bytes

    res, _ = _read_gim(a.programa, _semantics(a.semantica), hmac_key=_key_bytes(a.clave_hmac))
    stem = a.programa.stem
    _publish_or_fail(a.salida, {f"{stem}.ir.json": to_json_bytes(res.instrument), f"{stem}.report.json": res.report_json_bytes()},
                     f"{stem}.report.json")
    c = res.report.counts
    print(f"variables={c['variables']} grillas={c['grids']} reglas={c['rules']} opacas={c['opaque_rules']} no_resueltos={c['unresolved']}")
    print(f"salida: {a.salida}/{stem}.ir.json (IR confidencial) y {stem}.report.json")
    return EXIT_OK


def _pair_inputs(a):
    lex, sem, cfg = _lexicon(a.lexico), _semantics(a.semantica), _corr_config(a.config)
    d, _ = _read_word(a.diseno, lex)
    p, _ = _read_gim(a.programa, sem)
    return d.instrument, p.instrument, lex, sem, cfg


def _pairs_template(rep: dict, binding: dict) -> bytes:
    import yaml

    pares = [{"design": p["design"], "program": p["program"], "estado_propuesto": p["state"], "confirmado": False,
              "motivo": ""} for p in rep["matching"]["pairs"]]
    doc = {"record_type": "pareos", "binding": binding,
           "instrucciones": "Propuesta, no confirmación (v1.1, §3.4). Revise cada pareja y ponga confirmado: true solo "
                            "en las que haya verificado. Las parejas sin confirmar nunca permiten concluir «equivalente».",
           "pares": pares}
    return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False).encode("utf-8")


def _equivalences_template(rep: dict, binding: dict) -> bytes:
    import yaml

    doc = {"record_type": "equivalencias", "binding": binding,
           "instrucciones": "Equivalencias de tipo declaradas por variable (v1.1, §4). No hay coerción general: sin "
                            "declaración, «1» (texto) y 1 (número) no son equivalentes. Añada una entrada por variable, "
                            "p. ej. {lado: program, variable: P1, pares: [{texto: '1', literal: 1}]}.",
           "variables": []}
    return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False).encode("utf-8")


def cmd_corresponder(a) -> int:
    from survey_sim.correspondence.records import RecordError
    from survey_sim.correspondence.run import correspond, report_dict, report_md, report_xlsx

    d, p, lex, sem, cfg = _pair_inputs(a)
    protector = _protector(a.clave_hmac)
    import yaml
    try:
        r = correspond(d, p, cfg=cfg, semantics=sem, lexicon=lex, review_log=a.registro, pairs_path=a.pareos,
                       equivalences_path=a.equivalencias)
    except (OSError, RecordError, yaml.YAMLError) as e:
        where = "registros del usuario (--registro, --pareos, --equivalencias)"
        raise CliError("configuración", where, _os_reason(e) if isinstance(e, OSError) else _record_reason(e), EXIT_CONFIG)
    rep = report_dict(r, include_text=a.incluir_texto, protector=protector)
    files = {
        "correspondencia.xlsx": report_xlsx(rep),
        "correspondencia.md": report_md(rep).encode("utf-8"),
        "plantilla_pareos.yaml": _pairs_template(rep, r.binding),
        "plantilla_equivalencias.yaml": _equivalences_template(rep, r.binding),
    }
    files["correspondencia.json"] = _dump({**rep, "files": {k: hashlib.sha256(v).hexdigest() for k, v in sorted(files.items())}})
    _publish_or_fail(a.salida, files, "correspondencia.json")
    idn, comp = rep["identity"], rep["comparison"]
    print(f"resultado={rep['result']} identidad={idn['verdict']} comparación_habilitada={idn['comparison_enabled']} "
          f"parejas={len(rep['matching']['pairs'])} hallazgos=" + ",".join(f"{k}:{v}" for k, v in comp["findings_by_level"].items()))
    open_b = [b["id"] for b in idn["blocking"] if b.get("status") == "open" and not b["informative"]]
    if not idn["comparison_enabled"]:
        print("la comparación no está habilitada: revise el informe; si corresponde, registre una declaración con "
              "`declarar-identidad`" + (f" (bloqueos abiertos: {', '.join(open_b)})" if open_b else ""))
    print(f"salida: {a.salida}/correspondencia.json, .xlsx, .md, plantilla_pareos.yaml, plantilla_equivalencias.yaml")
    return EXIT_OK


def cmd_declarar_identidad(a) -> int:
    from survey_sim.correspondence.records import RecordError, append_record
    from survey_sim.correspondence.run import correspond, make_binding

    d, p, lex, sem, cfg = _pair_inputs(a)
    binding = make_binding(d, p, cfg, sem, lex)
    ack = [x for x in (a.reconoce or "").split(",") if x]
    if a.reconocer_bloqueos:
        r = correspond(d, p, cfg=cfg, semantics=sem, lexicon=lex, review_log=a.registro)
        ack += [b["id"] for b in r.identity.blocking if b.get("status") == "open"]
    date = a.fecha or _dt.date.today().isoformat()
    rid = a.id or "decl-" + hashlib.sha256(json.dumps([binding, a.veredicto, date, sorted(set(ack))], sort_keys=True).encode()).hexdigest()[:12]
    rec = {"record_type": "identity_declaration", "id": rid, "date": date, "binding": binding, "verdict": a.veredicto,
           "reason": a.motivo, "declared_by_role": a.rol, "acknowledges": sorted(set(ack))}
    if a.sustituye:
        rec["supersedes"] = a.sustituye
    try:
        append_record(a.registro, rec)
    except RecordError as e:
        raise CliError("configuración", a.registro, _record_reason(e), EXIT_CONFIG)
    except OSError as e:
        raise CliError("salida", a.registro, _os_reason(e), EXIT_OUTPUT)
    print(f"declaración registrada: id={rid} veredicto={a.veredicto} reconoce={len(set(ack))} bloqueos; registro={a.registro}")
    return EXIT_OK


def _equivalences_for(path: Optional[pathlib.Path], inst) -> Any:
    """Equivalencias para simular un instrumento: el archivo debe ser de una pareja que lo incluya."""
    from survey_sim.correspondence.records import NO_EQUIVALENCES, load_equivalences

    if path is None:
        return NO_EQUIVALENCES
    import yaml
    try:
        data = yaml.safe_load(path.read_bytes())
    except (OSError, yaml.YAMLError) as e:
        raise CliError("configuración", path, _config_reason(e), EXIT_CONFIG)
    b = data.get("binding") if isinstance(data, dict) else None
    eq = load_equivalences(path, b if isinstance(b, dict) else {})
    if eq.errors:
        raise CliError("configuración", path, f"equivalencias no válidas: {len(eq.errors)} errores", EXIT_CONFIG)
    inputs = (b or {}).get("inputs") or {}
    vers = (b or {}).get("versions") or {}
    cur = _versions()
    if inst.source.sha256 not in inputs.values() or any(vers.get(k) != cur[k] for k in vers if k in cur and k != "correspondence") \
            or vers.get("correspondence") not in (None, cur["correspondence"]):
        raise CliError("configuración", path, "equivalencias no vigentes para este instrumento o estas versiones (binding)", EXIT_CONFIG)
    return eq


def cmd_simular(a) -> int:
    from survey_sim.flowmodel import build
    from survey_sim.simulate.engine import EXPERIMENTAL, STANDARD, SimulationError, Simulator, _grid_options
    from survey_sim.simulate.export import build_outputs
    from survey_sim.simulate.tree import build_tree

    if a.agentes < 1:
        raise CliError("configuración", "--agentes", "debe ser al menos 1", EXIT_CONFIG)
    if a.instrumento.suffix.lower() == ".docx":
        inst = _read_word(a.instrumento, _lexicon(a.lexico))[0].instrument
    else:
        inst = _read_gim(a.instrumento, _semantics(a.semantica))[0].instrument
    scenario = _scenario(a.escenario)
    eq = _equivalences_for(a.equivalencias, inst)
    mode = EXPERIMENTAL if a.modo == "experimental" else STANDARD
    try:
        sim = Simulator(inst, mode=mode, scenario=scenario, seed=a.semilla, eq=eq)
        res = sim.run(a.agentes)
    except SimulationError as e:
        raise CliError("configuración", "parámetros de simulación", _record_reason(e), EXIT_CONFIG)
    tree = build_tree(build(inst), mode, _grid_options(inst))
    hashes = {"scenario_sha256": scenario.sha256, "equivalences_sha256": eq.sha256 or None}
    files = build_outputs(res, tree, versions=_versions(), include_text=a.incluir_texto, config_hashes=hashes)
    marker = next(n for n in files if n.endswith("_metadatos.json"))
    _publish_or_fail(a.salida, files, marker)
    from collections import Counter
    st = Counter(x.status for x in res.agents)
    print(("HIPOTÉTICO · " if mode == EXPERIMENTAL else "") + scenario.label)
    print(f"modo={mode} semilla={a.semilla} agentes={len(res.agents)} estados=" + ",".join(f"{k}:{v}" for k, v in sorted(st.items())) +
          f" ramas={tree['counts']}")
    for w in res.warnings:
        print(f"aviso: {w}")
    print(f"salida: {a.salida}/{marker} (+ {len(files) - 1} archivos)")
    return EXIT_OK


def _scenario(path):
    from survey_sim.simulate.scenarios import ScenarioError, load_scenario, uniform

    if path is None:
        return uniform()
    import yaml
    try:
        return load_scenario(path)
    except (OSError, ScenarioError, yaml.YAMLError) as e:
        raise CliError("configuración", path, _record_reason(e) if isinstance(e, ScenarioError) else _config_reason(e), EXIT_CONFIG)


def cmd_consolidar(a) -> int:
    from survey_sim import consolidated
    from survey_sim.correspondence.records import RecordError, load_equivalences
    from survey_sim.correspondence.run import correspond, report_dict
    from survey_sim.simulate.differential import run_differential
    from survey_sim.simulate.engine import EXPERIMENTAL, STANDARD, SimulationError

    if a.agentes < 1:
        raise CliError("configuración", "--agentes", "debe ser al menos 1", EXIT_CONFIG)
    d, p, lex, sem, cfg = _pair_inputs(a)
    protector = _protector(a.clave_hmac)
    scenario = _scenario(a.escenario)
    import yaml
    try:
        r = correspond(d, p, cfg=cfg, semantics=sem, lexicon=lex, review_log=a.registro, pairs_path=a.pareos,
                       equivalences_path=a.equivalencias)
        eq = load_equivalences(a.equivalencias, r.binding)
    except (OSError, RecordError, yaml.YAMLError) as e:
        raise CliError("configuración", "registros del usuario (--registro, --pareos, --equivalencias)",
                       _os_reason(e) if isinstance(e, OSError) else _record_reason(e), EXIT_CONFIG)
    rep = report_dict(r, include_text=a.incluir_texto, protector=protector)
    declared = [(x.design, x.program) for x in r.matching.pairs if x.state == "declarada"]
    mode = EXPERIMENTAL if a.modo == "experimental" else STANDARD
    try:
        diff = run_differential(d, p, declared_pairs=declared, comparison_enabled=r.identity.comparison_enabled,
                                mode=mode, scenario=scenario, seed=a.semilla, n_agents=a.agentes, eq=eq)
    except SimulationError as e:
        raise CliError("configuración", "parámetros de simulación", _record_reason(e), EXIT_CONFIG)
    hashes = {"scenario_sha256": scenario.sha256, "equivalences_sha256": eq.sha256 or None,
              "pairs_sha256": (rep["matching"]["declared_pairs_file"] or {}).get("sha256")}
    c = consolidated.build(rep, diff, versions=_versions(), hashes=hashes)
    files, marker = consolidated.outputs(c, diff)
    _publish_or_fail(a.salida, files, marker)
    dd = c["differential"]
    print(("HIPOTÉTICO · " if diff.hypothetical else "") + "SINTÉTICO · informe consolidado")
    print(f"correspondencia={rep['result']} comparación_habilitada={r.identity.comparison_enabled} parejas_declaradas={len(declared)} "
          f"hallazgos=" + ",".join(f"{k}:{v}" for k, v in rep["comparison"]["findings_by_level"].items()))
    if not dd["enabled"]:
        print(f"diferencial no ejecutado: {dd['reason']}")
    else:
        print(f"diferencial: modo={mode} semilla={a.semilla} agentes={a.agentes} divergencias={len(dd['divergences'])} "
              + ",".join(f"{k}:{v}" for k, v in sorted(dd["divergences_by_evidence"].items())))
    print(f"salida: {a.salida}/{marker} (+ {len(files) - 1} archivos)")
    return EXIT_OK


# --------------------------------------------------------------------------


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m survey_sim", description="Survey Simulator: lectura, correspondencia y simulación sintética.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    P = pathlib.Path

    s = sub.add_parser("comprobar", help="comprueba dependencias y versiones")
    s.set_defaults(fn=cmd_comprobar)

    s = sub.add_parser("leer-word", help="IR e informe de un cuestionario .docx")
    s.add_argument("docx", type=P)
    s.add_argument("--salida", type=P, required=True)
    s.add_argument("--lexico", type=P)
    s.add_argument("--incluir-texto", action="store_true")
    s.add_argument("--clave-hmac", type=P)
    s.set_defaults(fn=cmd_leer_word)

    s = sub.add_parser("leer-gim", help="IR e informe de un programa GIM")
    s.add_argument("programa", type=P)
    s.add_argument("--salida", type=P, required=True)
    s.add_argument("--semantica", type=P)
    s.add_argument("--clave-hmac", type=P, help="clave local de 32 bytes: HMAC de intro y svarenlacecod (v1.1, §8.1)")
    s.set_defaults(fn=cmd_leer_gim)

    def pair_args(s):
        s.add_argument("diseno", type=P, help="cuestionario Word (.docx)")
        s.add_argument("programa", type=P, help="programa GIM")
        s.add_argument("--lexico", type=P)
        s.add_argument("--semantica", type=P)
        s.add_argument("--config", type=P, help="config/correspondence.yaml alternativo")

    s = sub.add_parser("corresponder", help="identidad, emparejamiento y comparación")
    pair_args(s)
    s.add_argument("--salida", type=P, required=True)
    s.add_argument("--registro", type=P, help="review_log.jsonl con declaraciones humanas")
    s.add_argument("--pareos", type=P, help="archivo de pareos confirmados")
    s.add_argument("--equivalencias", type=P, help="archivo de equivalencias por variable")
    s.add_argument("--incluir-texto", action="store_true")
    s.add_argument("--clave-hmac", type=P)
    s.set_defaults(fn=cmd_corresponder)

    s = sub.add_parser("declarar-identidad", help="registra una declaración humana de identidad")
    pair_args(s)
    s.add_argument("--registro", type=P, required=True)
    s.add_argument("--veredicto", choices=("MISMO", "DISTINTO"), required=True)
    s.add_argument("--motivo", required=True)
    s.add_argument("--rol", required=True, help="rol de quien declara (no un nombre personal)")
    s.add_argument("--reconoce", help="ids de bloqueo reconocidos, separados por comas")
    s.add_argument("--reconocer-bloqueos", action="store_true", help="reconoce todos los bloqueos abiertos actuales")
    s.add_argument("--sustituye", help="id de una declaración anterior que esta sustituye")
    s.add_argument("--fecha", help="AAAA-MM-DD (por defecto, hoy)")
    s.add_argument("--id")
    s.set_defaults(fn=cmd_declarar_identidad)

    s = sub.add_parser("simular", help="simulación sintética de un instrumento")
    s.add_argument("instrumento", type=P, help=".docx (diseño) o programa GIM")
    s.add_argument("--salida", type=P, required=True)
    s.add_argument("--agentes", type=int, default=100)
    s.add_argument("--semilla", type=int, required=True)
    s.add_argument("--modo", choices=("estandar", "experimental"), default="estandar")
    s.add_argument("--escenario", type=P, help="YAML de escenario o guion; sin él, escenario artificial uniforme")
    s.add_argument("--equivalencias", type=P)
    s.add_argument("--lexico", type=P)
    s.add_argument("--semantica", type=P)
    s.add_argument("--incluir-texto", action="store_true")
    s.set_defaults(fn=cmd_simular)

    s = sub.add_parser("consolidar", help="correspondencia + simulación diferencial (informe consolidado)")
    pair_args(s)
    s.add_argument("--salida", type=P, required=True)
    s.add_argument("--registro", type=P)
    s.add_argument("--pareos", type=P)
    s.add_argument("--equivalencias", type=P)
    s.add_argument("--agentes", type=int, default=200)
    s.add_argument("--semilla", type=int, required=True)
    s.add_argument("--modo", choices=("estandar", "experimental"), default="estandar")
    s.add_argument("--escenario", type=P)
    s.add_argument("--incluir-texto", action="store_true")
    s.add_argument("--clave-hmac", type=P)
    s.set_defaults(fn=cmd_consolidar)
    return ap


def main(argv=None) -> int:
    a = parser().parse_args(argv)
    try:
        return a.fn(a)
    except CliError as e:
        print(f"ERROR [{e.kind}] {e.where}: {e.reason}", file=sys.stderr)
        return e.code
