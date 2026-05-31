#!/bin/bash

# =============================================================================
# DIALOGIX - Script de Deployment Automatizado para AWS EC2
# =============================================================================

set -e  # Exit on any error

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
PROJECT_NAME="DIALOGIX"
DOCKER_COMPOSE_FILE="docker-compose.prod.yml"
ENV_FILE=".env.prod"

echo -e "${BLUE}🚀 Iniciando deployment de ${PROJECT_NAME}...${NC}"

# Function to print status
print_status() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
    exit 1
}

# Check if running as root
if [[ $EUID -eq 0 ]]; then
   print_error "No ejecutes este script como root"
fi

# Check if required files exist
echo -e "${BLUE}📋 Verificando archivos necesarios...${NC}"

if [ ! -f "$DOCKER_COMPOSE_FILE" ]; then
    print_error "No se encontró $DOCKER_COMPOSE_FILE"
fi

if [ ! -f "$ENV_FILE" ]; then
    print_error "No se encontró $ENV_FILE. Copia .env.prod.example a .env.prod y configúralo"
fi

print_status "Archivos necesarios encontrados"

# Check if Docker is installed
echo -e "${BLUE}🐳 Verificando Docker...${NC}"
if ! command -v docker &> /dev/null; then
    print_error "Docker no está instalado. Instálalo primero."
fi

if ! command -v docker compose &> /dev/null; then
    print_error "Docker Compose no está instalado. Instálalo primero."
fi

print_status "Docker y Docker Compose están instalados"

# Load environment variables
echo -e "${BLUE}🔧 Cargando variables de entorno...${NC}"
source $ENV_FILE
print_status "Variables de entorno cargadas"

# Create necessary directories
echo -e "${BLUE}📁 Creando directorios necesarios...${NC}"
mkdir -p ssl/nginx ssl/postgresql logs backups
print_status "Directorios creados"

# Stop existing containers
echo -e "${BLUE}🛑 Deteniendo contenedores existentes...${NC}"
docker compose -f $DOCKER_COMPOSE_FILE down --remove-orphans || true
print_status "Contenedores detenidos"

# Pull latest images
echo -e "${BLUE}📥 Descargando imágenes actualizadas...${NC}"
docker compose -f $DOCKER_COMPOSE_FILE pull

# Build custom images (optimized for instance stability)
echo -e "${BLUE}🔨 Construyendo imágenes personalizadas (modo optimizado)...${NC}"
print_warning "Usando build selectivo para evitar freeze en instancia"

# Build only services that need updates (prevents instance freeze)
docker compose -f $DOCKER_COMPOSE_FILE build autoscaler backend frontend

print_status "Imágenes construidas (build optimizado)"

