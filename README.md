# scrum-kanban-agents

**Del encargo al merge, con agentes coordinados y evidencia en cada entrega.**

Un flujo de desarrollo para equipos de agentes: el dev implementa en su propio worktree;
el PM verifica el commit y prepara el PR; el PO presenta los cambios al dueño, hace merge
y se encarga del cierre. El dueño conserva las decisiones de producto y arquitectura.

Nació de un proyecto real con agentes trabajando en paralelo sobre el mismo repositorio.
Su foco es que puedas revisar lo que construyeron, comprobar qué llegó a main y conservar
el trabajo cuando algo falla.

## El flujo

```text
Dueño → objetivo, alcance y decisiones
            ↓
PM    → ticket, dependencias y encargo
            ↓
Dev   → worktree propio → implementación → pruebas → commit + reporte
            ↓
PM    → verifica evidencia y SHA → push de la rama → PR → aviso al PO
            ↓
PO    → localhost + explicación de cambios → revisión del dueño → merge
            ↓
PO    → cierre verificado: main, gate, preview, worktree y rama
            ↓
PM    → registra closed con la evidencia del cierre
```

Si la revisión técnica falla, el PM devuelve el ticket a una **nueva sesión dev**.
El trabajo conserva su estado `reported` mientras falte revisión, merge o cierre verificable.
Un commit, un reporte o un PR abierto son pasos de la entrega; `closed` exige completar el ciclo.

| Rol | Responsabilidad |
|---|---|
| Dueño | Define objetivos, decide los cambios de arquitectura y acepta los cambios visibles. |
| PO | Coordina producto y dependencias, presenta el preview, ejecuta el merge y cierra sus recursos. |
| PM | Prepara tickets, verifica el trabajo, sube la rama, abre el PR y registra el cierre. |
| Dev | Resuelve un encargo por sesión, prueba, hace commit y entrega SHA, reporte y bitácora. |
| Gates | Comprueban condiciones reproducibles sobre el commit que se está evaluando. |

## Qué puedes esperar

### Una entrega que puedes comprobar

Los reportes incluyen comandos y salidas literales. El PM contrasta esa evidencia, revisa el
commit y consulta los registros reales de la sesión. Un build sobre el checkout compartido
puede depender de archivos sin commit: la verificación debe corresponder al SHA entregado.

Los gates conservan sus condiciones de aceptación. Si el proyecto admite fallos conocidos,
el trinquete impide ampliar la lista para esconder regresiones.
[Reportes y evidencia](references/evidence.md) · [Gates](references/gates.md).

### Trabajo paralelo con límites claros

Cada encargo tiene un worktree temporal, rama, base y entrega identificables. Las lanes
coordinan cambios sobre los mismos archivos; los worktrees aíslan árboles e índices.
El dev conserva su trabajo hasta que se comprueba la integración y el cierre.

