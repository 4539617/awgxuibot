# ============================================
# Stage 1: Node.js Base (AWGBot)
# ============================================
FROM node:18-alpine AS awg-base

WORKDIR /app

# Install Docker CLI and curl (needed for AWG management)
RUN apk add --no-cache docker-cli curl

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm install --omit=dev

# Copy application files
COPY src/ ./src/

# Create output directory
RUN mkdir -p /app/output

# Set environment variables
ENV NODE_ENV=production

# ============================================
# Stage 2: Python Base (XUIBot)
# ============================================
FROM python:3.10-slim AS xuibot-base

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy Python application files
COPY python/ ./python/

# Create directories for logs and data
RUN mkdir -p /app/logs /app/data

# ============================================
# Stage 3: AWGBot Runtime
# ============================================
FROM awg-base AS awgbot

# Run the bot
CMD ["node", "src/index.js"]

# ============================================
# Stage 4: XUIBot Runtime
# ============================================
FROM xuibot-base AS xuibot

# Run the bot
CMD ["python", "-u", "python/bot.py"]
