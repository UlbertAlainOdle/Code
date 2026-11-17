"""Bridge real synchronous work callbacks into a cancellable SSE consumer."""
import asyncio
import inspect


async def run_blocking(function, *args):
    loop = asyncio.get_running_loop()
    events = asyncio.Queue()
    active = True

    def progress(phase, message):
        if active and not loop.is_closed():
            loop.call_soon_threadsafe(events.put_nowait, ("progress", dict(phase=phase, message=message)))

    async def run():
        try:
            parameters = inspect.signature(function).parameters
            kwargs = {"progress": progress} if "progress" in parameters else {}
            result = await asyncio.to_thread(function, *args, **kwargs)
            await events.put(("result", result))
        except Exception as error:
            await events.put(("error", error))

    task = asyncio.create_task(run())
    try:
        while True:
            kind, data = await events.get()
            if kind == "error":
                raise data
            yield kind, data
            if kind == "result":
                break
    finally:
        active = False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
