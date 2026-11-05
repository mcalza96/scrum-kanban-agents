# Adapter: opencode as the dev agent

## Checking what a session really did (evidence rule 2)

opencode stores sessions in a SQLite database, usually at `~/.local/share/opencode/opencode.db`.
Open it **read-only** and query only the session and message-part tables. Never read the tables
that hold credentials or accounts.

```bash
python3 - <<'EOF'
import sqlite3, json, datetime
db = sqlite3.connect("file:" + __import__("os").path.expanduser(
    "~/.local/share/opencode/opencode.db") + "?mode=ro", uri=True)
# recent sessions
for sid, title, t in db.execute(
        "select id, title, time_created from session order by time_created desc limit 5"):
    print(sid, datetime.datetime.fromtimestamp(t / 1000).strftime("%F %T"), title)
EOF
```

Each row of the `part` table has a `session_id`, a `time_created` in milliseconds, and a `data`
JSON. Tool calls have `type == "tool"`, a `tool` name such as `bash`, `edit`, or `write`, and
`state.input` and `state.output`. To list the commands a session ran:

```python
for t, data in db.execute(
        "select time_created, data from part where session_id = ? order by time_created", (sid,)):
    d = json.loads(data)
    if d.get("type") == "tool" and d.get("tool") == "bash":
        print(datetime.datetime.fromtimestamp(t / 1000).strftime("%T"),
              d["state"]["input"].get("command", "")[:200])
```

For edits, use `state.input.filePath`, `oldString`, and `newString`.

With these you can check three things:
- whether the command in the report ran;
- when it ran;
- which files the session edited, and at what time.

The schema may change between versions. Check it with `.schema part` before you rely on it.

## Measuring a session

The numbers of `references/traceability.md` come straight from opencode's store, read-only. One
query per dev session (`:suite` is a piece of the suite command, e.g. `verificar-commit`):

```bash
sqlite3 -readonly -header -column -cmd ".parameter set :s 'ses_...'" \
  -cmd ".parameter set :suite 'verificar-commit'" ~/.local/share/opencode/opencode.db < medir.sql
```

```sql
with tools as (
  select json_extract(p.value, '$.name') as name,
         json_extract(p.value, '$.state.status') as status,
         coalesce(json_extract(p.value, '$.state.input.command'),
                  json_extract(p.value, '$.state.input.filePath')) as arg
  from session_message m, json_each(m.data, '$.content') p
  where m.session_id = :s and m.type = 'assistant' and json_extract(p.value, '$.type') = 'tool')
select
  (select count(*) from session_message where session_id = :s and type = 'user') as prompts,
  (select round((time_idle - time_created) / 60000.0, 1) from session_v2 where id = :s) as minutes,
  (select count(*) from tools where name = 'shell' and arg like '%' || :suite || '%') as suite_runs,
  (select count(*) - count(distinct arg) from tools where name = 'read') as rereads,
  (select count(*) from tools where status = 'error') as failed,
  (select count(*) from tools where name = 'shell' and arg like '%inbox.py send%') as sends;
```

`prompts` above 1 means the session was not one-shot. `sends` counts every `inbox.py send`; to see
whom it wrote, list the shell calls (`select arg from tools where name = 'shell'`). Read the output
of a call from `$.state.content`. Tested on opencode 2.2.5 (2026-11-04): on SIM-102 it gave
`prompts 3, minutes 28.7, suite_runs 6`.

## Pitfalls

- **A rate-limited model makes opencode wait instead of fail.** No stdout, no stderr, and the
  process stays alive until something kills it. When tasks always die at the same elapsed time, read
  `~/.local/share/opencode/log/`.
- **A model listed by the provider's `/models` endpoint can still fail when invoked.** Make one
  real call before wiring it in.
- **Custom headers for an OpenAI-compatible provider go per model**
  (`provider.<id>.models.<model>.headers`), not in `provider.<id>.options`.
- **Config changes need a restart.** opencode reads its config at startup.
- **opencode v2 plugins are not the v1 API.** A plugin is `export default { id, setup }`, and
  `setup(api)` hooks with `api.session.hook("context", fn)`. A v1 plugin (`export const X = async
  () => ({ "chat.message": ... })`) does not load: the log says «Plugin must export a default
  definition with an id and an effect or setup function». `adapters/opencode/inbox.js` is a working
  v2 plugin; `scripts/opencode_inbox_smoke.sh` proves it still works after an upgrade.
- `opencode run --session <id> "<text>"` without `--standalone` sends a turn through the running
  service: it shows live in the window that has that session open. `adapters/opencode/wake.py`
  uses it to wake idle PMs; idle means `session_v2.time_busy <= time_idle` in the database.
- A detached process started from a session's shell tool did not survive the command in a real
  run (2026-10-30: two sends, no line in the log; the cause was not isolated). Work that must
  outlive a turn starts from a plugin, which runs in the service (`inbox.js` starts `wake.py`).
- **Lanzador de devs (CAL-25).** `opencode run --title "dev <ticket>"` crea la sesión del dev;
  flags `--model` y `--agent` fijan el modelo/agente si hiciera falta (verificados con
  `opencode run --help`), pero por defecto se usan los del servicio. No hay flag `--parent` para
  fijar el padre; la trazabilidad dev→PM queda en `<buzón>/.lanzar/log` y en el título `dev <ticket>`
  (que `medir-agentes.py` ya reconoce). El PM encola con `inbox.py lanzar` que escribe en
  `<buzón>/.lanzar/`; `inbox.js` observa esa carpeta y arranca `adapters/opencode/lanzar.py` en el
  servicio, que respeta el tope global (10, sin tope por PM) y la cola FIFO, y encola el aviso de falla si
  el dev muere sin reportar. Un ticket no se encola dos veces mientras haya uno en cola, tomado o con dev vivo: `lanzar` dice `en cola, no reintentes, mira <buzón>/.lanzar/log` (D-285) y un candado verifica que dos `lanzar` seguidos del mismo ticket generen un solo pedido. No toques `<buzón>/.lanzar/` a mano.
