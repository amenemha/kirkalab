from fastapi import APIRouter

from app.api.v1 import auth, calc, devices, internal, qr_auth, users

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(qr_auth.router)
api_router.include_router(users.router)
api_router.include_router(calc.router)
api_router.include_router(devices.router)
api_router.include_router(internal.router)
