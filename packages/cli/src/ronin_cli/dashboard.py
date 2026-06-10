"""ronin status — a mission-control dashboard.

One command that surfaces every system ronin runs: the Cost Router's lifetime
savings, the Dojo leaderboard, what the self-tuning router has learned, your
session archive, git state, and the last Nightshift. The data gathering is pure
(it just reads the on-disk stores); rendering composes Rich panels into a cockpit.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def _git_state(root: Path | str) -> dict:
    try:
        r = subprocess.run(["git", "-C", str(Path(root).resolve()), "status", "--porcelain", "-b"],
                           capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return {"repo": False}
    if r.returncode != 0:
        return {"repo": False}
    lines = r.stdout.splitlines()
    branch = ""
    if lines and lines[0].startswith("##"):
        branch = lines[0][2:].strip().split("...")[0].strip()
    changes = sum(1 for ln in lines if ln and not ln.startswith("##"))
    return {"repo": True, "branch": branch, "changes": changes}


def gather(root: Path | str, config) -> dict:
    """Collect a snapshot of every ronin subsystem. Pure given the on-disk state."""
    from .leaderboard import load_leaderboard
    from .nightshift import load_report
    from .router_stats import load_stats, rows
    from .savings import load_summary
    from .sessions import list_sessions

    report = load_report(root)
    patched = [r for r in report if r.get("status") == "patched"]
    return {
        "provider": config.provider,
        "model": config.resolved_model(),
        "routing": (config.route_fast, config.route_strong) if config.route_fast and config.route_strong else None,
        "git": _git_state(root),
        "savings": load_summary(root),
        "leaderboard": load_leaderboard(root).standings()[:3],
        "router": rows(load_stats(root))[:5],
        "sessions": len(list_sessions(root)),
        "nightshift": {"total": len(report), "patched": len(patched)},
    }


def render(console, data: dict) -> None:
    from rich.columns import Columns
    from rich.panel import Panel
    from rich.table import Table

    from .theme import ACCENT, MUTE, gradient_text

    # header
    head = gradient_text("ronin")
    head.append(f"  {data['provider']} · {data['model']}", style=f"bold {MUTE}")
    g = data["git"]
    if g.get("repo"):
        head.append(f"   ⎇ {g.get('branch','?')}", style="#7dcfff")
        if g.get("changes"):
            head.append(f" · {g['changes']} change(s)", style="#e0af68")
    console.print()
    console.print(head)
    console.print()

    panels = []

    # Cost Router
    sv = data["savings"]
    cost_t = Table.grid(padding=(0, 1))
    cost_t.add_column(style=MUTE)
    cost_t.add_column(style="bold")
    if sv["turns"]:
        cost_t.add_row("routed", f"{sv['turns']} ({sv['free_turns']} free)")
        cost_t.add_row("spent", f"${sv['spent']:.4f}")
        cost_t.add_row("saved", f"[green]${sv['saved']:.4f}[/green]")
    else:
        cost_t.add_row("", "[dim]not set up[/dim]")
    panels.append(Panel(cost_t, title="💰 cost router", border_style=ACCENT, padding=(0, 1)))

    # Leaderboard
    lb = data["leaderboard"]
    lb_t = Table.grid(padding=(0, 1))
    lb_t.add_column(style="bold")
    lb_t.add_column(justify="right", style="cyan")
    if lb:
        for r in lb:
            lb_t.add_row(r["provider"], f"{r['rate']*100:.0f}%")
    else:
        lb_t.add_row("[dim]no dojos yet[/dim]", "")
    panels.append(Panel(lb_t, title="🏆 dojo leaderboard", border_style="#bb9af7", padding=(0, 1)))

    # Router learning
    rt = data["router"]
    rt_t = Table.grid(padding=(0, 1))
    rt_t.add_column(style="bold")
    rt_t.add_column(style=MUTE)
    if rt:
        for r in rt:
            mark = {"reliable": "[green]●[/green]", "escalate": "[red]●[/red]"}.get(r["status"], "[#6b7089]●[/#6b7089]")
            rt_t.add_row(f"{mark} {r['provider']}", f"{r['ok']}/{r['total']}")
    else:
        rt_t.add_row("[dim]learning…[/dim]", "")
    panels.append(Panel(rt_t, title="🧠 router learned", border_style="#7dcfff", padding=(0, 1)))

    # Sessions + Nightshift
    ns = data["nightshift"]
    misc = Table.grid(padding=(0, 1))
    misc.add_column(style=MUTE)
    misc.add_column(style="bold")
    misc.add_row("sessions", str(data["sessions"]))
    misc.add_row("nightshift", f"{ns['patched']}/{ns['total']} patched" if ns["total"] else "[dim]none[/dim]")
    panels.append(Panel(misc, title="📚 history", border_style="#9ece6a", padding=(0, 1)))

    console.print(Columns(panels, equal=True, expand=True))
    console.print()
