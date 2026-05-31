# Scripts de DIALOGIX

Scripts útiles para el proyecto DIALOGIX organizados por categoría.

## 🧪 Testing (`/testing`)

### `test_final.sh`
Script optimizado para ejecutar todos los tests unitarios del proyecto.
```bash
./scripts/testing/test_final.sh
```

## 🔐 Security (`/security`)

### `test_security_features_fixed.py`
Tests específicos para validar las funcionalidades de seguridad.
```bash
docker compose exec backend python scripts/security/test_security_features_fixed.py
```

## 🚀 Production (`/`)

### `deploy.sh`
Script automatizado para deployment en AWS EC2.
```bash
./scripts/deploy.sh
```

### `backup.sh`
Script automatizado para backups de base de datos, media y configuraciones.
```bash
./scripts/backup.sh
```

## 📋 Uso

| Script | Entorno | Descripción |
|--------|---------|-------------|
| `test_final.sh` | Desarrollo | Ejecuta suite completa de tests |
| `test_security_features_fixed.py` | Desarrollo | Valida funciones de seguridad |
| `deploy.sh` | Producción | Deployment automatizado a EC2 |
| `backup.sh` | Producción | Backup automático de datos |
