#!/bin/bash

# =============================================================================
# DIALOGIX - Local Health Check Load Test (Sin Cloudflare)
# =============================================================================
# Prueba básica de carga para el endpoint de health check directamente

# Obtener la IP local del contenedor backend
BACKEND_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' dialogix-backend-prod 2>/dev/null)

if [ -z "$BACKEND_IP" ]; then
    echo "❌ Error: No se pudo obtener la IP del contenedor backend"
    exit 1
fi

ENDPOINT="http://$BACKEND_IP:8000/health/"

echo "🔥 INICIANDO PRUEBA DE CARGA LOCAL - HEALTH CHECK"
echo "Endpoint: $ENDPOINT"
echo "Backend IP: $BACKEND_IP"
echo "Duración: 30 segundos"
echo "Conexiones concurrentes: 10"
echo "============================================"

wrk -t4 -c10 -d30s "$ENDPOINT"

echo "✅ Prueba completada"