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
    # === Existing fields ===
    user = models.ForeignKey('auth.User', on_delete=models.CASCADE)
    topic = models.CharField(max_length=255)
    prompt = models.TextField()
    status = models.CharField(max_length=20, default='pending')
    result = models.TextField(blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    tracking_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    # === Progress fields ===
    papers_found = models.IntegerField(default=0)
    papers_downloaded = models.IntegerField(default=0)
    papers_extracted = models.IntegerField(default=0)
    papers_summarized = models.IntegerField(default=0)
    total_papers_target = models.IntegerField(null=True, blank=True)
    progress_percent = models.FloatField(default=0.0)

    # === Stage with CHOICES (required for get_current_stage_display) ===
    STAGE_SEARCHING_OPENALEX = 'searching_openalex'
    STAGE_DOWNLOADING_PDFS = 'downloading_pdfs'
    STAGE_EXTRACTING_TEXT = 'extracting_text'
    STAGE_SUMMARIZING_PAPERS = 'summarizing_papers'
    STAGE_GENERATING_REVIEW = 'generating_review'

    STAGE_CHOICES = [
        (STAGE_SEARCHING_OPENALEX, 'Searching OpenAlex'),
        (STAGE_DOWNLOADING_PDFS, 'Downloading PDFs'),
        (STAGE_EXTRACTING_TEXT, 'Extracting Text'),
        (STAGE_SUMMARIZING_PAPERS, 'Summarizing Papers'),
        (STAGE_GENERATING_REVIEW, 'Generating Final Review'),
    ]

    current_stage = models.CharField(
        max_length=30,
        choices=STAGE_CHOICES,
        blank=True,
        null=True
    )

    # === Relations ===
    papers = models.ManyToManyField('Paper', related_name='review_tasks')

    def __str__(self):
        return f"{self.topic} ({self.status})"