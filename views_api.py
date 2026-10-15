from fastapi import APIRouter, Depends, Query
from lnbits.core.crud import get_user, get_wallet
from lnbits.core.models import WalletTypeInfo
from lnbits.decorators import check_admin, require_admin_key, require_invoice_key

from .crud import get_all_swaps, get_swaps
from .helpers import GET_INFO, litd_json
from .models import (
    AdminLoopSwap,
    CreateLoopOut,
    LoopOutQuote,
    LoopOutTerms,
    LoopSwap,
)
from .services import get_quote, get_terms, start_loop_out

loop_api_router = APIRouter()


@loop_api_router.get("/api/v1/info", dependencies=[Depends(check_admin)])
async def api_loop_info() -> dict:
    """Equivalent of `loop getinfo`."""
    return await litd_json(GET_INFO)


@loop_api_router.get(
    "/api/v1/terms", dependencies=[Depends(require_invoice_key)]
)
async def api_loop_out_terms() -> LoopOutTerms:
    """Loop Out part of `loop terms`."""
    return await get_terms()


@loop_api_router.get(
    "/api/v1/quote", dependencies=[Depends(require_invoice_key)]
)
async def api_loop_out_quote(amount: int = Query(gt=0)) -> LoopOutQuote:
    return await get_quote(amount)


@loop_api_router.post("/api/v1/swaps")
async def api_create_loop_out(
    data: CreateLoopOut,
    key_info: WalletTypeInfo = Depends(require_admin_key),
) -> LoopSwap:
    return await start_loop_out(key_info.wallet, data)


@loop_api_router.get("/api/v1/swaps")
async def api_list_swaps(
    key_info: WalletTypeInfo = Depends(require_invoice_key),
) -> list[LoopSwap]:
    """All swaps of the wallet owner's wallets."""
    user = await get_user(key_info.wallet.user)
    return await get_swaps(user.wallet_ids if user else [key_info.wallet.id])


@loop_api_router.get("/api/v1/swaps/all", dependencies=[Depends(check_admin)])
async def api_list_all_swaps() -> list[AdminLoopSwap]:
    """Every swap, with the name of the wallet that started it."""
    swaps = await get_all_swaps()
    names: dict[str, str | None] = {}
    for wallet_id in {swap.wallet for swap in swaps}:
        wallet = await get_wallet(wallet_id, deleted=None)
        names[wallet_id] = wallet.name if wallet else None
    return [
        AdminLoopSwap(**swap.dict(), wallet_name=names[swap.wallet])
        for swap in swaps
    ]
