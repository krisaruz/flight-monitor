from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.deps import get_current_user
from app.models import User
from app.schemas import PlaceSuggestionOut
from app.services.places import list_suggestions

router = APIRouter(prefix="/api/places", tags=["places"])


@router.get("/suggest", response_model=list[PlaceSuggestionOut])
def suggest_places(
    q: str = Query(default="", max_length=64),
    limit: int = Query(default=20, ge=1, le=50),
    _user: User = Depends(get_current_user),
) -> list[PlaceSuggestionOut]:
    return [
        PlaceSuggestionOut(
            kind=s.kind,
            id=s.id,
            label=s.label,
            subtitle=s.subtitle,
            display=s.display,
            codes=s.codes,
        )
        for s in list_suggestions(q, limit=limit)
    ]
