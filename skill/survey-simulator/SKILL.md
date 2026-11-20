---
name: survey-simulator
description: Lee cuestionarios Word (.docx) y programas de captura GIM, verifica su correspondencia (identidad del estudio, pareo de preguntas, códigos, opciones, filtros y saltos) y genera simulaciones sintéticas reproducibles con exportación CSV/Excel. Úsala para revisar si un programa de encuesta implementa su cuestionario o para probar el flujo de un instrumento con datos sintéticos.
---

# Survey Simulator

Herramienta de revisión de instrumentos de encuesta. Norma: especificación v1.0 más su documento de cambios v1.1.

## Reglas que nunca se rompen

1. **Los datos simulados son sintéticos.** Nunca los presentes como respuestas, resultados, estimaciones ni observaciones reales. Las salidas llevan la marca SINTÉTICO, y además HIPOTÉTICO en modo experimental: menciónalas siempre al usuario.
2. **Confidencialidad.** Los cuestionarios y programas del usuario son confidenciales. Los informes son vistas minimizadas, sin texto del instrumento. Usa `--incluir-texto` solo si el usuario lo pide expresamente. Si el usuario tiene una clave local de 32 bytes, `--clave-hmac` permite comparar textos por HMAC sin copiarlos; nunca muestres ni copies la clave. Los archivos `*.ir.json` contienen el instrumento completo: no los muestres ni los copies en la conversación.
3. **No confirmes nada en nombre del usuario.** Las declaraciones de identidad, los pareos y las equivalencias son decisiones humanas. Propónlas, explícalas y espera a que el usuario las confirme.
4. **No instales dependencias ni uses la red.** Si `comprobar` informa que falta algo, díselo al usuario y detente.
5. **Interpreta los niveles de evidencia tal como vienen.** «Sospecha» y «no verificable» no son errores comprobados. «Equivalente» solo aparece con pareja declarada y comparación habilitada.

## Preparación

Ejecuta todos los comandos desde la carpeta de esta Skill (la que contiene este archivo):

```bash
python -m survey_sim comprobar
```

Códigos de salida: 0 éxito; 2 entrada no legible o no soportada; 3 configuración o registros del usuario inválidos; 4 IR inválida (defecto del lector: infórmalo); 5 no se pudo escribir la salida; 1 error inesperado (muestra la traza al usuario). Los errores esperados se informan en una línea `ERROR [...]`.

## Flujo A · Correspondencia entre cuestionario y programa

**A1. Primera pasada (diagnóstico).**

```bash
python -m survey_sim corresponder <DISENO> <PROGRAMA> --salida <SALIDA>/c1
```

Lee `c1/correspondencia.md` y explica al usuario el resultado: veredicto de identidad, respaldo, bloqueos abiertos y parejas propuestas. Si la comparación no está habilitada, la correspondencia necesita una **declaración humana de identidad** (v1.0, 7.1). El emparejamiento de preguntas nunca la sustituye.

**A2. Declaración de identidad (solo si el usuario la confirma).** Pregunta al usuario si ambos archivos son del mismo estudio y qué rol tiene (no su nombre). Revisa con él los bloqueos abiertos que aparecen en el informe. Después:

```bash
python -m survey_sim declarar-identidad <DISENO> <PROGRAMA> --registro <SALIDA>/review_log.jsonl --veredicto MISMO --motivo "<MOTIVO>" --rol "<ROL>" --reconocer-bloqueos
```

`--reconocer-bloqueos` solo se usa si el usuario revisó y aceptó todos los bloqueos abiertos; si no, indica los ids con `--reconoce id1,id2`. El registro es de solo adición.

**A3. Comparación con identidad declarada.**

```bash
python -m survey_sim corresponder <DISENO> <PROGRAMA> --salida <SALIDA>/c2 --registro <SALIDA>/review_log.jsonl
```

**A4. Pareos y equivalencias (decisión del usuario).** La carpeta `c2` contiene `plantilla_pareos.yaml` (parejas propuestas con `confirmado: false`) y `plantilla_equivalencias.yaml`.

- Pareos: muestra al usuario las parejas propuestas. Solo las que él confirme pasan a `confirmado: true`. Guarda el resultado como `<SALIDA>/pareos.yaml`.
- Equivalencias: si el informe muestra condiciones «no verificable» por evaluaciones U al comparar texto con número, pregunta al usuario, **por variable**, qué códigos de texto equivalen a qué literales numéricos. Guárdalo como `<SALIDA>/equivalencias.yaml`. No hay coerción general: sin declaración, «1» y 1 no son equivalentes.

**A5. Comparación final.**

```bash
python -m survey_sim corresponder <DISENO> <PROGRAMA> --salida <SALIDA>/c3 --registro <SALIDA>/review_log.jsonl --pareos <SALIDA>/pareos.yaml --equivalencias <SALIDA>/equivalencias.yaml
```

