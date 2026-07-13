from aiogram import Router

from .start import router as start_router
from .accounts import router as accounts_router
from .autoresponder import router as autoresponder_router
from .mailings import router as mailings_router
from .subscription import router as subscription_router
from .admin import router as admin_router
from .referral import router as referral_router


def setup_routers() -> Router:
    main_router = Router()

    # Створюємо список усіх твоїх роутерів
    routers = [
        start_router,
        accounts_router,
        autoresponder_router,
        mailings_router,
        subscription_router,
        admin_router,
        referral_router
    ]

    # Безпечно підключаємо кожен, якщо він ще не має батька
    for r in routers:
        if r.parent_router is None:
            main_router.include_router(r)

    return main_router

