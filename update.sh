#!/bin/bash
# =============================================================================
# JARVIS Update Script
# =============================================================================
# Usage: ./update.sh
# Description: Pulls latest code, rebuilds containers, and restarts services
# =============================================================================

set -e  # Exit on any error

echo "=============================================="
echo "🚀 JARVIS Update Script"
echo "=============================================="
echo ""

# Get script directory (works even if called from another location)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "📁 Working directory: $SCRIPT_DIR"
echo ""

# Step 1: Pull latest code from git
echo "📥 Step 1/5: Pulling latest code from git..."
git pull
echo "✅ Git pull completed"
echo ""

# Step 2: Stop running containers
echo "🛑 Step 2/5: Stopping running containers..."
docker compose down
echo "✅ Containers stopped"
echo ""

# Step 3: Rebuild containers (ensures new dependencies are installed)
echo "🔨 Step 3/5: Rebuilding containers..."
docker compose build --no-cache
echo "✅ Build completed"
echo ""

# Step 4: Start containers in detached mode
echo "🚀 Step 4/5: Starting containers..."
docker compose up -d
echo "✅ Containers started"
echo ""

# Step 5: Clean up unused images
echo "🧹 Step 5/5: Cleaning up unused Docker images..."
docker image prune -f
echo "✅ Cleanup completed"
echo ""

echo "=============================================="
echo "✅ JARVIS Update Complete!"
echo "=============================================="
echo ""
echo "📊 Container Status:"
docker compose ps
echo ""
echo "📝 View logs with: docker compose logs -f backend"

