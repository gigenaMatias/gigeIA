"""Enrutamiento de comandos a acciones locales o al worker del cerebro."""

import asyncio
import inspect
from collections.abc import Callable
from collections.abc import Awaitable

from core.messages import Command, Response

QuickAction = Callable[[str], str | None | Awaitable[str | None]]


async def router_worker(
    command_queue: asyncio.Queue[Command | None],
    llm_queue: asyncio.Queue[Command | None],
    response_queue: asyncio.Queue[Response | None],
    quick_action: QuickAction,
) -> None:
    """Las acciones locales no esperan al LLM; el resto pasa por llm_queue."""
    while True:
        command = await command_queue.get()
        if command is None:
            await llm_queue.put(None)
            return

        if inspect.iscoroutinefunction(quick_action):
            result = await quick_action(command.text)
        else:
            result = await asyncio.to_thread(quick_action, command.text)
        if inspect.isawaitable(result):
            result = await result
        if result is None:
            await llm_queue.put(command)
        else:
            await response_queue.put(Response.now(result))
