#!/bin/bash

# =============================================================================
# DIALOGIX - Health Check Load Test
# =============================================================================
# Prueba básica de carga para el endpoint de health check

HOST="https://dialogix.anuarbarrera.dev"
ENDPOINT="$HOST/health/"

echo "🔥 INICIANDO PRUEBA DE CARGA - HEALTH CHECK"
echo "Endpoint: $ENDPOINT"
echo "Duración: 30 segundos"
echo "Conexiones concurrentes: 10"
echo "============================================"

wrk -t4 -c10 -d30s "$ENDPOINT"

echo "✅ Prueba completada"