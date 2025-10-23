# LitRevAI - Automated Literature Review System

A Django-based application that automatically generates comprehensive literature reviews using OpenAlex API and OpenAI. The system searches for relevant papers, downloads PDFs, extracts text, generates summaries, and produces a structured academic review.

## Features

- 🔍 Automated paper search using OpenAlex API
- 📄 PDF download and text extraction
- 🤖 AI-powered paper summarization using OpenAI GPT-4
- 📝 Comprehensive literature review generation
- 📊 Real-time progress tracking with stage-based updates
- 🔐 JWT-based authentication
- 📤 Export reviews as PDF or DOCX
- ⚡ Asynchronous task processing with Celery

## Architecture

- **Backend**: Django 5.x + Django REST Framework
- **Task Queue**: Celery with Redis broker
- **Database**: PostgreSQL 15
- **AI**: OpenAI GPT-4 for summarization and review generation
- **Authentication**: JWT tokens

## Deployment Options

This project supports two deployment modes:

1. **Hybrid Mode (Recommended)**: Run Django and Celery on host machine, use Docker for PostgreSQL and Redis
2. **Full Docker Mode**: Run all services in Docker containers

---

## 🚀 Quick Start - Hybrid Mode (Recommended)

### Prerequisites

- Python 3.11+
- Docker and Docker Compose
- Git

### Step 1: Clone Repository

```bash
git clone <repository-url>
cd litRevAI
```

### Step 2: Create Virtual Environment

```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
# On Linux/macOS:
source venv/bin/activate

# On Windows:
venv\Scripts\activate
```

### Step 3: Install Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### Step 4: Configure Environment Variables

```bash
# Create .env file from sample
cp .env.sample .env

# Edit .env file with your settings
nano .env  # or use your preferred editor
```

**Important**: Make sure to update the following in `.env`:
- `OPENAI_API_KEY`: Your OpenAI API key
- `OPENALEX_DEFAULT_MAILTO`: Your email for OpenAlex API
- `DJANGO_SECRET_KEY`: Generate a secure secret key

### Step 5: Start PostgreSQL and Redis (Docker)

```bash
# Start only database and cache services
docker-compose up -d db redis

# Verify services are running
docker-compose ps
```

Expected output:
```
NAME                   STATUS    PORTS
litrevai_postgres      Up        0.0.0.0:5433->5432/tcp
litrevai_redis         Up        0.0.0.0:6380->6379/tcp
```

### Step 6: Run Database Migrations

```bash
python manage.py migrate
```

### Step 7: Create Superuser (Optional)

```bash
python manage.py createsuperuser
```

### Step 8: Start Django Development Server

```bash
# Terminal 1: Django server
python manage.py runserver
```

The API will be available at `http://localhost:8000`

### Step 9: Start Celery Worker

```bash
# Terminal 2: Celery worker (in new terminal, with venv activated)
celery -A litRevAI worker --pool=solo -l info
```

**Note**: Use `--pool=solo` on Windows. On Linux/macOS, you can use `--pool=prefork` for better performance.

### Step 10: Test the API

```bash
# Register a new user
curl -X POST http://localhost:8000/api/auth/register/ \
  -H "Content-Type: application/json" \
  -d '{"username": "testuser",  "password": "securepass123", "password2": "securepass123"}'

# Login and get JWT token
curl -X POST http://localhost:8000/api/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{"username": "testuser", "password": "securepass123"}'

# Create a review task (use the access token from login)
curl -X POST http://localhost:8000/api/literature/reviews \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <your-access-token>" \
  -d '{"topic": "machine learning", "prompt": "Focus on deep learning applications in healthcare"}'
```

---

## 🐳 Full Docker Deployment

### Step 1: Configure Docker Environment

```bash
cp .docker.env.sample .docker.env
# Edit .docker.env.sample with your settings Try to add your api keys
nano .docker.env
```

