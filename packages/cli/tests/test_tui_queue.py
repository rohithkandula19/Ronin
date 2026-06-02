from __future__ import annotations

from unittest.mock import patch

from textual.widgets import Input

from ronin_cli.config import RoninConfig
from ronin_cli.tui import RoninApp


async def test_typing_while_busy_queues_then_runs() -> None:
    app = RoninApp(config=RoninConfig(provider="anthropic"))
    async with app.run_test() as pilot:
        # while a turn is "running" (we stub _run_agent to just mark busy)
        with patch.object(app, "_run_agent", side_effect=lambda q: setattr(app, "busy", True)):
            app.query_one("#prompt", Input).value = "first"
            await pilot.press("enter")
            assert app.busy is True
            # a second message typed while busy should QUEUE, not drop
            app.query_one("#prompt", Input).value = "second"
            await pilot.press("enter")
            assert app.queue == ["second"]

        # turn finishes → draining runs the queued message in order
        started: list[str] = []
        app.busy = False
        with patch.object(app, "_run_agent", side_effect=lambda q: started.append(q)):
            app._drain_queue()
        assert started == ["second"]
        assert app.queue == []
