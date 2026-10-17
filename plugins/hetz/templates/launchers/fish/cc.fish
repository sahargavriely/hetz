# hetz launcher (/hetz:setup); remove with /hetz:setup undo
function cc --wraps claude --description 'hetz: Claude Code on Sonnet'
    claude --model sonnet $argv
end
