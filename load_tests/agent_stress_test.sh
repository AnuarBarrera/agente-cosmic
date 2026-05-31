#!/bin/bash
# Prueba de estrés progresiva: endpoint del agente
# Requiere: wrk y ab (apache2-utils)

HOST="${1:-http://localhost:8000}"
WEBHOOK="$HOST/api/v1/agent/webhook/"

echo "🚀 PRUEBA DE ESTRÉS PROGRESIVA — Agente"
echo "Endpoint: $WEBHOOK"
echo "=========================================="

# Cuerpo de prueba (simula mensaje de Telegram)
PAYLOAD='/tmp/agent_test_payload.json'
cat > "$PAYLOAD" << 'EOF'
{
  "update_id": 1,
  "message": {
    "message_id": 1,
    "from": {"id": 999999, "first_name": "Test", "username": "testuser"},
    "chat": {"id": 999999, "type": "private"},
    "text": "hola"
  }
}
EOF

for CONCURRENCY in 5 10 20; do
    echo ""
    echo "🔥 Concurrencia: $CONCURRENCY usuarios"
    echo "Recursos antes:"
    docker stats --no-stream chatbot-backend-1 2>/dev/null | tail -1

    ab -n 50 -c "$CONCURRENCY" -T 'application/json' -p "$PAYLOAD" "$WEBHOOK" \
       2>&1 | grep -E "Requests per second|Time per request|Failed"

    echo "Recursos después:"
    docker stats --no-stream chatbot-backend-1 2>/dev/null | tail -1
    sleep 3
done

rm -f "$PAYLOAD"
echo ""
echo "✅ Prueba completada"
