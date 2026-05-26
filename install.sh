#!/usr/bin/env bash
#
# Install csk from source.
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/rohithkandula19/Ronin/main/install.sh | bash
#
# Or with a pinned version:
#   curl -sSL https://raw.githubusercontent.com/rohithkandula19/Ronin/main/install.sh | bash -s -- --ref v0.2.0
#
# What it does:
#   1. Installs `uv` (https://github.com/astral-sh/uv) if it's not on PATH.
#   2. Clones the repo to ~/.local/share/ro-claude-kit (or updates if it's already there).
#   3. Runs `uv sync --all-packages` to resolve the workspace.
#   4. Creates a `csk` shim at ~/.local/bin/csk that invokes the workspace-managed venv.
#   5. Tells you to add ~/.local/bin to PATH if it's not there.
#
# This is the bootstrap install. Once `ro-claude-kit-cli` lands on PyPI, you can
# switch to `pipx install ro-claude-kit-cli`.

set -euo pipefail

REPO_URL="https://github.com/rohithkandula19/Ronin"
INSTALL_DIR="${CSK_INSTALL_DIR:-$HOME/.local/share/ro-claude-kit}"
BIN_DIR="${CSK_BIN_DIR:-$HOME/.local/bin}"
REF="main"

# --- args ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ref) REF="$2"; shift 2 ;;
    --dir) INSTALL_DIR="$2"; shift 2 ;;
    -h|--help)
      sed -n '3,18p' "$0"
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# --- colors ---
if [[ -t 1 ]]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'
  C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_CYAN=$'\033[36m'; C_RED=$'\033[31m'
else
  C_RESET=''; C_BOLD=''; C_DIM=''; C_GREEN=''; C_YELLOW=''; C_CYAN=''; C_RED=''
fi
say()  { echo "${C_BOLD}${C_CYAN}▸ $*${C_RESET}"; }
ok()   { echo "  ${C_GREEN}✓${C_RESET} $*"; }
warn() { echo "  ${C_YELLOW}!${C_RESET} $*"; }
die()  { echo "  ${C_RED}✗${C_RESET} $*" >&2; exit 1; }

# --- platform ---
OS="$(uname -s)"
case "$OS" in
  Darwin|Linux) ;;
  *) die "unsupported OS: $OS (this installer supports macOS and Linux only)" ;;
esac
ok "platform: $OS"

# --- uv ---
say "Checking uv"
if ! command -v uv >/dev/null 2>&1; then
  warn "uv not found — installing via the official installer"
  curl -sSLf https://astral.sh/uv/install.sh | sh
  # Source the shell config the installer dropped to pick up uv on PATH
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  if ! command -v uv >/dev/null 2>&1; then
    die "uv install ran but uv is not on PATH — restart your shell and re-run this installer"
  fi
fi
ok "uv: $(uv --version 2>&1)"

# --- git ---
if ! command -v git >/dev/null 2>&1; then
  die "git is required but not installed. Install git and re-run."
fi

# --- clone / update ---
say "Fetching ro-claude-kit ($REF)"
mkdir -p "$(dirname "$INSTALL_DIR")"
if [[ -d "$INSTALL_DIR/.git" ]]; then
  ok "existing checkout at $INSTALL_DIR — fetching latest"
  git -C "$INSTALL_DIR" fetch --tags --quiet origin
  git -C "$INSTALL_DIR" checkout --quiet "$REF"
  git -C "$INSTALL_DIR" pull --quiet --ff-only origin "$REF" 2>/dev/null || true
else
  ok "cloning to $INSTALL_DIR"
  git clone --quiet --branch "$REF" "$REPO_URL" "$INSTALL_DIR" 2>/dev/null \
    || git clone --quiet "$REPO_URL" "$INSTALL_DIR"
  git -C "$INSTALL_DIR" checkout --quiet "$REF" 2>/dev/null || true
fi

# --- sync ---
say "Resolving workspace with uv"
(cd "$INSTALL_DIR" && uv sync --all-packages --quiet)
ok "workspace synced"

# --- shim ---
say "Installing csk shim"
mkdir -p "$BIN_DIR"
SHIM="$BIN_DIR/csk"
cat > "$SHIM" <<EOF
#!/usr/bin/env bash
# csk shim installed by ro-claude-kit's install.sh
exec uv --project "$INSTALL_DIR" run csk "\$@"
EOF
chmod +x "$SHIM"
ok "shim at $SHIM"

# --- PATH check ---
case ":$PATH:" in
  *":$BIN_DIR:"*) ok "$BIN_DIR is on PATH" ;;
  *)
    warn "$BIN_DIR is NOT on PATH — add this to your shell rc:"
    echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
    ;;
esac

# --- done ---
echo
echo "${C_BOLD}${C_GREEN}csk installed.${C_RESET}"
echo
echo "Try it now (no API keys required):"
echo "  ${C_CYAN}csk init --demo -y${C_RESET}"
echo "  ${C_CYAN}csk briefing${C_RESET}"
echo
echo "Update later by re-running this installer."
echo "Uninstall: rm -rf $INSTALL_DIR $SHIM"
