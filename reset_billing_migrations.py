#!/usr/bin/env python
"""
Reset billing migrations in the database.

This script clears all billing migration records from django_migrations table
to resolve conflicts between database state and filesystem migrations.

Run this on the server with: python reset_billing_migrations.py
"""
import os
import sys
import sqlite3

# Set Django settings
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
from django.conf import settings

django.setup()

def reset_billing_migrations():
    """Clear billing migrations from database and show status."""
    db_path = settings.DATABASES['default']['NAME']
    
    print(f"📦 Database: {db_path}")
    print()
    
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # Show current billing migrations
    print("Current billing migrations in database:")
    cur.execute("SELECT app, name FROM django_migrations WHERE app='billing' ORDER BY name")
    rows = cur.fetchall()
    if rows:
        for app, name in rows:
            print(f"  ✓ {app}.{name}")
    else:
        print("  (none)")
    
    print()
    print("🗑️  Clearing billing migrations from database...")
    cur.execute("DELETE FROM django_migrations WHERE app='billing'")
    deleted = cur.rowcount
    conn.commit()
    
    print(f"✅ Deleted {deleted} migration records")
    print()
    
    # Show all remaining migrations by app
    print("Remaining migrations by app:")
    cur.execute("SELECT app, COUNT(*) as count FROM django_migrations GROUP BY app ORDER BY app")
    for app, count in cur.fetchall():
        print(f"  {app}: {count}")
    
    conn.close()
    print()
    print("✨ Done! Now run these commands:")
    print()
    print("  ./.venv/bin/python manage.py migrate billing")
    print("  ./.venv/bin/python manage.py migrate")
    print()

if __name__ == '__main__':
    try:
        reset_billing_migrations()
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)
