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
    main_router.include_router(start_router)
    main_router.include_router(accounts_router)
    main_router.include_router(autoresponder_router)
    main_router.include_router(mailings_router)
    main_router.include_router(subscription_router)
    main_router.include_router(admin_router)
    main_router.include_router(referral_router)
    return main_router
