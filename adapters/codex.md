# Codex como PO; PM y devs en opencode

El transporte es el buzón de archivos existente. No hace falta un segundo servidor MCP.
Codex conserva el nombre PO; los agentes siguen escribiendo en inbox/PO.md.

## Cada turno

1. Lee SKILL.md y el ancla vigente de focus/PROJECT.md.
2. Ejecuta scripts/inbox.py read <repo>/inbox PO antes de actuar.
3. Revisa los STATUS y .wakes.log, contrasta los reportes y manda el siguiente turno
   con scripts/inbox.py send <repo>/inbox --to PM-X --from PO <texto>.
4. Haz ack únicamente de mensajes sobre los que ya actuaste. Una lectura no es ack.
5. No repitas mutaciones de producción al comprobar evidencia; usa comprobaciones de lectura.

Usa la ruta de esta instalación para scripts/inbox.py y scripts/focus.py.
No copies scripts ni cambies los plugins opencode existentes para transportar el mismo mensaje.
El plugin opencode puede seguir usando su instalación .claude; ambos leen el mismo inbox.

## Recepción en el chat

El puente `adapters/codex/wake.py` se ejecuta por launchd WatchPaths sobre inbox/PO.md.
Usa la interfaz pública del CLI instalado: `codex queue --thread UUID --message TEXTO --resume`.
El servidor local se inicia mediante `codex app-server daemon start` (idempotente), sin habilitar
control remoto. No se cambia el watcher opencode ni se usa una API privada de Desktop.

La configuración local pasa --root (inbox), --thread (este chat), --state (JSON fuera del repo)
y --codex (ruta absoluta del CLI). RunAtLoad permite recoger mensajes al iniciar sesión.
El estado guarda IDs bajo flock; ack y reescrituras no reenvían mensajes ya encolados.
El puente no hace ack. Los contenidos del buzón se leen con inbox.py, no se ejecutan como código.

**Encolado no equivale a despertar.** CLI 0.166.0-alpha.5.1 puede devolver exit 0 con warning si
resume falla. El puente conserva el recibo de cola para evitar duplicados y registra el warning.
En la prueba real Desktop mantuvo el chat con `already has an active writer`: no se confirmó
el despertar de un chat inactivo. No prometas recepción inmediata hasta verificar ese caso.
El bloqueo impide que el daemon tome simultáneamente el chat que Desktop mantiene cargado.
La disponibilidad de app/equipo y servidor limita la entrega; WatchPaths tampoco ofrece una SLA.

La automatización heartbeat de este chat ejecuta además la ronda cada 30 minutos, como respaldo.
Permanece silenciosa sin cambios relevantes. No arranques tail -F: los ack reescriben el archivo
y ese método ya reprodujo mensajes antiguos. No mantengas simultáneamente dos PO activos.

## Envío y devs

inbox.py send escribe bajo candado. El watcher ya instalado en opencode observa los buzones de los
PM y wake.py continúa sus sesiones. Que el archivo se escriba no demuestra que el PM haya actuado:
comprueba el ack o una respuesta.

Para un dev, usa opencode run con un prompt autocontenido TO: dev ID y el modelo autorizado por
el proyecto. Ejecútalo con la herramienta de terminal y conserva su identificador de ejecución;
no uses una sesión Codex como sustituto de un dev opencode pedido por el dueño.

## Permisos y verificación

Respeta los permisos de la sesión. Nunca declares un envío o cierre cuando el comando fue rechazado.
Prueba send/read/ack en un buzón temporal y luego pide una respuesta real a un PM para comprobar
el retorno a PO. La comprobación de ida y vuelta debe quedar en el reporte del traspaso.
