import os
import uuid
import aiofiles
from fastapi import APIRouter, UploadFile, File, Form, BackgroundTasks, HTTPException, Depends
from sqlalchemy.orm import Session
from uuid import UUID, uuid4
from sqlalchemy import func
import asyncio
from core.config import settings
from core.database import get_db, get_pool
from core.models import TenantFile, TenantFileSubcategory, TenantSubcategoryConfig
from core.schemas import FileResponse, FileSubcategoryLinkCreate, FileSubcategoryLinkResponse,AuthContext
from methods.auth import get_current_user

GCS_FILES_DIR = settings.GCS_MODELS_PATH
os.makedirs(GCS_FILES_DIR, exist_ok=True)
ALLOWED_EXTENSIONS = {"pdf", "json", "txt", "xml", "csv"}

router = APIRouter(prefix="/tenants/{tenant_id}", tags=["Files"])



async def _process_file_background(file_id: UUID):
    from services.embedding_worker import process_single_file
    await process_single_file(file_id)


# ══════════════════════════════════════════════════════════════════════════════
# UPLOAD
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/files", response_model=FileResponse)
async def upload_file(
    background_tasks: BackgroundTasks,
    tenant_id: UUID,
    name: str = Form(...),
    agent_type: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: AuthContext = Depends(get_current_user),
):
    original_name = file.filename or "file"
    ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else "bin"
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Format non supporté: {ext}")

    contents = await file.read()
    
    file_id = uuid4()
    unique_filename = f"{file_id}_{original_name}"
    filepath = os.path.join(GCS_FILES_DIR, unique_filename)

    async with aiofiles.open(filepath, "wb") as f:
        await f.write(contents)
    uploaded_by = None
    try:
        if current_user.user_id and current_user.user_id != "anonymous":
            uploaded_by = uuid.UUID(current_user.user_id)
    except (ValueError, AttributeError):
        uploaded_by = None
    db_file = TenantFile(
        file_id=file_id,
        tenant_id=tenant_id,
        name=name,
        file_type=ext,
        url=unique_filename,
        size_bytes=len(contents),
        agent_type=agent_type,
        embedding_status="pending",
        chunk_count=0,
        uploaded_by=uploaded_by
    )
    db.add(db_file)
    db.commit()
    db.refresh(db_file)

    background_tasks.add_task(_process_file_background, file_id)

    return db_file


# ══════════════════════════════════════════════════════════════════════════════
# LECTURE / STATUT
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/files", response_model=list[FileResponse])
def get_files(tenant_id: UUID, db: Session = Depends(get_db)):
    return db.query(TenantFile).filter(TenantFile.tenant_id == tenant_id).all()


@router.get("/files/{file_id}/status")
def get_file_status(tenant_id: UUID, file_id: UUID, db: Session = Depends(get_db)):
    db_file = db.query(TenantFile).filter_by(
        file_id=file_id, tenant_id=tenant_id
    ).first()
    if not db_file:
        raise HTTPException(404, "Fichier introuvable")

    return {
        "file_id": str(file_id),
        "name": db_file.name,
        "embedding_status": db_file.embedding_status,
        "chunk_count": db_file.chunk_count,
        "created_at": db_file.created_at.isoformat(),
    }


# ══════════════════════════════════════════════════════════════════════════════
# SUPPRESSION
# ══════════════════════════════════════════════════════════════════════════════

@router.delete("/files/{file_id}", status_code=204)
def delete_file(tenant_id: UUID, file_id: UUID, db: Session = Depends(get_db)):
    db_file = db.query(TenantFile).filter_by(
        file_id=file_id, tenant_id=tenant_id
    ).first()
    if not db_file:
        raise HTTPException(404, "Fichier introuvable")

    filepath = (
        db_file.url
        if os.path.isabs(db_file.url) and os.path.exists(db_file.url)
        else os.path.join(settings.GCS_MODELS_PATH, os.path.basename(db_file.url))
    )
    if os.path.exists(filepath):
        os.remove(filepath)

    db.delete(db_file)
    db.commit()


