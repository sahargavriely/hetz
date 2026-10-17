# hetz launcher (/hetz:setup); remove with /hetz:setup undo
function cc-deep --wraps claude --description 'hetz: Claude Code on Opus, high effort'
    claude --model opus --effort high $argv
end
