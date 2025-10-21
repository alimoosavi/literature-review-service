# literature/models.py
import uuid

from django.contrib.auth.models import User
from django.contrib.postgres.fields import ArrayField
from django.db import models


class Paper(models.Model):
    doi = models.CharField(max_length=255, unique=True, null=True, blank=True)
    openalex_id = models.CharField(max_length=255, unique=True)
    title = models.CharField(max_length=512)
    authors = ArrayField(models.CharField(max_length=255), blank=True)
    year = models.IntegerField(null=True, blank=True)
    pdf_url = models.URLField(max_length=512, null=True, blank=True)
    pdf_path = models.FileField(upload_to='pdfs/', null=True, blank=True)
    extracted_text = models.TextField(null=True, blank=True)
    summary = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['doi', 'openalex_id']
        indexes = [models.Index(fields=['doi']), models.Index(fields=['openalex_id'])]

    def __str__(self):
        return f"Paper: {self.title} ({self.year})"


class ReviewTask(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('finished', 'Finished'),
        ('canceled', 'Canceled'),
        ('failed', 'Failed'),
    ]

    STAGE_CHOICES = [
        ('searching_openalex', 'Searching OpenAlex'),
        ('downloading_pdfs', 'Downloading PDFs'),
        ('extracting_text', 'Extracting Text'),
        ('summarizing_papers', 'Summarizing Papers'),
        ('generating_review', 'Generating Review'),
    ]

    tracking_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='review_tasks')
    topic = models.CharField(max_length=255)
    prompt = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    current_stage = models.CharField(max_length=30, choices=STAGE_CHOICES, null=True, blank=True)
    celery_task_id = models.CharField(max_length=155, null=True, blank=True)
    papers = models.ManyToManyField(Paper, related_name='review_tasks', blank=True)
    result = models.TextField(null=True, blank=True)  # Markdown review
    error_message = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Task {self.tracking_id}: {self.topic} ({self.status})"