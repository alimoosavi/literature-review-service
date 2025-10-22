# Literature Review Generator – Full Stack Setup & Architecture Guide

A powerful **AI-powered literature review automation system** combining a **React frontend** and **Django backend**, enabling users to generate structured, citation-rich academic reviews from any research topic — fully automated using OpenAI, OpenAlex, and real-time progress tracking.

---

## Project Structure Overview

2. Start Docker Services (PostgreSQL + Redis)
The system requires PostgreSQL and Redis — both are containerized.
```docker-compose up -d```
This starts:

```commandline
db → PostgreSQL on localhost:5432
redis → Redis on localhost:6379

```


These services are required for Django DB and Celery.


3. Set Up Python Virtual Environment
```python -m venv venv```
```source venv/bin/activate    # On Windows: venv\Scripts\activate```
Install backend dependencies:
```pip install -r requirements.txt```

Includes: Django, DRF, Celery, Redis, Psycopg2, OpenAI, PyMuPDF, etc.


4. Configure Environment Variables
Copy and configure .env:
```cp .env.sample .env```
Edit .env with real values:
```DJANGO_SECRET_KEY=your-secret-key-here
DEBUG=1
ALLOWED_HOSTS=localhost,127.0.0.1,0.0.0.0

POSTGRES_DB=litrevai
POSTGRES_USER=litrevai_user
POSTGRES_PASSWORD=litrevai_pass
POSTGRES_HOST=db
POSTGRES_PORT=5432

REDIS_HOST=redis
REDIS_PORT=6379

OPENAI_API_KEY=sk-...your-real-key...
OPENALEX_DEFAULT_MAILTO=you@example.com
```
.env is gitignored — keep it secure.


5. Run Django Migrations

```python manage.py makemigrations``` 
```python manage.py migrate```

6. Start Django Server
In Terminal 1 (inside backend/):
```python manage.py runserver 0.0.0.0:8000```
API will be available at: http://localhost:8000

7. Start Celery Worker
In Terminal 2 (same virtual env, inside backend/):
```celery -A litRevAI worker --pool=solo -l info```

--pool=solo is ideal for development (handles signals properly)


System architecture
```[React Frontend] ←→ REST API ←→ [Django + DRF]
                             ↓
                   [Celery Workers + Redis]
                             ↓
            [PostgreSQL] ←→ [OpenAlex] ←→ [OpenAI]
                             ↓
                       [PDFs → media/pdfs/]```