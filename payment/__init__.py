# payment/__init__.py
from aiogram import Router

from .payment_admin import router as payment_admin_router

from .payment_main import router as payment_main_router

from .payment_CB import router as payment_CB_router

from .payment_card import router as payment_card_router



router = Router()

router.include_router(payment_admin_router)
router.include_router(payment_main_router)
router.include_router(payment_card_router)
router.include_router(payment_CB_router)