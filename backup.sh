#!/bin/bash

# =============================================================================
# DIALOGIX - Script de Respaldo Automatizado
# =============================================================================
# Este script crea backups completos de la base de datos, configuración,
# Redis y archivos media de Dialogix
#
# Uso: ./backup.sh
# =============================================================================

set -e  # Salir si hay algún error

# Colores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # Sin color

# Configuración
BACKUP_DIR="./backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
DB_CONTAINER="chatbot-db-1"
REDIS_CONTAINER="chatbot-redis-1"

# Credenciales de base de datos (del archivo .env)
if [ -f .env ]; then
    source .env
else
    echo -e "${RED}Error: Archivo .env no encontrado${NC}"
    exit 1
fi

# Crear directorio de backups si no existe
mkdir -p "$BACKUP_DIR"

echo -e "${GREEN}=== Iniciando respaldo de Dialogix ===${NC}"
echo -e "${YELLOW}Timestamp: $TIMESTAMP${NC}"
echo ""

# =============================================================================
# 1. Backup de Base de Datos PostgreSQL
# =============================================================================
echo -e "${YELLOW}[1/4] Respaldando base de datos PostgreSQL...${NC}"
docker exec $DB_CONTAINER pg_dump -U $DB_USER -d $DB_NAME | gzip > "$BACKUP_DIR/database_${TIMESTAMP}.sql.gz"
DB_SIZE=$(du -h "$BACKUP_DIR/database_${TIMESTAMP}.sql.gz" | cut -f1)
echo -e "${GREEN}✓ Base de datos respaldada (${DB_SIZE})${NC}"
echo ""

# =============================================================================
# 2. Backup de Configuración
# =============================================================================
echo -e "${YELLOW}[2/4] Respaldando archivos de configuración...${NC}"
tar -czf "$BACKUP_DIR/config_${TIMESTAMP}.tar.gz" \
    --exclude='*.pyc' \
    --exclude='__pycache__' \
    --exclude='node_modules' \
    .env .env.local .env.prod 2>/dev/null || true

# Añadir configuraciones de nginx y SSL si existen
if [ -d "nginx" ]; then
    tar -rzf "$BACKUP_DIR/config_${TIMESTAMP}.tar.gz" nginx/ 2>/dev/null || true
fi
if [ -d "ssl" ]; then
    tar -rzf "$BACKUP_DIR/config_${TIMESTAMP}.tar.gz" ssl/ 2>/dev/null || true
fi

CONFIG_SIZE=$(du -h "$BACKUP_DIR/config_${TIMESTAMP}.tar.gz" | cut -f1)
echo -e "${GREEN}✓ Configuración respaldada (${CONFIG_SIZE})${NC}"
echo ""

# =============================================================================
# 3. Backup de Redis
# =============================================================================
echo -e "${YELLOW}[3/4] Respaldando datos de Redis...${NC}"
# Forzar save de Redis
docker exec $REDIS_CONTAINER redis-cli BGSAVE >/dev/null 2>&1 || true
sleep 2
# Copiar el archivo RDB
docker cp $REDIS_CONTAINER:/data/dump.rdb "$BACKUP_DIR/redis_${TIMESTAMP}.rdb" 2>/dev/null || echo "No Redis data to backup"
if [ -f "$BACKUP_DIR/redis_${TIMESTAMP}.rdb" ]; then
    REDIS_SIZE=$(du -h "$BACKUP_DIR/redis_${TIMESTAMP}.rdb" | cut -f1)
    echo -e "${GREEN}✓ Redis respaldado (${REDIS_SIZE})${NC}"
else
    echo -e "${YELLOW}⚠ No se encontraron datos de Redis${NC}"
fi
echo ""

# =============================================================================
# 4. Backup de archivos Media
# =============================================================================
echo -e "${YELLOW}[4/4] Respaldando archivos media...${NC}"
if [ -d "media" ]; then
    tar -czf "$BACKUP_DIR/media_${TIMESTAMP}.tar.gz" media/ 2>/dev/null || true
    MEDIA_SIZE=$(du -h "$BACKUP_DIR/media_${TIMESTAMP}.tar.gz" | cut -f1)
    echo -e "${GREEN}✓ Media respaldada (${MEDIA_SIZE})${NC}"
else
    # Crear archivo vacío para mantener consistencia
    touch "$BACKUP_DIR/media_${TIMESTAMP}.tar.gz"
    echo -e "${YELLOW}⚠ No se encontró directorio media${NC}"
fi
echo ""

# =============================================================================
# Limpieza de backups antiguos (opcional)
# Mantener solo los últimos 7 backups
# =============================================================================
echo -e "${YELLOW}Limpiando backups antiguos (manteniendo los últimos 7)...${NC}"
cd "$BACKUP_DIR"
ls -t database_*.sql.gz 2>/dev/null | tail -n +8 | xargs -r rm -f
ls -t config_*.tar.gz 2>/dev/null | tail -n +8 | xargs -r rm -f
ls -t redis_*.rdb 2>/dev/null | tail -n +8 | xargs -r rm -f
ls -t media_*.tar.gz 2>/dev/null | tail -n +8 | xargs -r rm -f
cd - >/dev/null

# =============================================================================
# Resumen
# =============================================================================
echo -e "${GREEN}=== Respaldo completado exitosamente ===${NC}"
echo ""
echo "Archivos generados:"
echo "  • database_${TIMESTAMP}.sql.gz"
echo "  • config_${TIMESTAMP}.tar.gz"
echo "  • redis_${TIMESTAMP}.rdb"
echo "  • media_${TIMESTAMP}.tar.gz"
echo ""
echo "Ubicación: $BACKUP_DIR"
echo ""

# Mostrar espacio total usado por backups
TOTAL_SIZE=$(du -sh "$BACKUP_DIR" | cut -f1)
echo -e "${YELLOW}Espacio total usado por backups: ${TOTAL_SIZE}${NC}"
echo ""

# Opcional: Crear un archivo de verificación
echo "Backup creado el $(date)" > "$BACKUP_DIR/last_backup.txt"
echo "Timestamp: $TIMESTAMP" >> "$BACKUP_DIR/last_backup.txt"

echo -e "${GREEN}✓ Script de respaldo finalizado${NC}"
