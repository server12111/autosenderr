from aiogram import Router

from .start import router as start_router
from .accounts import router as accounts_router
from .autoresponder import router as autoresponder_router
from .mailings import router as mailings_router
from .subscription import router as subscription_router
from .admin import router as admin_router
from .referral import router as referral_router
from .stars_payment import router as stars_payment_router


def setup_routers() -> Router:
    main_router = Router()
    child_routers = (
        start_router,
        accounts_router,
        autoresponder_router,
        mailings_router,
        subscription_router,
        admin_router,
        referral_router,
        stars_payment_router,
    )
    for child_router in child_routers:
        parent_router = child_router.parent_router
        if parent_router is not None:
            parent_router.sub_routers.remove(child_router)
            child_router._parent_router = None
        main_router.include_router(child_router)
    return main_router
