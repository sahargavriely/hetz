# hetz launcher (/hetz:setup); remove with /hetz:setup undo
function cc-spec --description 'hetz: /hetz:interview on Opus'
    claude --model opus "/hetz:interview $argv"
end
