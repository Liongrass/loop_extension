from datetime import datetime, timezone

from lnbits.db import Database

from .models import LoopSwap

db = Database("ext_loop")


async def create_swap(swap: LoopSwap) -> LoopSwap:
    await db.insert("loop.swaps", swap)
    return swap


async def get_swap(swap_id: str) -> LoopSwap | None:
    return await db.fetchone(
        "SELECT * FROM loop.swaps WHERE id = :id",
        {"id": swap_id},
        LoopSwap,
    )


async def get_swaps(wallet_ids: list[str]) -> list[LoopSwap]:
    if not wallet_ids:
        return []
    placeholders = ",".join(f":w{i}" for i in range(len(wallet_ids)))
    values = {f"w{i}": w for i, w in enumerate(wallet_ids)}
    return await db.fetchall(
        f"SELECT * FROM loop.swaps WHERE wallet IN ({placeholders}) "  # noqa: S608
        "ORDER BY created_at DESC",
        values,
        model=LoopSwap,
    )


async def get_unfinished_swaps() -> list[LoopSwap]:
    return await db.fetchall(
        "SELECT * FROM loop.swaps WHERE done = :done ORDER BY created_at",
        {"done": False},
        model=LoopSwap,
    )


async def update_swap(swap: LoopSwap) -> LoopSwap:
    swap.updated_at = datetime.now(timezone.utc)
    await db.update("loop.swaps", swap)
    return swap
