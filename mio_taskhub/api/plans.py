from fastapi import APIRouter, Query

router = APIRouter(prefix="/plans", tags=["plans"])

@router.get("/night")
def night_plan(start: str = Query("22:00"), end: str = Query("07:00"), task_ids: str = Query(None)):
    return {"window_start": start, "window_end": end, "has_overflow": False, "items": []}
