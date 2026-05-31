# Comandos para probar la documentación

## Opción 1: Acceso directo al servicio de docs

```bash
# El servicio de docs está corriendo en el puerto interno
# Necesitas acceder mediante:

# 1. Acceder directamente al contenedor
docker exec -it chatbot-docs-1 /bin/bash
# Dentro del contenedor, el servidor está en localhost:8001

# 2. O exponer el puerto directamente:
# Modifica docker-compose.yml para añadir:
# ports:
#   - "8001:8001"
```

## Opción 2: Configurar puerto directo

Modificar docker-compose.yml para exponer docs directamente.