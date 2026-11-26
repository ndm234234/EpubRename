#!/usr/bin/env bash
# End-to-end checks of the reader in headless Chrome (a touch phone by default).
#   tests/reader/run.sh "Author/Author - Title.epub" ["Other/Other - Book.epub" ...]
# Books are paths under Out/. Several books run side by side. FULL=1: all fonts at 80/100/150/200%.
# Needs google-chrome and Node 22+. Each check prints "ok"/"FAIL" lines and ends "ALL OK" or "N FAILED".
set -u
cd "$(dirname "$0")/../.."
[ $# -gt 0 ] || { sed -n 2,5p "$0"; exit 2; }
PORT=${PORT:-$(python3 -c "import socket; s = socket.socket(); s.bind(('127.0.0.1', 0)); print(s.getsockname()[1])")}   # a free one
python3 -m http.server "$PORT" --bind 127.0.0.1 >/dev/null 2>&1 & SERVER=$!
trap 'kill $SERVER 2>/dev/null' EXIT
sleep 1
B=http://127.0.0.1:$PORT T=tests/reader
LOGS=$(mktemp -d)
trap 'kill $SERVER 2>/dev/null; rm -rf "$LOGS"' EXIT
i=0 PIDS=()
for book in "$@"; do
  i=$((i + 1))
  (
    echo "### $book"
    if [ "${FULL:-0}" = 1 ]; then SIZES=80,100,150,200 node $T/nav.mjs $B "$book" 400
    else FACES=book,literata,opendyslexic SIZES=100 node $T/nav.mjs $B "$book" 150; fi
    node $T/place.mjs $B "$book" 15 "${WORD:-что}"
    node $T/hits.mjs $B "$book" "${WORD:-что}" 60 opendyslexic 120
    node $T/touch.mjs $B "$book"; node $T/status.mjs $B "$book"; node $T/typo.mjs $B "$book"
  ) > "$LOGS/$i.txt" 2>&1 &
  PIDS+=($!)
done
wait "${PIDS[@]}"   # the checks, not the server
cat "$LOGS"/*.txt | grep -v "^  ok"
! grep -q "FAIL\|Error" "$LOGS"/*.txt   # exit status: 0 when every check passed
