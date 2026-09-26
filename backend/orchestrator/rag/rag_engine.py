# rag_engine.py
import os
import asyncio
import hashlib
import json
import csv
import io
import re
import tempfile
import uuid
from typing import Optional, List, Dict, Any
from rank_bm25 import BM25Okapi
from langchain_community.vectorstores import FAISS
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from core.database import get_pool
import os
from core.config import settings

SCORE_THRESHOLD = 2.0
BM25_THRESHOLD  = 0.1
MIN_CONFIDENCE  = 0.01
RRF_K           = 30
FAISS_DIR = os.getenv("FAISS_DIR", "faiss_index")


ALLOWED_FILE_TYPES = {"pdf", "json", "txt", "xml", "csv"}


_embeddings_instance = None

def get_embeddings():
    global _embeddings_instance
    if _embeddings_instance is None:
        print("[RAG] 🔄 Chargement all-MiniLM-L6-v2...")
        _embeddings_instance = HuggingFaceEmbeddings(
            model_name="sentence-transformers/all-MiniLM-L6-v2",
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True}
        )
        print("[RAG] ✅ all-MiniLM-L6-v2 chargé")
    return _embeddings_instance
class RAGEngine:
    """Moteur RAG générique utilisable par tous les agents"""

    def __init__(self, tenant_id: str, agent_type: str, user_id: str = "", session_id: str = ""):
        self.tenant_id    = tenant_id
        self.agent_type   = agent_type
        self.user_id      = user_id
        self.session_id   = session_id
        self.vectorstore  = None
        self.bm25         = None
        self.bm25_docs    = []
        self._pending_failed_docs = []

        self.splitter_index   = RecursiveCharacterTextSplitter(chunk_size=400,  chunk_overlap=80)
        self.splitter_context = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)



    def _is_local_path(self, url: str) -> bool:
        """
        Détermine si l'URL est un fichier local (pas une URL HTTP).
        Gère : chemin absolu Windows, chemin absolu Linux, nom relatif.
        """
        if url.startswith(('http://', 'https://')):
            return False
        return True





    def _get_content_from_url(self, url: str, binary: bool = False):
        """
        Résout le chemin du fichier selon l'environnement.
        Stratégie :
        1. Chemin absolu existant → utiliser tel quel (rétro-compat)
        2. Nom relatif → résoudre depuis GCS_MODELS_PATH
        3. Chemin absolu Windows sur Linux → extraire le nom et résoudre
        """
        file_path = url.replace('file://', '')

        if os.path.isabs(file_path) and os.path.exists(file_path):
            pass  

        elif not os.path.isabs(file_path):
            file_path = os.path.join(settings.GCS_MODELS_PATH, file_path)

        else:
            filename = os.path.basename(file_path)
            resolved = os.path.join(settings.GCS_MODELS_PATH, filename)
            if os.path.exists(resolved):
                file_path = resolved
            else:
                raise FileNotFoundError(
                    f"Fichier introuvable: {file_path}\n"
                    f"Essayé aussi: {resolved}\n"
                    f"GCS_MODELS_PATH={settings.GCS_MODELS_PATH}"
                )

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Fichier introuvable: {file_path}")

        mode     = 'rb' if binary else 'r'
        encoding = None if binary else 'utf-8'
        with open(file_path, mode, encoding=encoding) as f:
            return f.read()
    async def _fetch_remote(self, url: str, binary: bool = False):
        """Pour les URLs HTTP — vrai I/O async, ne bloque pas l'event loop."""
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=15),
                headers={"User-Agent": "Mozilla/5.0"}
            ) as r:
                r.raise_for_status()
                return await r.read() if binary else await r.text()

    def _make_failed_doc(self, url: str, name: str, file_type: str) -> Document:
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
        return Document(
            page_content="",
            metadata={"source": url, "name": name, "type": file_type, "hash": url_hash, "failed": True}
        )

    def _normalize_text(self, text: str) -> str:
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'[^\w\s\.,;:!?\-\'\"\(\)]', ' ', text)
        return text.strip()

    def _tokenize(self, text: str) -> list:
        text   = text.lower()
        text   = re.sub(r'[^\w\s]', ' ', text)
        tokens = text.split()
        return [t for t in tokens if len(t) > 2]



    async def _extract_pdf(self, url: str, name: str) -> List[Document]:
        try:
            if self._is_local_path(url):
                content = self._get_content_from_url(url, binary=True)
            else:
                content = await self._fetch_remote(url, binary=True)
                
            if not content or content[:4] != b"%PDF":
                print(f"[RAG] ⚠ {name} n'est pas un PDF valide")
                return []
                
            tmp = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4()}.pdf")
            try:
                with open(tmp, "wb") as f:
                    f.write(content)
                pages = PyPDFLoader(tmp).load()
                pages = [p for p in pages if len(p.page_content.strip()) > 100]
                return self.splitter_index.split_documents(pages) if pages else []
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
        except Exception as e:
            print(f"[RAG] ⚠ PDF {name}: {e}")
            return []

    async def _extract_json(self, url: str, name: str) -> List[Document]:
        try:
            if self._is_local_path(url):
                content = self._get_content_from_url(url, binary=False) 
            else:
                content = await self._fetch_remote(url, binary=False)
            data    = json.loads(content)
            lines   = []
            if isinstance(data, dict):
                for key, value in data.items():
                    if isinstance(value, list):
                        for item in value[:30]:
                            if isinstance(item, dict):
                                lines.append(f"{key}: " + ", ".join(f"{k}={v}" for k, v in item.items() if v))
                    else:
                        lines.append(f"{key}: {value}")
            elif isinstance(data, list):
                for item in data[:50]:
                    if isinstance(item, dict):
                        lines.append(", ".join(f"{k}={v}" for k, v in item.items() if v))
            text = self._normalize_text("\n".join(lines))
            if len(text) < 50:
                return []
            chunks = self.splitter_index.split_text(text)
            return [Document(page_content=c) for c in chunks]
        except Exception as e:
            print(f"[RAG] ⚠ JSON {name}: {e}")
            return []

    async def _extract_csv(self, url: str, name: str) -> List[Document]:
        try:
            if self._is_local_path(url):
                content = self._get_content_from_url(url, binary=False)  
            else:
                content = await self._fetch_remote(url, binary=False)    
            reader  = csv.DictReader(io.StringIO(content))
            rows    = list(reader)[:100]
            lines   = []
            for row in rows:
                first_col  = list(row.keys())[0] if row else ""
                identifier = row.get(first_col, row.get('destination', row.get('Ville', 'item')))
                details    = ", ".join(f"{k}={v}" for k, v in row.items() if v)
                lines.append(f"{identifier}: {details}")
            text = self._normalize_text("\n".join(lines))
            if len(text) < 50:
                return []
            chunks = self.splitter_index.split_text(text)
            return [Document(page_content=c) for c in chunks]
        except Exception as e:
            print(f"[RAG] ⚠ CSV {name}: {e}")
            return []

    async def _extract_txt(self, url: str, name: str) -> List[Document]:
        try:
            if self._is_local_path(url):
                content = self._get_content_from_url(url, binary=False) 
            else:
                content = await self._fetch_remote(url, binary=False)    
            text    = self._normalize_text(content)
            if len(text) < 100:
                return []
            chunks = self.splitter_index.split_text(text)
            return [Document(page_content=c) for c in chunks]
        except Exception as e:
            print(f"[RAG] ⚠ TXT {name}: {e}")
            return []

    async def _extract_xml(self, url: str, name: str) -> List[Document]:
        try:
            if self._is_local_path(url):
                content = self._get_content_from_url(url, binary=False) 
            else:
                content = await self._fetch_remote(url, binary=False)    
            text    = re.sub(r'<[^>]+>', ' ', content)
            text    = self._normalize_text(text)
            if len(text) < 100:
                return []
            chunks = self.splitter_index.split_text(text)
            return [Document(page_content=c) for c in chunks]
        except Exception as e:
            print(f"[RAG] ⚠ XML {name}: {e}")
            return []

    async def _extract_text(self, url: str, file_type: str, name: str, file_id: str = None) -> List[Document]:
        ft = file_type.lower().strip()
        if ft not in ALLOWED_FILE_TYPES:
            return [self._make_failed_doc(url, name, file_type)]

        url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]

        for attempt in range(3):
            try:
                if ft == "pdf":
                    chunks = await self._extract_pdf(url, name)
                elif ft == "json":
                    chunks = await self._extract_json(url, name)
                elif ft == "csv":
                    chunks = await self._extract_csv(url, name)
                elif ft == "txt":
                    chunks = await self._extract_txt(url, name)
                elif ft == "xml":
                    chunks = await self._extract_xml(url, name)
                else:
                    return [self._make_failed_doc(url, name, file_type)]

                valid_chunks = [c for c in chunks if c.page_content.strip()]

                if not valid_chunks:
                    return [self._make_failed_doc(url, name, file_type)]

                for c in valid_chunks:
                    c.metadata.update({
                        "source":   url,
                        "name":     name,
                        "type":     ft,
                        "hash":     url_hash,
                        "file_id": file_id,
                    })
                print(f"[RAG] ✅ {name} — {len(valid_chunks)} chunks")
                return valid_chunks

            except Exception as e:
                if attempt == 2:
                    print(f"[RAG] ❌ {name}: {e}")
                    return [self._make_failed_doc(url, name, file_type)]

        return [self._make_failed_doc(url, name, file_type)]


    async def load_docs_from_db(self, existing_hashes: set = None, sub_category: str = None) -> List[Document]:
        valid_docs, failed_docs = [], []
        existing_hashes = existing_hashes or set()
        self._pending_failed_docs = []

        try:
            pool = await get_pool()
            
            if sub_category:
                rows = await pool.fetch(
                    """
                    SELECT tf.name, tf.file_type, tf.url, tf.file_id
                    FROM tenant_files tf
                    INNER JOIN tenant_file_subcategory tfs ON tf.file_id = tfs.file_id
                    INNER JOIN tenant_subcategory_config tsc ON tfs.subcategory_id = tsc.id
                    WHERE tf.tenant_id = $1
                    AND tf.agent_type = $2
                    AND tsc.sub_category = $3
                    AND tsc.response_type = 'rag'
                    AND tfs.is_active = TRUE
                    AND tf.embedding_status IN ('completed', 'chunks_ready')
                    ORDER BY tfs.priority ASC, tf.created_at DESC
                    LIMIT 50
                    """,
                    uuid.UUID(self.tenant_id), self.agent_type, sub_category
                )
                print(f"[RAG] 📚 {len(rows)} fichiers pour sous-catégorie '{sub_category}'")
            else:
                rows = await pool.fetch(
                    """
                    SELECT name, file_type, url, file_id
                    FROM tenant_files
                    WHERE tenant_id = $1
                    AND agent_type = $2
                    AND embedding_status = 'completed'
                    ORDER BY created_at DESC
                    LIMIT 50
                    """,
                    uuid.UUID(self.tenant_id), self.agent_type
                )
                print(f"[RAG] 📚 {len(rows)} fichiers (tous)")

            for row in rows:
                url, ft, name, file_id = row["url"], row["file_type"], row["name"], str(row["file_id"])
                url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
                if url_hash in existing_hashes:
                    print(f"[RAG] ⏭ {name} déjà indexé")
                    continue

                chunks = await self._extract_text(url, ft, name, file_id)
                for c in chunks:
                    (failed_docs if c.metadata.get("failed") else valid_docs).append(c)

        except Exception as e:
            print(f"[RAG] ❌ Erreur DB: {e}")

        self._pending_failed_docs = failed_docs
        return valid_docs

    def _build_bm25(self, docs: List[Document]):
        valid = [d for d in docs if not d.metadata.get("failed") and d.page_content.strip()]
        if not valid:
            return
        self.bm25_docs = valid
        self.bm25 = BM25Okapi([self._tokenize(d.page_content) for d in valid])
        print(f"[RAG] ✅ BM25 ({len(valid)} docs)")

    def _get_existing_hashes(self) -> set:
        if not self.vectorstore:
            return set()
        return {
            hashlib.sha256(doc.metadata["source"].encode()).hexdigest()[:16]
            for doc in self.vectorstore.docstore._dict.values()
            if doc.metadata.get("source")
        }

    async def build_index(self, force_refresh: bool = False, sub_category: str = None):
        """Construit l'index RAG, optionnellement pour une sous-catégorie spécifique"""
        faiss_path = os.path.join(FAISS_DIR, self.tenant_id, self.agent_type)
        if sub_category:
            clean_name = re.sub(r'[^a-zA-Z0-9_]', '_', sub_category)
            faiss_path = os.path.join(FAISS_DIR, self.tenant_id, self.agent_type, clean_name)
        
        embeddings = get_embeddings()
        self._pending_failed_docs = []

        if os.path.exists(faiss_path) and not force_refresh:
            try:
                self.vectorstore = FAISS.load_local(faiss_path, embeddings, allow_dangerous_deserialization=True)
                print(f"[RAG] ✅ FAISS chargé")
                self._build_bm25(list(self.vectorstore.docstore._dict.values()))

                new_valid = await self.load_docs_from_db(self._get_existing_hashes(), sub_category)
                if new_valid or self._pending_failed_docs:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, lambda: self.vectorstore.add_documents(new_valid))
                    self.vectorstore.save_local(faiss_path)
                    self._build_bm25(list(self.vectorstore.docstore._dict.values()))
                return
            except Exception as e:
                print(f"[RAG] ⚠ Erreur disque: {e}")

        valid_docs = await self.load_docs_from_db(sub_category=sub_category)
        if not valid_docs:
            print("[RAG] ⚠ Aucun document valide")
            self.vectorstore = None
            return

        all_docs = valid_docs
        loop = asyncio.get_running_loop()
        self.vectorstore = await loop.run_in_executor(None, lambda: FAISS.from_documents(all_docs, embeddings))
        os.makedirs(faiss_path, exist_ok=True)
        self.vectorstore.save_local(faiss_path)
        self._build_bm25(valid_docs)
        print(f"[RAG] ✅ FAISS construit ({len(valid_docs)} valides)")



    def search(self, query: str, k: int = 5,filter_by_loc: list = None) -> Optional[Dict]:
        if not self.vectorstore:
            return None

        search_k = min(k * 3, 30)

        dense = self.vectorstore.similarity_search_with_score(query, k=search_k)
        dense_docs = []
        for doc, score in dense:
            print(f"[RAG:debug] score={score:.4f} | {doc.page_content[:50]}")

            if score > SCORE_THRESHOLD:
                continue
            if doc.metadata.get("failed"):
                continue
            if filter_by_loc:
                content_lower = doc.page_content.lower()
                if not any(loc.lower() in content_lower for loc in filter_by_loc):
                    continue
            dense_docs.append((doc, score))

        sparse_docs = []
        if self.bm25 and self.bm25_docs:
            tokens = self._tokenize(query)
            scores = self.bm25.get_scores(tokens)
            top_idx = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:search_k]
            for i in top_idx:
                if scores[i] < BM25_THRESHOLD:
                    continue
                doc = self.bm25_docs[i]
                if filter_by_loc:
                    content_lower = doc.page_content.lower()
                    if not any(loc.lower() in content_lower for loc in filter_by_loc):
                        continue
                sparse_docs.append((doc, scores[i]))


        if not dense_docs and not sparse_docs:
            return None

        rrf: dict = {}
        for rank, (doc, _) in enumerate(dense_docs):
            key = hashlib.md5(doc.page_content.encode()).hexdigest()
            rrf.setdefault(key, {"doc": doc, "score": 0.0})
            rrf[key]["score"] += 1 / (RRF_K + rank + 1)

        for rank, (doc, _) in enumerate(sparse_docs):
            key = hashlib.md5(doc.page_content.encode()).hexdigest()
            rrf.setdefault(key, {"doc": doc, "score": 0.0})
            rrf[key]["score"] += 1 / (RRF_K + rank + 1)

        best = max(item["score"] for item in rrf.values())
        if best < MIN_CONFIDENCE:
            return None

        merged = sorted(rrf.values(), key=lambda x: x["score"], reverse=True)

        seen, top_chunks = set(), []
        for item in merged:
            doc = item["doc"]
            key = hashlib.md5(doc.page_content.encode()).hexdigest()
            if key not in seen:
                seen.add(key)
                top_chunks.append(doc)
            if len(top_chunks) >= k:
                break

        if not top_chunks:
            return None

        context, sources = [], []
        for doc in top_chunks:
            src  = doc.metadata.get("source", "")
            name = doc.metadata.get("name", os.path.basename(src) if src else "source")
            context.append(f"[Source: {name}]\n{doc.page_content.strip()}")
            if name not in sources:
                sources.append(name)

        return {
            "context":      "\n\n".join(context),
            "confidence":   best,
            "sources":      sources,
            "chunks_count": len(top_chunks),
        }
    async def search_with_subcategory(self, query: str, sub_category: str, k: int = 5, filter_by_loc: list = None) -> Optional[Dict]:
        if not self.vectorstore:  
            await self.build_index(sub_category=sub_category)
        return self.search(query, k, filter_by_loc)