Entrega al usuario `c3/correspondencia.xlsx` y resume los hallazgos por nivel (error comprobado, sospecha, no verificable) y las condiciones con su resultado (equivalente, diferente con su contraejemplo, no verificable con sus límites). Los registros, pareos y equivalencias quedan vinculados a los archivos y versiones: si un insumo cambia, dejan de surtir efecto y hay que revisarlos de nuevo.

## Flujo B · Simulación sintética de un instrumento

```bash
python -m survey_sim simular <INSTRUMENTO> --salida <SALIDA>/s1 --agentes 100 --semilla <SEMILLA>
```

- Sin `--escenario`, las respuestas siguen un **escenario artificial uniforme**: no es una estimación poblacional. Con `--escenario archivo.yaml` se usan distribuciones declaradas por el usuario, rotuladas como escenario. Nunca uses datos reales sin autorización expresa.
- **Modo estándar** (por defecto): solo se ejecutan reglas explícitas. Si una regla de flujo es opaca, inferida o evalúa U, el agente se **interrumpe** (`interrumpido_indeterminado`) y las preguntas siguientes quedan `NO_SIMULADO`. No es un fallo: es la política de la norma (v1.1, §7.2).
- **Limitación de los programas GIM:** mientras ninguna semántica GIM esté confirmada, la simulación de un programa GIM en modo estándar se interrumpe en la primera regla de flujo GIM. No intentes evitarlo; explícaselo al usuario.
- **Modo experimental** (solo si el usuario lo pide): `--modo experimental` ejecuta también reglas inferidas como hipótesis. Todas las salidas llevan HIPOTÉTICO y la lista de hipótesis; nada de lo que produce confirma la semántica de esas reglas. Para programas GIM suele necesitar `--equivalencias` del flujo A.

```bash
python -m survey_sim simular <INSTRUMENTO> --salida <SALIDA>/s2 --agentes 100 --semilla <SEMILLA> --modo experimental --equivalencias <SALIDA>/equivalencias.yaml
```

Salidas: base (`*_datos.csv`), estados por celda (`*_estados.csv`), agentes (`*_agentes.csv`), libro Excel con hoja LEEME, árbol de recorridos posibles (`*_recorridos.json`) y metadatos (`*_metadatos.json`, con semilla, versiones y SHA-256 de todo). El árbol distingue `rama_demostrada` (con testigo, solo reglas explícitas) de `rama_estructural_no_verificada`; **no prueba que esos recorridos ocurran en el programa**.

La misma semilla con los mismos insumos produce archivos idénticos byte a byte.

## Flujo C · Informe consolidado y simulación diferencial

Requiere lo mismo que A5: identidad declarada, pareos confirmados y, si hacen falta, equivalencias. Los mismos agentes sintéticos recorren el cuestionario y el programa con las mismas respuestas, y se informa dónde divergen sus recorridos.

```bash
python -m survey_sim consolidar <DISENO> <PROGRAMA> --salida <SALIDA>/consolidado --registro <SALIDA>/review_log.jsonl --pareos <SALIDA>/pareos.yaml --equivalencias <SALIDA>/equivalencias.yaml --agentes 200 --semilla <SEMILLA>
```

- Solo se comparan parejas **declaradas** con comparación habilitada, y solo donde **ambos** lados decidieron el recorrido. Lo que quedó sin decidir (agente interrumpido) no produce divergencias.
- En modo estándar las divergencias son **confirmadas**: solo intervienen reglas explícitas. Como las reglas GIM son inferidas, en estándar suele haber poca cobertura comparable; explícaselo al usuario con la hoja «Comparabilidad».
- Con `--modo experimental`, una divergencia lleva la marca HIPOTÉTICO cuando algún lado usó una hipótesis hasta decidir esa pregunta, con la lista exacta de hipótesis. Preséntalas como hipótesis, nunca como errores comprobados.
- `traslado`: el diseño dio una respuesta cuyo código no existe en el programa. `opcion_oculta`: el código existe en el programa pero una regla de presentación de opciones lo oculta; no lo describas como código inexistente.

Entrega `*_consolidado.xlsx` y resume: resultado de la correspondencia, hallazgos por nivel, condiciones y divergencias por evidencia (confirmada, mixta, hipotética). Recuerda que todo lo que proviene de la simulación es sintético.

## Lectura individual (opcional)

```bash
python -m survey_sim leer-word <DISENO> --salida <SALIDA>/lectura
python -m survey_sim leer-gim <PROGRAMA> --salida <SALIDA>/lectura
```

## Referencias

- `references/limitaciones.md`: lo que la herramienta no hace y por qué.
- `references/formatos.md`: formato de los archivos de pareos, equivalencias, escenarios y del registro de identidad.
