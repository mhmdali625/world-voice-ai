"""
Hedged requests: a standard tail-latency technique.

Free-tier APIs sometimes queue a request for many seconds,
while an identical request sent a moment later is answered
immediately. If the first request has not finished after
`after` seconds, an identical backup request is started and
whichever succeeds first is used; the other is cancelled.

Same model, same input: quality is unchanged. The extra
request is only sent for slow cases.
"""

import asyncio
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


async def hedged(
    factory: Callable[[], Awaitable[T]],
    after: float,
    on_hedge: Callable[[], None] | None = None,
) -> tuple[T, int]:
    """Return (result, winner) where winner is 1 or 2."""

    first = asyncio.ensure_future(factory())
    pending = {first}
    tasks = [first]

    try:
        if after > 0:
            done, _ = await asyncio.wait(pending, timeout=after)
            if not done:
                if on_hedge:
                    on_hedge()
                second = asyncio.ensure_future(factory())
                pending.add(second)
                tasks.append(second)

        last_error: BaseException | None = None
        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                if task.exception() is None:
                    return task.result(), tasks.index(task) + 1
                last_error = task.exception()

        assert last_error is not None
        raise last_error

    finally:
        for task in pending:
            task.cancel()
