#!/bin/bash
# Prueba de carga: endpoint /health/
# Requiere: wrk (brew install wrk / apt install wrk)

HOST="${1:-http://localhost:8000}"
ENDPOINT="$HOST/health/"

echo "🔥 PRUEBA DE CARGA — Health Check"
echo "Endpoint: $ENDPOINT"
echo "Duración: 30s | Hilos: 4 | Conexiones: 20"
echo "============================================"

wrk -t4 -c20 -d30s "$ENDPOINT"
