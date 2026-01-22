# 🚨 CRITICAL SECURITY ISSUE - READ IMMEDIATELY

## YOUR .ENV FILE CONTAINS EXPOSED CREDENTIALS!

Your `.env` file in the repository contains:
- Production SECRET_KEY
- Microsoft Graph Client Secret
- reCAPTCHA keys
- Database credentials (if any)

### IMMEDIATE ACTIONS REQUIRED:

1. **Remove .env from Git History** (if committed)
   ```bash
   # Remove .env from git completely
   git rm --cached .env
   git commit -m "Remove .env from repository"
   git push
   
   # If already pushed to remote, you need to purge history:
   git filter-branch --force --index-filter \
     'git rm --cached --ignore-unmatch .env' \
     --prune-empty --tag-name-filter cat -- --all
   git push origin --force --all
   ```

2. **Rotate All Credentials**
   - Generate new Django SECRET_KEY
   - Rotate Microsoft Graph Client Secret in Azure Portal
   - Generate new reCAPTCHA keys at https://www.google.com/recaptcha/admin
   - Update .env with new credentials

3. **Verify .gitignore**
   ```bash
   # Ensure .env is ignored
   echo ".env" >> .gitignore
   git add .gitignore
   git commit -m "Ensure .env is ignored"
   ```

---

# Deployment Checklist for Provost Home Design

## Pre-Deployment (CRITICAL)

### 1. Security Configuration

- [ ] **Generate new SECRET_KEY** for production
  ```python
  python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
  ```

- [ ] **Set DEBUG=False** in production .env

- [ ] **Verify .env is NOT in git**
  ```bash
  git ls-files | grep .env  # Should return nothing
  ```

- [ ] **Rotate Microsoft Graph Client Secret**
  - Go to Azure Portal → App Registrations
  - Generate new client secret
  - Update MICROSOFT_GRAPH_CLIENT_SECRET in .env

- [ ] **Update reCAPTCHA keys** (if exposed)
  - Visit https://www.google.com/recaptcha/admin
  - Generate new site/secret keys
  - Update RECAPTCHA_SITE_KEY and RECAPTCHA_SECRET_KEY

### 2. Database Configuration

- [ ] **Set up PostgreSQL database**
  ```bash
  # Example for PostgreSQL
  DB_ENGINE=django.db.backends.postgresql
  DB_NAME=provost_home_design
  DB_USER=provost_user
  DB_PASSWORD=<strong-password>
  DB_HOST=your-db-host
  DB_PORT=5432
  ```

- [ ] **Test database connection**
  ```bash
  python manage.py check --database default
  ```

- [ ] **Run migrations on production database**
  ```bash
  python manage.py migrate
  ```

### 3. Environment Variables

Update your production `.env` with:

```bash
# Core
SECRET_KEY=<new-generated-key>
DEBUG=False
ALLOWED_HOSTS=provosthomedesign.com,www.provosthomedesign.com
CSRF_TRUSTED_ORIGINS=https://provosthomedesign.com,https://www.provosthomedesign.com

# Database
DB_ENGINE=django.db.backends.postgresql
DB_NAME=your_db_name
DB_USER=your_db_user
DB_PASSWORD=your_db_password
DB_HOST=your_db_host
DB_PORT=5432

# Email
MICROSOFT_GRAPH_CLIENT_ID=<your-client-id>
MICROSOFT_GRAPH_CLIENT_SECRET=<new-rotated-secret>
MICROSOFT_GRAPH_TENANT_ID=<your-tenant-id>
MSGRAPH_USER_ID=mike@provosthomedesign.com

# reCAPTCHA
RECAPTCHA_SITE_KEY=<your-site-key>
RECAPTCHA_SECRET_KEY=<your-secret-key>

# Security
SECURE_SSL_REDIRECT=True
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
SECURE_HSTS_SECONDS=31536000
BEHIND_PROXY=True
```

### 4. Static & Media Files

- [ ] **Create logs directory**
  ```bash
  mkdir -p logs
  ```

- [ ] **Collect static files**
  ```bash
  python manage.py collectstatic --noinput
  ```

- [ ] **Configure media file storage** (for production)
  - Option A: Use AWS S3, Azure Blob Storage, or similar
  - Option B: Ensure media directory has proper permissions

### 5. Application Setup

- [ ] **Create Django superuser**
  ```bash
  python manage.py createsuperuser
  ```

- [ ] **Load fixtures** (if needed)
  ```bash
  python manage.py loaddata plans_fixture.json
  python manage.py loaddata pages_fixture.json
  ```

