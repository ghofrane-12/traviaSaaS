# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — Builder : installe les dépendances Python dans un venv isolé
# ─────────────────────────────────────────────────────────────────────────────
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Dépendances système minimales
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 \
    python3.11-dev \
    python3.11-venv \
    python3-pip \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Créer un virtualenv propre
RUN python3.11 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Upgrader pip
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# ── Installer PyTorch avec support CUDA 12.1 en premier (gros package) ────────
RUN pip install --no-cache-dir \
    torch==2.4.1 \
    torchvision==0.19.1 \
    --index-url https://download.pytorch.org/whl/cu121

# ── Installer le reste des dépendances ────────────────────────────────────────
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — Runtime : image finale légère
# ─────────────────────────────────────────────────────────────────────────────
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04 AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8080 \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    MODELS_DIR=/app/trained_models \
    FAISS_DIR=/app/faiss_index \
    TRANSFORMERS_NO_ADVISORY_WARNINGS=1 \
    HF_HUB_DISABLE_TELEMETRY=1 
# ── Runtime système : Python + Redis + gsutil (Google Cloud SDK) ──────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 \
    libgomp1 \
    libsndfile1 \
    # Redis server
    redis-server \
    # Prérequis gsutil
    curl \
    gnupg \
    apt-transport-https \
    ca-certificates \
    && echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] \
       https://packages.cloud.google.com/apt cloud-sdk main" \
       > /etc/apt/sources.list.d/google-cloud-sdk.list \
    && curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
       | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg \
    && apt-get update && apt-get install -y --no-install-recommends \
       google-cloud-cli \
    && rm -rf /var/lib/apt/lists/*

# Copier le virtualenv depuis le stage builder
COPY --from=builder /opt/venv /opt/venv

# Répertoire de travail
WORKDIR /app

# Copier le code source
# ⚠️  Le .dockerignore doit exclure : venv/, trained_models/, faiss_index/
COPY . .

# ── Copier et rendre exécutable le script de démarrage ────────────────────────
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

# ── Créer les répertoires attendus par l'app ───────────────────────────────────
# trained_models et faiss_index seront remplis par start.sh depuis GCS
RUN mkdir -p /app/trained_models \
             /app/faiss_index \
             /app/files \
             /app/logs

# ── Utilisateur non-root pour la sécurité ─────────────────────────────────────
RUN groupadd --gid 1001 appgroup && \
    useradd --uid 1001 --gid 1001 --create-home appuser && \
    mkdir -p /home/appuser/.config/gcloud && \
    chown -R appuser:appgroup /app && \
    chown -R appuser:appgroup /home/appuser && \
    chown appuser:appgroup /var/run

USER appuser

ENV HOME=/home/appuser

# Port exposé
EXPOSE 8080

# ── Health check ───────────────────────────────────────────────────────────────
# start-period élevé car le démarrage inclut le téléchargement GCS (~3-5 min)
HEALTHCHECK --interval=30s --timeout=10s --start-period=300s --retries=5 \
    CMD python3.11 -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

# ── Démarrage via script ────────────────────────────────────────────────────────
CMD ["/app/start.sh"]