# Guía de instalación

La Skill se distribuye de dos formas, con el mismo contenido:

- `distribucion/survey-simulator.zip`: paquete listo para subir a Claude.
- `skill/survey-simulator/`: la misma carpeta, descomprimida.

## Requisitos

| Requisito | Detalle |
|---|---|
| Python | 3.11 o superior |
| Paquetes | `pydantic>=2.5`, `lxml>=5.0`, `PyYAML>=6.0`, `openpyxl>=3.1` |
| Red | No hace falta. La Skill no usa la red ni instala nada por su cuenta |
| En Claude | Ejecución de código activada y Skills habilitadas en la cuenta u organización |

Si falta una dependencia, el comando `comprobar` lo informa y la Skill se detiene. No intenta instalarla.

## Opción A · Claude en la web o en la aplicación de escritorio

1. Descarga `distribucion/survey-simulator.zip` desde este repositorio. Sube el ZIP tal cual, sin descomprimirlo.
2. En Claude, abre **Customize > Skills**.
3. Pulsa **«+»**, luego **«+ Create skill»** y elige **«Upload a skill»**.
4. Sube el ZIP. La Skill aparece en la lista como `survey-simulator`. Comprueba que el interruptor esté activado.
5. En una conversación nueva, adjunta un cuestionario `.docx` y un programa `.gim` y pide, por ejemplo: *«Usa la skill survey-simulator para comparar este cuestionario con este programa»*.

En los planes Team y Enterprise, la persona propietaria de la organización debe tener habilitadas las Skills y la ejecución de código. También puede publicar la Skill para toda la organización. Una Skill que sube un usuario es privada para su cuenta hasta que se comparte.

Ayuda oficial: https://support.claude.com/en/articles/12512180

## Opción B · Claude Code

Instalación personal, disponible en todos tus proyectos:

```bash
git clone https://github.com/Daniel-Silva03/Simulador-de-Encuestas.git
mkdir -p ~/.claude/skills
cp -r Simulador-de-Encuestas/skill/survey-simulator ~/.claude/skills/
```

Instalación en un solo proyecto: copia la carpeta a `.claude/skills/survey-simulator` dentro del proyecto.

Claude Code pide permiso antes de ejecutar los comandos de la Skill.

## Opción C · Línea de comandos, sin Claude

Sirve para ejecutar el flujo a mano o para comprobar la instalación.

```bash
git clone https://github.com/Daniel-Silva03/Simulador-de-Encuestas.git
cd Simulador-de-Encuestas/skill/survey-simulator
python -m pip install "pydantic>=2.5" "lxml>=5.0" "PyYAML>=6.0" "openpyxl>=3.1"   # solo si faltan
python -m survey_sim comprobar
```

Salida esperada (las versiones de los paquetes pueden variar):

```
dependencias: pydantic 2.x, lxml 6.x, yaml 6.x, openpyxl 3.1.x; python 3.1x
versiones: word_adapter=0.4.0, gim_adapter=0.3.0, expr_parser=0.1.0, correspondence=0.1.0, ruleset=1.1.0, simulator=0.1.0
```

Todos los comandos se ejecutan **desde la carpeta `skill/survey-simulator`**, que es la que contiene `SKILL.md`.

## Verificar que el paquete es el aprobado

El archivo `distribucion/SHA256SUMS.txt` contiene la huella del ZIP aprobado en la validación final V:

```bash
cd distribucion
sha256sum -c SHA256SUMS.txt
```

## Problemas frecuentes

| Síntoma | Causa probable y solución |
|---|---|
| `ERROR [...]` y código de salida 2 | El archivo de entrada no se pudo leer o no es `.docx` / GIM |
| Código de salida 3 | Hay un error en `pareos.yaml`, `equivalencias.yaml`, el registro o la configuración |
| Código de salida 4 | Defecto del lector. Repórtalo al responsable sin adjuntar el instrumento |
| Código de salida 5 | No se pudo escribir en la carpeta de salida |
| Claude no usa la Skill | Verifica que esté activada en *Customize > Skills* o nómbrala en la petición |
| La subida del ZIP falla | Sube el ZIP sin modificar. La carpeta interna debe llamarse `survey-simulator`, igual que el campo `name`. Algunas versiones de la ayuda de Claude mencionan un límite de 200 caracteres para la descripción; la descripción de esta Skill tiene 377 (el formato de Skills admite hasta 1.024). Si tu cuenta lo rechaza por ese motivo, avisa al responsable: no edites el paquete por tu cuenta |
| Un comando se corta en Claude | Cada comando tiene un límite de tiempo. Usa menos agentes por llamada o divide el instrumento |
