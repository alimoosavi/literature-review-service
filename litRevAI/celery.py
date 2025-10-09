import os
from celery import Celery
from django.conf import settings

# Set the default Django settings module for the 'celery' program.
# This ensures Django settings (like INSTALLED_APPS) are loaded.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'litRevAI.settings')

# Create the Celery application instance
app = Celery('litRevAI')

# Load configuration from your Django settings (settings.py).
# The namespace='CELERY' means it only looks for settings prefixed with 'CELERY_'.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Automatically discover tasks in all installed apps (like 'litapp')
app.autodiscover_tasks(lambda: settings.INSTALLED_APPS)
