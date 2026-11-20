# Funcionamiento

Este documento explica qué hace la herramienta en cada etapa y por qué toma sus decisiones. No hace falta leerlo para usarla, pero ayuda a interpretar los resultados.

## 1. Representación común (IR)

Los dos lectores convierten sus insumos a una misma estructura: la **IR**, versión 2.0.0.

- **Lector Word (0.4.0):** reconoce preguntas, opciones con código, tablas y grillas, y las instrucciones de programación escritas en el cuestionario («PROG: RU», «APLIQUE SI…», «CONTINUAR CON…»). El vocabulario reconocido está en `config/lexicon/default_es.yaml`. Solo se aceptan como códigos los dígitos 0-9; cualquier otro dígito se registra como `non_ascii_digit`.
- **Lector GIM (0.3.0):** lee la exportación JSON del programa: páginas, variables, códigos, filtros, saltos y reglas de opciones. Las expresiones se **analizan sintácticamente**, pero **nunca se ejecutan**. Tampoco se ejecuta ningún código, SQL ni script embebido. Los campos `intro` y `svarenlacecod` solo se registran por presencia y longitud, y por HMAC si el usuario aporta una clave local.

Cada regla de la IR tiene un nivel de certeza:

| Nivel | Significado | Ejemplo |
|---|---|---|
| Explícita | Su significado es inequívoco | «CONTINUAR CON P4» en el Word |
| Inferida | Su significado se deduce, pero no está confirmado | Toda regla GIM; los «APLIQUE SI» del Word |
| Opaca | No se puede interpretar con seguridad | Funciones, aritmética, destinos especiales `@…@` |

## 2. Correspondencia

### 2.1 Identidad del estudio

Antes de comparar, la herramienta evalúa si cuestionario y programa son del mismo estudio. **Solo una declaración humana**, registrada en `review_log.jsonl`, habilita la comparación. Que las preguntas se parezcan nunca prueba que sea el mismo estudio.

### 2.2 Pareo de preguntas

Cada pareja de preguntas tiene uno de tres estados:

- `propuesta_heuristica`: el parecido de nombre o de texto la sugiere;
- `evidencia_exacta`: coincide de forma exacta;
- `declarada`: el usuario la confirmó en `pareos.yaml`.

Solo las parejas **declaradas** pueden producir «equivalente» o «error comprobado».

### 2.3 Hallazgos

Los hallazgos se clasifican por nivel de evidencia:

- **error comprobado:** diferencia demostrada entre parejas declaradas que no depende de reglas inferidas;
- **sospecha:** posible diferencia que depende de una regla inferida;
- **no verificable:** no se puede decidir.

### 2.4 Comparación de condiciones

Los filtros y saltos se comparan por identidad estructural o por **enumeración completa** de un dominio finito, con una lógica de tres valores: verdadero, falso y desconocido (U). El resultado es:

- **equivalente:** coinciden en todas las asignaciones posibles;
- **diferente:** hay un contraejemplo, que se informa;
- **no verificable:** hay reglas opacas, valores U, un dominio no finito o se alcanzó el límite de enumeración. El informe indica esos límites.

No hay conversión automática entre texto y número: «1» y 1 solo son equivalentes si el usuario lo declara para esa variable.

## 3. Simulación sintética

Cada agente sintético recibe una semilla derivada de la semilla general y recorre el instrumento respondiendo según el escenario: uniforme por defecto, o el que declare el usuario.

| Modo | Reglas que ejecuta | Marca |
|---|---|---|
| Estándar | Solo explícitas | SINTÉTICO |
| Experimental | Explícitas e inferidas, como hipótesis | SINTÉTICO · HIPOTÉTICO |

Las reglas opacas **no se ejecutan en ningún modo**. Si un agente llega a una regla que no puede ejecutar, se **interrumpe** (`interrumpido_indeterminado`) y las preguntas siguientes quedan `NO_SIMULADO`. Así nunca se inventa un recorrido.

El **árbol de recorridos** distingue dos tipos de rama:

- `rama_demostrada`: tiene un testigo y usa solo reglas explícitas;
- `rama_estructural_no_verificada`: es posible por la estructura del instrumento.

El árbol **no prueba** lo que hará el programa con encuestados reales.

## 4. Simulación diferencial e informe consolidado

Los mismos agentes, con las mismas respuestas, recorren el cuestionario y el programa. Se comparan solo las **parejas declaradas**, y solo donde **ambos lados** decidieron el recorrido. Las divergencias se clasifican así:

- `presentacion`: la pregunta se muestra en un lado y no en el otro;
- `terminacion`: la entrevista termina en un lado y no en el otro;
- `traslado`: el cuestionario usa un código que no existe en el programa;
- `opcion_oculta`: el código existe en el programa, pero una regla de opciones lo oculta.

Una divergencia es **HIPOTÉTICA** cuando alguno de los dos lados usó una regla inferida para decidir esa pregunta. El informe lista exactamente qué hipótesis intervinieron.

## 5. Reproducibilidad

Con los mismos insumos, la misma configuración y la misma semilla, las salidas son **idénticas byte a byte**, incluidos los Excel. Cada salida registra:

- los SHA-256 de los insumos y de la configuración;
- las versiones de los componentes;
- la semilla.

Los archivos del usuario (registro, pareos, equivalencias) quedan **vinculados** a esos hashes. Si un insumo cambia, dejan de surtir efecto.

Las salidas se publican de forma coherente: se escriben primero en archivos temporales y al final se escribe un archivo marcador con las huellas de los demás. Si algo falla, no queda una salida a medias.

## 6. Privacidad y seguridad

- Los informes son **vistas minimizadas**: no copian el texto del instrumento salvo con `--incluir-texto`.
- Los textos se pueden comparar mediante HMAC-SHA256 con una clave local de 32 bytes. Nunca se usa SHA-256 sin clave sobre texto libre, porque permitiría adivinar el texto.
- Los archivos `*.ir.json` contienen el instrumento completo. Se tratan como confidenciales.
- La herramienta no usa la red ni instala dependencias.

## 7. Códigos de salida

| Código | Significado |
|---|---|
| 0 | Éxito |
| 1 | Error inesperado (se muestra la traza) |
| 2 | Entrada no legible o no soportada |
| 3 | Configuración o archivos del usuario inválidos |
| 4 | IR inválida (defecto del lector) |
| 5 | No se pudo escribir la salida |

## 8. Validación

La versión publicada superó la validación integral **V** (2026-11-20) con cuatro parejas sintéticas: concordante, discrepante, enrutamiento complejo y casos límite.

- Se detectaron el 100 % de las discrepancias deterministas introducidas a propósito, cada una en su categoría.
- La suite de pruebas dio 898 pruebas aprobadas, 0 fallidas y 1 omitida (la prueba con instrumentos reales, que no se ejecuta) con tres semillas de entorno distintas.
- La reproducibilidad fue byte a byte entre procesos.
- La marca secreta de la prueba de privacidad no apareció en ninguna salida por defecto.
- Las 12 mediciones de rendimiento quedaron sin regresión.
