#!/bin/sh

# Apply database migrations
echo "Applying database migrations..."
python manage.py migrate

# Collect static files
echo "Collecting static files..."
python manage.py collectstatic --noinput

# Start Gunicorn server
echo "Starting Gunicorn..."
gunicorn saas_chatbot.wsgi:application --bind 0.0.0.0:8000 --timeout 300 --workers=1 --log-level debug
