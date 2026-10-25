from gsuid_core.server import on_core_shutdown

from .api.request import KoromoApi

majs_api = KoromoApi()


@on_core_shutdown
async def _close_majs_api() -> None:
    await majs_api.aclose()
