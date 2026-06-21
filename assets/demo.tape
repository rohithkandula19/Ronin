# ronin showcase — generated with charmbracelet/vhs
Output "assets/ronin-demo.gif"

Set Shell bash
Set FontSize 16
Set Width 1200
Set Height 720
Set Padding 24
Set TypingSpeed 50ms

# --- silent setup (hidden) ---
Hide
Type "cd ~/ronin && shopt -s expand_aliases && alias ronin='.venv/bin/ronin' && clear"
Enter
Sleep 600ms
Show

# --- beat 1: usage dashboard ---
Type "ronin stats"
Enter
Sleep 3.8s

Hide
Type "clear"
Enter
Sleep 200ms
Show

# --- beat 2: gamified profile ---
Type "ronin profile"
Enter
Sleep 3.4s

Hide
Type "clear"
Enter
Sleep 200ms
Show

# --- beat 3: the arcade (finale — GIF ends here, no exit needed) ---
Type "ronin play"
Enter
Sleep 1.8s
Down@520ms 9
Sleep 800ms
Up@460ms 4
Sleep 1.8s
