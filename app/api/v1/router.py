from fastapi import APIRouter

from app.api.v1 import admin, auth, calc, connections, devices, energy, farm, firmware, internal, pools, qr_auth, users, wallets

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(qr_auth.router)
api_router.include_router(users.router)
api_router.include_router(calc.router)
api_router.include_router(devices.router)
api_router.include_router(firmware.router)
api_router.include_router(internal.router)
api_router.include_router(farm.router)
api_router.include_router(pools.router)
api_router.include_router(energy.router)
api_router.include_router(admin.router)
api_router.include_router(connections.router)
api_router.include_router(wallets.router)
api_router.include_router(admin.track_router)
