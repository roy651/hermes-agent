"""
Lightweight always-on health server for the Hermes gateway.

Starts unconditionally with the gateway (no config required) on port 8766.
Exposes a single /health endpoint that reports whether the agent is idle or
processing, and for how long — enabling an external watchdog to detect a
stuck gateway without going through the LLM.

Usage (in start_gateway):
    health_task = asyncio.create_task(
        start_health_server(runner, port=HEALTH_PORT)
    )
"""

import asyncio
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gateway.run import GatewayRunner

logger = logging.getLogger(__name__)

HEALTH_PORT = 8766


async def start_health_server(runner: "GatewayRunner", port: int = HEALTH_PORT) -> None:
    """Start the aiohttp health server as an asyncio task."""
    try:
        from aiohttp import web
    except ImportError:
        logger.warning("aiohttp not available — gateway health server disabled")
        return

    async def handle_health(request: web.Request) -> web.Response:
        now = time.time()
        ts_map: dict = getattr(runner, "_running_agents_ts", {})

        sessions = {}
        longest = 0.0
        for key, started_at in ts_map.items():
            duration = now - started_at
            sessions[key] = {
                "running_since": started_at,
                "duration_seconds": round(duration, 1),
            }
            if duration > longest:
                longest = duration

        status = "processing" if sessions else "idle"
        telegram_polling = any(
            t.get_name() == "Updater:start_polling:polling_task" and not t.done()
            for t in asyncio.all_tasks()
        )
        return web.json_response({
            "status": status,
            "active_sessions": len(sessions),
            "longest_running_seconds": round(longest, 1),
            "sessions": sessions,
            "telegram_polling": telegram_polling,
        })

    app = web.Application()
    app.router.add_get("/health", handle_health)

    runner_obj = web.AppRunner(app)
    await runner_obj.setup()
    site = web.TCPSite(runner_obj, "127.0.0.1", port)
    try:
        await site.start()
        logger.info("Gateway health server listening on http://127.0.0.1:%d/health", port)
        # Run forever (cancelled when the gateway shuts down)
        await asyncio.Future()
    except asyncio.CancelledError:
        pass
    finally:
        await runner_obj.cleanup()
