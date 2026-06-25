#!/bin/bash

# =============================================================================
# AGENTE COSMIC — Prueba de carga simple con Apache Bench
# Target: nginx en localhost:3002
# Uso: bash load_tests/simple_load_test.sh
# =============================================================================

BASE="http://localhost:3002"
BACKEND="agente-cosmic-backend-1"
NGINX="agente-cosmic-nginx-1"

echo "INICIANDO PRUEBA DE CARGA SIMPLE"
echo "Target: $BASE"
echo "==========================================================="

# Prueba 1: Health check
echo ""
echo "PRUEBA 1: Health check (/health/)"
echo "100 peticiones, 10 concurrentes"
ab -n 100 -c 10 "$BASE/health/" 2>/dev/null \
  | grep -E "Requests per second|Time per request \(mean\)|Failed requests"

# Prueba 2: Landing page
echo ""
echo "PRUEBA 2: Landing page (/)"
echo "200 peticiones, 20 concurrentes"
ab -n 200 -c 20 "$BASE/" 2>/dev/null \
  | grep -E "Requests per second|Time per request \(mean\)|Failed requests"

# Prueba 3: Login page
echo ""
echo "PRUEBA 3: Login (/auth/login/)"
echo "200 peticiones, 20 concurrentes"
ab -n 200 -c 20 "$BASE/auth/login/" 2>/dev/null \
  | grep -E "Requests per second|Time per request \(mean\)|Failed requests"

# Estado de contenedores
echo ""
echo "ESTADO DE CONTENEDORES:"
docker stats --no-stream "$BACKEND" "$NGINX" 2>/dev/null

echo ""
echo "Prueba completada."
