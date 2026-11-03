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

En Codex Desktop, una automatización heartbeat de este chat ejecuta la ronda cada 30 minutos.
El heartbeat usa las herramientas nativas de automations: no escribe directamente archivos de
configuración ni invoca una API privada del app-server. El evento de un archivo no es una
notificación instantánea a Codex. La recepción queda limitada por el intervalo, la disponibilidad
de la app/equipo y los permisos de ejecución de la automatización.

El heartbeat lee mensajes sin ack y no genera salida si nada cambió. Solo comunica avances
relevantes, fallas y decisiones del dueño. No arranques además tail -F: los ack reescriben el archivo
y ese método ya reprodujo mensajes antiguos. No mantengas simultáneamente dos PO activos.

## Envío y devs

inbox.py send escribe bajo candado. El watcher ya instalado en opencode observa los buzones de los
PM y wake.py continúa sus sesiones. Que el archivo se escriba no demuestra que el PM haya actuado:
comprueba el ack o una respuesta.

Para un dev, usa opencode run con un prompt autocontenido TO: dev ID y el modelo autorizado por
el proyecto. Ejecútalo con la herramienta de terminal y conserva su identificador de ejecución;
no uses una sesión Codex como sustituto de un dev opencode pedido por el dueño.

## Permisos y verificación

El sandbox puede ser de solo lectura. Para send, ack, commits y lanzamientos solicita la escalación
nativa del comando; nunca declares un envío o cierre cuando el comando fue rechazado.
Prueba send/read/ack en un buzón temporal y luego pide una respuesta real a un PM para comprobar
el retorno a PO. La comprobación de ida y vuelta debe quedar en el reporte del traspaso.
