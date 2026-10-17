# hetz launcher (/hetz:setup); remove with /hetz:setup undo
function cc-do --description 'hetz: /hetz-crew:delegate on Sonnet'
    claude --model sonnet "/hetz-crew:delegate $argv"
end
