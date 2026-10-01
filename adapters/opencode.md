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

## Tracing

`scripts/trace/tracer.py ingest --source opencode` reads the same tables, read-only, and builds the
trace described in `references/traceability.md`.

## Pitfalls

- **A rate-limited model makes opencode wait instead of fail.** No stdout, no stderr, and the
  process stays alive until something kills it. When tasks always die at the same elapsed time, read
  `~/.local/share/opencode/log/`.
- **A model listed by the provider's `/models` endpoint can still fail when invoked.** Make one
  real call before wiring it in.
- **Custom headers for an OpenAI-compatible provider go per model**
  (`provider.<id>.models.<model>.headers`), not in `provider.<id>.options`.
- **Config changes need a restart.** opencode reads its config at startup.
