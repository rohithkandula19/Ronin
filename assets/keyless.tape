# ronin keyless — runs a model IN-PROCESS, no API key. charmbracelet/vhs
Output "assets/ronin-keyless.gif"

Set Shell bash
Set FontSize 16
Set Width 1200
Set Height 700
Set Padding 24
Set TypingSpeed 55ms

Hide
Type "cd /tmp/ronin-keyless-demo && export HF_HUB_DISABLE_PROGRESS_BARS=1 && shopt -s expand_aliases && alias ronin='/Users/rohithkandula/ronin/.venv/bin/ronin' && clear"
Enter
Sleep 700ms
Show

# beat 1: configured on a LOCAL in-process model — NO KEY
Type "ronin doctor"
Enter
Sleep 4s

Hide
Type "clear"
Enter
Sleep 200ms
Show

# beat 2: a real coding answer, generated in-process, ZERO api key
Type "ronin ask 'write a one-line python function to reverse a string'"
Enter
Sleep 18s
