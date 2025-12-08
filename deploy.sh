#!/bin/bash
# =============================================================================
# JARVIS Deploy Script (Initial Deployment)
# =============================================================================
# Usage: ./deploy.sh
# Description: Installs Docker, sets up environment, and starts JARVIS services
# Run this script on a fresh VPS server
# =============================================================================

set -e  # Exit on any error

echo "=============================================="
echo "🚀 JARVIS Initial Deployment Script"
echo "=============================================="
echo ""

# Get script directory (works even if called from another location)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "📁 Working directory: $SCRIPT_DIR"
echo ""

# =============================================================================
# Step 1: Check and Install Docker
# =============================================================================
echo "🐳 Step 1/5: Checking Docker installation..."

if command -v docker &> /dev/null; then
    echo "✅ Docker is already installed: $(docker --version)"
else
    echo "📦 Installing Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
    rm get-docker.sh
    echo "✅ Docker installed: $(docker --version)"
fi
echo ""

# =============================================================================
# Step 2: Check and Install Docker Compose
# =============================================================================
echo "🐳 Step 2/5: Checking Docker Compose..."

if docker compose version &> /dev/null; then
    echo "✅ Docker Compose is available: $(docker compose version --short)"
else
    echo "📦 Installing Docker Compose plugin..."
    apt-get update
    apt-get install -y docker-compose-plugin
    echo "✅ Docker Compose installed: $(docker compose version --short)"
fi
echo ""

# =============================================================================
# Step 3: Check .env file
# =============================================================================
echo "🔐 Step 3/5: Checking environment configuration..."

if [ -f ".env" ]; then
    echo "✅ .env file found"
else
    echo "⚠️  .env file not found!"
    echo ""
    echo "📝 Creating template .env file..."
    cat > .env << 'EOF'
# =============================================================================
# JARVIS Environment Configuration
# =============================================================================
# IMPORTANT: Fill in your API keys before running docker compose!

# OpenAI API Key (Required)
OPENAI_API_KEY=sk-your-openai-api-key-here

# Optional: Add other environment variables below
# DATABASE_URL=sqlite:////app/db/jarvis.db
# QDRANT_HOST=qdrant
# QDRANT_PORT=6333

EOF
    echo "✅ Template .env file created"
    echo ""
    echo "❗ IMPORTANT: Please edit .env file and add your API keys!"
    echo "   Run: nano .env"
    echo ""
    read -p "Press Enter after you've edited .env file, or Ctrl+C to exit..."
fi
echo ""

# =============================================================================
# Step 4: Create necessary directories
# =============================================================================
echo "📁 Step 4/5: Creating necessary directories..."

mkdir -p ./db
mkdir -p ./reports
mkdir -p ./qdrant_data
mkdir -p ./backups

echo "✅ Directories created: db/, reports/, qdrant_data/, backups/"
echo ""

# =============================================================================
# Step 5: Build and Start Containers
# =============================================================================
echo "🔨 Step 5/5: Building and starting containers..."

docker compose up -d --build

echo ""
echo "=============================================="
echo "✅ JARVIS Deployment Complete!"
echo "=============================================="
echo ""

# Get server IP
SERVER_IP=$(curl -s ifconfig.me 2>/dev/null || echo "your-server-ip")

echo "📊 Container Status:"
docker compose ps
echo ""
echo "🌐 Access your JARVIS instance at:"
echo "   - API: http://${SERVER_IP}:8000"
echo "   - API Docs: http://${SERVER_IP}:8000/docs"
echo "   - Qdrant Dashboard: http://${SERVER_IP}:6333/dashboard"
echo ""
echo "📝 Useful commands:"
echo "   - View logs: docker compose logs -f"
echo "   - Stop services: docker compose down"
echo "   - Update code: ./update.sh"
echo "   - Backup data: ./backup.sh"
echo ""

