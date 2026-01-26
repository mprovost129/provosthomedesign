#!/usr/bin/env python
import os
import sys
import django

# Set up Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
sys.path.insert(0, os.path.dirname(__file__))

django.setup()

from django.db import connection

with connection.cursor() as cursor:
    cursor.execute("SELECT app, name FROM django_migrations WHERE app='billing' ORDER BY name")
    for row in cursor.fetchall():
        print(f"{row[0]}: {row[1]}")
