#!/bin/bash

# Pre-deployment script for Provost Home Design
# Run this before deploying to production

echo "🚀 Pre-deployment checks..."

# Check if .env file exists
if [ ! -f .env ]; then
    echo "❌ ERROR: .env file not found!"
    echo "   Create .env from .env.example and configure it."
    exit 1
fi

# Check if DEBUG is False
if grep -q "DEBUG=True" .env; then
    echo "⚠️  WARNING: DEBUG=True in .env"
    echo "   Set DEBUG=False for production!"
    exit 1
fi

# Check if SECRET_KEY is set
if grep -q "dev-insecure" .env; then
    echo "❌ ERROR: Using development SECRET_KEY!"
    echo "   Generate a new SECRET_KEY for production."
    exit 1
fi

echo "✅ Environment checks passed"

# Create logs directory
echo "📁 Creating logs directory..."
mkdir -p logs

# Collect static files
echo "📦 Collecting static files..."
python manage.py collectstatic --noinput

# Run migrations
echo "🗄️  Running migrations..."
python manage.py migrate --noinput

# Check for issues
echo "🔍 Running Django system checks..."
python manage.py check --deploy

echo "✅ Pre-deployment complete!"
echo ""
echo "📋 Manual checklist:"
echo "   - Database configured and accessible?"
echo "   - Email (Microsoft Graph) credentials valid?"
echo "   - reCAPTCHA keys configured?"
echo "   - ALLOWED_HOSTS set correctly?"
echo "   - SSL certificate configured?"
echo "   - Media files storage configured?"
echo ""
