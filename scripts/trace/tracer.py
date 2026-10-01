#!/usr/bin/env python3
"""Trace agent work: what each dev session did, how long each step took, what it cost.

Layout of a data directory (keep it in a PRIVATE repo; it names your tickets and commands):

    <data>/<project>/sessions/<session_id>.json   measured facts, rewritten on each ingest
    <data>/<project>/events.jsonl                 PM/human facts, append-only (verdicts, logbook,
                                                  manual assignments)
    <data>/trace.db                               derived; rebuild any time with `build`

Commands:
    ingest   read agent sessions from a source (opencode) and write session files
    event    append a verdict, a logbook entry, or a manual assignment
    build    rebuild trace.db from the session files and events
    report   print a view of trace.db

Measured numbers come from the agent runtime's own records, never from what the agent says.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DEFAULT_TICKET = [r"\b([A-Z][A-Z0-9]*-\d+[a-z]?)\b"]
DEFAULT_AGENT = [r"(?im)^\s*(?:PARA|TO)\s*:\s*(.+?)\s*(?:—|-{2}|$)",
                 r"(?i)\b(?:eres|you are)\s+(?:el\s+|la\s+|the\s+)?(.+?)\s*(?:,|\.|\bde la\b|\bagente\b|\bon the\b)"]
DEFAULT_RETURN = r"(?i)devuelt|devoluci|correcci|corrige|\breturned\b|\bfix the findings\b"
DEFAULT_SUITE = ["pytest", "unittest", "paralelo.py", "npm test", "npm run test", "go test",
                 "cargo test", "jest", "vitest", "tox", "make test"]
READ_TOOLS = {"read", "grep", "glob", "list", "view"}
EDIT_TOOLS = {"edit", "write", "patch", "multiedit"}
SHELL_TOOLS = {"bash", "shell", "terminal"}
SECRET = re.compile(r"(?i)((?:api[_-]?key|token|secret|password|passwd|authorization)\s*[=:]\s*)\S+")


def read_text(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def first_match(patterns: list[str], *texts: str) -> str | None:
    """Earliest match in the first text that has one (a ticket is usually named first)."""
    for text in texts:
        hits = [m for p in patterns for m in [re.search(p, text or "")] if m]
        if hits:
            m = min(hits, key=lambda m: m.start())
            return (m.group(1) if m.groups() else m.group(0)).strip()
    return None


def step_summary(t: dict) -> str:
    s = t.get("command") or t.get("path") or t.get("pattern") or ""
    s = SECRET.sub(r"\1<redacted>", " ".join(str(s).split()))
    return s[:160]


def normalize(raw: dict, project: str, cfg: dict) -> dict:
    """Split a raw session into tasks (one per user prompt) and compute per-task metrics."""
    msgs = raw["messages"]
    tasks: list[dict] = []
    cur = None
    for m in msgs:
        if m["role"] == "user":
            cur = {"prompt": m["text"], "start": m["start"], "end": m["end"], "msgs": []}
            tasks.append(cur)
        elif cur is None:                     # assistant output before any prompt: own task
            cur = {"prompt": "", "start": m["start"], "end": m["end"], "msgs": []}
            tasks.append(cur)
        if m["role"] != "user":
            cur["msgs"].append(m)
            cur["end"] = max(cur["end"], m["end"])

    out_tasks, out_steps = [], []
    prev_agent = None
    for n, t in enumerate(tasks, 1):
        prompt = t["prompt"]
        agent = first_match(cfg["agent"], prompt) or prev_agent or "unknown"
        agent = re.sub(r"\s+", " ", agent).strip(" *·:")[:40]
        prev_agent = agent
        ticket = first_match(cfg["ticket"], prompt, raw.get("title") or "")
        kind = "return" if re.search(cfg["return"], prompt[:400]) else "work"
        if not prompt.strip():
            kind = "other"
        calls = [c for m in t["msgs"] for c in m["tools"]]
        calls.sort(key=lambda c: c["start"])
        seen, rereads, kinds = set(), 0, Counter()
        suite_runs, suite_ms, longest_call, longest_gap = 0, 0, 0, 0
        prev_end = t["start"]
        for i, c in enumerate(calls, 1):
            tool = (c["tool"] or "").lower()
            dur = max(0, c["end"] - c["start"])
            gap = max(0, c["start"] - prev_end)
            prev_end = max(prev_end, c["end"])
            longest_call, longest_gap = max(longest_call, dur), max(longest_gap, gap)
            cmd = c.get("command") or ""
            is_suite = tool in SHELL_TOOLS and any(p in cmd for p in cfg["suite"])
            if is_suite:
                suite_runs += 1
                suite_ms += dur
            if tool in READ_TOOLS and c.get("path"):
                rereads += c["path"] in seen
                seen.add(c["path"])
            kinds["read" if tool in READ_TOOLS else "edit" if tool in EDIT_TOOLS
                  else "shell" if tool in SHELL_TOOLS else "other"] += 1
            out_steps.append({"task_n": n, "n": i, "start_ms": c["start"], "end_ms": c["end"],
                              "dur_s": round(dur / 1000, 1), "gap_s": round(gap / 1000, 1),
                              "tool": tool, "summary": step_summary(c), "ok": int(c["ok"]),
                              "is_suite": int(is_suite)})
        tool_ms = sum(max(0, c["end"] - c["start"]) for c in calls)
        wall_ms = max(0, t["end"] - t["start"])
        s = lambda k: sum(m[k] for m in t["msgs"])  # noqa: E731
        out_tasks.append({
            "n": n, "agent": agent, "ticket": ticket, "kind": kind,
            "prompt_head": " ".join(prompt.split())[:160],
            "start_ms": t["start"], "end_ms": t["end"],
            "wall_min": round(wall_ms / 60000, 2), "tool_min": round(tool_ms / 60000, 2),
            "other_min": round(max(0, wall_ms - tool_ms) / 60000, 2),
            "steps": len(t["msgs"]), "calls": len(calls),
            "reads": kinds["read"], "edits": kinds["edit"], "shell": kinds["shell"],
            "suite_runs": suite_runs, "suite_min": round(suite_ms / 60000, 2), "rereads": rereads,
            "failed": sum(not c["ok"] for c in calls),
            "longest_call_min": round(longest_call / 60000, 2),
            "longest_gap_min": round(longest_gap / 60000, 2),
            "tokens_in": s("tokens_in"), "tokens_out": s("tokens_out"),
            "tokens_reasoning": s("tokens_reasoning"), "tokens_cache_read": s("tokens_cache_read"),
            "cost": round(s("cost"), 4),
        })
    start = min([raw["start"]] + [t["start"] for t in tasks])
    end = max([raw["start"]] + [t["end"] for t in tasks])
    return {
        "session": {"id": raw["id"], "project": project, "source": raw["source"],
                    "title": raw.get("title"), "model": raw.get("model"),
                    "parent_id": raw.get("parent_id"), "start_ms": start, "end_ms": end,
                    "wall_min": round((end - start) / 60000, 2),
                    "prompts": sum(1 for m in msgs if m["role"] == "user"),
                    "ingested_at": now_iso()},
        "tasks": out_tasks, "steps": out_steps,
    }


# ---------------------------------------------------------------- commands

def cmd_ingest(a) -> int:
    if a.source != "opencode":
        sys.exit(f"unknown source {a.source}")
    from sources import opencode
    cfg = {"ticket": a.ticket_regex or DEFAULT_TICKET, "agent": a.agent_regex or DEFAULT_AGENT,
           "return": a.return_regex or DEFAULT_RETURN, "suite": a.suite_pattern or DEFAULT_SUITE}
    since = int(dt.datetime.fromisoformat(a.since).timestamp() * 1000) if a.since else 0
    db = opencode.connect(os.path.expanduser(a.opencode_db))
    title_re = re.compile(a.title_regex) if a.title_regex else None
    outdir = os.path.join(a.data, a.project, "sessions")
    os.makedirs(outdir, exist_ok=True)
    n = 0
    for sid in opencode.list_sessions(db, since, a.session):
        raw = opencode.read_session(db, sid)
        if title_re and not title_re.search(raw.get("title") or ""):
            continue
        rec = normalize(raw, a.project, cfg)
        with open(os.path.join(outdir, f"{sid}.json"), "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=1)
        s = rec["session"]
        print(f"{sid}  prompts={s['prompts']}  {s['wall_min']:6.1f} min  {(s['title'] or '')[:50]}")
        n += 1
    print(f"{n} sessions -> {outdir}")
    return 0


def cmd_event(a) -> int:
    ev = {"type": a.type, "at": now_iso()}
    for k in ("session", "task", "ticket", "round", "verdict", "cause", "agent", "kind", "note"):
        v = getattr(a, k, None)
        if v is not None:
            ev[k] = v
    if a.type == "log":
        ev["text"] = read_text(a.file) if a.file else a.text
        if not ev.get("text"):
            sys.exit("log needs --text or --file")
    if a.type == "verdict" and not (a.ticket and a.verdict):
        sys.exit("verdict needs --ticket and --verdict")
    if a.type == "assign" and not a.session:
        sys.exit("assign needs --session")
    path = os.path.join(a.data, a.project, "events.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    print(f"appended {a.type} to {path}")
    return 0


def cmd_build(a) -> int:
    dbp = a.db or os.path.join(a.data, "trace.db")
    if os.path.exists(dbp):
        os.remove(dbp)
    db = sqlite3.connect(dbp)
    db.executescript(read_text(os.path.join(HERE, "schema.sql")))
    counts = Counter()
    for project in sorted(os.listdir(a.data)):
        pdir = os.path.join(a.data, project)
        if not os.path.isdir(pdir):
            continue
        events = []
        ev_path = os.path.join(pdir, "events.jsonl")
        if os.path.exists(ev_path):
            events = [json.loads(l) for l in read_text(ev_path).splitlines() if l.strip()]
        assigns = {}
        for e in events:                       # later assignments win
            if e["type"] == "assign":
                assigns.setdefault((e["session"], e.get("task")), {}).update(
                    {k: e[k] for k in ("ticket", "agent", "kind") if k in e})
        sdir = os.path.join(pdir, "sessions")
        for fn in sorted(os.listdir(sdir)) if os.path.isdir(sdir) else []:
            rec = json.loads(read_text(os.path.join(sdir, fn)))
            s = rec["session"]
            db.execute("insert into session values (?,?,?,?,?,?,?,?,?,?,?)",
                       (s["id"], project, s["source"], s["title"], s["model"], s["parent_id"],
                        s["start_ms"], s["end_ms"], s["wall_min"], s["prompts"], s["ingested_at"]))
            for t in rec["tasks"]:
                t = dict(t)
                t.update(assigns.get((s["id"], None), {}))
                t.update(assigns.get((s["id"], t["n"]), {}))
                cols = ["n", "agent", "ticket", "kind", "prompt_head", "start_ms", "end_ms",
                        "wall_min", "tool_min", "other_min", "steps", "calls", "reads", "edits",
                        "shell", "suite_runs", "suite_min", "rereads", "failed",
                        "longest_call_min", "longest_gap_min", "tokens_in", "tokens_out",
                        "tokens_reasoning", "tokens_cache_read", "cost"]
                db.execute("insert into task (session_id, project, %s) values (?,?,%s)"
                           % (",".join(cols), ",".join("?" * len(cols))),
                           [s["id"], project] + [t[c] for c in cols])
                counts["task"] += 1
            for st in rec["steps"]:
                db.execute("insert into step values (?,?,?,?,?,?,?,?,?,?,?)",
                           (s["id"], st["task_n"], st["n"], st["start_ms"], st["end_ms"],
                            st["dur_s"], st["gap_s"], st["tool"], st["summary"], st["ok"],
                            st["is_suite"]))
            counts["session"] += 1
        for e in events:
            if e["type"] == "verdict":
                db.execute("insert into verdict values (?,?,?,?,?,?,?,?)",
                           (project, e["ticket"], e.get("round", 0), e["verdict"],
                            e.get("cause"), e.get("session"), e["at"], e.get("note")))
                counts["verdict"] += 1
            elif e["type"] == "log":
                db.execute("insert into log values (?,?,?,?,?,?)",
                           (project, e.get("session"), e.get("task"), e.get("ticket"),
                            e["at"], e["text"]))
                counts["log"] += 1
    db.commit()
    print(f"{dbp}: " + ", ".join(f"{v} {k}s" for k, v in sorted(counts.items())))
    return 0


def cmd_report(a) -> int:
    db = sqlite3.connect(a.db or os.path.join(a.data, "trace.db"))
    q = a.sql or f"select * from {a.view}"
    cur = db.execute(q)
    names = [d[0] for d in cur.description]
    rows = [["" if v is None else str(v) for v in r] for r in cur.fetchall()]
    w = [min(40, max([len(n)] + [len(r[i]) for r in rows])) for i, n in enumerate(names)]
    print("  ".join(n.ljust(w[i]) for i, n in enumerate(names)))
    for r in rows:
        print("  ".join(v[:40].ljust(w[i]) for i, v in enumerate(r)))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Trace agent work sessions.")
    ap.add_argument("--data", default=os.environ.get("TRACE_DATA", "."),
                    help="data directory (default $TRACE_DATA or .)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("ingest")
    g.add_argument("--project", required=True)
    g.add_argument("--source", default="opencode")
    g.add_argument("--opencode-db", default="~/.local/share/opencode/opencode.db")
    g.add_argument("--since", help="YYYY-MM-DD[THH:MM]")
    g.add_argument("--session", nargs="+")
    g.add_argument("--title-regex")
    g.add_argument("--ticket-regex", action="append")
    g.add_argument("--agent-regex", action="append")
    g.add_argument("--return-regex")
    g.add_argument("--suite-pattern", action="append")

    e = sub.add_parser("event")
    e.add_argument("type", choices=["verdict", "log", "assign"])
    e.add_argument("--project", required=True)
    e.add_argument("--session")
    e.add_argument("--task", type=int)
    e.add_argument("--ticket")
    e.add_argument("--round", type=int)
    e.add_argument("--verdict", choices=["closed", "closed-notes", "returned"])
    e.add_argument("--cause", choices=["evidence", "code", "scope", "other"])
    e.add_argument("--agent")
    e.add_argument("--kind", choices=["work", "return", "other"])
    e.add_argument("--note")
    e.add_argument("--text")
    e.add_argument("--file")

    b = sub.add_parser("build")
    b.add_argument("--db")

    r = sub.add_parser("report")
    r.add_argument("--db")
    r.add_argument("--view", default="v_agent",
                   choices=["v_agent", "v_ticket", "v_day", "v_one_shot", "v_return_cause",
                            "v_outliers", "task", "session", "verdict", "log"])
    r.add_argument("--sql")

    a = ap.parse_args(argv)
    return {"ingest": cmd_ingest, "event": cmd_event, "build": cmd_build,
            "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
