from fastapi import APIRouter, Depends
from lnbits.decorators import check_admin

from .helpers import loop_call

loop_api_router = APIRouter()


@loop_api_router.get("/api/v1/info", dependencies=[Depends(check_admin)])
async def api_loop_info() -> dict:
    """Equivalent of `loop getinfo`."""
    return await loop_call("GetInfo")


@loop_api_router.get("/api/v1/terms", dependencies=[Depends(check_admin)])
async def api_loop_terms() -> dict:
    """Equivalent of `loop terms`."""
    return {
        "loop_out": await loop_call("LoopOutTerms"),
        "loop_in": await loop_call("GetLoopInTerms"),
    }