# Generate SSL certificates if needed (Let's Encrypt)
if [ ! -f "ssl/nginx/fullchain.pem" ]; then
    echo -e "${BLUE}🔐 Generando certificados SSL...${NC}"
    print_warning "Asegúrate de que el DNS apunte a esta IP antes de continuar"
    read -p "¿Continuar con la generación de certificados SSL? (y/N): " -n 1 -r
    echo
    
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        # Install certbot if not installed
        if ! command -v certbot &> /dev/null; then
            sudo apt-get update
            sudo apt-get install -y certbot
        fi
        
        # Generate certificates
        sudo certbot certonly --standalone --non-interactive --agree-tos \
            --email admin@${ALLOWED_HOSTS##*,} \
            -d ${ALLOWED_HOSTS//,/ -d } \
            --cert-path ssl/nginx/fullchain.pem \
            --key-path ssl/nginx/privkey.pem
            
        print_status "Certificados SSL generados"
    else
        print_warning "Usando certificados self-signed para desarrollo"
        # Generate self-signed certificates
        openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
            -keyout ssl/nginx/privkey.pem \
            -out ssl/nginx/fullchain.pem \
            -subj "/C=US/ST=State/L=City/O=Organization/CN=localhost"
    fi
fi

# Update nginx config with actual domain
echo -e "${BLUE}🔧 Configurando nginx...${NC}"
sed -i "s/tu-dominio.com/${ALLOWED_HOSTS//,/ }/g" nginx/prod/conf.d/dialogix.conf
print_status "Nginx configurado"

# Start services
echo -e "${BLUE}🚀 Iniciando servicios...${NC}"
docker compose -f $DOCKER_COMPOSE_FILE up -d

# Wait for services to be healthy
echo -e "${BLUE}⏳ Esperando que los servicios estén listos...${NC}"
sleep 30

# Check if services are running
FAILED_SERVICES=()
for service in db redis backend nginx; do
    if ! docker compose -f $DOCKER_COMPOSE_FILE ps | grep -q "${service}.*Up"; then
        FAILED_SERVICES+=($service)
    fi
done

if [ ${#FAILED_SERVICES[@]} -ne 0 ]; then
    print_error "Los siguientes servicios fallaron: ${FAILED_SERVICES[*]}"
fi

print_status "Servicios iniciados correctamente"

# Note: Migrations and static files are handled automatically by container entrypoint
print_status "Migraciones y archivos estáticos manejados automáticamente por el contenedor"

# Create superuser if needed
echo -e "${BLUE}👤 Configurando superusuario...${NC}"
read -p "¿Crear superusuario? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    docker compose -f $DOCKER_COMPOSE_FILE exec backend python manage.py createsuperuser
fi

# Setup log rotation
echo -e "${BLUE}📋 Configurando rotación de logs...${NC}"
sudo tee /etc/logrotate.d/dialogix > /dev/null <<EOF
/home/$(whoami)/chatbot/logs/*.log {
    daily
    missingok
    rotate 52
    compress
    delaycompress
    notifempty
    create 644 $(whoami) $(whoami)
}
EOF

print_status "Rotación de logs configurada"

# Setup backup cron (optional)
echo -e "${BLUE}💾 Configurando backups automáticos...${NC}"
read -p "¿Configurar backups automáticos diarios? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    (crontab -l 2>/dev/null; echo "0 2 * * * /home/$(whoami)/chatbot/scripts/backup.sh") | crontab -
    print_status "Backup automático configurado (02:00 AM diario)"
fi

# Final health check
echo -e "${BLUE}🏥 Verificación final de salud...${NC}"
sleep 10

# Check if the application responds
if curl -f -s https://localhost/health/ > /dev/null 2>&1; then
    print_status "Aplicación respondiendo correctamente"
else
    print_warning "La aplicación puede necesitar unos minutos más para estar completamente lista"
fi

# Show status
echo -e "${BLUE}📊 Estado de los servicios:${NC}"
docker compose -f $DOCKER_COMPOSE_FILE ps

# Show useful information
echo -e "${GREEN}"
echo "============================================================================="
echo "🎉 ¡DEPLOYMENT COMPLETADO EXITOSAMENTE!"
echo "============================================================================="
echo -e "${NC}"

echo -e "${BLUE}📍 URLs importantes:${NC}"
echo "   • Aplicación: https://${ALLOWED_HOSTS%%,*}/"
echo "   • API: https://${ALLOWED_HOSTS%%,*}/api/"
echo "   • Admin: https://${ALLOWED_HOSTS%%,*}/admin/"
echo "   • Health Check: https://${ALLOWED_HOSTS%%,*}/health/"

echo -e "${BLUE}🔧 Comandos útiles:${NC}"
echo "   • Ver logs: docker compose -f $DOCKER_COMPOSE_FILE logs -f [servicio]"
echo "   • Reiniciar: docker compose -f $DOCKER_COMPOSE_FILE restart [servicio]"
echo "   • Build selectivo: docker compose -f $DOCKER_COMPOSE_FILE build autoscaler backend frontend"
echo "   • Actualizar: git pull && ./scripts/deploy.sh"
echo "   • Backup: ./scripts/backup.sh"

echo -e "${YELLOW}⚠️  Recordatorios importantes:${NC}"
echo "   • Configura el DNS para apuntar a esta IP"
echo "   • Revisa los logs regularmente"
echo "   • Mantén backups actualizados"
echo "   • Actualiza los certificados SSL cada 3 meses"

echo -e "${GREEN}✅ ¡Deployment completado! Tu aplicación DIALOGIX está lista para producción.${NC}"