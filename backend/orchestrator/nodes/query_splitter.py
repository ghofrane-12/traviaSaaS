# orchestrator/nodes/query_splitter_node.py

import json
import hashlib
from redis import asyncio as redis
from services.split import llm_split
from services.shield import shield
from core.streaming import emit 

from orchestrator.state import AgentState
import logging
logger = logging.getLogger("chat_logger")

REDIS_URL = "redis://localhost:6379"

CACHE_TTL_COMPLEXITY = 60 * 60 * 24 * 3

redis_client = redis.Redis.from_url(
    REDIS_URL,
    decode_responses=True,
    socket_timeout=2,
    socket_connect_timeout=2,
)



def make_key(prefix: str, text: str) -> str:
    return f"{prefix}:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


async def cache_get(key: str):
    try:
        value = await redis_client.get(key)
        if not value:
            return None
        return json.loads(value)
    except Exception:
        return None


async def cache_set(key: str, value, ttl: int):
    try:
        await redis_client.set(
            key,
            json.dumps(value, ensure_ascii=False),
            ex=ttl
        )
    except Exception:
        pass


# =========================================================
# NODE
# =========================================================
async def query_splitter_node(state: AgentState) -> AgentState:
    from models.deberta_cat import predict_deberta
    from models.complexity_xlm import predict_complexity
    import main as _main
    import asyncio

    waited = 0
    while not getattr(_main, 'models_ready', False) and waited < 180:
        await asyncio.sleep(2)
        waited += 2
        if waited % 20 == 0:
            logger.info(f"[Splitter] ⏳ Attente modèles... {waited}s")
    # ── Bypass : mode formulaire déjà classifié ───────────────────────────
    if state.get("entities") and state.get("classification"):
        logger.info("[Splitter] ⏭ Bypass — entities + classification déjà présents")
        return state 
    
    raw_text = state.get("raw_text", "")
    
    shield_result = shield(raw_text)
    if shield_result["status"] != "ok":
        message = f"⚠️ {shield_result['reason']}"
        
        await emit({
            "type":            "stream_done",
            "status":          "blocked",
            "message":         message,
            "loyalty_summary": {},
            "follow_up":       "",
            "total_segments":  0,
        })
        
        return {
            **state,
            "segments": [],
            "final_response": json.dumps({
                "message": message,
                "_meta": {"status": "blocked"}
            }, ensure_ascii=False)
        }

    text = shield_result["cleaned"]

    lang_result = predict_deberta(text)
    language    = lang_result["language"]

    cache_key = make_key("complexity", text)
    cached    = await cache_get(cache_key)

    if cached and "is_complex" in cached:
        cpx        = cached
        cpx_source = "cache"
    else:
        cpx        = predict_complexity(text)
        cpx_source = "model"
        await cache_set(cache_key, cpx, CACHE_TTL_COMPLEXITY)

    logger.info(
        f"[Splitter] lang={language} | "
        f"is_complex={cpx.get('is_complex')} | "
        f"complexity_score={cpx.get('score', cpx.get('confidence', '?')):.3f} | "
        f"source={cpx_source}"
    )

    split_cache_key = make_key("split", f"{language}:{text}")
    cached_split = await cache_get(split_cache_key)

    if cached_split and "segments" in cached_split:
        segments = cached_split["segments"]
        logger.info(f"[Splitter] ✅ Split depuis cache | {len(segments)} segments")
    elif cpx.get("is_complex"):
        segments = llm_split(text)
        await cache_set(split_cache_key, {"segments": segments}, CACHE_TTL_COMPLEXITY)
    else:
        segments = [text]

    logger.info(
        f"[Splitter] segments={len(segments)} → "
        + " | ".join(f'[{i+1}] "{s[:60]}"' for i, s in enumerate(segments))
    )


    return {
        **state,
        "language": language,
        "segments": segments,
    }