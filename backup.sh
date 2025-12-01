#!/bin/bash
# =============================================================================
# JARVIS Backup Script
# =============================================================================
# Usage: ./backup.sh
# Description: Creates a compressed backup of db/ and reports/ directories
# =============================================================================

set -e  # Exit on any error

echo "=============================================="
echo "💾 JARVIS Backup Script"
echo "=============================================="
echo ""

# Get script directory (works even if called from another location)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "📁 Working directory: $SCRIPT_DIR"
echo ""

# Generate backup filename with timestamp
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILENAME="backup_${TIMESTAMP}.tar.gz"
BACKUP_DIR="./backups"

# Create backups directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

echo "📅 Timestamp: $TIMESTAMP"
echo "📦 Backup filename: $BACKUP_FILENAME"
echo ""

# Check if directories exist
DIRS_TO_BACKUP=""

if [ -d "./db" ]; then
    DIRS_TO_BACKUP="$DIRS_TO_BACKUP ./db"
    echo "✅ Found: ./db"
else
    echo "⚠️  Warning: ./db directory not found (skipping)"
fi

if [ -d "./reports" ]; then
    DIRS_TO_BACKUP="$DIRS_TO_BACKUP ./reports"
    echo "✅ Found: ./reports"
else
    echo "⚠️  Warning: ./reports directory not found (skipping)"
fi

echo ""

if [ -z "$DIRS_TO_BACKUP" ]; then
    echo "❌ Error: No directories to backup!"
    exit 1
fi

# Create compressed tarball
echo "🗜️  Creating compressed backup..."
tar -czvf "${BACKUP_DIR}/${BACKUP_FILENAME}" $DIRS_TO_BACKUP
echo ""

# Show backup file info
BACKUP_SIZE=$(du -h "${BACKUP_DIR}/${BACKUP_FILENAME}" | cut -f1)
echo "✅ Backup created successfully!"
echo "   📁 Location: ${BACKUP_DIR}/${BACKUP_FILENAME}"
echo "   📊 Size: $BACKUP_SIZE"
echo ""

# =============================================================================
# TODO: Add logic to move backup to external storage
# =============================================================================
# 
# Option 1: Copy to NAS via rsync
# rsync -avz "${BACKUP_DIR}/${BACKUP_FILENAME}" user@nas-server:/backup/jarvis/
#
# Option 2: Copy to NAS via scp
# scp "${BACKUP_DIR}/${BACKUP_FILENAME}" user@nas-server:/backup/jarvis/
#
# Option 3: Upload to AWS S3
# aws s3 cp "${BACKUP_DIR}/${BACKUP_FILENAME}" s3://your-bucket/jarvis-backups/
#
# Option 4: Upload to Google Cloud Storage
# gsutil cp "${BACKUP_DIR}/${BACKUP_FILENAME}" gs://your-bucket/jarvis-backups/
#
# Option 5: Mount NFS and copy
# cp "${BACKUP_DIR}/${BACKUP_FILENAME}" /mnt/nas/jarvis-backups/
#
# =============================================================================

# Optional: Clean up old local backups (keep last 7 days)
echo "🧹 Cleaning up old backups (keeping last 7 days)..."
find "$BACKUP_DIR" -name "backup_*.tar.gz" -type f -mtime +7 -delete 2>/dev/null || true
echo ""

# List remaining backups
echo "📋 Current backups:"
ls -lh "$BACKUP_DIR"/*.tar.gz 2>/dev/null || echo "   (no backups found)"
echo ""

echo "=============================================="
echo "✅ JARVIS Backup Complete!"
echo "=============================================="

