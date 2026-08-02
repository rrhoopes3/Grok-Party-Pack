FROM python:3.11-slim

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy code
COPY . /app/

# Upgrade pip and install dependencies with protobuf fix
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir protobuf==4.24.4 && \
    pip install --no-cache-dir \
        flask \
        python-dotenv \
        xai-sdk \
        requests \
        pyyaml \
        chromadb \
        solana \
        aiohttp \
        pydantic \
        && echo "Dependencies installed with protobuf fix"

# Install as editable package so 'import forge' works reliably
RUN pip install -e .

# Fix Python path for package imports
ENV PYTHONPATH=/app
ENV FLASK_APP=app.py
ENV FLASK_ENV=development

EXPOSE 5000

# Run with proper path
CMD ["python", "-m", "app"]
