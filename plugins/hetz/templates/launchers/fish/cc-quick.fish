# hetz launcher (/hetz:setup); remove with /hetz:setup undo
function cc-quick --wraps claude --description 'hetz: Claude Code on Haiku, low effort'
    claude --model haiku --effort low $argv
end
