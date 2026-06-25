#!/bin/bash

# =============================================================================
# AGENTE COSMIC — Prueba de estrés progresiva
# Objetivo: encontrar el punto de quiebre y medir RPS/latencia por nivel
# Uso: bash load_tests/stress_test.sh
# =============================================================================

BASE="http://localhost:3002"
BACKEND="agente-cosmic-backend-1"
NGINX="agente-cosmic-nginx-1"

run_level() {
    local requests=$1
    local concurrency=$2
    local endpoint=$3
    local label=$4

    echo ""
    echo "--- $label ---"
    echo "Peticiones: $requests | Concurrencia: $concurrency | Endpoint: $endpoint"

    echo "Recursos ANTES:"
    docker stats --no-stream "$BACKEND" "$NGINX" 2>/dev/null \
      | awk 'NR>1 {printf "  %-30s CPU: %s  MEM: %s\n", $1, $3, $4}'

    ab -n "$requests" -c "$concurrency" "$BASE$endpoint" 2>/dev/null \
      | grep -E "Complete requests|Failed requests|Requests per second|Time per request \(mean\b|Transfer rate"

    echo "Recursos DESPUES:"
    docker stats --no-stream "$BACKEND" "$NGINX" 2>/dev/null \
      | awk 'NR>1 {printf "  %-30s CPU: %s  MEM: %s\n", $1, $3, $4}'

    echo "Pausa 5s..."
    sleep 5
}

echo "PRUEBA DE ESTRES PROGRESIVA — AGENTE COSMIC"
echo "============================================"

# Nivel 1: Carga ligera — baseline
run_level 100   5  "/health/"     "NIVEL 1 — Carga ligera (health)"
run_level 200  10  "/"            "NIVEL 1 — Carga ligera (landing)"

# Nivel 2: Carga moderada
run_level 500  20  "/health/"     "NIVEL 2 — Carga moderada (health)"
run_level 500  20  "/"            "NIVEL 2 — Carga moderada (landing)"
run_level 500  20  "/auth/login/" "NIVEL 2 — Carga moderada (login)"

# Nivel 3: Carga alta
run_level 1000 40  "/health/"     "NIVEL 3 — Carga alta (health)"
run_level 1000 40  "/"            "NIVEL 3 — Carga alta (landing)"

# Nivel 4: Carga extrema — buscar límite
run_level 2000 80  "/health/"     "NIVEL 4 — Carga extrema (health)"
run_level 1000 80  "/"            "NIVEL 4 — Carga extrema (landing)"

echo ""
echo "============================================"
echo "RESUMEN FINAL"
echo ""
echo "CPU y memoria del host:"
top -bn1 | grep -E "^%Cpu|^MiB Mem" | head -2

echo ""
echo "Estado de contenedores:"
docker ps --format "table {{.Names}}\t{{.Status}}" | grep agente-cosmic

echo ""
echo "Ultimos errores del backend:"
docker logs agente-cosmic-backend-1 --tail 10 2>&1 | grep -iE "error|exception|traceback" | tail -5

echo ""
echo "Prueba de estres completada."