**Important**: Update these values in `.docker.env`:
- `OPENAI_API_KEY`: Your OpenAI API key
- `OPENALEX_DEFAULT_MAILTO`: Your email
- `DJANGO_SECRET_KEY`: Generate a secure secret key

### Step 2: Build and Start All Services

```bash
# Build and start all services
docker-compose up --build

# Or run in detached mode
docker-compose up -d --build
```

### Step 3: Run Migrations

```bash
docker-compose exec web python manage.py migrate
```

### Step 4: Create Superuser

```bash
docker-compose exec web python manage.py createsuperuser
```

### Step 5: Access Services

- Django API: `http://localhost:8000`
- PostgreSQL: `localhost:5432`
- Redis: `localhost:6379`

### Docker Management Commands

```bash
# View logs
docker-compose logs -f web
docker-compose logs -f celery_worker

# Stop services
docker-compose down

# Stop and remove volumes (WARNING: deletes data)
docker-compose down -v

# Restart a specific service
docker-compose restart web
docker-compose restart celery_worker

# Execute management commands
docker-compose exec web python manage.py createsuperuser
docker-compose exec web python manage.py shell
```

---

## 📁 Project Structure

```
.
├── authapp/                 # JWT authentication app
│   ├── models.py
│   ├── serializers.py
│   ├── views.py
│   └── urls.py
├── literature/              # Core literature review functionality
│   ├── models.py           # Paper and ReviewTask models
│   ├── tasks.py            # Celery tasks for review generation
│   ├── views.py            # API endpoints
│   ├── serializers.py
│   └── utils.py            # PDF/DOCX export utilities
├── litRevAI/               # Project settings
│   ├── settings.py
│   ├── celery.py           # Celery configuration
│   ├── urls.py
│   └── wsgi.py
├── logs/                   # Application logs
├── media/                  # Uploaded files and PDFs
├── manage.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh
├── .env                    # Host deployment config
├── .docker.env             # Docker deployment config
└── README.md
```

---

## 🔧 Configuration

### Environment Variables

#### Database Configuration
```bash
POSTGRES_DB=litrevai
POSTGRES_USER=litrevai_user
POSTGRES_PASSWORD=litrevai_pass
POSTGRES_HOST=localhost      # Use 'db' for Docker
POSTGRES_PORT=5433           # Use 5432 for Docker
```

#### Redis Configuration
```bash
REDIS_HOST=localhost         # Use 'redis' for Docker
REDIS_PORT=6380              # Use 6379 for Docker
```

#### Django Configuration
```bash
DJANGO_SECRET_KEY=your-secret-key
DEBUG=True                   # Set to False in production
ALLOWED_HOSTS=localhost,127.0.0.1,0.0.0.0
```

#### JWT Configuration
```bash
JWT_ACCESS_LIFETIME=60       # Access token lifetime in minutes
JWT_REFRESH_LIFETIME=7       # Refresh token lifetime in days
```

#### External APIs
```bash
OPENAI_API_KEY=your-openai-api-key
OPENALEX_WORKS_URL=https://api.openalex.org/works
OPENALEX_DEFAULT_MAILTO=your-email@example.com
```

#### Celery Configuration
```bash
CELERY_TASK_TIME_LIMIT=1800       # Hard time limit (seconds)
CELERY_TASK_SOFT_TIME_LIMIT=1500  # Soft time limit (seconds)
```

---

## 📚 API Endpoints

### Authentication

- `POST /api/auth/register/` - Register new user
- `POST /api/auth/login/` - Login and get JWT tokens
- `POST /api/auth/token/refresh/` - Refresh access token

### Literature Review

- `POST /api/literature/reviews` - Create new review task
- `GET /api/literature/reviews` - List all user's reviews
- `GET /api/literature/reviews/{tracking_id}` - Get review details
- `GET /api/literature/reviews/{tracking_id}/status` - Get task status and progress
- `GET /api/literature/reviews/{tracking_id}/result` - Get finished review result
- `GET /api/literature/reviews/{tracking_id}/export?format=pdf` - Export as PDF
- `GET /api/literature/reviews/{tracking_id}/export?format=docx` - Export as DOCX
- `POST /api/literature/reviews/{tracking_id}/cancel` - Cancel running task

