from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.abtest_ozon import AbTestService

router = APIRouter(prefix="/abtest", tags=["abtest"])
service = AbTestService()


class StartBody(BaseModel):
    tenant_id: Optional[str] = None
    product_id: str = Field(..., min_length=1)
    images: List[str] = Field(..., min_length=1)


@router.post("/start")
def start_test(body: StartBody):
    test_id = service.start(body.tenant_id, body.product_id, body.images)
    return {"ok": True, "id": test_id}


@router.get("/status")
def test_status(id: int = Query(..., ge=1)):
    data = service.status(id)
    if not data:
        raise HTTPException(status_code=404, detail="Test not found")
    return {"ok": True, "data": data}


@router.post("/stop")
def stop_test(id: int = Query(..., ge=1)):
    result = service.finalize(id)
    if not result.get("ok", True):
        raise HTTPException(status_code=404, detail="Test not found")
    return {"ok": True, "result": result}
