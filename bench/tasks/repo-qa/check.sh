#!/bin/sh
# Pass = repo untouched and the final answer states every key fact.
# Usage: check.sh FIXTURE_DIR ANSWER_FILE
set -u
. "$(dirname "$0")/../_lib.sh"
ANS=$(abspath "${2:-}")
cd "$1" || fail "no fixture dir"
unchanged . || fail "repository was modified"
[ -s "$ANS" ] || fail "empty answer"
for fact in '\b7\b' 'webhook_retry\.py' 'RELAY_WEBHOOK_MAX_ATTEMPTS' 'compute_backoff' '\b900\b' '\bMemoryBlobStore\b'; do
  python3 -c 'import re, sys; sys.exit(0 if re.search(sys.argv[1], open(sys.argv[2], errors="replace").read(), re.I) else 1)' \
    "$fact" "$ANS" || fail "missing fact: $fact"
done
echo PASS
