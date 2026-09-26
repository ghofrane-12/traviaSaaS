"""import asyncio
from models.bge_subcat import predict_bge
from models.deberta_cat import predict_deberta
from models.bge_complex import predict_complexity
from models.mdeberta_ner import predict_ner
from services.split import llm_split
from services.shield import shield
import redis
from services.redis import get_redis_client, store_entities_pipeline

async def process_segment(seg, lang, cat_deberta, cpx_info, session_id,tenant_id,user_id):
    cat_bge, subcat, score = predict_bge(seg)
    entities = predict_ner(seg)
    print(f"Entities: {entities}\n{'-'*50}")
    # ─── Stocker dans Redis avec contexte tenan
    redis_client = get_redis_client()
    if redis_client:
        store_entities_pipeline(redis_client, session_id, entities)
    base_context = {
        "session_id": session_id,
        "tenant_id":  tenant_id,
        "user_id":    user_id,
        "language":   lang,
        "score":      score,
        "entities":   entities,
        "complexity": cpx_info,
        "category":   cat_bge,
        "sub_category": subcat,
    }
    #  Rejet
    if score <= 0.5:
        return{**base_context,"reply": "⚠️ Requête hors domaine. Merci de préciser votre demande.",
            "score": score}

    #  Zone de doute
    elif 0.5 < score < 0.7:
        # Ajustement pour derja-l
        if lang == "query_derja_l" and score >= 0.55 and cat_bge == cat_deberta:
            entities = predict_ner(seg)
            return{**base_context,
                "reply": f"Votre demande concerne : {cat_bge}",
                "category": cat_bge,
                "sub_category": subcat,
                "language": lang,
                "score": score,
                "entities": entities,
                "complexity": cpx_info
                }
        if cat_bge == cat_deberta:
            entities = predict_ner(seg)
            return {**base_context,
                "reply": f"Votre demande concerne : {cat_bge}",
                "category": cat_bge,
                "sub_category": subcat,
                "language": lang,
                "score": score,
                "entities": entities,
                "complexity": cpx_info
                }

        else:
            return {**base_context,
                "reply": "Je ne suis pas sûr d'avoir compris. Pouvez-vous reformuler ?",
                "category": cat_bge,
                "deberta_category": cat_deberta,
                "sub_category": subcat,
                "language": lang,
                "score": score,
                "entities": entities,
                "complexity": cpx_info
            }

    #  Validation directe
    else:
        entities = predict_ner(seg)
        return {**base_context,
            "reply": f"Je comprends que vous cherchez : {cat_bge}",
            "category": cat_bge,
            "sub_category": subcat,
            "language": lang,
            "score": score,
            "entities": entities,
            "complexity": cpx_info
        }

async def process_query(query: str,session_id: str,tenant_id:str,user_id:str) -> list[dict]:
    # Étape 1 : Bouclier
    shield_result = shield(query)
    if shield_result["status"] != "ok":
        return [{
            "reply":      f"⚠️ {shield_result['reason']}",
            "session_id": session_id,
            "tenant_id":  tenant_id,
            "user_id":    user_id
        }]

    cleaned_query = shield_result["cleaned"]
    print(f"Query nettoyée : {cleaned_query}")

    # Détection langue et cat via mDeBERTa
    deberta_result = predict_deberta(cleaned_query)
    cat_deberta = deberta_result["category"]
    lang = deberta_result["language"]

    # ── 2. Routeur et Découpeur ──
    cpx_info = predict_complexity(cleaned_query)
    print(f"Complexity info: {cpx_info}")
    if cpx_info["is_complex"]:
        segments = llm_split(cleaned_query)
    else:
        segments = [cleaned_query]
    print(f"Segments détectés : {segments}")
    # Étape 3 : Radar (BGE) en parallèle
    tasks = [process_segment(seg, lang, cat_deberta, cpx_info, session_id, tenant_id, user_id) for seg in segments]
    results = await asyncio.gather(*tasks)
    print(f"Résultats intermédiaires : {results}")

    return results"""
