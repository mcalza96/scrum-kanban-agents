"""Read opencode sessions (read-only) into the normalized trace format.

Only the `session`, `message` and `part` tables are read. Never the credential/account tables.
Schema checked against opencode's SQLite store; verify with `.schema part` after upgrades.
"""
from __future__ import annotations

import json
import sqlite3


def connect(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def list_sessions(db: sqlite3.Connection, since_ms: int = 0, ids: list[str] | None = None):
    q = "select id from session where time_created >= ?"
    args: list = [since_ms]
    if ids:
        q += " and id in (%s)" % ",".join("?" * len(ids))
        args += ids
    return [r[0] for r in db.execute(q + " order by time_created", args)]


def read_session(db: sqlite3.Connection, sid: str) -> dict:
    """Return {session, messages: [{role, time, tokens, cost, text, tools:[...]}]} in time order."""
    title, model, parent, tc = db.execute(
        "select title, model, parent_id, time_created from session where id = ?", (sid,)).fetchone()
    try:
        model = json.loads(model).get("id", model) if model else None
    except (ValueError, AttributeError):
        pass
    parts_by_msg: dict[str, list] = {}
    for mid, pt, pdata in db.execute(
            "select message_id, time_created, data from part where session_id = ? "
            "order by time_created", (sid,)):
        parts_by_msg.setdefault(mid, []).append((pt, json.loads(pdata)))
    messages = []
    for mid, mt, mdata in db.execute(
            "select id, time_created, data from message where session_id = ? order by time_created",
            (sid,)):
        m = json.loads(mdata)
        parts = parts_by_msg.get(mid, [])
        text = "\n".join(p.get("text", "") for _, p in parts if p.get("type") == "text")
        tools = []
        last = mt
        for pt, p in parts:
            last = max(last, pt)
            if p.get("type") != "tool":
                continue
            st = p.get("state") or {}
            tm = st.get("time") or {}
            inp = st.get("input") or {}
            start, end = tm.get("start") or pt, tm.get("end") or tm.get("start") or pt
            last = max(last, end)
            tools.append({
                "tool": p.get("tool"), "start": start, "end": end,
                "ok": st.get("status") != "error",
                "command": inp.get("command"), "path": inp.get("filePath") or inp.get("path"),
                "pattern": inp.get("pattern"),
            })
        tok = m.get("tokens") or {}
        cache = tok.get("cache") or {}
        mtime = m.get("time") or {}
        messages.append({
            "role": m.get("role"), "start": mtime.get("created") or mt,
            "end": max(mtime.get("completed") or mt, last),
            "text": text, "tools": tools,
            "tokens_in": tok.get("input") or 0, "tokens_out": tok.get("output") or 0,
            "tokens_reasoning": tok.get("reasoning") or 0, "tokens_cache_read": cache.get("read") or 0,
            "cost": m.get("cost") or 0.0,
        })
    return {"id": sid, "source": "opencode", "title": title, "model": model,
            "parent_id": parent, "start": tc, "messages": messages}
