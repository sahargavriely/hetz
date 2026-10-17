# hetz launchers (/hetz:setup): source this file from your shell rc; remove with /hetz:setup undo
cc() { claude --model sonnet "$@"; }
cc-deep() { claude --model opus --effort high "$@"; }
cc-quick() { claude --model haiku --effort low "$@"; }
cc-spec() { claude --model opus "/hetz:interview $*"; }
cc-do() { claude --model sonnet "/hetz-crew:delegate $*"; }
