# DIALOGIX Makefile
# Automate common development and documentation tasks

.PHONY: help install dev docs serve-docs build-docs clean test lint format

# Default target
help: ## Show this help message
	@echo "DIALOGIX - Development Commands"
	@echo "================================"
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

# Installation and Setup
install: ## Install all dependencies (backend + frontend + docs)
	@echo "Installing backend dependencies..."
	pip install -r requirements.txt
	@echo "Installing frontend dependencies..."
	cd frontend && npm install
	@echo "Installing documentation dependencies..."
	pip install -r docs/requirements.txt
	@echo "✅ All dependencies installed!"

dev: ## Start development servers (backend + frontend)
	@echo "Starting development servers..."
	@echo "Backend: http://localhost:8000"
	@echo "Frontend: http://localhost:3000"
	@echo "Docs: Run 'make serve-docs' in another terminal"
	@echo ""
	@echo "Press Ctrl+C to stop all servers"
	@trap 'kill 0' EXIT; \
	python manage.py runserver & \
	cd frontend && npm start & \
	wait

dev-docker: ## Start all services with Docker (includes docs)
	@echo "🐳 Starting DIALOGIX with Docker..."
	@echo "Application: http://localhost:3000"
	@echo "Documentation: http://localhost:3000/docs/"
	@echo "Backend API: http://localhost:3000/api/"
	@echo ""
	docker-compose up --build

# Documentation Commands
docs: install-docs serve-docs ## Install docs dependencies and serve locally

install-docs: ## Install documentation dependencies
	pip install -r docs/requirements.txt

serve-docs: ## Serve documentation locally with live reload
	@echo "📚 Starting documentation server..."
	@echo "Documentation: http://localhost:8001"
	mkdocs serve -a localhost:8001

build-docs: ## Build documentation for production
	@echo "🔨 Building documentation..."
	mkdocs build --clean --strict
	@echo "✅ Documentation built to ./site/"

deploy-docs: build-docs ## Deploy documentation to GitHub Pages
	@echo "🚀 Deploying documentation..."
	mkdocs gh-deploy --clean --message "Deploy documentation [skip ci]"

# Development Tools
clean: ## Clean build artifacts and cache files
	@echo "🧹 Cleaning build artifacts..."
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	rm -rf build/
	rm -rf dist/
	rm -rf site/
	cd frontend && rm -rf build/ node_modules/.cache/
	@echo "✅ Cleaned!"

test: ## Run all tests
	@echo "🧪 Running tests..."
	python manage.py test
	cd frontend && npm test -- --watchAll=false
	@echo "✅ All tests passed!"

lint: ## Run linting on all code
	@echo "🔍 Running linters..."
	# Python linting
	flake8 --max-line-length=88 --exclude=venv,migrations .
	# JavaScript linting
	cd frontend && npm run lint
	@echo "✅ Linting completed!"

format: ## Format all code
	@echo "💅 Formatting code..."
	# Python formatting
	black --line-length=88 .
	isort .
	# JavaScript formatting
	cd frontend && npm run format
	@echo "✅ Code formatted!"

# Database Commands
migrate: ## Run database migrations
	python manage.py makemigrations
	python manage.py migrate

reset-db: ## Reset database (⚠️  DESTRUCTIVE)
	@echo "⚠️  This will delete all data!"
	@read -p "Are you sure? [y/N] " -n 1 -r; \
	echo; \
	if [[ $$REPLY =~ ^[Yy]$$ ]]; then \
		python manage.py flush --noinput; \
		python manage.py migrate; \
		echo "✅ Database reset!"; \
	fi

# Production Commands
build: ## Build for production
	@echo "🏗️  Building for production..."
	# Build frontend
	cd frontend && npm run build
	# Collect static files
	python manage.py collectstatic --noinput
	@echo "✅ Production build complete!"

# Docker Commands (if using Docker)
docker-build: ## Build Docker containers
	docker-compose build

docker-up: ## Start Docker containers
	docker-compose up -d

docker-down: ## Stop Docker containers
	docker-compose down

docker-logs: ## View Docker logs
	docker-compose logs -f

# Quick start for new developers
quickstart: ## Quick setup for new developers
	@echo "🚀 DIALOGIX Quick Start"
	@echo "======================="
	@echo ""
	@echo "1. Setting up Python virtual environment..."
	python -m venv venv
	@echo "   Activate with: source venv/bin/activate (Linux/Mac) or venv\\Scripts\\activate (Windows)"
	@echo ""
	@echo "2. Run 'make install' after activating venv"
	@echo "3. Set up your .env file with database and API keys"
	@echo "4. Run 'make migrate' to set up the database"
	@echo "5. Run 'make dev' to start development servers"
	@echo ""
	@echo "📚 Documentation: make serve-docs"
	@echo "🧪 Tests: make test"
	@echo "❓ Help: make help"

# Version and release
version: ## Show current version information
	@echo "DIALOGIX Version Information"
	@echo "============================"
	@python -c "import sys; print(f'Python: {sys.version}')"
	@python -c "import django; print(f'Django: {django.VERSION}')"
	@cd frontend && node -v | sed 's/v/Node.js: /'
	@cd frontend && npm -v | sed 's/^/npm: /'
	@echo "Documentation: MkDocs with Material Theme"

# Development status
status: ## Show development environment status
	@echo "DIALOGIX Development Status"
	@echo "=========================="
	@echo -n "Backend (Django): "
	@curl -s http://localhost:8000/admin/ > /dev/null && echo "✅ Running" || echo "❌ Not running"
	@echo -n "Frontend (React): "
	@curl -s http://localhost:3000 > /dev/null && echo "✅ Running" || echo "❌ Not running"  
	@echo -n "Documentation: "
	@curl -s http://localhost:8001 > /dev/null && echo "✅ Running" || echo "❌ Not running"