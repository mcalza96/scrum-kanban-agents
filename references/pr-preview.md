# Revisión de PR en localhost y cierre a cargo del PO

El PM entrega al PO el PR, el SHA revisado, reporte y evidencia técnica. El PO es responsable
de la vista previa para el dueño y de su cierre después del merge, incluido worktree y rama.
El PM conserva la revisión técnica y la escritura de closed en el tablero.

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
