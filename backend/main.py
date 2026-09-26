from contextlib import asynccontextmanager
from routers import api_keys, config, files, providers, subcategories, super_admin
from core.firebase import delete_anonymous_users_older_than
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from core.config import settings
from core.database import get_pool, close_pool
from core.firestore import firestore_service
from routers import authn, admin, users,offers, agency,messenger,results
from api import chat, bookings
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
from datetime import datetime, timedelta
import asyncio
import os
from core.cleanup import cleanup_unverified_users
import shutil

models_ready = False

_cache_ready = False

async def _cache_models_async():
    global _cache_ready
    import time, logging
    logger = logging.getLogger("chat_logger")
    
    GCS_DIR   = os.getenv("MODELS_DIR", "/app/gcs_models/trained_models")
    LOCAL_DIR = "/tmp/models_cache"
    os.makedirs(LOCAL_DIR, exist_ok=True)
    subcat_cache  = os.path.join(LOCAL_DIR, "travel_model_32subcats_xlmr_large")
    subcat_model  = os.path.join(subcat_cache, "mdeberta_subcategories", "model.safetensors")
    subcat_old    = os.path.join(subcat_cache, "mdeberta_subcategories", "model-001.safetensors")

    if os.path.exists(subcat_cache):
        if os.path.exists(subcat_old) or not os.path.exists(subcat_model):
            logger.info(f"[Cache] 🗑 Cache subcat obsolète → suppression forcée")
            shutil.rmtree(subcat_cache)
    models_to_cache = [
        "xlm-roberta-large-final",
        "xlm-roberta-base-is-complex",
        "travel_model_32subcats_xlmr_large",
        "mdeberta_finetuned",  
    ]

    for model_name in models_to_cache:
        src = os.path.join(GCS_DIR, model_name)
        dst = os.path.join(LOCAL_DIR, model_name)

        cache_incomplet = False
        if os.path.exists(dst):
            for root, dirs, files in os.walk(dst):
                model_files = [f for f in files if f in (
                    'model.safetensors', 'pytorch_model.bin',
                    'model_weights.pt', 'tf_model.h5'
                )]
                if 'config.json' in files and not model_files:
                    cache_incomplet = True
                    logger.warning(f"[Cache] ⚠ Cache incomplet détecté dans {root} → recopie")
                    break

        if os.path.exists(dst) and not cache_incomplet:
            logger.info(f"[Cache] ✅ {model_name} déjà en cache")
            continue

        if cache_incomplet:
            logger.info(f"[Cache] 🗑 Suppression cache incomplet : {dst}")
            shutil.rmtree(dst)

        if os.path.exists(src):
            logger.info(f"[Cache] 🔄 Copie {model_name}...")
            t0 = time.time()
            await asyncio.get_event_loop().run_in_executor(
                None, shutil.copytree, src, dst
            )
            logger.info(f"[Cache] ✅ {model_name} copié en {time.time()-t0:.1f}s")
        else:
            logger.warning(f"[Cache] ⚠ {model_name} non trouvé dans {src}")

    os.environ["MODELS_DIR"] = LOCAL_DIR
    _cache_ready = True
    logger.info(f"[Cache] ✅ MODELS_DIR → {LOCAL_DIR} | Cache prêt")

async def cleanup_anonymous_conversations():
    print("🧹 Nettoyage des conversations anonymes...")
    await firestore_service.delete_old_anonymous_conversations(hours=1)
    print("✅ Nettoyage Firestore terminé")

async def cleanup_anonymous_auth_users():
    print("🧹 Nettoyage des comptes Firebase Auth anonymes...")
    deleted = await delete_anonymous_users_older_than(hours=1)
    print(f"✅ Nettoyage Firebase Auth terminé: {deleted} comptes supprimés")

