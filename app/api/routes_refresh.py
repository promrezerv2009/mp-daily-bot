# app/api/routes_refresh.py
from fastapi import APIRouter, Query
from app.services.etl import refresh

router = APIRouter()

@router.post("/refresh")
def refresh_endpoint(period: str = Query("yesterday")):
    return refresh(period)
