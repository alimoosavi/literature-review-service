import logging
import uuid

from celery import current_app
from django.db import transaction
from django.utils import timezone

from .models import LiteratureReviewJob, SearchHistory
from .tasks import generate_literature_review_job

logger = logging.getLogger(__name__)


def create_literature_review_job(user, topic):
    """Create and enqueue a new literature review job."""
    pending_jobs = LiteratureReviewJob.objects.filter(
        user=user, status__in=["pending", "processing"]
    ).count()
    if pending_jobs >= 3:
        raise ValueError(f"Too many pending jobs ({pending_jobs})")

    with transaction.atomic():
        job = LiteratureReviewJob.objects.create(
            user=user, topic=topic, status="pending"
        )
        SearchHistory.objects.create(user=user, topic=topic, job=job)
        job.celery_task_id = str(uuid.uuid4())
        job.save(update_fields=["celery_task_id"])

    generate_literature_review_job.apply_async(
        args=[job.id],
        countdown=1,
        task_id=job.celery_task_id,
    )
    return job


def cancel_literature_review_job(job):
    """Cancel an active job and revoke the Celery task."""
    if job.status not in ["pending", "processing"]:
        raise ValueError(f"Cannot cancel job with status '{job.status}'")

    if job.celery_task_id:
        try:
            current_app.control.revoke(job.celery_task_id, terminate=True, signal="SIGKILL")
        except Exception as e:
            logger.warning(f"Failed to revoke Celery task {job.celery_task_id}: {e}")

    job.status = "failed"
    job.error_message = "Cancelled by user"
    job.completed_at = timezone.now()
    job.save(update_fields=["status", "error_message", "completed_at"])
    return job
