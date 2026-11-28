#!/usr/bin/env bash
# End-to-end checks of the reader in headless Chrome (a touch phone by default).
#   tests/reader/run.sh "Author/Author - Title.epub" ["Other/Other - Book.epub" ...]
# Books are paths under Out/. Several books run side by side. FULL=1: all fonts at 80/100/150/200%.
# Needs google-chrome and Node 22+. Each check prints "ok"/"FAIL" lines and ends "ALL OK" or "N FAILED".
set -u
cd "$(dirname "$0")/../.."
[ $# -gt 0 ] || { sed -n 2,5p "$0"; exit 2; }
LOGS=$(mktemp -d)
# The library's files plus the sync routes (accounts in ./.sync/, git-ignored). PORT=0 (default): a
# free port, which the server prints once it listens.
python3 launchers/library_sync.py --serve . --port "${PORT:-0}" > "$LOGS/server.out" 2>&1 & SERVER=$!
trap 'kill $SERVER 2>/dev/null; rm -rf "$LOGS"' EXIT
for _ in $(seq 100); do grep -q "^Library:" "$LOGS/server.out" && break; sleep 0.1; done
PORT=$(sed -n 's|^Library: http://127.0.0.1:\([0-9]*\)/.*|\1|p' "$LOGS/server.out")
[ -n "$PORT" ] || { echo "Error: the server did not start"; cat "$LOGS/server.out"; exit 1; }
B=http://127.0.0.1:$PORT T=tests/reader
# the network library's sign-in gate (--gate): its own server on the same library
python3 launchers/library_sync.py --serve . --port 0 --gate > "$LOGS/gate.out" 2>&1 & GATE=$!
trap 'kill $SERVER $GATE 2>/dev/null; rm -rf "$LOGS"' EXIT
for _ in $(seq 100); do grep -q "^Code:" "$LOGS/gate.out" && break; sleep 0.1; done
G=$(sed -n 's|^Library: \(http://127.0.0.1:[0-9]*\)/.*|\1|p' "$LOGS/gate.out")
CODE=$(sed -n 's/^Code: *//p' "$LOGS/gate.out")
# the catalog's place: once, not per book
node $T/catalog-place.mjs $B > "$LOGS/0-catalog.txt" 2>&1 &
i=0 PIDS=($!)
node $T/signin.mjs "$G" "$CODE" > "$LOGS/0-signin.txt" 2>&1 &
PIDS+=($!)
for book in "$@"; do
  i=$((i + 1))
  (
    echo "### $book"
    if [ "${FULL:-0}" = 1 ]; then SIZES=80,100,150,200 node $T/nav.mjs $B "$book" 400
    else FACES=book,literata,opendyslexic SIZES=100 node $T/nav.mjs $B "$book" 150; fi
    node $T/place.mjs $B "$book" 15 "${WORD:-что}"
    node $T/hits.mjs $B "$book" "${WORD:-что}" 60 opendyslexic 120
    node $T/touch.mjs $B "$book"; node $T/status.mjs $B "$book"; node $T/typo.mjs $B "$book"
    node $T/subpixel.mjs $B "$book"
    node $T/openfile.mjs $B "$book"
    node $T/scroll.mjs $B "$book" "${WORD:-что}"
    # bold word starts: addresses, the switch, and the page turns and the ribbon with it on
    node $T/bold-unit.mjs $B "$book"; node $T/bold.mjs $B "$book"
    BOLD=1 FACES=literata SIZES=100 node $T/nav.mjs $B "$book" 150; BOLD=1 node $T/scroll.mjs $B "$book" "${WORD:-что}"
    # one sync check at a time: each tries one wrong library code, and wrong codes from checks side
    # by side would add up to the library-wide pause (after 5 in a row)
    flock "$LOGS/sync.lock" node $T/sync.mjs $B "$book"
  ) > "$LOGS/$i.txt" 2>&1 &
  PIDS+=($!)
done
wait "${PIDS[@]}"   # the checks, not the server
# the folders check after the others, not beside them: its own library build and three more Chromes
# slow the per-book checks enough to break the timing of scroll.mjs
node $T/folders.mjs $B > "$LOGS/0-folders.txt" 2>&1
# the app's theme (catalog and reader), also with a library of its own, after the others likewise
node $T/theme.mjs $B > "$LOGS/0-theme.txt" 2>&1
node $T/pictures.mjs $B > "$LOGS/0-pictures.txt" 2>&1
cat "$LOGS"/*.txt | grep -v "^  ok"
! grep -q "FAIL\|Error" "$LOGS"/*.txt   # exit status: 0 when every check passed
