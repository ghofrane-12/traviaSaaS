#!/bin/bash
set -e

echo "🚀 Démarrage du conteneur travel-ai..."

# 1. Redis
redis-server --daemonize yes --maxmemory 512mb --maxmemory-policy allkeys-lru --save ""
until redis-cli ping | grep -q PONG; do sleep 1; done
echo "✅ Redis prêt"

# 2. FastAPI (modèles montés via volume GCS)
echo "🌐 Démarrage FastAPI..."
exec uvicorn main:app \
     --host 0.0.0.0 \
     --port "${PORT:-8080}" \
     --workers 1 \
     --loop uvloop \
     --timeout-keep-alive 75 \
     --log-level info