El guard de OpenCode bloquea operaciones que pueden pisar trabajo ajeno en el checkout
compartido, mutaciones de stashes y atajos de retiro sobre worktrees administrados.
Los stashes pertenecen al repositorio y se comparten entre worktrees.
[Flujo Git y aislamiento](references/file-mode.md#worktree-temporal-y-cierre-verificado).

### Revisión humana sobre cambios visibles

Para un cambio revisable en navegador, el PO abre localhost y explica **qué cambió,
qué revisar y qué limitaciones quedan**, con enlaces a las rutas afectadas y al PR.
Comprueba el SHA y el contenido mostrado. Identifica la configuración de datos utilizada:
localhost puede conectarse a una base remota.

El preview queda registrado con PR, SHA, directorio, PID, inicio del proceso y puerto.
El cierre exige integración, gate verde, proceso terminado y puerto libre antes de retirar
los worktrees y la rama local correspondientes. Si falta evidencia o main diverge,
se conserva la entrega y se informa el bloqueo. [Preview y cierre](references/pr-preview.md).

### Coordinación que sobrevive entre sesiones

Los objetivos y decisiones vigentes viven en archivos de focus; los tickets y estados,
en el tablero; las conversaciones entre responsables, en buzones con mensajes y acuses.
El siguiente agente puede retomar desde esos registros.

En OpenCode, los mensajes despiertan al responsable cuando está idle. El lanzador procesa
encargos en segundo plano y avisa al PM cuando termina un dev. El barrido existente de
cinco minutos detecta PRs abiertos listos para revisión, cambios de HEAD y cierres pendientes
tras el merge; avisa al PO aunque se haya omitido el aviso manual. Los avisos de PR se deduplican
por repositorio, PR y SHA; los de cierre, por entrega y PR.
[Focus](references/focus-log.md) · [Buzones, wake y lanzador](references/inbox.md).

## Empieza con el alcance que necesitas

La skill contiene instrucciones, plantillas y scripts. Puedes usar el flujo con un tablero
Markdown o un tablero externo. La automatización depende del adaptador y de su instalación.

1. Define el objetivo, las restricciones y quién ocupa los roles de dueño, PO y PM.
2. Crea la épica y tickets desde [las plantillas](templates/), con alcance y criterios verificables.
3. Lee [SKILL.md](SKILL.md) y elige [modo archivos](references/file-mode.md) o
   [modo tablero](references/board-mode.md).
4. Instala el adaptador que corresponda si necesitas lanzamiento, avisos y protecciones automáticas.

Para trabajo complejo, la skill incluye revisión adversarial del plan y un plan de cierre
para épicas que siguen creciendo. Las referencias detallan cuándo aplicarlos.

## Instalación: una implementación canónica

Clona una sola vez donde tu agente pueda descubrir la skill. Por ejemplo:

```sh
git clone https://github.com/mcalza96/scrum-kanban-agents \
  "$HOME/.agents/skills/scrum-kanban-agents"
```

Si ya tienes una instalación canónica en otra ubicación, reutilízala. Las demás rutas de
descubrimiento pueden apuntar a ella mediante enlaces simbólicos. Los proyectos cargan esa
implementación a través de entradas mínimas; copiar los adaptadores crea versiones que divergen.

El nombre técnico actual para invocar la skill sigue siendo `scrum-kanban-agents`.
Pide al agente que lea su `SKILL.md` y asuma el rol que le corresponde.

### Automatización en OpenCode

Desde la instalación canónica:

```sh
python3 scripts/install.py /ruta/al/proyecto
python3 scripts/install.py /ruta/al/proyecto --check
```

El instalador crea las entradas mínimas. Por defecto cargan
`~/.agents/skills/scrum-kanban-agents`; `SCRUM_SKILL` permite indicar otra ubicación canónica.
Si encuentra una implementación previa que requiere revisión, informa el conflicto.

La configuración de `.scrum.toml` permite separar el repositorio de aplicación del de gestión.
El buzón es estado vivo y queda fuera de Git. Configura el barrido con launchd o cron cada cinco
minutos, pasando **el repositorio de aplicación**:

```sh
python3 adapters/opencode/wake.py --sweep /ruta/al/proyecto
```

El instalador de entradas y la configuración del timer son pasos distintos. Comprueba los
plugins en el runtime; al cambiar su código JavaScript puede hacer falta reiniciar OpenCode
para recargarlo. Después de actualizar OpenCode, ejecuta `scripts/opencode_inbox_smoke.sh`.
[Configuración y diagnóstico del adaptador](adapters/opencode.md).

### Otros entornos

| Entorno | Guía |
|---|---|
| Codex como PO, con PM y devs en OpenCode | [Adaptador Codex](adapters/codex.md), incluidos los límites observados del despertar. |
| Claude Code | Hooks de focus y buzón descritos en [focus](references/focus-log.md) y [buzones](references/inbox.md). |
| Git | [Gates y pre-push](references/gates.md); la configuración del hook es explícita por repositorio. |
| Otros agentes o tableros | Usa roles, tickets, reportes y criterios de cierre desde `SKILL.md`; conecta el transporte disponible. |

## Herramientas principales

| Herramienta | Para qué sirve |
|---|---|
| `scripts/inbox.py` | Enviar, leer y acusar mensajes; encolar encargos. |
| `adapters/opencode/lanzar.py` | Admitir y lanzar encargos en worktrees propios, con registro recuperable del arranque. |
| `adapters/opencode/wake.py` | Despertar responsables idle y ejecutar el barrido existente. |
| `scripts/dev_worktree.py` | Registrar entrega y preview; cerrar el worktree administrado tras las verificaciones. |
| `scripts/check_report.py` | Extraer comandos de un reporte y contrastar sus salidas. |
| `scripts/candados.py` / `scripts/trinquete.py` | Detectar aserciones retiradas y ampliaciones de fallos conocidos. |
| `scripts/focus.py` | Cargar objetivos y decisiones vigentes y detectar problemas en sus registros. |
| `scripts/archivar.py` | Archivar estado operativo; STATUS y focus requieren la acción manual explícita `--tableros`, sin editores concurrentes. |

Para revisar un reporte, primero lista sus comandos:

```sh
python3 scripts/check_report.py reports/T-07.md
```

Después de leerlos, puedes ejecutarlos con `--run --cwd /ruta/al/repo`, o seleccionar un
subconjunto con `--only`. Esa opción ejecuta comandos del reporte.

Los comandos completos de entrega, registro del preview y cierre están en
[el protocolo de revisión local](references/pr-preview.md#cierre-único-verificable).

## Alcance y verificación

Las protecciones reducen errores operativos; requieren configuración y comprobación en el
runtime utilizado. El guard es una barrera sobre herramientas conocidas, con límites frente
a comandos indirectos. El responsable debe comprobar la entrega y el cierre reales.

La suite de scripts se ejecuta con:

```sh
python3 -m unittest discover -s scripts/tests
```

Los scripts Python usan la biblioteca estándar. Las pruebas del guard y del preview requieren
Node; la observación de previews utiliza `ps` y `lsof`. La suite verifica el flujo de la skill;
los tests, builds y gates de cada aplicación siguen siendo responsabilidad del proyecto.

## Estructura

```text
SKILL.md       roles, ciclo de trabajo, reglas y definición de terminado
references/    procedimientos por tema: evidencia, gates, focus, buzones, preview
templates/    plantillas de épicas, tickets, estados, prompts y revisiones
scripts/       herramientas deterministas, hooks y pruebas
adapters/      integración con los runtimes y tableros disponibles
```

## Contribuir

Trae el caso observado, el comando, su salida y la verificación del cambio.
[Formato de lecciones](references/lessons.md). Licencia [MIT](LICENSE).
