#!/usr/bin/env bash
#
# Install ronin from source.
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/rohithkandula19/Ronin/main/install.sh | bash
#
# Or with a pinned version:
#   curl -sSL https://raw.githubusercontent.com/rohithkandula19/Ronin/main/install.sh | bash -s -- --ref v1.0.0-rc.2
#
# What it does:
#   1. Installs `uv` (https://github.com/astral-sh/uv) if it's not on PATH.
#   2. Clones the repo to ~/.local/share/ronin (or updates if it's already there).
#   3. Runs `uv sync --all-packages` to resolve the workspace.
#   4. Creates `ronin` and `ro` shims at ~/.local/bin that invoke the workspace-managed venv.
#   5. Tells you to add ~/.local/bin to PATH if it's not there.
#
# This is the bootstrap install. `ronin-cli` is not on PyPI yet; once it lands
# you'll be able to `pipx install ronin-cli` / `uv tool install ronin-cli`.

set -euo pipefail

REPO_URL="https://github.com/rohithkandula19/Ronin"
# Pinned uv version for reproducible installs (override with UV_INSTALL_VERSION).
UV_INSTALL_VERSION="${UV_INSTALL_VERSION:-0.5.11}"
INSTALL_DIR="${RONIN_INSTALL_DIR:-${CSK_INSTALL_DIR:-$HOME/.local/share/ronin}}"
BIN_DIR="${RONIN_BIN_DIR:-${CSK_BIN_DIR:-$HOME/.local/bin}}"
REF="main"

# --- args ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ref) REF="$2"; shift 2 ;;
    --dir) INSTALL_DIR="$2"; shift 2 ;;
    -h|--help)
      sed -n '3,20p' "$0"
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
  MINGW*|MSYS*|CYGWIN*|Windows_NT)
    die "Windows is not yet supported natively. Run this installer inside WSL (Windows Subsystem for Linux): open a WSL shell and re-run the same curl | bash command." ;;
  *) die "unsupported OS: $OS. Supported: macOS and Linux (Windows via WSL)." ;;
esac
ok "platform: $OS"

# --- uv ---
say "Checking uv"
if ! command -v uv >/dev/null 2>&1; then
  warn "uv not found — installing the pinned official installer ($UV_INSTALL_VERSION)"
  # Pin the uv version (reproducible installs) and stage the installer to a file
  # before running it, rather than piping the network stream straight into `sh`
  # (a piped partial download can execute half a script). If UV_INSTALLER_SHA256
  # is set, the download is verified against it before execution.
  _uv_installer="$(mktemp)"
  curl -sSLf "https://astral.sh/uv/${UV_INSTALL_VERSION}/install.sh" -o "$_uv_installer" \
    || die "failed to download the uv $UV_INSTALL_VERSION installer"
  if [[ -n "${UV_INSTALLER_SHA256:-}" ]]; then
    echo "${UV_INSTALLER_SHA256}  ${_uv_installer}" | shasum -a 256 -c - \
      || die "uv installer checksum mismatch — refusing to run it"
  fi
  sh "$_uv_installer"
  rm -f "$_uv_installer"
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
say "Fetching ronin ($REF)"
mkdir -p "$(dirname "$INSTALL_DIR")"
if [[ -d "$INSTALL_DIR/.git" ]]; then
  ok "existing checkout at $INSTALL_DIR — fetching latest"
  git -C "$INSTALL_DIR" fetch --tags --quiet origin || die "git fetch failed for $REF"
  git -C "$INSTALL_DIR" checkout --quiet "$REF" || die "git checkout '$REF' failed — is it a valid branch/tag?"
  # A fast-forward can legitimately fail on a tag or a diverged local tree; only
  # fatal when updating a branch. Never swallow it silently.
  git -C "$INSTALL_DIR" pull --quiet --ff-only origin "$REF" 2>/dev/null \
    || warn "could not fast-forward '$REF' (tag or diverged checkout) — using the checked-out commit"
else
  ok "cloning to $INSTALL_DIR"
  git clone --quiet --branch "$REF" "$REPO_URL" "$INSTALL_DIR" 2>/dev/null \
    || git clone --quiet "$REPO_URL" "$INSTALL_DIR" \
    || die "git clone failed for $REPO_URL"
  git -C "$INSTALL_DIR" checkout --quiet "$REF" \
    || die "'$REF' is not a valid branch/tag in $REPO_URL — refusing to silently install main"
fi
# Prove we are on the ref the user asked for, and print the exact commit.
_head="$(git -C "$INSTALL_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
ok "checked out $REF @ $_head"

# --- sync ---
say "Resolving workspace with uv"
(cd "$INSTALL_DIR" && uv sync --all-packages --quiet)
ok "workspace synced"

# --- shims (ronin + ro) ---
say "Installing ronin + ro shims"
mkdir -p "$BIN_DIR"
for name in ronin ro; do
  SHIM="$BIN_DIR/$name"
  cat > "$SHIM" <<EOF
#!/usr/bin/env bash
# $name shim installed by ronin's install.sh
exec uv --project "$INSTALL_DIR" run $name "\$@"
EOF
  chmod +x "$SHIM"
  ok "shim at $SHIM"
done

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
echo "${C_BOLD}${C_GREEN}ronin installed.${C_RESET}"
echo
echo "Get started:"
echo "  ${C_CYAN}ronin${C_RESET}                 # the agent (free-first onboarding on first run)"
echo "  ${C_CYAN}ronin doctor${C_RESET}          # check provider/model/config"
echo "  ${C_CYAN}ronin play${C_RESET}            # take a break — the free terminal-game arcade"
echo
echo "Update later: ${C_CYAN}ronin update${C_RESET} (or re-run this installer)."
echo "Uninstall: rm -rf $INSTALL_DIR $BIN_DIR/ronin $BIN_DIR/ro"
