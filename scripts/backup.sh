#!/bin/bash

# =============================================================================
# DIALOGIX - Script de Backup Automatizado
# =============================================================================

set -e

# Configuration
BACKUP_DIR="/home/$(whoami)/chatbot/backups"
DATE=$(date +%Y%m%d_%H%M%S)
DOCKER_COMPOSE_FILE="docker-compose.prod.yml"
RETENTION_DAYS=30

# Load environment variables
source .env.prod

echo "🗄️  Iniciando backup de DIALOGIX - $DATE"

# Create backup directory
mkdir -p $BACKUP_DIR

# Database backup
echo "📊 Respaldando base de datos PostgreSQL..."
docker compose -f $DOCKER_COMPOSE_FILE exec -T db pg_dump \
    -U $POSTGRES_USER \
    -d $POSTGRES_DB \
    --clean \
    --if-exists \
    > $BACKUP_DIR/database_$DATE.sql

gzip $BACKUP_DIR/database_$DATE.sql

# Media files backup
echo "📁 Respaldando archivos media..."
docker run --rm \
    -v dialogix_media_data_prod:/data \
    -v $BACKUP_DIR:/backup \
    alpine tar czf /backup/media_$DATE.tar.gz -C /data .

# Configuration backup
echo "⚙️  Respaldando configuraciones..."
tar czf $BACKUP_DIR/config_$DATE.tar.gz \
    .env.prod \
    nginx/prod/ \
    ssl/ \
    docker-compose.prod.yml

# Redis backup (opcional)
echo "🔴 Respaldando Redis..."
docker compose -f $DOCKER_COMPOSE_FILE exec -T redis redis-cli --rdb /data/dump_$DATE.rdb
docker run --rm \
    -v dialogix_redis_data_prod:/data \
    -v $BACKUP_DIR:/backup \
    alpine cp /data/dump_$DATE.rdb /backup/redis_$DATE.rdb

# Clean old backups
echo "🧹 Limpiando backups antiguos (>$RETENTION_DAYS días)..."
find $BACKUP_DIR -name "*_*.sql.gz" -mtime +$RETENTION_DAYS -delete
find $BACKUP_DIR -name "*_*.tar.gz" -mtime +$RETENTION_DAYS -delete
find $BACKUP_DIR -name "*_*.rdb" -mtime +$RETENTION_DAYS -delete

# Send backup to RDS
if [ "$RDS_BACKUP_ENABLED" = "true" ]; then
    echo "🗃️  Enviando backup a RDS..."

    # Install psql if not available
    if ! command -v psql &> /dev/null; then
        echo "📦 Instalando PostgreSQL client..."
        sudo apt-get update && sudo apt-get install -y postgresql-client
    fi

    # Create backup database in RDS with timestamp
    BACKUP_DB_NAME="backup_${DATE}"

    echo "📄 Creando base de datos de backup: $BACKUP_DB_NAME"
    PGPASSWORD=$RDS_PASSWORD createdb -h $RDS_HOST -p $RDS_PORT -U $RDS_USER $BACKUP_DB_NAME

    echo "📤 Restaurando backup en RDS..."
    gunzip -c $BACKUP_DIR/database_$DATE.sql.gz | PGPASSWORD=$RDS_PASSWORD psql -h $RDS_HOST -p $RDS_PORT -U $RDS_USER -d $BACKUP_DB_NAME

    echo "🧹 Limpiando backups antiguos en RDS (>$RETENTION_DAYS días)..."
    # Get list of backup databases older than retention period
    OLD_DATE=$(date -d "$RETENTION_DAYS days ago" +%Y%m%d)
    PGPASSWORD=$RDS_PASSWORD psql -h $RDS_HOST -p $RDS_PORT -U $RDS_USER -d $RDS_DB -t -c "
        SELECT datname FROM pg_database
        WHERE datname LIKE 'backup_%'
        AND datname < 'backup_${OLD_DATE}_000000';" | while read db; do
        if [ ! -z "$db" ]; then
            db=$(echo $db | xargs)  # trim whitespace
            echo "🗑️  Eliminando backup antiguo: $db"
            PGPASSWORD=$RDS_PASSWORD dropdb -h $RDS_HOST -p $RDS_PORT -U $RDS_USER $db
        fi
    done

    echo "✅ Backup enviado a RDS: $BACKUP_DB_NAME"
fi

echo "✅ Backup completado: $BACKUP_DIR/*_$DATE.*"