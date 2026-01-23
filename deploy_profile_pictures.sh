#!/bin/bash
# Deploy profile picture functionality to production

echo "Deploying profile picture updates..."

# Fetch and reset to latest
export GIT_SSH_COMMAND='ssh -i ~/.ssh/id_deploy_phd -o IdentitiesOnly=yes'
git fetch --prune
git reset --hard origin/main

# Create profile_pictures directory with correct permissions
echo "Creating profile_pictures directory..."
sudo mkdir -p /srv/phdapp/media/profile_pictures
sudo chown -R www-data:www-data /srv/phdapp/media/profile_pictures
sudo chmod -R 755 /srv/phdapp/media/profile_pictures

# Ensure media root has correct permissions
echo "Setting media directory permissions..."
sudo chown -R www-data:www-data /srv/phdapp/media
sudo chmod -R 755 /srv/phdapp/media

# Restart the service
echo "Restarting service..."
sudo systemctl restart phdapp

echo "Deployment complete!"
echo "Profile picture upload should now work at provosthomedesign.com/portal/profile/"
