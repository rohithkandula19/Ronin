# ronin "runs free" showcase — charmbracelet/vhs
Output "assets/ronin-free.gif"

Set Shell bash
Set FontSize 16
Set Width 1200
Set Height 700
Set Padding 24
Set TypingSpeed 55ms

Hide
Type "cd ~/ronin && shopt -s expand_aliases && alias ronin='.venv/bin/ronin' && clear"
Enter
Sleep 600ms
Show

# beat 1: configured on a FREE provider (cerebras), no key leaked
Type "ronin doctor"
Enter
Sleep 3.6s

Hide
Type "clear"
Enter
Sleep 200ms
Show

# beat 2: a real coding answer, generated for $0
Type "ronin ask 'in one sentence, what is a closure in programming?'"
Enter
Sleep 11s
