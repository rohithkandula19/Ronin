# vhs tape — generates docs/demo/ronin.gif
# Install vhs (https://github.com/charmbracelet/vhs): `brew install vhs`
# Then from the repo root:  vhs docs/demo/demo.tape
#
# Uses only keyless commands (panda animation, image generation via the free
# Pollinations backend, the command tour) so it records with no API key.

Output docs/demo/ronin.gif

Set Shell zsh
Set FontSize 18
Set Width 1100
Set Height 680
Set Padding 18
Set Theme "Catppuccin Mocha"

# the animated panda mascot on launch
Type "ronin panda dancing" Sleep 500ms Enter
Sleep 4s

# free, terminal-native image generation (no API key)
Type "ronin image 'a red panda samurai, neon, flat vector' --out panda.png" Sleep 500ms Enter
Sleep 7s

# the full command surface
Type "ronin --help" Sleep 500ms Enter
Sleep 4s

Sleep 1s
