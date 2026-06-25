#!/bin/bash

# =============================================================================
# AGENTE COSMIC — Prueba de estrés en endpoints autenticados
# Objetivo: medir latencia en API de status y métricas bajo carga
# Uso: bash load_tests/agent_stress_test.sh [job_uuid]
# Ejemplo: bash load_tests/agent_stress_test.sh 6eff29ff-52bb-45fa-9ce0-03441cc956a4
# =============================================================================

BASE="${1:-http://localhost:3002}"
JOB_UUID="${2:-6eff29ff-52bb-45fa-9ce0-03441cc956a4}"  # UUID de job existente

BACKEND="agente-cosmic-backend-1"

echo "PRUEBA DE ESTRES — ENDPOINTS API"
echo "Base: $BASE"
echo "Job UUID: $JOB_UUID"
echo "============================================"

run_api_test() {
    local requests=$1
    local concurrency=$2
    local endpoint=$3
    local label=$4

    echo ""
    echo "--- $label ---"
    echo "Peticiones: $requests | Concurrencia: $concurrency"

    ab -n "$requests" -c "$concurrency" "$BASE$endpoint" 2>/dev/null \
      | grep -E "Complete requests|Failed requests|Requests per second|Time per request \(mean\b"

    docker stats --no-stream "$BACKEND" 2>/dev/null \
      | awk 'NR>1 {printf "  CPU: %s  MEM: %s\n", $3, $4}'
    sleep 3
}

# Endpoint público: métricas Prometheus
echo ""
echo "--- METRICAS PROMETHEUS (/metrics) ---"
ab -n 200 -c 20 "$BASE/metrics" 2>/dev/null \
  | grep -E "Requests per second|Failed requests|Time per request \(mean\b"

# Endpoint API: status de job (requiere auth — mide comportamiento con 403/redirect)
run_api_test 300 30 "/api/brand-dna/status/$JOB_UUID/" "API STATUS — sin auth (mide overhead de auth check)"

# Endpoint público: registro
run_api_test 200 20 "/auth/register/"  "REGISTRO — carga moderada"

# Endpoint público: forgot password
run_api_test 200 20 "/auth/forgot-password/" "FORGOT PASSWORD — carga moderada"

echo ""
echo "============================================"
echo "Estado del worker RQ:"
docker logs agente-cosmic-rqworker-1 --tail 5 2>&1

echo ""
echo "Prueba completada."