- [ ] **Run Django deployment checks**
  ```bash
  python manage.py check --deploy
  ```

## Deployment Steps

### Option A: Traditional Server (Ubuntu/Debian)

1. **Install system dependencies**
   ```bash
   sudo apt update
   sudo apt install python3.11 python3.11-venv python3-pip postgresql nginx
   ```

2. **Clone repository**
   ```bash
   cd /var/www
   git clone <your-repo-url> provost_home_design
   cd provost_home_design
   ```

3. **Set up virtual environment**
   ```bash
   python3.11 -m venv env
   source env/bin/activate
   pip install -r requirements.txt
   ```

4. **Configure environment**
   ```bash
   cp .env.example .env
   nano .env  # Edit with production values
   ```

5. **Set up database and static files**
   ```bash
   python manage.py migrate
   python manage.py collectstatic --noinput
   python manage.py createsuperuser
   ```

6. **Configure Gunicorn service**
   ```bash
   sudo nano /etc/systemd/system/provost.service
   ```
   
   ```ini
   [Unit]
   Description=Provost Home Design Gunicorn
   After=network.target

   [Service]
   User=www-data
   Group=www-data
   WorkingDirectory=/var/www/provost_home_design
   Environment="PATH=/var/www/provost_home_design/env/bin"
   ExecStart=/var/www/provost_home_design/env/bin/gunicorn \
             --workers 3 \
             --bind unix:/var/www/provost_home_design/provost.sock \
             config.wsgi:application

   [Install]
   WantedBy=multi-user.target
   ```

7. **Configure Nginx**
   ```bash
   sudo nano /etc/nginx/sites-available/provost
   ```
   
   ```nginx
   server {
       listen 80;
       server_name provosthomedesign.com www.provosthomedesign.com;
       
       location /static/ {
           alias /var/www/provost_home_design/staticfiles/;
       }
       
       location /media/ {
           alias /var/www/provost_home_design/media/;
       }
       
       location / {
           proxy_pass http://unix:/var/www/provost_home_design/provost.sock;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
       }
   }
   ```

8. **Enable and start services**
   ```bash
   sudo systemctl start provost
   sudo systemctl enable provost
   sudo ln -s /etc/nginx/sites-available/provost /etc/nginx/sites-enabled/
   sudo systemctl restart nginx
   ```

9. **Set up SSL with Let's Encrypt**
   ```bash
   sudo apt install certbot python3-certbot-nginx
   sudo certbot --nginx -d provosthomedesign.com -d www.provosthomedesign.com
   ```

### Option B: Cloud Platform (Heroku, AWS, etc.)

Follow platform-specific deployment guides. The `Procfile` and `runtime.txt` are already configured.

## Post-Deployment Verification

- [ ] **Test website loads** (https://provosthomedesign.com)
- [ ] **Verify SSL certificate** (green padlock)
- [ ] **Test contact form** (check reCAPTCHA works)
- [ ] **Test email sending** (contact form, get started)
- [ ] **Check admin panel** (/admin/)
- [ ] **Verify static files load** (CSS, images)
- [ ] **Test plan filtering and search**
- [ ] **Check 404 and 500 error pages**
- [ ] **Monitor logs for errors**
  ```bash
  tail -f logs/django.log
  ```

## Monitoring & Maintenance

- [ ] Set up error monitoring (Sentry, Rollbar, etc.)
- [ ] Configure database backups
- [ ] Set up log rotation
- [ ] Monitor disk space (media uploads)
- [ ] Review security headers
- [ ] Test backup restoration process

## Rollback Plan

If issues occur:
1. Check logs: `tail -f logs/django.log`
2. Verify environment variables
3. Check database connectivity
4. Restart services:
   ```bash
   sudo systemctl restart provost
   sudo systemctl restart nginx
   ```
5. If critical, rollback to previous commit:
   ```bash
   git revert HEAD
   sudo systemctl restart provost
   ```

## Common Issues

### Static files not loading
```bash
python manage.py collectstatic --noinput
sudo systemctl restart provost
```

### Database connection errors
- Check DB_* environment variables
- Verify PostgreSQL is running
- Check firewall rules

### Email not sending
- Verify Microsoft Graph credentials
- Check token hasn't expired
- Review Azure API permissions

### reCAPTCHA failing
- Verify site key matches domain
- Check HTTPS is enabled
- Ensure keys are for correct environment

---

## Support

For issues, contact: mike@provosthomedesign.com
