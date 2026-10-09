from fastapi import APIRouter

from . import access, auth, devices, users

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(access.router)
api_router.include_router(devices.router)
