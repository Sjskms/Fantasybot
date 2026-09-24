# handlers/session/__init__.py
from aiogram import Router

from .lifecycle import router as lifecycle_router
from .channels import router as channels_router
from .statistics import router as statistics_router
from .text_transform import router as text_transform_router
from .logging import router as logging_router

router = Router()

# Объединяем все роутеры подпапки в один общий роутер сессий
router.include_router(lifecycle_router)
router.include_router(channels_router)
router.include_router(statistics_router)
router.include_router(text_transform_router)
router.include_router(logging_router)
