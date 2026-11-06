#!/usr/bin/env bash
# The delivery director's check of the last N minutes, read-only (references/lessons.md,
# "Supervision"). It reads git and opencode's own database, never the agents' accounts.
#
#     control.sh <repo> [minutes]     e.g. control.sh ~/proj 10
set -u
R=${1:?usage: control.sh <repo> [minutes]}
M=${2:-10}
DB=~/.local/share/opencode/opencode.db
DESDE="strftime('%s','now','-$M minutes')*1000"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$R" || exit 1
# Usa el mismo resolver que el resto de la skill, también para rutas absolutas.
GESTION_ROOT="$(python3 - "$SCRIPT_DIR/../../scripts" "$PWD" <<'PYTHON'
import sys
sys.path.insert(0, sys.argv[1])
from gestion import raiz_gestion
print(raiz_gestion(sys.argv[2]))
PYTHON
)" || exit 1
# inbox/focus resueltos vía gestion
INBOX_DIR="$GESTION_ROOT/inbox"
echo "== $(date '+%H:%M') · últimos $M min =="
echo "-- commits"
git log --since="$M minutes ago" --format='%h %cd %s' --date=format:%H:%M | cut -c1-120
echo "-- sesiones activas (título | mensajes | último)"
sqlite3 "$DB" "select s.title, count(*), datetime(max(m.time_created)/1000,'unixepoch','localtime') from session_message m join session_v2 s on s.id=m.session_id where m.time_created > $DESDE group by s.id order by 3 desc"
echo "-- devs duplicados (mismo ticket, varias sesiones activas)"
sqlite3 "$DB" "select t, n from (select upper(substr(s.title, instr(upper(s.title),'-')-3, 7)) t, count(distinct s.id) n from session_message m join session_v2 s on s.id=m.session_id where m.time_created > $DESDE and lower(s.title) like '%dev%' group by t) where n>1"
echo "-- comandos prohibidos o sospechosos"
sqlite3 "$DB" "select datetime(m.time_created/1000,'unixepoch','localtime'), s.title, substr(replace(c,char(10),' '),1,150) from (select m.*, json_extract(p.value,'$.state.input.command') c from session_message m, json_each(json_extract(m.data,'$.content')) p where m.time_created > $DESDE and json_extract(p.value,'$.type')='tool') m join session_v2 s on s.id=m.session_id where c glob '*git stash*' or c glob '*git reset*' or c glob '*git checkout *--*' or c glob '*git restore*' or c glob '*add -A*' or c glob '*--amend*' or c glob '*git push*' or c glob '*pkill*' or c glob '*killall*' or c glob '*kill [0-9]*' or c glob '*kill -9*' or c glob '*npm run build *' or c glob '*npm install*' or (c glob '*sleep*' and (c glob '*while*' or c glob '*until*'))"
echo "-- pedidos en $INBOX_DIR/.lanzar"
ls "$INBOX_DIR/.lanzar" 2>/dev/null
echo "-- buzón PO: filas y sin leer"
grep -c '^| M-' "$INBOX_DIR/PO.md"
python3 "$SCRIPT_DIR/../../scripts/inbox.py" read "$INBOX_DIR" PO 2>/dev/null | head -5
echo "-- D-282: turnos del PO en la ventana, despertares y retenciones"
sqlite3 "$DB" "select count(*) from session_message m join session_v2 s on s.id=m.session_id where s.title like '%PO%' and s.parent_id is null and m.type='user' and m.time_created > $DESDE"
grep -E " PO (woke|held)" "$INBOX_DIR/.wakes.log" 2>/dev/null | tail -5
echo "-- mensajes al PO sin tipo (deberían bajar)"
sqlite3 "$DB" "select count(*), sum(c like '%[bloqueo]%' or c like '%[decision]%' or c like '%[aviso]%') from (select json_extract(p.value,'$.state.input.command') c from session_message m, json_each(json_extract(m.data,'$.content')) p where m.time_created > $DESDE and json_extract(p.value,'$.type')='tool') where c like '%inbox.py send%--to PO %'"
