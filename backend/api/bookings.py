# api/bookings.py
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from core.database import get_pool
from pydantic import BaseModel
from typing import Optional
import uuid, json
from datetime import date, datetime
import asyncpg
from fastapi import Depends, HTTPException
from methods.auth import get_current_user,require_min_role
from core.schemas import AuthContext
router = APIRouter(prefix="/bookings", tags=["bookings"])


def parse_date(val: Optional[str]) -> Optional[date]:
    if val is None:
        return None
    if isinstance(val, (date, datetime)):
        return val if isinstance(val, date) else val.date()
    try:
        return datetime.strptime(val[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


class BookingCreate(BaseModel):
    tenant_id:      str
    user_id:        str
    session_id:     str
    booking_type:   str
    origin:          Optional[str]   = None
    destination:     Optional[str]   = None
    departure_date:  Optional[str]   = None
    return_date:     Optional[str]   = None
    adults:          Optional[int]   = None
    cabin_class:     Optional[str]   = None
    airline:         Optional[str]   = None
    flight_number:   Optional[str]   = None
    city:            Optional[str]   = None
    check_in:        Optional[str]   = None
    check_out:       Optional[str]   = None
    guests:          Optional[int]   = None
    hotel_name:      Optional[str]   = None
    activity_name:   Optional[str]   = None
    activity_date:   Optional[str]   = None
    venue:           Optional[str]   = None
    activity_city:   Optional[str]   = None
    price:           Optional[float] = None
    currency:        Optional[str]   = "EUR"
    raw_data:        Optional[dict]  = None


# ── Requêtes de détection doublon par type ────────────────────────────────
DUPLICATE_QUERIES = {
    "flight": (
        """
        SELECT booking_id FROM user_bookings
        WHERE user_id       = $1
        AND   tenant_id     = $2
        AND   flight_number = $3
        AND   departure_date = $4
        AND   status        = 'completed'
        """,
        lambda b: [
            uuid.UUID(b.user_id),
            uuid.UUID(b.tenant_id),
            b.flight_number,
            parse_date(b.departure_date),
        ],
    ),
    "hotel": (
        """
        SELECT booking_id FROM user_bookings
        WHERE user_id     = $1
        AND   tenant_id   = $2
        AND   hotel_name  = $3
        AND   check_in    = $4
        AND   status      = 'completed'
        """,
        lambda b: [
            uuid.UUID(b.user_id),
            uuid.UUID(b.tenant_id),
            b.hotel_name,
            parse_date(b.check_in),
        ],
    ),
    "tour": (
        """
        SELECT booking_id FROM user_bookings
        WHERE user_id       = $1
        AND   tenant_id     = $2
        AND   activity_name = $3
        AND activity_date IS NOT DISTINCT FROM $4
        AND   status        = 'completed'
        """,
        lambda b: [
            uuid.UUID(b.user_id),
            uuid.UUID(b.tenant_id),
            b.activity_name,
            parse_date(b.activity_date),
        ],
    ),
    "restaurant": (
        """
        SELECT booking_id FROM user_bookings
        WHERE user_id       = $1
        AND   tenant_id     = $2
        AND   activity_name = $3
        AND   booking_type  = 'restaurant'
        AND   status        = 'completed'
        """,
        lambda b: [
            uuid.UUID(b.user_id),
            uuid.UUID(b.tenant_id),
            b.activity_name,
        ],
    ),
}


@router.post("/create")
async def create_booking(
    booking: BookingCreate,
    current_user: AuthContext = Depends(require_min_role("client"))):
    if str(current_user.user_id) != booking.user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    pool = await get_pool()

    # ── 1. Vérification doublon ───────────────────────────────────────
    dup_entry = DUPLICATE_QUERIES.get(booking.booking_type)
    if dup_entry:
        dup_query, dup_params_fn = dup_entry
        try:
            existing = await pool.fetchrow(dup_query, *dup_params_fn(booking))
            if existing:
                return JSONResponse(
                    status_code=200,
                    content={
                        "booking_id": str(existing["booking_id"]),
                        "status":     "already_exists",
                    }
                )
        except Exception as e:
            pass

    # ── 2. INSERT ─────────────────────────────────────────────────────
    try:
        result = await pool.fetchrow(
            """
            INSERT INTO user_bookings (
                tenant_id, user_id, session_id, booking_type,
                origin, destination, departure_date, return_date,
                adults, cabin_class, airline, flight_number,
                city, check_in, check_out, guests, hotel_name,
                activity_name, activity_date, venue, activity_city,
                price, currency, raw_data, status
            ) VALUES (
                $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,
                $13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25
            ) RETURNING booking_id, created_at
            """,
            uuid.UUID(booking.tenant_id),
            uuid.UUID(booking.user_id),
            booking.session_id,
            booking.booking_type,
            booking.origin, booking.destination,
            parse_date(booking.departure_date),
            parse_date(booking.return_date),
            booking.adults, booking.cabin_class,
            booking.airline, booking.flight_number,
            booking.city,
            parse_date(booking.check_in),
            parse_date(booking.check_out),
            booking.guests, booking.hotel_name,
            booking.activity_name,
            parse_date(booking.activity_date),
            booking.venue, booking.activity_city,
            booking.price, booking.currency,
            json.dumps(booking.raw_data) if booking.raw_data else None,
            "completed",
        )
        return {
            "booking_id": str(result["booking_id"]),
            "status":     "created",
        }

    # ── 3. Violation contrainte unique DB (filet de sécurité) ─────────
    except asyncpg.UniqueViolationError:
        existing = await pool.fetchrow(
            """
            SELECT booking_id FROM user_bookings
            WHERE user_id     = $1
            AND   tenant_id   = $2
            AND   booking_type = $3
            AND   status      = 'completed'
            ORDER BY created_at DESC LIMIT 1
            """,
            uuid.UUID(booking.user_id),
            uuid.UUID(booking.tenant_id),
            booking.booking_type,
        )
        return JSONResponse(
            status_code=200,
            content={
                "booking_id": str(existing["booking_id"]) if existing else None,
                "status":     "already_exists",
            }
        )

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": str(e), "status": "error"}
        )


@router.get("/last")
async def get_last_booking(
    tenant_id:    str,
    booking_type: Optional[str] = None,
    current_user = Depends(get_current_user)
):
    pool = await get_pool()
    params = [current_user.user_id, uuid.UUID(tenant_id)]
    query  = """
        SELECT * FROM user_bookings
        WHERE user_id = $1 AND tenant_id = $2
        AND status != 'cancelled'
    """
    if booking_type:
        query += " AND booking_type = $3"
        params.append(booking_type)
    query += " ORDER BY created_at DESC LIMIT 5"

    rows = await pool.fetch(query, *params)
    return {"bookings": [dict(r) for r in rows]}