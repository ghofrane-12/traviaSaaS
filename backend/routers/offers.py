# routers/offers.py
from fastapi import APIRouter, HTTPException, Depends, Header
from typing import Optional, List
from methods.auth import require_min_role
from core.database import get_pool
from core.schemas import (
    AuthContext, OfferMappingCreate, OfferMappingResponse, 
    OfferItem, OffersResponse
)
import uuid
import httpx
import json
import os
import re
router = APIRouter(prefix="/offers", tags=["Offers"])

STANDARD_FIELDS = {
    "id", "title", "destination", "origin",
    "price", "currency", "departure_date",
    "duration_days", "image_url", "description",
    "is_available"
}

def _extract_price(value) -> Optional[float]:
    """Extrait un float depuis n'importe quelle valeur."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = re.sub(r'[^\d.,]', '', value.replace(' ', ''))
        if not cleaned:
            return None
        cleaned = cleaned.replace(',', '.')
        parts = cleaned.split('.')
        if len(parts) > 2:
            cleaned = ''.join(parts[:-1]) + '.' + parts[-1]
        try:
            return float(cleaned)
        except ValueError:
            return None
    if isinstance(value, dict):
        for key in ('price', 'prix', 'priceTND', 'prixTND', 'amount', 'total', 'pricePerPerson'):
            if key in value:
                result = _extract_price(value[key])
                if result is not None:
                    return result
    if isinstance(value, list) and len(value) > 0:
        return _extract_price(value[0])
    return None


def _extract_first(value) -> any:
    """Retourne le premier élément si liste, sinon la valeur directe."""
    if isinstance(value, list) and len(value) > 0:
        return value[0]
    return value

def _extract_value_by_path(raw: dict, source_field: str):
    """
    Extrait une valeur depuis un dict en utilisant dot notation.
    Supporte : "field", "field.subfield", "field.0.subfield"
    Si le champ direct n'existe pas → cherche récursivement dans tout le dict.
    """
    value = raw
    for part in source_field.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list):
            try:
                value = value[int(part)]
            except (IndexError, ValueError):
                value = None
        else:
            value = None
        if value is None:
            break

    if value is not None:
        return value

    target_key = source_field.split(".")[-1]  
    return _search_recursive(raw, target_key)


def _search_recursive(data, target_key: str, depth: int = 0):
    """Cherche récursivement une clé dans un dict imbriqué."""
    if depth > 5: 
        return None

    if isinstance(data, dict):
        for k, v in data.items():
            if k.lower() == target_key.lower() and not isinstance(v, (dict, list)):
                return v
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                result = _search_recursive(v, target_key, depth + 1)
                if result is not None:
                    return result

    if isinstance(data, list):
        for item in data:
            result = _search_recursive(item, target_key, depth + 1)
            if result is not None:
                return result

    return None


def normalize_offer(
    raw: dict,
    offer_type: str,
    field_map: dict,
    default_values: dict,
    index: int = 0
) -> OfferItem:
    result = {**default_values, "type": offer_type}

    for standard_field, source_field in field_map.items():
        if standard_field not in STANDARD_FIELDS:
            continue

        value = _extract_value_by_path(raw, source_field)

        if value is None:
            continue

        if standard_field == "price":
            if isinstance(value, str):
                if "TND" in value.upper():
                    result["currency"] = "TND"
                elif "EUR" in value.upper():
                    result["currency"] = "EUR"
                elif "USD" in value.upper():
                    result["currency"] = "USD"
            value = _extract_price(value)

        elif standard_field == "image_url":
            if isinstance(value, list):
                value = value[0] if value else None
            elif isinstance(value, dict):
                for k in ('url', 'src', 'href', 'link'):
                    if k in value:
                        value = value[k]
                        break

        elif standard_field == "duration_days":
            if isinstance(value, str):
                match = re.search(r'(\d+)', value)
                value = int(match.group(1)) if match else None
            elif isinstance(value, (int, float)):
                value = int(value)
            else:
                value = None

        elif standard_field == "departure_date":
            if isinstance(value, str):
                for sep in ("|", ",", "/", ";"):
                    if sep in value and len(value.split(sep)[0]) > 4:
                        value = value.split(sep)[0].strip()
                        break
            elif isinstance(value, list):
                value = value[0] if value else None

        elif standard_field == "currency":
            if isinstance(value, str):
                value = value.strip().upper()[:3]

        elif standard_field == "is_available":
            if isinstance(value, str):
                value = value.lower() in ('true', 'oui', 'yes', '1', 'disponible')
            elif isinstance(value, int):
                value = bool(value)

        if value is not None:
            result[standard_field] = value

    if "price" not in result or result.get("price") is None:
        for price_key in ('price', 'prix', 'priceTND', 'prixTND', 'prixTotal',
                          'amount', 'total', 'cost', 'tarif', 'pricePerPerson'):
            found = _search_recursive(raw, price_key)
            if found is not None:
                extracted = _extract_price(found)
                if extracted and extracted > 0:
                    result["price"] = extracted
                    break

    if "title" not in result or not result.get("title"):
        for title_key in ('title', 'titre', 'name', 'nom', 'label'):
            found = _search_recursive(raw, title_key)
            if found and isinstance(found, str):
                result["title"] = found
                break

    if "id" not in result:
        raw_id = raw.get("id") or raw.get("_id") or raw.get("idFlight") or f"{offer_type}_{index}"
        result["id"] = str(raw_id)

    result["raw"] = raw

    return OfferItem(**{k: v for k, v in result.items() if k in OfferItem.model_fields})

async def _fetch_json_from_url(url: str) -> list:
    try:
        data = None

        if url.startswith("http://") or url.startswith("https://"):
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url)
                response.raise_for_status()
                data = response.json()

        elif url.startswith("C:\\") or url.startswith("D:\\"):
            if not os.path.exists(url):
                print(f"[OFFERS] ❌ Fichier Windows introuvable: {url}")
                return []
            with open(url, "r", encoding="utf-8") as f:
                data = json.load(f)

        elif url.startswith("/"):
            if not os.path.exists(url):
                print(f"[OFFERS] ❌ Fichier Linux introuvable: {url}")
                return []
            with open(url, "r", encoding="utf-8") as f:
                data = json.load(f)

        else:
            from core.config import settings
            resolved = os.path.join(settings.GCS_MODELS_PATH, os.path.basename(url))
            if not os.path.exists(resolved):
                print(f"[OFFERS] ❌ Fichier relatif introuvable: {resolved}")
                return []
            print(f"[OFFERS] ✅ Fichier relatif résolu: {resolved}")
            with open(resolved, "r", encoding="utf-8") as f:
                data = json.load(f)

        if data is None:
            return []

        if isinstance(data, list):
            return data

        for key in ("offers", "flights", "hotels", "data", "results", "items"):
            if key in data and isinstance(data[key], list):
                return data[key]

        for value in data.values():
            if isinstance(value, list):
                return value

        return []

    except json.JSONDecodeError as e:
        print(f"[OFFERS] ❌ JSON invalide: {e}")
        return []
    except Exception as e:
        print(f"[OFFERS] ❌ Erreur fetch JSON: {e}")
        return []


# =========================================================================
# GET /offers?tenant=uuid&type=omra  — PUBLIC
# =========================================================================

@router.get("/", response_model=OffersResponse)
async def get_offers(
    tenant_id: str,
    offer_type: Optional[str] = None,
    destination: Optional[str] = None,
    price_min: Optional[float] = None,
    price_max: Optional[float] = None,
    x_tenant_id: Optional[str] = Header(None)
):
    """
    Endpoint public — retourne les offres normalisées d'un tenant.
    Filtres disponibles : type, destination, price_min, price_max.
    """
    resolved_tenant = tenant_id or x_tenant_id
    if not resolved_tenant:
        raise HTTPException(status_code=400, detail="tenant_id requis")

    try:
        tenant_uuid = uuid.UUID(resolved_tenant)
    except ValueError:
        raise HTTPException(status_code=400, detail="tenant_id invalide")

    pool = await get_pool()

    tenant_row = await pool.fetchrow(
        "SELECT tenant_id, name, currency FROM tenants WHERE tenant_id = $1",
        tenant_uuid
    )
    if not tenant_row:
        raise HTTPException(status_code=404, detail="Tenant introuvable")

    mapping_query = """
        SELECT offer_type, field_map, default_values
        FROM offer_mappings
        WHERE tenant_id = $1
    """
    params = [tenant_uuid]

    if offer_type:
        mapping_query += " AND offer_type = $2"
        params.append(offer_type)

    mappings = await pool.fetch(mapping_query, *params)

    if not mappings:
        return OffersResponse(
            tenant_id=resolved_tenant,
            agency_name=tenant_row["name"],
            total=0,
            offers=[]
        )

    files_query = """
        SELECT file_id, url, name, agent_type
        FROM tenant_files
        WHERE tenant_id = $1
          AND file_type IN ('json', 'application/json')
          AND embedding_status != 'deleted'
    """
    file_params = [tenant_uuid]

    if offer_type:
        files_query += " AND agent_type = $2"
        file_params.append(offer_type)

    files = await pool.fetch(files_query, *file_params)

    if not files:
        return OffersResponse(
            tenant_id=resolved_tenant,
            agency_name=tenant_row["name"],
            total=0,
            offers=[]
        )

    mapping_by_type = {}
    for m in mappings:
        field_map = m["field_map"]
        default_values = m["default_values"]
        if isinstance(field_map, str):
            field_map = json.loads(field_map)
        if isinstance(default_values, str):
            default_values = json.loads(default_values)
        mapping_by_type[m["offer_type"]] = {
            "field_map":      field_map,
            "default_values": default_values
        }

    all_offers: List[OfferItem] = []

    for file in files:
        file_type = file["agent_type"] or "circuit"
        mapping = mapping_by_type.get(file_type)
        if not mapping:
            continue

        raw_items = await _fetch_json_from_url(file["url"])

        for i, raw in enumerate(raw_items):
            if not isinstance(raw, dict):
                continue
            offer = normalize_offer(
                raw=raw,
                offer_type=file_type,
                field_map=mapping["field_map"],
                default_values={
                    "currency": mapping["default_values"].get("currency") or tenant_row["currency"],
                    **mapping["default_values"],
                },
                index=i
            )
            all_offers.append(offer)

    if destination:
        dest_lower = destination.lower()
        all_offers = [
            o for o in all_offers
            if o.destination and dest_lower in o.destination.lower()
        ]

    if price_min is not None:
        all_offers = [o for o in all_offers if o.price and o.price >= price_min]

    if price_max is not None:
        all_offers = [o for o in all_offers if o.price and o.price <= price_max]

    return OffersResponse(
        tenant_id=resolved_tenant,
        agency_name=tenant_row["name"],
        total=len(all_offers),
        offers=all_offers
    )


# =========================================================================
# POST /offers/mapping — ADMIN
# =========================================================================

@router.post("/mapping", response_model=OfferMappingResponse, status_code=201)
async def create_or_update_mapping(
    payload: OfferMappingCreate,
    admin: AuthContext = Depends(require_min_role("admin"))
):
    pool = await get_pool()

    row = await pool.fetchrow(
        """
        INSERT INTO offer_mappings (tenant_id, offer_type, field_map, default_values)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (tenant_id, offer_type) DO UPDATE
            SET field_map      = EXCLUDED.field_map,
                default_values = EXCLUDED.default_values,
                updated_at     = NOW()
        RETURNING id, tenant_id, offer_type, field_map, default_values, created_at, updated_at
        """,
        uuid.UUID(admin.tenant_id),
        payload.offer_type,
        json.dumps(payload.field_map),
        json.dumps(payload.default_values)
    )

    row_dict = dict(row)
    if isinstance(row_dict.get("field_map"), str):
        row_dict["field_map"] = json.loads(row_dict["field_map"])
    if isinstance(row_dict.get("default_values"), str):
        row_dict["default_values"] = json.loads(row_dict["default_values"])

    return OfferMappingResponse(**row_dict)


# =========================================================================
# GET /offers/mappings — ADMIN — lister tous les mappings
# =========================================================================
@router.get("/mappings", response_model=List[OfferMappingResponse])
async def list_mappings(
    admin: AuthContext = Depends(require_min_role("admin"))
):
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id, tenant_id, offer_type, field_map, default_values, created_at, updated_at
        FROM offer_mappings
        WHERE tenant_id = $1
        ORDER BY offer_type
        """,
        uuid.UUID(admin.tenant_id)
    )

    result = []
    for r in rows:
        row_dict = dict(r)
        if isinstance(row_dict.get("field_map"), str):
            row_dict["field_map"] = json.loads(row_dict["field_map"])
        if isinstance(row_dict.get("default_values"), str):
            row_dict["default_values"] = json.loads(row_dict["default_values"])
        result.append(OfferMappingResponse(**row_dict))
    return result
# =========================================================================
# GET /offers/mapping/{offer_type} — ADMIN
# =========================================================================

@router.get("/mapping/{offer_type}", response_model=OfferMappingResponse)
async def get_mapping(
    offer_type: str,
    admin: AuthContext = Depends(require_min_role("admin"))
):
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT id, tenant_id, offer_type, field_map, default_values, created_at, updated_at
        FROM offer_mappings
        WHERE tenant_id = $1 AND offer_type = $2
        """,
        uuid.UUID(admin.tenant_id), offer_type
    )

    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"Aucun mapping trouvé pour le type '{offer_type}'"
        )

    row_dict = dict(row)
    if isinstance(row_dict.get("field_map"), str):
        row_dict["field_map"] = json.loads(row_dict["field_map"])
    if isinstance(row_dict.get("default_values"), str):
        row_dict["default_values"] = json.loads(row_dict["default_values"])

    return OfferMappingResponse(**row_dict)