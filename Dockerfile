# Use an official Python runtime as a parent image
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set work directory
WORKDIR /app

# Install dependencies for system packages if needed
# e.g., postgresql-client to use pg_dump or psql
RUN apt-get update && apt-get install -y postgresql-client ffmpeg && rm -rf /var/lib/apt/lists/*

# Playwright system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libdbus-1-3 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libasound2 libpango-1.0-0 libcairo2 libatspi2.0-0 \
    libx11-6 libxcb1 libxext6 wget && \
    rm -rf /var/lib/apt/lists/*

# Copy only the requirements file to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt && \
    python -m playwright install chromium

# Copy the rest of the application code
COPY . .

# Expose port 8000
EXPOSE 8000

# Run entrypoint script
# Using an entrypoint allows running migrations before starting the server
ENTRYPOINT ["/app/entrypoint.sh"]
# CMD ["gunicorn", "saas_chatbot.wsgi:application", "--bind", "0.0.0.0:8000"]
