#!/bin/bash
# Script to update just the template files on production

echo "Updating template files..."

# Pull just the templates directory
git checkout origin/main -- templates/

echo "Templates updated. Restarting service..."
sudo systemctl restart phdapp

echo "Done!"