# ══════════════════════════════════════════════════════════════════════════════
# LIENS FICHIER ↔ SOUS-CATÉGORIE
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/file-subcategory", response_model=FileSubcategoryLinkResponse)
async def link_file_to_subcategory(  
    tenant_id: UUID,
    payload: FileSubcategoryLinkCreate,
    db: Session = Depends(get_db),
):
    subcat = db.query(TenantSubcategoryConfig).filter_by(
        id=payload.subcategory_id, tenant_id=tenant_id
    ).first()
    if not subcat:
        raise HTTPException(404, "Sous-catégorie introuvable")

    existing = db.query(TenantFileSubcategory).filter_by(
        file_id=payload.file_id, subcategory_id=payload.subcategory_id
    ).first()
    if existing:
        raise HTTPException(409, "Lien déjà existant")

    max_priority = db.query(func.max(TenantFileSubcategory.priority)).filter_by(
    subcategory_id=payload.subcategory_id
    ).scalar()

    next_priority = (max_priority + 1) if max_priority is not None else 0

    link = TenantFileSubcategory(
        id=uuid4(),
        file_id=payload.file_id,
        subcategory_id=payload.subcategory_id,
        priority=next_priority,
        is_active=payload.is_active
    )
    db.add(link)
    db.commit()
    db.refresh(link)


    asyncio.create_task(_rebuild_index_after_link(    
        tenant_id=str(tenant_id),
        agent_type=subcat.agent_type,
        sub_category=subcat.sub_category,
        file_id=str(payload.file_id)
    ))

    return link

async def _rebuild_index_after_link(tenant_id: str, agent_type: str, sub_category: str, file_id: str = None):
    from orchestrator.rag.rag_engine import RAGEngine
    from core.database import get_pool
    engine = RAGEngine(tenant_id=tenant_id, agent_type=agent_type)
    await engine.build_index(force_refresh=True, sub_category=sub_category)
    
    if file_id:
        pool = await get_pool()
        await pool.execute(
            """
            UPDATE tenant_files
            SET embedding_status = 'completed', updated_at = NOW()
            WHERE file_id = $1
            """,
            uuid.UUID(file_id)
        )
    print(f"[Files] ✅ Index RAG reconstruit pour {sub_category}")


@router.delete("/file-subcategory/{link_id}", status_code=204)
async def unlink_file(tenant_id: UUID, link_id: UUID, db: Session = Depends(get_db)):
    link = db.query(TenantFileSubcategory).filter_by(id=link_id).first()
    if not link:
        raise HTTPException(404, "Lien introuvable")

    file_id = link.file_id  
    db.delete(link)
    db.commit()

    remaining = db.query(TenantFileSubcategory).filter_by(
        file_id=file_id, is_active=True
    ).count()

    if remaining == 0:
        pool = await get_pool()
        await pool.execute(
            """
            UPDATE tenant_files
            SET embedding_status = 'chunks_ready', updated_at = NOW()
            WHERE file_id = $1
            """,
            file_id
        )


@router.get("/subcategories/{subcategory_id}/files")
def get_files_for_subcategory(
    tenant_id: UUID,
    subcategory_id: UUID,
    db: Session = Depends(get_db),
):
    subcat = db.query(TenantSubcategoryConfig).filter_by(
        id=subcategory_id, tenant_id=tenant_id
    ).first()
    if not subcat:
        raise HTTPException(404, "Sous-catégorie introuvable")
    return subcat.rag_files


# ══════════════════════════════════════════════════════════════════════════════
# INDEX RAG
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/subcategories/{sub_category}/rebuild-index")
async def rebuild_subcategory_index(
    tenant_id: UUID,
    sub_category: str,
    force: bool = True,
    db: Session = Depends(get_db),
):
    from orchestrator.rag.rag_engine import RAGEngine

    subcat = db.query(TenantSubcategoryConfig).filter_by(
        sub_category=sub_category, tenant_id=tenant_id
    ).first()
    if not subcat:
        raise HTTPException(404, "Sous-catégorie non trouvée")

    engine = RAGEngine(tenant_id=str(tenant_id), agent_type=subcat.agent_type)
    await engine.build_index(force_refresh=force, sub_category=sub_category)

    return {"message": f"Index reconstruit pour {sub_category}"}

@router.get("/subcategories/{subcategory_id}/links", response_model=list[FileSubcategoryLinkResponse])
def get_links_for_subcategory(
    tenant_id: UUID,
    subcategory_id: UUID,
    db: Session = Depends(get_db),
):
    """Retourne tous les liens fichier ↔ sous-catégorie avec leurs IDs."""
    subcat = db.query(TenantSubcategoryConfig).filter_by(
        id=subcategory_id, tenant_id=tenant_id
    ).first()
    if not subcat:
        raise HTTPException(404, "Sous-catégorie introuvable")

    links = db.query(TenantFileSubcategory).filter_by(
        subcategory_id=subcategory_id,
        is_active=True
    ).all()
    
    return [
        FileSubcategoryLinkResponse(
            id=link.id,
            file_id=link.file_id,
            subcategory_id=link.subcategory_id,
            priority=link.priority,
            is_active=link.is_active,
            created_at=link.created_at,
            file_name=link.file.name if link.file else None,
            sub_category=subcat.sub_category
        )
        for link in links
    ]