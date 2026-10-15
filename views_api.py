from fastapi import APIRouter, Depends, Query
from lnbits.core.crud import get_user
from lnbits.core.models import WalletTypeInfo
from lnbits.decorators import check_admin, require_admin_key, require_invoice_key

from .crud import get_swaps
from .helpers import GET_INFO, LOOP_IN_TERMS, LOOP_OUT_TERMS, litd_json
from .models import CreateLoopOut, LoopOutQuote, LoopSwap
from .services import get_quote, start_loop_out

loop_api_router = APIRouter()


@loop_api_router.get("/api/v1/info", dependencies=[Depends(check_admin)])
async def api_loop_info() -> dict:
    """Equivalent of `loop getinfo`."""
    return await litd_json(GET_INFO)


@loop_api_router.get("/api/v1/terms", dependencies=[Depends(check_admin)])
async def api_loop_terms() -> dict:
    """Equivalent of `loop terms`."""
    return {
        "loop_out": await litd_json(LOOP_OUT_TERMS),
        "loop_in": await litd_json(LOOP_IN_TERMS),
    }


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
