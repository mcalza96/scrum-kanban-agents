# Revisión de PR en localhost y cierre a cargo del PO

El PM entrega al PO el PR, el SHA revisado, reporte y evidencia técnica. El PO es responsable
de la vista previa para el dueño y de su cierre después del merge, incluido worktree y rama.
El PM conserva la revisión técnica y la escritura de closed en el tablero. El aviso del PR
se envía al buzón del PO; el barrido de 5 minutos cubre omisiones y HEAD nuevos (ver `inbox.md`).

## Antes de pedir revisión al dueño

1. El PO consulta si el PR ya tiene preview. Reutiliza el que corresponda al HEAD actual;
   no levanta otro servidor para el mismo PR/SHA. El registro vive en el review del ticket,
   que enlaza el PR. Si el PM ya lo inició, entrega también su identidad y cómo detenerlo.
2. Usa un worktree del SHA exacto del PR. Comprueba HEAD y que no hay cambios de aplicación
   pendientes que alteren lo mostrado. El servidor no corre sobre el checkout compartido.
   Usa el comando de desarrollo/preview del proyecto y un puerto libre, limitado a loopback.
3. Verifica la configuración antes de arrancar: localhost no implica DB local. Identifica
   stack y base local/remota sin mostrar credenciales; usa la configuración aprobada del
   proyecto, sin copiar .env a ciegas. Revisar una UI no autoriza migraciones ni escrituras
   de prueba sobre datos reales. Si falta configuración, comunica el bloqueo.
4. Registra URL, rutas, PR, SHA, worktree, comando, PID/job del runtime, identidad al inicio
   (fecha de arranque y cwd), puerto, log y responsable. Usa el mecanismo persistente de
   procesos del runtime; conserva la identidad que permitirá detenerlo al terminar.
5. Comprueba respuesta HTTP y contenido de las rutas afectadas, no solo la portada ni un
   mensaje Ready. Detecta redirecciones a login y errores de datos; no entregues una página
   de error como preview válido. Si el PR cambia, actualiza y verifica el nuevo SHA y solicita
   de nuevo la revisión visual.
6. Abre la URL localhost en el navegador del dueño con la herramienta disponible y envía
   en el mismo mensaje: enlace al PR, URL/rutas directas, qué cambió (antes → después),
   qué debe observar/probar y cualquier limitación real. Para cambios responsive, incluye
   los tamaños afectados. El dueño no debería tener que preguntar «¿qué mejora debo ver?».

El PO espera el visto bueno del dueño en cambios visibles antes de hacer merge. Si un PR
no produce una aplicación revisable en navegador, explica esa limitación y presenta la evidencia
pertinente; no levanta un servidor vacío para simular una revisión.

## Después del merge

1. El PO revisa alcance, prioridad, dependencias y checks vigentes, obtiene el OK visual del
   dueño y hace merge con la estrategia configurada del repositorio. Confirma PR merged y SHA integrado. En esa misma gestión, detiene el preview oficial
   y los duplicados propios registrados. Si creó el proceso otro agente, coordina su detención
   con ese responsable y comprueba el resultado; no evade el guard de procesos.
2. Detiene solo el job/proceso identificado al inicio, revalidando identidad/cwd. No mata por
   nombre ni toma el primer PID de un puerto: podría ser un cliente o un servidor ajeno.
   Comprueba que el proceso terminó y que no queda listener de ese preview en su puerto.
   Una petición HTTP fallida por sí sola no acredita que el proceso terminó.
3. Con el preview detenido, sincroniza main de forma segura y ejecuta desde el principal:

   ```sh
   python3 <skill>/scripts/dev_worktree.py cerrar <buzón> --pedido <id> --integrado <SHA-actual-main>
   ```

   El gate del SHA integrado debe pasar. El helper conserva la entrega si no puede verificar
   sesión idle, árbol limpio o integración. No se borra worktree/rama al hacer commit, abrir PR
   ni dar OK visual. Para worktrees anteriores sin metadata administrada, exige las mismas
   verificaciones y usa `git worktree remove` sin force y borrado local seguro de rama.
4. Si hubo un worktree extra exclusivo para preview, lo retira también, después de comprobar
   árbol limpio e integración. No elimina directorios compartidos, ramas remotas ni datos/DB.
5. Registra localhost apagado, gate y ausencia de worktree/rama local, y envía la evidencia al PM.
   El PM entonces marca closed. Si falla algún paso, comunica el pendiente y conserva reported;
   el dueño no tiene que recordar pedir la limpieza. Un PR abandonado o rechazado también exige
   detener su preview, conservando los commits mientras no exista una decisión sobre la entrega.

## Cierre único verificable

El PO registra el preview antes del merge con el helper canónico:

```sh
python3 <skill>/scripts/dev_worktree.py registrar-preview <gestion/inbox> --pedido <ID> --pr <N> --pid <PID-listener> --puerto <puerto> --directorio <worktree-preview> --sha <SHA-revisado>
# Solo para cambios sin revisión en navegador:
python3 <skill>/scripts/dev_worktree.py registrar-preview <gestion/inbox> --pedido <ID> --pr <N> --sin-preview
```

Si el PM transplantó el commit a otra rama para el PR, el PO añade `--rama-pr <rama-real>`
al registro; el recibo conserva tanto la rama dev como la rama real del PR.
El registro liga PID, inicio del proceso, cwd, puerto y commit. No acepta sustituir un preview
registrado. El PO conserva la evidencia del PR/SHA y no registra procesos de otro encargo.
Después del merge ejecuta el comando completo indicado en el aviso, con **inbox**, no gestión:

```sh
python3 <skill>/scripts/dev_worktree.py cerrar <gestion/inbox> --pedido <ID> --integrado <SHA-origin/main-actual> --sincronizar
```

El helper sincroniza main solo por avance directo y sin cambiar el checkout compartido;
si main está en uso y atrasado, se detiene para sincronización segura en ese checkout.
Exige el gate integrado verde antes de señalar únicamente el PID registrado con SIGTERM,
verifica que terminó y que el puerto quedó libre, y recién entonces retira la entrega.
Si el proceso todavía está terminando, se repite el mismo comando; no escala a SIGKILL.
Un error conserva el recibo pendiente y el worktree. No ocultar el exit code con `| tail`.
No usar unlock, remove, branch -D ni update-ref manuales para evitar un rechazo del helper.
Un preview adicional debe usar un worktree detached dedicado, limpio y del mismo repositorio.
El helper comprueba nuevamente su SHA y limpieza antes de retirarlo sin force, junto con el
worktree del encargo. El PM marca closed solo con recibo `status=closed`. Un recibo cuyo worktree ya desapareció
antes del gate exige revisión explícita: el barrido avisa, nunca fabrica un cierre.
