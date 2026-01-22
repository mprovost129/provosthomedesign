# Provost Home Design

Django-based website for custom house plans and framing plans.

## Features

- Custom house plan catalog with filtering and search
- Contact forms with reCAPTCHA v3 protection
- Project inquiry system with file uploads
- Testimonials management
- Email notifications via Microsoft Graph API
- SEO optimization with sitemaps and structured data
- Responsive Bootstrap 5 design

## Tech Stack

- Django 5.2
- Python 3.11
- PostgreSQL (production)
- WhiteNoise for static files
- Microsoft Graph for email
- reCAPTCHA v3 for spam protection

## Local Development Setup

1. **Clone the repository**
   ```bash
   git clone <your-repo-url>
   cd provost_home_design
   ```

2. **Create virtual environment**
   ```bash
   python -m venv env
   source env/bin/activate  # On Windows: env\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables**
   ```bash
   cp .env.example .env
   # Edit .env with your settings
   ```

5. **Run migrations**
   ```bash
   python manage.py migrate
   ```

6. **Create superuser**
   ```bash
   python manage.py createsuperuser
   ```

7. **Collect static files**
   ```bash
   python manage.py collectstatic --noinput
   ```

8. **Run development server**
   ```bash
   python manage.py runserver
   ```

## Production Deployment

### Pre-deployment Checklist

- [ ] Set `DEBUG=False` in `.env`
- [ ] Configure production `SECRET_KEY`
- [ ] Set up PostgreSQL database
- [ ] Configure `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`
- [ ] Set up Microsoft Graph API credentials
- [ ] Configure reCAPTCHA v3 keys
- [ ] Set up SSL/TLS certificates
- [ ] Configure email settings
- [ ] Create logs directory: `mkdir -p logs`
- [ ] Run `collectstatic`
- [ ] Run migrations
- [ ] Never commit `.env` file!

### Environment Variables

See `.env.example` for all required environment variables.

**Critical variables:**
- `SECRET_KEY` - Django secret key (generate new for production)
- `DEBUG` - Set to `False` in production
- `ALLOWED_HOSTS` - Your domain names
- `DB_*` - Database credentials
- `MICROSOFT_GRAPH_*` - Email API credentials
- `RECAPTCHA_*` - Spam protection keys

### Database Setup (Production)

```bash
# PostgreSQL example
DB_ENGINE=django.db.backends.postgresql
DB_NAME=provost_db
DB_USER=provost_user
DB_PASSWORD=secure_password
DB_HOST=localhost
DB_PORT=5432
```

### Running with Gunicorn

```bash
gunicorn config.wsgi:application --bind 0.0.0.0:8000
```

### Static Files

Run before each deployment:
```bash
python manage.py collectstatic --noinput
```

## Security Notes

⚠️ **IMPORTANT:**
- Never commit `.env` file to version control
- Rotate Microsoft Graph client secret regularly
- Keep `SECRET_KEY` secure and unique per environment
- Use strong database passwords
- Enable HTTPS in production
- Review security settings before deployment

## Project Structure

```
provost_home_design/
├── config/          # Django settings and URLs
├── core/            # Core app (shared utilities)
├── pages/           # Main pages (home, contact, about, etc.)
├── plans/           # House plans catalog
├── templates/       # Django templates
├── static/          # Static files (CSS, JS, images)
├── media/           # User uploads
└── requirements.txt # Python dependencies
```

## License

Proprietary - All rights reserved

## Contact

Michael Provost - mike@provosthomedesign.com
Provost Home Design
