# Simulador de Encuestas

Herramienta para leer, validar, comparar y simular cuestionarios mediante población sintética. Permite analizar instrumentos Word y programas GIM, evaluar diferencias en preguntas, opciones y rutas de navegación, y generar resultados reproducibles en Excel, CSV y JSON.

> **Importante: los resultados de simulación son sintéticos.** Los genera un conjunto de agentes artificiales con escenarios artificiales. No son respuestas de personas, ni resultados de campo, ni estimaciones de una población real. Todas las salidas de simulación llevan la marca **SINTÉTICO**, y además **HIPOTÉTICO** cuando se usa el modo experimental.

---

## 1. ¿Qué es?

El Simulador de Encuestas es una **Skill para Claude** (nombre técnico: `survey-simulator`). Revisa instrumentos de encuesta antes de salir a campo:

- comprueba si un **programa de captura GIM** implementa fielmente su **cuestionario Word**;
- recorre el instrumento con **agentes sintéticos** para probar filtros, saltos y terminaciones;
- informa dónde divergen las rutas del cuestionario y del programa cuando ambos reciben las mismas respuestas.

Responde preguntas como: ¿falta alguna pregunta en el programa?, ¿cambió algún código de respuesta?, ¿el filtro de P2 es el mismo en los dos documentos?, ¿qué preguntas ve un encuestado que contesta «No» en P1?

## 2. Formatos que procesa

| Entrada | Formato |
|---|---|
| Cuestionario de diseño | Word `.docx` |
| Programa de captura | Exportación GIM (`.gim`, JSON) |
| Decisiones del usuario | YAML (pareos, equivalencias, escenarios) y JSONL (registro de identidad) |

| Salida | Formato |
|---|---|
| Informes | Excel `.xlsx`, Markdown `.md`, JSON |
| Datos sintéticos | CSV y Excel |
| Árbol de recorridos y metadatos | JSON |

## 3. Funcionalidades

- **Lectura** de cuestionarios Word y programas GIM a una representación común. Las expresiones GIM se analizan, **nunca se ejecutan**.
- **Correspondencia** entre cuestionario y programa: identidad del estudio, pareo de preguntas, códigos, opciones, filtros y saltos, con tres niveles de evidencia: *error comprobado*, *sospecha* y *no verificable*.
- **Comparación de condiciones** de filtros y saltos: *equivalente*, *diferente* (con un contraejemplo) o *no verificable*.
- **Simulación sintética** de un instrumento con N agentes, reproducible con una semilla.
- **Simulación diferencial**: los mismos agentes recorren cuestionario y programa, y se informa dónde divergen.
- **Informe consolidado** en Excel con hojas de resumen, hallazgos, condiciones, divergencias, comparabilidad y limitaciones.
- **Privacidad**: por defecto, los informes no copian el texto del instrumento.

## 4. Flujo general de trabajo

```
Cuestionario Word ─┐
                   ├─► 1. Correspondencia (diagnóstico)
Programa GIM ──────┘          │
                              ▼
                   2. Declaración humana de identidad del estudio
                              │
                              ▼
                   3. Confirmación de pareos y equivalencias (usuario)
                              │
                              ▼
                   4. Comparación final ──► hallazgos y condiciones
                              │
                              ▼
                   5. Simulación sintética y diferencial ──► informe consolidado
```

Las decisiones de los pasos 2 y 3 **siempre las toma una persona**. La herramienta propone, pero nunca confirma por su cuenta. Detalle en [docs/funcionamiento.md](docs/funcionamiento.md).

## 5. Instalación rápida

**En Claude (web o escritorio):** abre *Customize > Skills*, pulsa «+», elige «Upload a skill» y sube [`distribucion/survey-simulator.zip`](distribucion/survey-simulator.zip). Necesitas tener activada la ejecución de código.

**En Claude Code:** copia la carpeta [`skill/survey-simulator`](skill/survey-simulator) a `~/.claude/skills/`.

**Desde la terminal (sin Claude):** necesitas Python 3.11 o superior con `pydantic`, `lxml`, `PyYAML` y `openpyxl`:

```bash
cd skill/survey-simulator
python -m survey_sim comprobar
```

Guía completa: [docs/guia_de_instalacion.md](docs/guia_de_instalacion.md).

