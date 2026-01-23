#!/bin/bash
# Deploy reCAPTCHA bot protection to production

echo "=== Deploying reCAPTCHA Bot Protection ==="
echo ""

# Fetch and reset to latest
echo "1. Fetching latest code..."
export GIT_SSH_COMMAND='ssh -i ~/.ssh/id_deploy_phd -o IdentitiesOnly=yes'
git fetch --prune
git reset --hard origin/main

echo ""
echo "2. Installing django-recaptcha..."
source .venv/bin/activate
pip install django-recaptcha==4.0.0

echo ""
echo "3. Collecting static files..."
python manage.py collectstatic --noinput

echo ""
echo "4. Checking .env file for reCAPTCHA keys..."
if grep -q "RECAPTCHA_PUBLIC_KEY" .env; then
    echo "   ✓ RECAPTCHA_PUBLIC_KEY found in .env"
else
    echo "   ✗ RECAPTCHA_PUBLIC_KEY NOT found in .env"
    echo "   Please add: RECAPTCHA_PUBLIC_KEY=your-site-key"
fi

if grep -q "RECAPTCHA_PRIVATE_KEY" .env; then
    echo "   ✓ RECAPTCHA_PRIVATE_KEY found in .env"
else
    echo "   ✗ RECAPTCHA_PRIVATE_KEY NOT found in .env"
    echo "   Please add: RECAPTCHA_PRIVATE_KEY=your-secret-key"
fi

echo ""
echo "5. Restarting service..."
sudo systemctl restart phdapp

echo ""
echo "=== Deployment Complete ==="
echo ""
echo "Next steps:"
echo "1. Get reCAPTCHA keys from https://www.google.com/recaptcha/admin"
echo "2. Add keys to .env file (if not already done)"
echo "3. Restart service if you added keys: sudo systemctl restart phdapp"
echo "4. Test registration: https://provosthomedesign.com/portal/register/"
echo "5. Test contact form: https://provosthomedesign.com/contact/"
echo ""
