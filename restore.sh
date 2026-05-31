#!/bin/bash

# =============================================================================
# DIALOGIX - Script de Restauración desde Backup
# =============================================================================
# Este script restaura backups de la base de datos, configuración,
# Redis y archivos media de Dialogix
#
# Uso: ./restore.sh [TIMESTAMP]
# Ejemplo: ./restore.sh 20251207_140645
# Si no se proporciona timestamp, se usa el backup más reciente
# =============================================================================

set -e  # Salir si hay algún error

# Colores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # Sin color

# Configuración
BACKUP_DIR="./backups"
DB_CONTAINER="chatbot-db-1"
REDIS_CONTAINER="chatbot-redis-1"

# Credenciales de base de datos (del archivo .env)
if [ -f .env ]; then
    source .env
else
    echo -e "${RED}Error: Archivo .env no encontrado${NC}"
    exit 1
fi

# Determinar el timestamp a restaurar
if [ -z "$1" ]; then
    echo -e "${YELLOW}No se especificó timestamp, buscando backup más reciente...${NC}"
    TIMESTAMP=$(ls -t "$BACKUP_DIR"/database_*.sql.gz 2>/dev/null | head -1 | sed 's/.*database_\(.*\)\.sql\.gz/\1/')
    if [ -z "$TIMESTAMP" ]; then
        echo -e "${RED}Error: No se encontraron backups en $BACKUP_DIR${NC}"
        exit 1
    fi
    echo -e "${GREEN}Usando backup: $TIMESTAMP${NC}"
else
    TIMESTAMP="$1"
    echo -e "${BLUE}Restaurando backup: $TIMESTAMP${NC}"
fi

echo ""
echo -e "${YELLOW}=== Iniciando restauración de Dialogix ===${NC}"
echo ""

# Verificar que existan los archivos de backup
DB_BACKUP="$BACKUP_DIR/database_${TIMESTAMP}.sql.gz"
CONFIG_BACKUP="$BACKUP_DIR/config_${TIMESTAMP}.tar.gz"
REDIS_BACKUP="$BACKUP_DIR/redis_${TIMESTAMP}.rdb"
MEDIA_BACKUP="$BACKUP_DIR/media_${TIMESTAMP}.tar.gz"

if [ ! -f "$DB_BACKUP" ]; then
    echo -e "${RED}Error: No se encontró el backup de base de datos: $DB_BACKUP${NC}"
    exit 1
fi

# =============================================================================
# Confirmación del usuario
# =============================================================================
echo -e "${RED}ADVERTENCIA: Esta operación sobrescribirá los datos actuales.${NC}"
echo -e "${YELLOW}Archivos a restaurar:${NC}"
[ -f "$DB_BACKUP" ] && echo "  ✓ Base de datos: $DB_BACKUP"
[ -f "$CONFIG_BACKUP" ] && echo "  ✓ Configuración: $CONFIG_BACKUP"
[ -f "$REDIS_BACKUP" ] && echo "  ✓ Redis: $REDIS_BACKUP"
[ -f "$MEDIA_BACKUP" ] && echo "  ✓ Media: $MEDIA_BACKUP"
echo ""
read -p "¿Deseas continuar con la restauración? (s/N): " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Ss]$ ]]; then
    echo -e "${YELLOW}Restauración cancelada${NC}"
    exit 0
fi

# =============================================================================
# 1. Restaurar Base de Datos
# =============================================================================
echo -e "${YELLOW}[1/4] Restaurando base de datos PostgreSQL...${NC}"

# Verificar que PostgreSQL esté corriendo
if ! docker ps | grep -q $DB_CONTAINER; then
    echo -e "${RED}Error: El contenedor de PostgreSQL no está corriendo${NC}"
    echo -e "${YELLOW}Ejecuta: docker compose up -d db${NC}"
    exit 1
fi

# Descomprimir y restaurar
gunzip -c "$DB_BACKUP" > /tmp/restore_db.sql
docker exec -i $DB_CONTAINER psql -U $DB_USER -d $DB_NAME < /tmp/restore_db.sql >/dev/null 2>&1
rm /tmp/restore_db.sql
echo -e "${GREEN}✓ Base de datos restaurada${NC}"
echo ""

# =============================================================================
# 2. Restaurar Configuración
# =============================================================================
if [ -f "$CONFIG_BACKUP" ]; then
    echo -e "${YELLOW}[2/4] Restaurando configuración...${NC}"
    tar -xzf "$CONFIG_BACKUP" -C . 2>/dev/null || true
    echo -e "${GREEN}✓ Configuración restaurada${NC}"
else
    echo -e "${YELLOW}[2/4] ⚠ No se encontró backup de configuración${NC}"
fi
echo ""

# =============================================================================
# 3. Restaurar Redis
# =============================================================================
if [ -f "$REDIS_BACKUP" ]; then
    echo -e "${YELLOW}[3/4] Restaurando datos de Redis...${NC}"

    # Verificar que Redis esté corriendo
    if docker ps | grep -q $REDIS_CONTAINER; then
        # Detener Redis temporalmente
        docker compose stop redis
        # Copiar el archivo RDB
        docker cp "$REDIS_BACKUP" $REDIS_CONTAINER:/data/dump.rdb
        # Reiniciar Redis
        docker compose start redis
        sleep 2
        echo -e "${GREEN}✓ Redis restaurado${NC}"
    else
        echo -e "${YELLOW}⚠ Redis no está corriendo, omitiendo restauración${NC}"
    fi
else
    echo -e "${YELLOW}[3/4] ⚠ No se encontró backup de Redis${NC}"
fi
echo ""

# =============================================================================
# 4. Restaurar archivos Media
# =============================================================================
if [ -f "$MEDIA_BACKUP" ] && [ -s "$MEDIA_BACKUP" ]; then
    echo -e "${YELLOW}[4/4] Restaurando archivos media...${NC}"
    tar -xzf "$MEDIA_BACKUP" -C . 2>/dev/null || true
    echo -e "${GREEN}✓ Media restaurada${NC}"
else
    echo -e "${YELLOW}[4/4] ⚠ No se encontró backup de media${NC}"
fi
echo ""

# =============================================================================
# Reiniciar servicios
# =============================================================================
echo -e "${YELLOW}Reiniciando servicios...${NC}"
docker compose restart backend rqworker 2>/dev/null || true
sleep 3
echo -e "${GREEN}✓ Servicios reiniciados${NC}"
echo ""

# =============================================================================
# Resumen
# =============================================================================
echo -e "${GREEN}=== Restauración completada exitosamente ===${NC}"
echo ""
echo "Backup restaurado: $TIMESTAMP"
echo "Ubicación de backups: $BACKUP_DIR"
echo ""
echo -e "${BLUE}Dialogix está listo para usar en http://localhost:3001${NC}"
echo ""
