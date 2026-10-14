from fastapi import APIRouter

from .crud import db
from .views import loop_generic_router
from .views_api import loop_api_router

loop_ext: APIRouter = APIRouter(prefix="/loop", tags=["loop"])
loop_ext.include_router(loop_generic_router)
loop_ext.include_router(loop_api_router)

loop_static_files = [
    {
        "path": "/loop/static",
        "name": "loop_static",
    }
]


def loop_stop():
    pass


def loop_start():
    pass


__all__ = [
    "db",
    "loop_ext",
    "loop_start",
    "loop_static_files",
    "loop_stop",
]
