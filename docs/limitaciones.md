# Limitaciones y condiciones de operación

Conviene leer esta página antes de usar los resultados para tomar decisiones. La lista técnica que acompaña a la Skill está en `skill/survey-simulator/references/limitaciones.md`.

## 1. Naturaleza de los resultados

- **Los datos de simulación son sintéticos.** Los producen agentes artificiales con escenarios artificiales. No son respuestas, resultados de campo, observaciones ni estimaciones de una población real, y no deben citarse como tales.
- **Escenario uniforme.** Sin `--escenario`, las respuestas se reparten de forma uniforme («escenario artificial uniforme»). Un escenario declarado por el usuario también es artificial.
- **Árbol de recorridos y divergencias.** Muestran recorridos posibles según las reglas leídas. No prueban lo que hará el programa con encuestados reales.

## 2. Semántica de los programas GIM

- **Todas las reglas GIM son inferidas.** Ninguna hipótesis de `config/semantics/gim.yaml` está confirmada. Por eso:
  - los hallazgos sobre reglas GIM llegan, como máximo, a **sospecha**;
  - en **modo estándar**, la simulación de un programa GIM se detiene en la primera regla de flujo GIM. No es un fallo, sino la política de seguridad de la norma (v1.1, §7.5), y no se relaja para aumentar la cobertura;
  - para recorrer el programa hace falta el **modo experimental**, cuyos resultados son **HIPOTÉTICOS** y no confirman la semántica de esas reglas.
- **Cobertura de la comparación diferencial.** En modo estándar suele ser pequeña por la misma razón. La hoja «Comparabilidad» del consolidado la cuantifica.
- **Incidencias GIM.** No producen terminaciones (v1.1, §2.2). La terminación solo se compara como «no verificable».

## 3. Cuestionarios Word

- Los universos «APLIQUE SI» del Word son **inferidos**. En modo estándar, el agente se detiene al llegar a ellos.
- El lector reconoce las convenciones del léxico configurado (`config/lexicon/default_es.yaml`). Formatos de redacción muy distintos pueden no reconocerse, y se registran como no resueltos.
- Los dígitos que no son 0-9 no se leen como códigos; se registran como `non_ascii_digit`.

## 4. Reglas y condiciones

- **Reglas opacas.** Las condiciones con funciones, `O`, conjuntos con comas, aritmética o destinos especiales `@…@` son opacas. No se ejecutan nunca y producen «no verificable».
- **Equivalencia de condiciones.** Solo se demuestra por identidad estructural o por enumeración completa de un dominio finito, y siempre con pareja declarada y comparación habilitada. Con dominios no finitos, valores desconocidos (U) o el límite de enumeración alcanzado, el resultado es «no verificable».
- **Tipos.** No hay conversión general entre texto y número. Hace falta declarar las equivalencias variable por variable.
- **No se comparan ni se simulan** los valores calculados, las validaciones (controles) ni las dependencias externas (GPS, tablas, cuotas).

## 5. Decisiones humanas obligatorias

- **Identidad del estudio.** Las asignaciones de identificadores son inferidas. La comparación solo se habilita con una declaración humana registrada y vigente. La semejanza entre preguntas nunca prueba la identidad.
- **Pareos y equivalencias.** Las propone la herramienta, pero solo cuentan las que confirma el usuario.
- **Vigencia.** Si cambia un insumo, la configuración o una versión, las declaraciones, pareos y equivalencias anteriores dejan de surtir efecto.

## 6. Validación

- **Solo se ha validado con instrumentos sintéticos.** No hay validación con cuestionarios ni programas reales. La prueba con instrumentos de referencia («golden») no se ejecutó.
- Los umbrales de emparejamiento están calibrados con parejas sintéticas.

## 7. Rendimiento y entorno

Tiempos medidos en la validación V (mediana de 5 ejecuciones):

| Tamaño del instrumento | Leer Word | Leer GIM | Corresponder |
|---|---|---|---|
| Referencia (151 variables) | 0,19 s | 0,04 s | 0,13 s |
| Estrés (20.000 variables) | 61,7 s | 26,8 s | 71,1 s |

- Dentro de Claude, cada comando tiene un **límite de tiempo** (120 s por defecto). Con instrumentos muy grandes, la lectura y la comparación pueden superarlo. En ese caso hay que ampliar el límite, si el entorno lo permite, o dividir el instrumento.
- **Simulaciones grandes.** Conviene repartir los agentes en varias llamadas con semillas distintas. Como referencia, con un instrumento de 20.000 variables caben unos 950 a 1.200 agentes por llamada de 120 s.

## 8. Confidencialidad

- Antes de procesar instrumentos confidenciales, confirma que tu cuenta, tu organización y el modo de ejecución están autorizados para manejarlos, según la política interna y la del cliente.
- Los scripts de la Skill no usan la red. Lo que Claude lee durante la conversación depende del entorno donde se use, y la herramienta **no garantiza** que el procesamiento sea solo local.
- No compartas los archivos `*.ir.json` ni la clave HMAC.
