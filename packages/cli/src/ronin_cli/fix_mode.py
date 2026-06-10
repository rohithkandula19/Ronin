"""``ronin fix <command>`` — an autonomous fix-until-green loop.

Runs a command (your tests, a script, a build). If it fails, ronin reads the
error, edits the code to fix the root cause, and **re-runs** — looping until the
command exits 0 or a round cap is hit. The command's exit code is an objective
success oracle, which makes this real closed-loop self-correction (not just a
generic "review your work" pass).

    ronin fix "pytest -q"
    ronin fix "npm test"
    ronin fix "python app.py"

Edits are gated by approval as usual (``--yolo`` to auto-approve in a sandbox).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from rich.console import Console

from .config import RoninConfig

FIX_SYSTEM = """You are a focused debugging agent. A shell command is failing and
you're given its output. Find the ROOT CAUSE: read the relevant files, reason
about the error, and make minimal, targeted edits to fix it. Do NOT run the
command yourself — it will be re-run for you after your edits. Preserve existing
style. If the failure is environmental (missing dep, not a code bug), say so."""


def run_fix(
    config: RoninConfig,
    command: str,
    *,
    root: Path | str = ".",
    console: Console,
    max_rounds: int = 5,
    yolo: bool = False,
) -> bool:
    """Loop: run ``command`` → if it fails, let ronin edit → re-run. Returns True
    once the command passes, False if it's still failing after ``max_rounds``."""
    from .code_mode import run_code_agent

    root_path = Path(root).resolve()
    for rnd in range(1, max_rounds + 1):
        console.print(f"[#6b7089]▶ run {rnd}/{max_rounds}: [bold]{command}[/bold][/#6b7089]")
        try:
            proc = subprocess.run(command, shell=True, cwd=str(root_path),
                                   capture_output=True, text=True, timeout=600)
        except subprocess.SubprocessError as e:
            console.print(f"[red]couldn't run the command:[/red] {e}")
            return False

        if proc.returncode == 0:
            console.print(f"[bold green]✓ passing[/bold green] [dim](round {rnd})[/dim]")
            return True

        output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()[-4000:]
        console.print(f"[yellow]✗ exit {proc.returncode} — asking ronin to fix…[/yellow]")
        if rnd == max_rounds:
            break  # don't bother editing on the final round; we won't re-run

        prompt = (f"The command `{command}` is failing. Its output:\n\n```\n{output}\n```\n\n"
                  "Diagnose the root cause, read the relevant files, and make focused edits "
                  "to fix it. Do not run the command — it will be re-run after your edits.")
        res = run_code_agent(
            config, prompt, root=root_path, console=console, yolo=yolo,
            base_system=FIX_SYSTEM, include_image_tool=False, max_iterations=15,
        )
        if not res.success:
            console.print(f"\n{res.output}\n")
            return False

    console.print(f"[yellow]still failing after {max_rounds} rounds — stopping. "
                  "The last error is above; might need a human eye.[/yellow]")
    return False
