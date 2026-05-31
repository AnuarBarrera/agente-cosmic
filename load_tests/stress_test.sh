#!/bin/bash

# =============================================================================
# DIALOGIX - Stress Test Progresivo
# =============================================================================
# Prueba gradual de estrés para encontrar los límites del sistema

echo "🚀 INICIANDO PRUEBA DE ESTRÉS PROGRESIVA"
echo "=========================================="

# Función para ejecutar prueba y monitorear recursos
run_stress_test() {
    local requests=$1
    local concurrency=$2
    local endpoint=$3
    local test_name=$4

    echo ""
    echo "🔥 $test_name"
    echo "Peticiones: $requests, Concurrencia: $concurrency"
    echo "Endpoint: $endpoint"
    echo "----------------------------------------"

    # Monitorear recursos antes
    echo "Recursos ANTES:"
    docker stats --no-stream dialogix-backend-prod dialogix-nginx-prod | tail -2

    # Ejecutar prueba
    echo ""
    echo "Ejecutando prueba..."
    ab -n $requests -c $concurrency $endpoint 2>/dev/null | grep -E 'Complete requests|Failed requests|Requests per second|Time per request|Transfer rate'

    # Monitorear recursos después
    echo ""
    echo "Recursos DESPUÉS:"
    docker stats --no-stream dialogix-backend-prod dialogix-nginx-prod | tail -2

    echo "💤 Pausa de 10 segundos..."
    sleep 10
}

# Prueba 1: Carga ligera
run_stress_test 100 5 "http://localhost:8080/" "PRUEBA 1: Carga Ligera - Documentación"

# Prueba 2: Carga moderada
run_stress_test 500 15 "http://localhost:8080/" "PRUEBA 2: Carga Moderada"

# Prueba 3: Carga alta
run_stress_test 1000 25 "http://localhost:8080/" "PRUEBA 3: Carga Alta"

# Prueba 4: Carga extrema
run_stress_test 2000 50 "http://localhost:8080/" "PRUEBA 4: Carga Extrema"

# Resumen final del sistema
echo ""
echo "📈 RESUMEN FINAL DEL SISTEMA"
echo "============================"

echo ""
echo "Uso de CPU y memoria:"
top -bn1 | grep -E "Cpu|MiB Mem" | head -2

echo ""
echo "Estado de contenedores:"
docker ps --format "table {{.Names}}\t{{.Status}}"

echo ""
echo "Logs de errores del backend (últimas 5 líneas):"
docker logs dialogix-backend-prod --tail 5

echo ""
echo "✅ PRUEBA DE ESTRÉS COMPLETADA"