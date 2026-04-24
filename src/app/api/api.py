from fastapi import APIRouter

from .endpoints import calendar
from .endpoints import dashboard
from .endpoints import knowledge
from .endpoints import proactor
from .endpoints import action_items

router = APIRouter()

router.include_router(dashboard.router, prefix="/dashboard")
router.include_router(calendar.router, prefix="/calendar")
router.include_router(proactor.router, prefix="/proactor")
router.include_router(knowledge.router, prefix="/knowledge")
router.include_router(action_items.router)
