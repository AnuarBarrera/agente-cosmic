#!/bin/bash

# =============================================================================
# DIALOGIX - Simple Load Test with Apache Bench
# =============================================================================
# Prueba usando ab directamente contra nginx

echo "🔥 INICIANDO PRUEBA DE CARGA SIMPLE CON APACHE BENCH"
echo "Target: nginx container (puerto 80)"
echo "==========================================================="

# Test 1: Health check interno en nginx
echo ""
echo "📊 PRUEBA 1: Health check interno"
echo "100 peticiones, 10 concurrentes"

docker exec dialogix-nginx-prod sh -c "
echo '🔗 Testing /health/ endpoint...'
ab -n 100 -c 10 http://localhost/health/ 2>/dev/null | grep -E 'Requests per second|Time per request|Transfer rate|Failed requests'
"

echo ""
echo "📊 PRUEBA 2: Página de documentación (puerto 8080)"
echo "50 peticiones, 5 concurrentes"

ab -n 50 -c 5 http://localhost:8080/ 2>/dev/null | grep -E 'Requests per second|Time per request|Transfer rate|Failed requests'

echo ""
echo "📊 PRUEBA 3: Monitoreo de recursos durante carga"
echo "Estado de contenedores:"
docker stats --no-stream dialogix-backend-prod dialogix-nginx-prod dialogix-db-prod dialogix-redis-prod

echo ""
echo "✅ Pruebas completadas"