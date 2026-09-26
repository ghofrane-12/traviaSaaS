import json
from pathlib import Path
from fastapi import APIRouter

router = APIRouter(prefix="/config", tags=["Config"])

CATEGORIES_FILE = Path(__file__).parent.parent / "categories.json"

@router.get("/categories")
def get_categories():
    with open(CATEGORIES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)