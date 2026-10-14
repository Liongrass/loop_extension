from fastapi import APIRouter, Depends
from lnbits.core.views.generic import index
from lnbits.decorators import check_user_exists

loop_generic_router = APIRouter()

loop_generic_router.add_api_route(
    "/", methods=["GET"], endpoint=index, dependencies=[Depends(check_user_exists)]
)
