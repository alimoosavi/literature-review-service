from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class SearchQuery(models.Model):
    """
    Represents a user-initiated literature search request.
    Each query corresponds to one topic searched on OpenAlex and one user prompt for AI generation.
    """
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="search_queries",
        help_text="The user who submitted this search query."
    )
    topic = models.CharField(max_length=255, help_text="The topic to search for in OpenAlex.")
    user_prompt = models.TextField(help_text="User’s instruction for AI model focus.")
    timestamp = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.topic} ({self.user.username})"


class Paper(models.Model):
    """
    Stores metadata, file info, and extracted text for papers retrieved from OpenAlex.
    """
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("downloaded", "Downloaded"),
        ("extracted", "Extracted"),
        ("failed", "Failed"),
    ]

    search_query = models.ForeignKey(SearchQuery, on_delete=models.CASCADE, related_name="papers")
    openalex_id = models.CharField(max_length=100, blank=True, null=True)
    title = models.CharField(max_length=500)
    authors = models.TextField(help_text="Comma-separated list of authors.")
    year = models.IntegerField(blank=True, null=True)
    doi = models.CharField(max_length=100, blank=True, null=True)
    url = models.URLField(blank=True, null=True)
    open_access_pdf = models.URLField(blank=True, null=True)
    pdf_file = models.FileField(upload_to="papers/pdfs/", blank=True, null=True)

    # Status + text extraction
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
        help_text="Current processing status."
    )
    content = models.TextField(blank=True, null=True, help_text="Extracted full text from the paper PDF.")
    word_count = models.IntegerField(blank=True, null=True)
    extraction_error = models.TextField(blank=True, null=True)
    timestamp = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-year", "title"]

    def __str__(self):
        return f"{self.title[:80]}"

    @property
    def short_title(self):
        return self.title[:60]

    def mark_downloaded(self):
        self.status = "downloaded"
        self.save(update_fields=["status", "timestamp"])

    def mark_extracted(self, text):
        self.content = text
        self.word_count = len(text.split()) if text else 0
        self.status = "extracted"
        self.save(update_fields=["content", "word_count", "status", "timestamp"])

    def mark_failed(self, error_message):
        self.status = "failed"
        self.extraction_error = error_message
        self.save(update_fields=["status", "extraction_error", "timestamp"])


class LiteratureReview(models.Model):
    """
    The AI-generated literature review based on a SearchQuery and extracted papers.
    """
    search_query = models.OneToOneField(SearchQuery, on_delete=models.CASCADE, related_name="review")
    generated_text = models.TextField(help_text="The full AI-generated literature review (≥3000 words).")
    word_count = models.IntegerField(default=0)
    model_name = models.CharField(max_length=100, blank=True, null=True)
    bibliography = models.TextField(blank=True, null=True, help_text="APA or IEEE formatted references.")
    timestamp = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        return f"Review for '{self.search_query.topic}'"


class Citation(models.Model):
    """
    Represents inline citations and formatted references used in the generated review.
    """
    review = models.ForeignKey(LiteratureReview, on_delete=models.CASCADE, related_name="citations")
    paper = models.ForeignKey(Paper, on_delete=models.CASCADE, related_name="citations")
    inline_citation = models.CharField(max_length=255)
    formatted_reference = models.TextField()
    timestamp = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.inline_citation


class TaskStatus(models.Model):
    """
    Tracks background progress for long-running operations:
    - OpenAlex search
    - PDF download
    - Text extraction
    - Review generation
    """
    TASK_CHOICES = [
        ("search", "Search Papers"),
        ("download", "Download PDFs"),
        ("extract", "Extract Texts"),
        ("generate", "Generate Review"),
    ]

    search_query = models.ForeignKey(SearchQuery, on_delete=models.CASCADE, related_name="tasks")
    task_type = models.CharField(max_length=20, choices=TASK_CHOICES)
    status = models.CharField(max_length=20, default="pending")
    progress = models.FloatField(default=0.0)
    message = models.TextField(blank=True, null=True)
    timestamp = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["timestamp"]

    def __str__(self):
        return f"{self.task_type} ({self.status}) - {self.search_query.topic}"