@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_pool()
    asyncio.create_task(_cache_models_async())

    asyncio.create_task(load_models())
    scheduler = AsyncIOScheduler()

    def job_listener(event):
        if event.exception:
            print(f"❌ Job {event.job_id} a échoué: {event.exception}")
            import traceback
            traceback.print_exception(
                type(event.exception),
                event.exception,
                event.exception.__traceback__
            )
        else:
            print(f"✅ Job {event.job_id} exécuté")

    scheduler.add_listener(job_listener, EVENT_JOB_ERROR | EVENT_JOB_EXECUTED)

    scheduler.add_job(
        cleanup_anonymous_conversations,
        trigger="interval",
        minutes=30,
        id="cleanup_firestore_periodic",
        misfire_grace_time=300
    )

    scheduler.add_job(
        cleanup_anonymous_auth_users,
        trigger="interval",
        minutes=30,
        id="cleanup_auth_periodic",
        misfire_grace_time=300
    )

    scheduler.add_job(
        cleanup_anonymous_conversations,
        trigger=DateTrigger(run_date=datetime.now() + timedelta(seconds=5)),
        id="cleanup_firestore_startup",
        misfire_grace_time=60
    )

    scheduler.add_job(
        cleanup_anonymous_auth_users,
        trigger=DateTrigger(run_date=datetime.now() + timedelta(seconds=10)),
        id="cleanup_auth_startup",
        misfire_grace_time=60
    )
    scheduler.add_job(
        cleanup_unverified_users,
        trigger="interval",
        hours=1,
        id="cleanup_unverified_periodic",
        misfire_grace_time=300
    )

    scheduler.add_job(
        cleanup_unverified_users,
        trigger=DateTrigger(run_date=datetime.now() + timedelta(seconds=15)),
        id="cleanup_unverified_startup",
        misfire_grace_time=60
    )

    scheduler.start()
    print(f"✅ Scheduler démarré | Jobs: {[job.id for job in scheduler.get_jobs()]}")

    yield

    scheduler.shutdown()
    await close_pool()
    print("🛑 Scheduler arrêté")

async def load_models():
    global models_ready
    import time, logging
    logger = logging.getLogger("chat_logger")

    waited = 0
    while not _cache_ready and waited < 300:
        await asyncio.sleep(2)
        waited += 2
        if waited % 20 == 0:
            logger.info(f"[Models] ⏳ Attente cache... {waited}s")

    if not _cache_ready:
        logger.warning("[Models] ⚠ Cache non prêt après 300s → chargement depuis GCS")

    t0 = time.time()
    logger.info("[Models] 🔄 Chargement de tous les modèles...")

    loop = asyncio.get_event_loop()

    def _load_all():
        from models.deberta_cat    import _load_deberta
        from models.complexity_xlm import _load_complexity
        from models.ner_xlm        import _load_ner
        from models.xlmr_subcat    import _load_subcat
        from orchestrator.rag.rag_engine import get_embeddings
        _load_deberta()
        _load_complexity()
        _load_ner()
        _load_subcat()
        get_embeddings()

    try:
        await loop.run_in_executor(None, _load_all)
        models_ready = True
        logger.info(f"[Models] ✅ Tous les modèles chargés en {time.time()-t0:.1f}s")
    except Exception as e:
        logger.error(f"[Models] ❌ Échec chargement : {e}")
        await asyncio.sleep(30)
        try:
            await loop.run_in_executor(None, _load_all)
            models_ready = True
            logger.info(f"[Models] ✅ Modèles chargés au 2e essai")
        except Exception as e2:
            logger.error(f"[Models] ❌ Échec définitif : {e2}")
            models_ready = True

app = FastAPI(title="VoyageBot API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router, prefix="/api")
app.include_router(authn.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(bookings.router, prefix="/api")
app.include_router(config.router, prefix="/api")
app.include_router(subcategories.router, prefix="/api")
app.include_router(files.router, prefix="/api")
app.include_router(providers.router, prefix="/api")
app.include_router(api_keys.router, prefix="/api")
app.include_router(super_admin.router, prefix="/api")
app.include_router(offers.router, prefix="/api")
app.include_router(agency.router, prefix="/api")
app.include_router(results.router, prefix="/api")
app.include_router(messenger.router)
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

@app.get("/health")
async def health():
    return {"status": "ok", "ready": models_ready}
@app.post("/admin/reload-models")
async def reload_models():
    global models_ready, _cache_ready
    
    subcat_cache = "/tmp/models_cache/travel_model_32subcats_xlmr_large"
    if os.path.exists(subcat_cache):
        shutil.rmtree(subcat_cache)
    
    models_ready = False
    _cache_ready = False
    asyncio.create_task(_cache_models_async())
    asyncio.create_task(load_models())
    
    return {"status": "reload started"}
