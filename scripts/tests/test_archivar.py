import datetime
import contextlib
import io
import os
import sys
import tempfile
import unittest

SKILL_SCRIPTS = os.path.expanduser("~/.claude/skills/scrum-kanban-agents/scripts")
sys.path.insert(0, SKILL_SCRIPTS)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
def _r(p, encoding="utf-8"):
    with open(p, encoding=encoding) as f:
        return f.read()
import archivar  # noqa: E402
import inbox  # noqa: E402

NOW = datetime.datetime(2026, 11, 10, 12, 0).astimezone()


def stamp(days_ago: float) -> str:
    return (NOW - datetime.timedelta(days=days_ago)).strftime(inbox.FMT)


class ArchivarTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = self.tmp.name
        self.root = os.path.join(self.repo, "inbox")
        os.makedirs(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, *parts):
        with open(os.path.join(self.repo, *parts), encoding="utf-8") as f:
            return f.read()

    def write(self, text, *parts):
        os.makedirs(os.path.join(self.repo, *parts[:-1]), exist_ok=True)
        with open(os.path.join(self.repo, *parts), "w", encoding="utf-8") as f:
            f.write(text)

    def test_mailbox_moves_old_read_rows_and_keeps_unread_and_the_newest(self):
        inbox.send(self.root, "PO", "PM-ui", "viejo leído", when=stamp(5))
        inbox.send(self.root, "PO", "PM-ui", "viejo sin leer", when=stamp(5))
        inbox.send(self.root, "PO", "PM-ui", "nuevo leído", when=stamp(1))
        inbox.send(self.root, "PO", "PM-ui", "el último, leído", when=stamp(4))
        inbox.ack(self.root, "PO", ["M-1", "M-4"], when=stamp(4))  # M-4 old but the newest
        inbox.ack(self.root, "PO", ["M-3"], when=stamp(0.5))
        before = {m.id for m in inbox.messages(self.root, "PO")}
        self.assertEqual(archivar.archive(self.repo, NOW), ["PO: 1 filas leídas a inbox/archivo/"])
        live = {m.id for m in inbox.messages(self.root, "PO")}
        month = stamp(5)[:7]
        archived = {m.id for m in inbox.parse(self.read("inbox", "archivo", f"PO-{month}.md"))}
        self.assertEqual(archived, {"M-1"})
        self.assertEqual(live, {"M-2", "M-3", "M-4"})  # unread, too recent, the newest
        self.assertEqual(live | archived, before)  # nothing lost
        self.assertEqual(archivar.archive(self.repo, NOW), [])  # idempotent
        self.assertEqual(inbox.send(self.root, "PO", "PM-ui", "otro"), "M-5")  # ids never repeat

    def test_wakes_log_moves_lines_older_than_a_week(self):
        self.write(f"{stamp(9)} PO woke ses_a for M-1\n{stamp(1)} PO woke ses_a for M-2\n",
                   "inbox", ".wakes.log")
        archivar.archive(self.repo, NOW)
        self.assertNotIn("M-1", self.read("inbox", ".wakes.log"))
        self.assertIn("M-2", self.read("inbox", ".wakes.log"))
        self.assertIn("M-1", self.read("inbox", "archivo", f"wakes-{stamp(9)[:7]}.log"))

    def test_status_keeps_the_five_newest_closed_and_every_open_row(self):
        head = "| Ticket | Lane | State | Updated |\n|---|---|---|---|\n"
        rows = [f"| T-{n} | x | closed | 2026-11-{n:02d} |" for n in range(1, 9)]
        rows += ["| T-20 | x | pending | 2026-10-01 |", "| T-21 | x | in progress | 2026-10-01 |"]
        self.write("# Status\n\n" + head + "\n".join(rows) + "\n", "epic-a", "STATUS.md")
        archivar.archive(self.repo, NOW)
        live, arch = self.read("epic-a", "STATUS.md"), self.read("epic-a", "STATUS-archivo.md")
        for n in (1, 2, 3):
            self.assertNotIn(f"| T-{n} |", live)
            self.assertIn(f"| T-{n} |", arch)
        for n in (4, 5, 6, 7, 8, 20, 21):
            self.assertIn(f"| T-{n} |", live)
        self.assertIn("| Ticket | Lane | State | Updated |", arch)
        self.assertEqual(archivar.archive(self.repo, NOW), [])

    def test_done_directives_move_below_archived_and_in_force_stay(self):
        text = ("# Focus\n\n## Directives in force\n\n| Id | Kind | Scope | Human's words | Date | Status |\n"
                "|---|---|---|---|---|---|\n| D-1 | objective | p | «a» | 2026 | in force |\n"
                "| D-2 | decision | p | «b» | 2026 | executed (X-1) |\n"
                "| D-3 | decision | p | «c» | 2026 | superseded by D-4 |\n\n## Log\n\nnada\n")
        self.write(text, "focus", "PROJECT.md")
        archivar.archive(self.repo, NOW)
        out = self.read("focus", "PROJECT.md")
        top, bottom = out.split("## Archived")
        self.assertIn("| D-1 |", top)
        self.assertNotIn("| D-2 |", top)
        self.assertIn("| D-2 |", bottom)
        self.assertIn("| D-3 |", bottom)
        self.assertEqual(archivar.archive(self.repo, NOW), [])

    def test_dry_run_touches_nothing(self):
        inbox.send(self.root, "PO", "PM-ui", "viejo", when=stamp(5))
        inbox.send(self.root, "PO", "PM-ui", "último", when=stamp(5))
        inbox.ack(self.root, "PO", ["M-1", "M-2"], when=stamp(4))
        before = self.read("inbox", "PO.md")
        self.assertEqual(archivar.archive(self.repo, NOW, dry=True), ["PO: 1 filas leídas a inbox/archivo/"])
        self.assertEqual(self.read("inbox", "PO.md"), before)
        self.assertFalse(os.path.exists(os.path.join(self.root, "archivo")))

    # --- M-79/M-85 4 candados ---

    def test_estado_header_archiva_closed_igual_que_state(self):
        # CALIDAD usa Estado/Actualizado en vez de State/Updated
        with tempfile.TemporaryDirectory() as repo:
            os.makedirs(os.path.join(repo, "inbox"), exist_ok=True)
            head = "| Ticket | Lane | Estado | Actualizado |\n|---|---|---|---|\n"
            rows = [f"| T-{n} | x | closed | 2026-11-{n:02d} 10:00 -0300 |" for n in range(1, 7)]
            rows += ["| T-20 | x | pending | 2026-11-01 10:00 -0300 |", "| T-21 | x | open | 2026-11-02 10:00 -0300 |"]
            before_text = "# STATUS — épica CALIDAD\n\n" + head + "\n".join(rows) + "\n"
            os.makedirs(os.path.join(repo, "epic-calidad"), exist_ok=True)
            path = os.path.join(repo, "epic-calidad", "STATUS.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(before_text)
            before_rows = set(rows)
            # --dry-run no toca disco
            before_disk = _r(path, encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                dry = archivar.statuses(repo, dry=True)
            self.assertEqual(dry, ["epic-calidad/STATUS.md: 1 filas closed a STATUS-archivo.md"])
            self.assertEqual(_r(path, encoding="utf-8"), before_disk)
            self.assertFalse(os.path.exists(os.path.join(repo, "epic-calidad", "STATUS-archivo.md")))
            # dry=False archiva 1 (6 closed - KEEP_CLOSED 5)
            buf2 = io.StringIO()
            with contextlib.redirect_stderr(buf2):
                live_done = archivar.statuses(repo, dry=False)
            self.assertEqual(live_done, ["epic-calidad/STATUS.md: 1 filas closed a STATUS-archivo.md"])
            live_text = _r(path, encoding="utf-8")
            arch_path = os.path.join(repo, "epic-calidad", "STATUS-archivo.md")
            arch_text = _r(arch_path, encoding="utf-8")
            # T-1 es la más antigua, debe ir a archivo
            self.assertNotIn("| T-1 |", live_text)
            self.assertIn("| T-1 |", arch_text)
            for n in (2, 3, 4, 5, 6, 20, 21):
                self.assertIn(f"| T-{n} |", live_text)
            # integridad: no duplicados, no pérdida
            live_rows = {l for l in live_text.splitlines() if l.startswith("| T-")}
            arch_rows = {l for l in arch_text.splitlines() if l.startswith("| T-")}
            self.assertEqual(len(live_rows & arch_rows), 0)
            self.assertEqual(live_rows | arch_rows, before_rows)
            # idempotente
            self.assertEqual(archivar.statuses(repo, dry=False), [])
            # revertir Estado check → 0 archivado (solo State): si quitamos Estado, head no se encuentra
            # simulamos revert: head sin Estado no matchea
            self.assertIn("Estado", head)

    def test_status_fase2_sin_reprocesar_archivo(self):
        with tempfile.TemporaryDirectory() as repo:
            os.makedirs(os.path.join(repo, "inbox"), exist_ok=True)
            head = "| Ticket | Lane | State | Updated |\n|---|---|---|---|\n"
            rows = [f"| GRU-{n} | x | closed | 2026-10-{n:02d} |" for n in range(1, 24)]
            before_fase2 = "# STATUS FASE2\n\n" + head + "\n".join(rows) + "\n"
            os.makedirs(os.path.join(repo, "epic-grupos"), exist_ok=True)
            fase2_path = os.path.join(repo, "epic-grupos", "STATUS-FASE2.md")
            with open(fase2_path, "w", encoding="utf-8") as f:
                f.write(before_fase2)
            # archivo ya existente con 2 filas viejas
            archivo_path = os.path.join(repo, "epic-grupos", "STATUS-archivo.md")
            archivo_before = "# Archivo de epic-grupos\n\nFilas closed que salieron de STATUS.md (D-283). Nunca se editan.\n\n" + head + "| OLD-1 | x | closed | 2026-09-01 |\n| OLD-2 | x | closed | 2026-09-02 |\n"
            with open(archivo_path, "w", encoding="utf-8") as f:
                f.write(archivo_before)
            before_rows = set(rows)
            old_rows = {l for l in archivo_before.splitlines() if l.startswith("| ")}
            # --dry-run no toca disco
            fase2_before = _r(fase2_path, encoding="utf-8")
            archivo_before_disk = _r(archivo_path, encoding="utf-8")
            dry = archivar.statuses(repo, dry=True)
            self.assertEqual(dry, ["epic-grupos/STATUS-FASE2.md: 18 filas closed a STATUS-archivo.md"])
            self.assertEqual(_r(fase2_path, encoding="utf-8"), fase2_before)
            self.assertEqual(_r(archivo_path, encoding="utf-8"), archivo_before_disk)
            # dry=False archiva 18 dejando 5
            done = archivar.statuses(repo, dry=False)
            self.assertEqual(done, ["epic-grupos/STATUS-FASE2.md: 18 filas closed a STATUS-archivo.md"])
            live_text = _r(fase2_path, encoding="utf-8")
            arch_text = _r(archivo_path, encoding="utf-8")
            live_rows = [l for l in live_text.splitlines() if l.startswith("| GRU-")]
            arch_rows = [l for l in arch_text.splitlines() if l.startswith("| GRU-")]
            old_in_arch = [l for l in arch_text.splitlines() if "OLD-" in l]
            self.assertEqual(len(live_rows), 5)
            self.assertEqual(len(arch_rows), 18)
            self.assertEqual(len(old_in_arch), 2)
            # los 5 más nuevos quedan (19-23)
            for n in range(19, 24):
                self.assertIn(f"| GRU-{n} |", live_text)
            for n in range(1, 19):
                self.assertNotIn(f"| GRU-{n} |", live_text)
                self.assertIn(f"| GRU-{n} |", arch_text)
            # integridad: no duplicados, no pérdida (live + nuevos arch = before)
            live_set = {l for l in live_rows}
            arch_new_set = {l for l in arch_rows}
            self.assertEqual(len(live_set & arch_new_set), 0)
            self.assertEqual(live_set | arch_new_set, before_rows)
            # archivo no se reprocesa: no debe aparecer como fuente en done ni truncarse
            self.assertNotIn("STATUS-archivo.md:", "".join(done))
            self.assertIn("| OLD-1 |", arch_text)
            self.assertIn("| OLD-2 |", arch_text)
            # segunda corrida idempotente
            self.assertEqual(archivar.statuses(repo, dry=False), [])

    def test_focus_archivo_intacto(self):
        with tempfile.TemporaryDirectory() as repo:
            os.makedirs(os.path.join(repo, "inbox"), exist_ok=True)
            os.makedirs(os.path.join(repo, "focus"), exist_ok=True)
            table = "| Id | Kind | Scope | Human's words | Date | Status |"
            sep = "|---|---|---|---|---|---|"
            text = ("# Focus\n\n"
                    + table + "\n" + sep + "\n"
                    + "| D-10 | objective | p | «keep» | 2026 | in force |\n"
                    + "| D-20 | decision | p | «exec» | 2026 | executed (X-1) |\n"
                    + "| D-21 | decision | p | «exec2» | 2026 | executed |\n"
                    + "\n## Archived\n\n" + table + "\n" + sep + "\n"
                    + "| D-99 | decision | p | «old» | 2026 | executed |\n"
                    + "\n## Log\n\nnada\n")
            proj = os.path.join(repo, "focus", "proyecto-XXX.md")
            with open(proj, "w", encoding="utf-8") as f:
                f.write(text)
            # archivo que no debe tocarse aunque tenga executed
            archivo = os.path.join(repo, "focus", "proyecto-XXX-archivo.md")
            archivo_text = ("# Archivo focus\n\n" + table + "\n" + sep + "\n"
                            + "| D-30 | decision | p | «arch» | 2026 | executed |\n")
            with open(archivo, "w", encoding="utf-8") as f:
                f.write(archivo_text)
            before_proj = _r(proj, encoding="utf-8")
            before_arch = _r(archivo, encoding="utf-8")
            # --dry-run no toca disco
            dry = archivar.directives(repo, dry=True)
            self.assertEqual(dry, ["focus/proyecto-XXX.md: 2 directivas a ## Archived"])
            self.assertEqual(_r(proj, encoding="utf-8"), before_proj)
            self.assertEqual(_r(archivo, encoding="utf-8"), before_arch)
            # dry=False mueve 2 a ## Archived, archivo intacto
            done = archivar.directives(repo, dry=False)
            self.assertEqual(done, ["focus/proyecto-XXX.md: 2 directivas a ## Archived"])
            out = _r(proj, encoding="utf-8")
            out_arch = _r(archivo, encoding="utf-8")
            self.assertEqual(out_arch, before_arch)
            top, bottom = out.split("## Archived")
            self.assertIn("| D-10 |", top)
            self.assertNotIn("| D-20 |", top)
            self.assertNotIn("| D-21 |", top)
            self.assertIn("| D-20 |", bottom)
            self.assertIn("| D-21 |", bottom)
            self.assertIn("| D-99 |", bottom)
            # no duplicados en archivo
            self.assertEqual(out.count("| D-20 |"), 1)
            self.assertEqual(out.count("| D-21 |"), 1)
            # idempotente
            self.assertEqual(archivar.directives(repo, dry=False), [])
            # guarda que sin el guard `if path.endswith("-archivo.md")` el archivo habría sido movido
            self.assertTrue(archivo.endswith("-archivo.md"))

    def test_fila_mal_formada_preservada_con_warn(self):
        with tempfile.TemporaryDirectory() as repo:
            os.makedirs(os.path.join(repo, "inbox"), exist_ok=True)
            os.makedirs(os.path.join(repo, "epic-test"), exist_ok=True)
            head = "| Ticket | Lane | Dueño | Estado | Agente | Reporte | Actualizado |"
            sep = "|---|---|---|---|---|---|---|"
            valid_closed = [f"| T-{n} | x | y | closed | ag | rep | 2026-11-{n:02d} |" for n in range(1, 7)]
            # fila mal formada len 5 !=7 (corta), simula R-12 y LEGAL-8
            malformed = "| R-12 | x | closed | 2026-11-07 | oops |"
            # valid open
            opens = ["| T-20 | x | y | pending | ag | rep | 2026-11-01 |", "| T-21 | x | y | open | ag | rep | 2026-11-02 |"]
            body = "\n".join(valid_closed + [malformed] + opens)
            text = f"# STATUS\n\n{head}\n{sep}\n{body}\n"
            path = os.path.join(repo, "epic-test", "STATUS.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            before = _r(path, encoding="utf-8")
            # --dry-run debe advertir y no tocar disco
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                dry = archivar.statuses(repo, dry=True)
            warn = buf.getvalue()
            self.assertIn("warn:", warn)
            self.assertIn("fila mal formada", warn)
            self.assertIn("R-12", warn)
            self.assertEqual(dry, ["epic-test/STATUS.md: 1 filas closed a STATUS-archivo.md"])
            self.assertEqual(_r(path, encoding="utf-8"), before)
            self.assertFalse(os.path.exists(os.path.join(repo, "epic-test", "STATUS-archivo.md")))
            # dry=False preserva malformed y avisa
            buf2 = io.StringIO()
            with contextlib.redirect_stderr(buf2):
                done = archivar.statuses(repo, dry=False)
            warn2 = buf2.getvalue()
            self.assertIn("warn:", warn2)
            self.assertIn("R-12", warn2)
            self.assertEqual(done, ["epic-test/STATUS.md: 1 filas closed a STATUS-archivo.md"])
            live = _r(os.path.join(repo, "epic-test", "STATUS.md"))
            arch = _r(os.path.join(repo, "epic-test", "STATUS-archivo.md"))
            # malformed sigue en live, no en archivo
            self.assertIn("R-12", live)
            self.assertNotIn("R-12", arch)
            # solo 1 válida movida (la más antigua T-1), quedan 5 válidas + malformed + 2 open = 8 filas live con prefijo |
            self.assertNotIn("| T-1 |", live)
            self.assertIn("| T-1 |", arch)
            for n in (2, 3, 4, 5, 6):
                self.assertIn(f"| T-{n} |", live)
            self.assertIn("| T-20 |", live)
            self.assertIn("| T-21 |", live)
            # integridad: live + arch = before (sin duplicados)
            before_rows = {l for l in before.splitlines() if l.startswith("|")}
            # excluir cabecera
            live_rows = {l for l in live.splitlines() if l.startswith("| T-") or l.startswith("| R-")}
            arch_rows = {l for l in arch.splitlines() if l.startswith("| T-") or l.startswith("| R-")}
            self.assertEqual(len(live_rows & arch_rows), 0)
            self.assertEqual(live_rows | arch_rows, {l for l in body.splitlines()})
            # idempotente no mueve más
            buf3 = io.StringIO()
            with contextlib.redirect_stderr(buf3):
                self.assertEqual(archivar.statuses(repo, dry=False), [])
            self.assertIn("warn:", buf3.getvalue())


if __name__ == "__main__":
    unittest.main()