## 6. Comandos

Con la Skill instalada basta con pedírselo a Claude, por ejemplo: *«Compara este cuestionario con este programa GIM»*. Claude sigue el procedimiento de `SKILL.md` y te pide confirmación en cada decisión.

Los comandos también se pueden ejecutar a mano, desde la carpeta `skill/survey-simulator`:

| Comando | Para qué sirve |
|---|---|
| `python -m survey_sim comprobar` | Verifica dependencias y versiones |
| `python -m survey_sim leer-word <cuestionario.docx> --salida <dir>` | Lee un cuestionario |
| `python -m survey_sim leer-gim <programa.gim> --salida <dir>` | Lee un programa |
| `python -m survey_sim corresponder <docx> <gim> --salida <dir> [--registro ...] [--pareos ...] [--equivalencias ...]` | Compara cuestionario y programa |
| `python -m survey_sim declarar-identidad <docx> <gim> --registro <log> --veredicto MISMO --motivo "..." --rol "..."` | Registra la decisión humana de identidad |
| `python -m survey_sim simular <instrumento> --salida <dir> --agentes 100 --semilla 2026 [--modo experimental]` | Simulación sintética |
| `python -m survey_sim consolidar <docx> <gim> --salida <dir> --registro ... --pareos ... --equivalencias ... --semilla 2026` | Informe consolidado y simulación diferencial |

Paso a paso con un ejemplo real del repositorio: [docs/guia_de_uso.md](docs/guia_de_uso.md).

## 7. Archivos que genera

| Comando | Archivos principales |
|---|---|
| `corresponder` | `correspondencia.xlsx`, `.md`, `.json`, `plantilla_pareos.yaml`, `plantilla_equivalencias.yaml` |
| `declarar-identidad` | `review_log.jsonl` (solo adición) |
| `simular` | `SINTETICO_*_datos.csv`, `*_estados.csv`, `*_agentes.csv`, `*.xlsx` (con hoja LEEME), `*_recorridos.json`, `*_metadatos.json` |
| `consolidar` | `SINTETICO_consolidado.xlsx`, `.md`, `.json` y `SINTETICO_diferencial_agentes.csv` |

La carpeta [`ejemplos/`](ejemplos/) contiene un caso completo, con entradas y salidas sintéticas.

## 8. Limitaciones principales

- La semántica de las reglas GIM es **inferida**, no confirmada. En modo estándar, la simulación de un programa GIM se detiene en su primera regla de flujo. Para recorrerlo hace falta el modo experimental, cuyos resultados son HIPOTÉTICOS.
- Las condiciones opacas (funciones, aritmética, destinos especiales `@…@`) **nunca se ejecutan** y dan «no verificable».
- No se simulan valores calculados, controles ni dependencias externas (GPS, tablas, cuotas).
- Solo se ha validado con **instrumentos sintéticos**; no hay validación con instrumentos reales.
- Instrumentos muy grandes (≈20.000 variables) pueden superar el tiempo máximo por comando dentro de Claude.

La lista completa está en [docs/limitaciones.md](docs/limitaciones.md).

## 9. Resultados sintéticos, no estimaciones

Los datos simulados sirven para **probar el instrumento**: sus rutas, filtros y códigos. No describen a ninguna población. Sin un escenario declarado, las respuestas siguen un **escenario artificial uniforme**. Nunca deben citarse como resultados de encuesta.

---

## Estructura del repositorio

```
README.md
skill/survey-simulator/      Skill tal como se distribuye (SKILL.md, código y configuración)
distribucion/                Paquete .zip de la Skill y sus sumas SHA-256
docs/                        Guías de instalación, uso, funcionamiento y limitaciones
ejemplos/                    Caso completo con insumos y resultados sintéticos
```

## Versión

Versión final validada en la etapa **V** (2026-11-20): IR 2.0.0, lector Word 0.4.0, lector GIM 0.3.0, parser de expresiones 0.1.0, correspondencia 0.1.0 (reglas 1.1.0), simulador 0.1.0. Norma: especificación funcional v1.0 con su documento de cambios v1.1.

## Licencia

Este repositorio no incluye una licencia porque los derechos de uso no se han definido. Consulta con el responsable del proyecto antes de reutilizar o redistribuir el código.
