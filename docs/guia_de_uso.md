# Guía de uso

Hay dos maneras de usar la herramienta:

1. **Conversando con Claude** (recomendada). Claude sigue el procedimiento de `SKILL.md`, ejecuta los comandos, explica los resultados y te pide confirmación en cada decisión.
2. **Ejecutando los comandos a mano** en una terminal.

En los dos casos, las decisiones de identidad del estudio, pareos y equivalencias **son tuyas**.

---

## 1. Uso con Claude

Adjunta el cuestionario y el programa en una conversación y pide lo que necesitas. Por ejemplo:

- *«Compara este cuestionario Word con este programa GIM y dime qué diferencias hay.»*
- *«Simula 200 encuestados sintéticos sobre este cuestionario y muéstrame qué preguntas quedan sin recorrer.»*
- *«Prepara el informe consolidado con simulación diferencial.»*

Claude te preguntará:

1. si los dos archivos son del **mismo estudio**, y qué rol tienes (no tu nombre);
2. qué **parejas de preguntas** confirmas;
3. si hace falta, qué **códigos de texto** del programa equivalen a qué números, variable por variable.

Antes de procesar instrumentos confidenciales, confirma que tu cuenta y tu organización están autorizadas para manejarlos en Claude, según la política interna y la del cliente.

---

## 2. Uso desde la terminal: ejemplo completo

El repositorio trae un caso sintético en `ejemplos/entradas/`:

- `transporte_cuestionario.docx`: cuestionario de 4 preguntas (P1 a P4).
- `transporte_programa_discrepante.gim`: programa con 5 diferencias introducidas a propósito.
- `transporte_programa_concordante.gim`: programa que implementa el cuestionario sin diferencias.

Todos los comandos se ejecutan desde `skill/survey-simulator`:

```bash
cd skill/survey-simulator
D=../../ejemplos/entradas/transporte_cuestionario.docx
G=../../ejemplos/entradas/transporte_programa_discrepante.gim
S=../../trabajo
```

En Windows (PowerShell), escribe las rutas completas en lugar de usar variables.

### Paso 1 · Diagnóstico

```bash
python -m survey_sim corresponder $D $G --salida $S/c1
```

Resultado: `CORRESPONDENCIA PROVISIONAL ... comparación_habilitada=False`. La herramienta propone parejas de preguntas, pero **no compara** hasta que una persona declare que los dos archivos son del mismo estudio. Revisa `c1/correspondencia.md`.

### Paso 2 · Declarar la identidad (decisión humana)

Hazlo solo si confirmas que ambos archivos son del mismo estudio y revisaste los bloqueos que lista el informe:

```bash
python -m survey_sim declarar-identidad $D $G --registro $S/review_log.jsonl \
  --veredicto MISMO --motivo "Revisado con el equipo de campo" --rol "analista" --reconocer-bloqueos
```

Si solo aceptas algunos bloqueos, usa `--reconoce id1,id2` en lugar de `--reconocer-bloqueos`. El registro es de solo adición: para corregir una declaración se agrega otra con `--sustituye <id>`.

### Paso 3 · Comparación con identidad declarada

```bash
python -m survey_sim corresponder $D $G --salida $S/c2 --registro $S/review_log.jsonl
```

Se generan dos plantillas en `c2/`:

- `plantilla_pareos.yaml`: parejas propuestas, todas con `confirmado: false`.
- `plantilla_equivalencias.yaml`: lista vacía de equivalencias.

### Paso 4 · Confirmar pareos y equivalencias (decisión humana)

Copia las plantillas, pon `confirmado: true` solo en las parejas que verificaste y declara las equivalencias por variable. Guárdalas como `$S/pareos.yaml` y `$S/equivalencias.yaml`.

Para este ejemplo puedes copiar los archivos ya preparados:

```bash
cp ../../ejemplos/salidas/pareos.yaml ../../ejemplos/salidas/equivalencias.yaml $S/
```

Así se ve una equivalencia: en el programa, el código de texto «1» de P1 equivale al número 1.

```yaml
variables:
  - lado: program
    variable: P1
    pares:
      - {texto: "1", literal: 1}
      - {texto: "2", literal: 2}
      - {texto: "9", literal: 9}
```

Los formatos completos están en `skill/survey-simulator/references/formatos.md`.

### Paso 5 · Comparación final

```bash
R="--registro $S/review_log.jsonl --pareos $S/pareos.yaml --equivalencias $S/equivalencias.yaml"
python -m survey_sim corresponder $D $G --salida $S/c3 $R
```

Resultado esperado con el ejemplo:

```
resultado=CORRESPONDEN identidad=MISMO comparación_habilitada=True parejas=3 hallazgos=error_comprobado:2,sospecha:3,no_verificable:2
```

