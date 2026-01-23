#!/bin/bash
# Script to fix migration state on production server
# Run this on the production server after pulling latest code

echo "Fixing migration state..."

# First, check which migrations Django thinks are applied
echo "Current migration status:"
python manage.py showmigrations billing

echo ""
echo "Marking existing migrations as applied (fake)..."

# Fake migrations 0002-0010 since tables already exist
python manage.py migrate billing 0002 --fake
python manage.py migrate billing 0003 --fake
python manage.py migrate billing 0004 --fake
python manage.py migrate billing 0005 --fake
python manage.py migrate billing 0006 --fake
python manage.py migrate billing 0007 --fake
python manage.py migrate billing 0008 --fake
python manage.py migrate billing 0009 --fake
python manage.py migrate billing 0010 --fake

echo ""
echo "Now applying new migrations (0011 and 0012)..."
# Apply the new migrations for real
python manage.py migrate billing

echo ""
echo "Final migration status:"
python manage.py showmigrations billing

echo ""
echo "Done! All migrations should now be in sync."
