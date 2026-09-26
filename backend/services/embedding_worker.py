# embedding_worker.py
import asyncio
import uuid
from core.database import get_pool
from orchestrator.rag.rag_engine import RAGEngine

async def process_single_file(file_id: uuid.UUID):
    """Extrait les chunks uniquement — sans construire l'index RAG"""
    pool = await get_pool()

    row = await pool.fetchrow(
        """
        SELECT tenant_id, agent_type, name, file_type, url
        FROM tenant_files
        WHERE file_id = $1
        """,
        file_id
    )

    if not row:
        print(f"[EmbeddingWorker] ❌ Fichier {file_id} non trouvé")
        return

    tenant_id = str(row["tenant_id"])
    agent_type = row["agent_type"]
    name = row["name"]
    file_type = row["file_type"]
    url = row["url"]

    updated = await pool.fetchval(
        """
        UPDATE tenant_files
        SET embedding_status = 'processing',
            updated_at = NOW()
        WHERE file_id = $1 AND embedding_status = 'pending'
        RETURNING file_id
        """,
        file_id
    )
    if not updated:
        return

    print(f"[EmbeddingWorker] ⚙ Extraction chunks: {name}")

    try:
        engine = RAGEngine(tenant_id=tenant_id, agent_type=agent_type)
        chunks = await engine._extract_text(url, file_type, name, str(file_id))
        valid = [c for c in chunks if not c.metadata.get("failed")]

        if not valid:
            raise ValueError("Aucun chunk valide")

        await pool.execute(
            """
            UPDATE tenant_files
            SET embedding_status = 'chunks_ready',
                chunk_count = $1,
                updated_at = NOW()
            WHERE file_id = $2
            """,
            len(valid),
            file_id
        )
        print(f"[EmbeddingWorker] ✅ {name} — {len(valid)} chunks extraits, en attente de liaison")

    except Exception as e:
        print(f"[EmbeddingWorker] ❌ {name}: {e}")
        await pool.execute(
            """
            UPDATE tenant_files
            SET embedding_status = 'failed',
                chunk_count = 0,
                updated_at = NOW()
            WHERE file_id = $1
            """,
            file_id
        )

async def process_pending_files():
    """Traite tous les fichiers en attente"""
    pool = await get_pool()

    rows = await pool.fetch(
        """
        SELECT file_id
        FROM tenant_files
        WHERE embedding_status = 'pending'
        ORDER BY created_at ASC
        LIMIT 20
        """
    )

    if not rows:
        return

    for row in rows:
        await process_single_file(row["file_id"])


async def recover_stuck_processing():
    """Récupère les fichiers bloqués en 'processing' depuis trop longtemps"""
    pool = await get_pool()
    result = await pool.execute(
        """
        UPDATE tenant_files
        SET embedding_status = 'pending'
        WHERE embedding_status = 'processing'
          AND updated_at < NOW() - INTERVAL '10 minutes'
        """
    )
    if result:
        print("[EmbeddingWorker] 🔄 Fichiers bloqués remis en pending")


async def run_worker(interval_seconds: int = 30):
    """Worker continu pour traiter les fichiers en arrière-plan"""
    print("[EmbeddingWorker] 🚀 Démarrage du worker")
    while True:
        try:
            await recover_stuck_processing()
            await process_pending_files()
        except Exception as e:
            print(f"[EmbeddingWorker] ❌ Erreur: {e}")
        await asyncio.sleep(interval_seconds)


if __name__ == "__main__":
    asyncio.run(run_worker(interval_seconds=30))