Abre `c3/correspondencia.xlsx`. Encontrarás:

- **error comprobado (2):** en P2, el programa tiene un código 4 que no existe en el cuestionario; en P4, la etiqueta del código 1 es distinta.
- **condición «diferente»:** el filtro de P2 no es el mismo. El contraejemplo P1=1 lo muestra en el cuestionario y no en el programa. Como depende de reglas inferidas, el hallazgo queda en nivel **sospecha**.
- **otras sospechas:** el código 3 de P2 solo existe en el cuestionario; el código 9 de P1 es de no respuesta solo en un lado.
- **no verificable:** P3 solo está en el cuestionario y P5 solo en el programa.

### Paso 6 · Simulación sintética

```bash
python -m survey_sim simular $D --salida $S/s1 --agentes 100 --semilla 2026
```

- **Modo estándar:** se ejecutan solo reglas explícitas. Con este ejemplo, 38 agentes completan el cuestionario y 62 se detienen (`interrumpido_indeterminado`) en el filtro «APLIQUE SI» de P2. Ese filtro es inferido del texto del Word, así que no se ejecuta en modo estándar. Es el comportamiento esperado, no un error.
- **Modo experimental:** ejecuta las reglas inferidas como hipótesis y marca todo como HIPOTÉTICO.

```bash
python -m survey_sim simular $G --salida $S/s2 --agentes 100 --semilla 2026 \
  --modo experimental --equivalencias $S/equivalencias.yaml
```

Con la misma semilla y los mismos archivos, los resultados son idénticos byte a byte.

Para usar distribuciones propias en lugar del escenario uniforme, añade `--escenario escenario.yaml` (formato en `references/formatos.md`). Un escenario sigue siendo artificial: no lo construyas con datos reales sin autorización.

### Paso 7 · Informe consolidado y simulación diferencial

```bash
python -m survey_sim consolidar $D $G --salida $S/consolidado $R --agentes 200 --semilla 2026
python -m survey_sim consolidar $D $G --salida $S/consolidado_exp $R --agentes 200 --semilla 2026 --modo experimental
```

- En modo estándar: 0 divergencias. El programa GIM se detiene en su primera regla de flujo, porque es inferida, y lo que queda sin decidir no se compara.
- En modo experimental: 1 divergencia HIPOTÉTICA. P2 se presenta en el cuestionario y no en el programa para 60 agentes, lo que confirma en la simulación el filtro distinto del paso 5.

Abre `SINTETICO_consolidado.xlsx`, con las hojas LEEME, Resumen, Hallazgos, Condiciones, Divergencias, Comparabilidad y Limitaciones.

Puedes comparar tus resultados con los de `ejemplos/salidas/`.

---

## 3. Cómo leer los resultados

| Término | Significado |
|---|---|
| Error comprobado | Diferencia demostrada entre parejas declaradas |
| Sospecha | Posible diferencia que depende de una regla inferida; requiere revisión humana |
| No verificable | La herramienta no puede decidir, por ejemplo por una regla opaca. No es un error |
| Equivalente / diferente | Resultado de comparar dos condiciones. «Diferente» incluye un contraejemplo |
| `interrumpido_indeterminado` | El agente llegó a una regla que no puede ejecutar en ese modo. Las preguntas siguientes quedan `NO_SIMULADO` |
| `rama_demostrada` / `rama_estructural_no_verificada` | Ruta con testigo / ruta posible solo por estructura |
| `traslado` | El cuestionario usa un código que no existe en el programa |
| `opcion_oculta` | El código existe en el programa, pero una regla de opciones lo oculta |
| SINTÉTICO / HIPOTÉTICO | Datos artificiales / resultados que dependen de reglas inferidas |

## 4. Opciones útiles

| Opción | Uso |
|---|---|
| `--incluir-texto` | Incluye texto del instrumento en los informes. Úsala solo si es necesario: por defecto se omite por privacidad |
| `--clave-hmac <archivo>` | Compara textos mediante HMAC con una clave local de 32 bytes, sin copiarlos. Nunca compartas ni subas la clave |
| `--fecha AAAA-MM-DD` | Fija la fecha de una declaración de identidad |
| `--semilla N` | Hace reproducible la simulación |

## 5. Buenas prácticas

- No compartas los archivos `*.ir.json` que genera la lectura: contienen el instrumento completo.
- Si cambias el cuestionario, el programa o la configuración, las declaraciones, pareos y equivalencias anteriores dejan de valer. Hay que revisarlos de nuevo.
- Para simulaciones grandes dentro de Claude, divide los agentes en varias llamadas con semillas distintas.
