# Windows / WSL

Prefer WSL2 for the Linux install path; native Windows is secondary.

- Run `install.sh` from a WSL home directory, not a `/mnt/c` working copy if possible.
- Keep line endings and executable bits consistent with the Linux tree.
- Point doctor at the WSL Node/Python toolchains, not Windows ones.
- Document known PATH issues when both Windows and WSL binaries exist.