---

## 🔍 How It Works

1. **Paper Search**: Queries OpenAlex API for relevant papers based on topic
2. **PDF Download**: Downloads open-access PDFs from available sources
3. **Text Extraction**: Extracts text from PDFs using PyMuPDF
4. **Summarization**: Generates paper summaries using GPT-4o-mini
5. **Review Generation**: Produces comprehensive literature review using GPT-4o
6. **Progress Tracking**: Real-time updates through database with stage information

### Processing Stages

- `searching_openalex` - Searching for papers (5%)
- `downloading_pdfs` - Downloading PDF files (25%)
- `extracting_text` - Extracting text from PDFs (25%)
- `summarizing_papers` - Generating paper summaries (30%)
- `generating_review` - Creating final review (15%)

---


## 📊 Monitoring

### Celery Task Monitoring

```bash
# Host mode
celery -A litRevAI inspect active
celery -A litRevAI inspect stats

# Docker mode
docker-compose exec celery_worker celery -A litRevAI inspect active
```

### Database Access

```bash
# Host mode (with PostgreSQL client installed)
psql -h localhost -p 5433 -U litrevai_user -d litrevai

# Docker mode
docker-compose exec db psql -U litrevai_user -d litrevai
```

### Redis Monitoring

```bash
# Host mode
redis-cli -p 6380

# Docker mode
docker-compose exec redis redis-cli
```

---

## 🐛 Troubleshooting

### Common Issues

**1. Celery worker not picking up tasks**
```bash
# Check Redis connection
redis-cli -p 6380 ping

# Restart Celery worker
# Kill the process and restart with:
celery -A litRevAI worker --pool=solo -l info
```

**2. Database connection errors**
```bash
# Check PostgreSQL is running
docker-compose ps db

# Check connection
psql -h localhost -p 5433 -U litrevai_user -d litrevai
```

**3. OpenAI API errors**
- Verify your API key is valid and has sufficient credits
- Check the API key format in `.env` file
- Ensure no extra spaces in the environment variable

**4. Permission errors on media files**
```bash
# Fix permissions
chmod -R 755 media/
mkdir -p media/pdfs
```

**5. Port already in use**
```bash
# Find process using port
lsof -i :8000  # macOS/Linux
netstat -ano | findstr :8000  # Windows

# Kill process or change port in .env
```

---

## 📦 Dependencies

Key Python packages:
- `Django==5.x` - Web framework
- `djangorestframework==3.x` - REST API
- `celery==5.x` - Task queue
- `redis==5.x` - Celery broker
- `psycopg2-binary==2.9.x` - PostgreSQL adapter
- `openai==1.x` - OpenAI API client
- `PyMuPDF==1.x` - PDF text extraction
- `reportlab==4.x` - PDF generation
- `python-docx==1.x` - DOCX generation
- `djangorestframework-simplejwt==5.x` - JWT authentication
- `gunicorn==21.x` - Production WSGI server (Docker only)

See `requirements.txt` for complete list.

---

## 🔒 Security Notes

- **Never commit `.env` or `.docker.env` files** - Add them to `.gitignore`
- Use strong, unique `DJANGO_SECRET_KEY` in production
- Set `DEBUG=False` in production
- Use environment-specific API keys
- Regularly rotate JWT secrets and API keys
- Keep dependencies updated: `pip list --outdated`

---

## 📝 License

[Your License Here]

---

## 👥 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## 📧 Contact

For questions or support, contact: [Your Contact Info]

---

## 🙏 Acknowledgments

- OpenAlex API for academic paper data
- OpenAI for GPT-4 models
- Django and Celery communities