#!/usr/bin/env python3
"""Ratchet for a red trunk: tolerate only the known failures, and let that list only shrink.

    trinquete.py list  <list> <parent-list>   exit 1 if <list> names a test <parent-list> did not
    trinquete.py suite <log> <list>           exit 1 if a test fails that is not in <list>, if a
                                              test in <list> no longer fails (remove it, so it
                                              cannot come back), or if the suite never reached
                                              its summary

<list> is versioned with the code (e.g. scripts/fallos-conocidos.txt): one test name per line,
blank lines and `#` comments ignored. The gate reads it from the commit under test, and checks it
against the parent's list before any cache answers, because the parent is not part of the code.

The defaults parse `node --test` output (`✖ name (3.4ms)` and `ℹ tests N`). For another runner,
pass --fail-re (one group: the test name) and --summary-re; for pytest -rf:
    --fail-re '^FAILED (\\S+)' --summary-re '^=+ .* in [0-9.]+s'

Why: in the project this skill comes from, the trunk carried 9 to 17 failing tests for days, and
every PM closed with «the failures are someone else's». With nine red, a tenth went unnoticed.
"""
import argparse
import re
import sys

NODE_FAIL = r"^\s*✖ (.*?)(?: \([0-9.]+m?s\))?$"
NODE_SUMMARY = r"^ℹ tests [0-9]+"


def known(path):
    try:
        with open(path, encoding="utf-8") as f:
            lines = [l.strip() for l in f]
    except FileNotFoundError:
        return set()
    return {l for l in lines if l and not l.startswith("#")}


def failing(log, fail_re):
    pat = re.compile(fail_re)
    names = set()
    for line in log.splitlines():
        m = pat.match(line)
        if m and m.group(1) != "failing tests:":
            names.add(m.group(1))
    return names


def reject(msg, names):
    print(f"trinquete: {msg}", file=sys.stderr)
    for n in sorted(names) if isinstance(names, set) else names:
        print(f"  {n}", file=sys.stderr)
    return 1


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("list")
    pl.add_argument("list")
    pl.add_argument("parent")
    ps = sub.add_parser("suite")
    ps.add_argument("log")
    ps.add_argument("list")
    ps.add_argument("--fail-re", default=NODE_FAIL)
    ps.add_argument("--summary-re", default=NODE_SUMMARY)
    a = p.parse_args(argv)

    if a.cmd == "list":
        added = known(a.list) - known(a.parent)
        return reject("the known-failures list can only shrink; this commit adds:", added) if added else 0

    with open(a.log, encoding="utf-8", errors="replace") as f:
        log = f.read()
    if not re.search(a.summary_re, log, re.M):
        return reject("the suite never reached its summary; rejected. Last lines:", log.splitlines()[-5:])
    fails, ok = failing(log, a.fail_re), known(a.list)
    if fails - ok:
        return reject("new failures, not in the known-failures list:", fails - ok)
    if ok - fails:
        return reject("no longer failing; remove them from the list in this same commit:", ok - fails)
    return 0


if __name__ == "__main__":
    sys.exit(main())
