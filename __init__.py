from fastapi import APIRouter
from lnbits.task_manager import Task, task_manager

from .crud import db
from .services import sync_swaps
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

# How often pending swaps are synced with loopd and lnd, in seconds.
SYNC_INTERVAL = 30

scheduled_tasks: list[Task] = []


def loop_stop():
    for task in scheduled_tasks:
        task_manager.cancel_task(task)
    scheduled_tasks.clear()


def loop_start():
    task = task_manager.create_permanent_task(
        sync_swaps, name="ext_loop_sync_swaps", interval=SYNC_INTERVAL
    )
    scheduled_tasks.append(task)


__all__ = [
    "db",
    "loop_ext",
    "loop_start",
    "loop_static_files",
    "loop_stop",
]
