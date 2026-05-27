# vhs tape — generates docs/demo/ronin.gif
# Install vhs (https://github.com/charmbracelet/vhs): `brew install vhs`
# Then from the repo root:  vhs docs/demo/demo.tape
#
# Keyless by design: the animated panda, the command surface, MCP wiring, and
# free image generation (Pollinations) all record with NO API key. To also show
# a live agent turn, configure a free provider first (`ronin login` → gemini /
# cerebras) and add a session snippet at the end.

Output docs/demo/ronin.gif

Set Shell zsh
Set FontSize 18
Set Width 1200
Set Height 760
Set Padding 18
Set Theme "Catppuccin Mocha"

# 1) the animated activity panda on launch (dancing / running / football / …)
Type "ronin panda" Sleep 500ms Enter
Sleep 4s

# 2) the full command surface — eval, mcp, investigate, explain, media, …
Type "ronin --help" Sleep 500ms Enter
Sleep 4s

# 3) MCP: connect Anthropic's filesystem server, its tools join the agent
Type "ronin mcp add fs npx -y @modelcontextprotocol/server-filesystem ." Sleep 400ms Enter
Sleep 2s
Type "ronin mcp list" Sleep 400ms Enter
Sleep 5s

# 4) free, terminal-native image generation (no API key)
Type "ronin image 'a red panda samurai, neon, flat vector' --out panda.png" Sleep 400ms Enter
Sleep 8s

Sleep 1s
