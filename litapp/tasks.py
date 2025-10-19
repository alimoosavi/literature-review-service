import logging
import os
from typing import List

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from litapp.models import Paper, LiteratureReviewJob, LiteratureReview
from litapp.utils import (
    download_pdf,
    extract_text_from_pdf,
    fetch_openalex_works_data,
)

from openai import OpenAI

logger = logging.getLogger(__name__)

client = OpenAI(api_key=settings.OPENAI_API_KEY)

# ---------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------


def create_or_update_paper(paper_data: dict) -> Paper:
    """
    Creates or updates a Paper instance from OpenAlex metadata.
    """
    openalex_id = paper_data.get("id")
    title = paper_data.get("display_name")
    year = paper_data.get("publication_year")
    doi = paper_data.get("doi")

    if not openalex_id or not title or not year:
        raise ValueError("Missing required fields (ID, Title, or Year) in paper data.")

    authors_list = [
        a.get("author", {}).get("display_name")
        for a in paper_data.get("authorships", [])
        if a.get("author", {}).get("display_name")
    ]
    authors = ", ".join(authors_list)
    url = openalex_id
    best_oa_location = paper_data.get("open_access", {}).get("best_oa_location")
    pdf_url = best_oa_location.get("pdf_url") if best_oa_location else None

    paper, created = Paper.objects.get_or_create(
        openalex_id=openalex_id,
        defaults={
            "title": title,
            "authors": authors,
            "year": year,
            "doi": doi,
            "url": url,
            "pdf_url": pdf_url,
        },
    )

    if not created:
        updated = False
        for field, value in [
            ("title", title),
            ("authors", authors),
            ("year", year),
            ("doi", doi),
            ("url", url),
            ("pdf_url", pdf_url),
        ]:
            if getattr(paper, field) != value:
                setattr(paper, field, value)
                updated = True
        if updated:
            paper.save()

    return paper


def process_pdf_and_extract_text(paper: Paper) -> bool:
    """
    Downloads a paper’s PDF, extracts text, and updates the Paper model.
    Returns True if successful.
    """
    if not paper.pdf_url:
        return False

    if paper.text and paper.cached_file:
        return True  # already processed

    pdf_path = download_pdf(paper.pdf_url, openalex_id=paper.openalex_id)
    if not pdf_path:
        return False

    extracted_text = extract_text_from_pdf(pdf_path)
    if extracted_text and len(extracted_text.strip()) > 100:
        paper.text = extracted_text
        rel_path = os.path.relpath(pdf_path, settings.REPO_CACHE_DIR)
        paper.cached_file = rel_path
        paper.save()
        return True

    return False


# ---------------------------------------------------------------------
# Celery tasks
# ---------------------------------------------------------------------


@shared_task(bind=True, name="litapp.tasks.generate_literature_review_job", max_retries=2, default_retry_delay=120)
def generate_literature_review_job(self, job_id: int):
    job = None
    try:
        # Start atomic transaction for select_for_update
        with transaction.atomic():
            job = LiteratureReviewJob.objects.select_for_update().get(pk=job_id)
            # If the task was triggered with a custom UUID, store it
            if not job.celery_task_id:
                job.celery_task_id = self.request.id  # Celery-generated task_id or custom UUID
                job.save(update_fields=["celery_task_id"])

            job.status = "processing"
            job.result_text = "Fetching papers and extracting full texts..."
            job.save()

        logger.info(f"Job {job_id}: Starting literature fetch for '{job.topic}'")

        # Fetch metadata from OpenAlex
        papers_data = fetch_openalex_works_data(job.topic, per_page=30)
        if not papers_data:
            raise ValueError(f"No papers found for topic '{job.topic}'")

        extracted_papers = []
        for raw_data in papers_data:
            try:
                paper = create_or_update_paper(raw_data)
                if process_pdf_and_extract_text(paper):
                    extracted_papers.append(paper.openalex_id)
            except Exception as e:
                logger.warning(f"Job {job_id}: Failed to process paper: {e}")
                continue

        if not extracted_papers:
            raise ValueError("No full text could be extracted from any paper")

        # Chain next task
        llm_generation_task.apply_async(
            args=[job.id, extracted_papers, job.topic],
            countdown=3
        )
        logger.info(f"Job {job_id}: Queued LLM generation for {len(extracted_papers)} papers")
        return {"success": True, "papers": len(extracted_papers)}

    except Exception as e:
        logger.exception(f"Error in job {job_id}: {e}")
        if job:
            job.status = "failed"
            job.error_message = str(e)
            job.save()
        return {"success": False, "error": str(e)}


@shared_task(bind=True, name="litapp.tasks.llm_generation_task", max_retries=3, default_retry_delay=60)
def llm_generation_task(self, job_id: int, paper_ids: List[str], topic: str):
    """
    Step 2: Summarizes extracted text using GPT to generate a literature review.
    """
    job = None
    try:
        job = LiteratureReviewJob.objects.select_for_update().get(pk=job_id)
        papers = Paper.objects.filter(openalex_id__in=paper_ids, text__isnull=False).exclude(text="")

        if not papers.exists():
            raise ValueError("No papers with extracted text available")

        paper_snippets = [
            {
                "title": p.title,
                "authors": p.authors,
                "year": p.year,
                "text": p.text[:4000],  # Truncate long texts
            }
            for p in papers
        ]

        context = "\n\n---\n\n".join(
            f"Title: {p['title']}\nAuthors: {p['authors']}\nYear: {p['year']}\n\n{p['text']}"
            for p in paper_snippets
        )

        # Static system and user prompts
        system_prompt = (
            "You are a scholarly AI specialized in writing academic literature reviews. "
            "You will read excerpts from research papers and synthesize them into a comprehensive, "
            "structured, and well-cited literature review in a formal academic tone."
        )

        user_prompt = (
            f"Topic: {topic}\n\n"
            "Using the following excerpts from papers, write a detailed literature review summarizing "
            "key findings, research gaps, and future directions. Include inline citations where appropriate.\n\n"
            f"{context}"
        )

        logger.info(f"Job {job_id}: Generating LLM-based literature review")

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=2500,
            temperature=0.5,
        )

        content = response.choices[0].message.content.strip()
        if not content or len(content) < 200:
            raise ValueError("Generated review content too short or invalid")

        citations = {
            f"paper-{i+1}": f"{p['authors']} ({p['year']})"
            for i, p in enumerate(paper_snippets[:10])
        }

        with transaction.atomic():
            review = LiteratureReview.objects.create(
                user=job.user,
                topic=topic,
                content=content,
                citations=citations,
            )
            review.papers.add(*Paper.objects.filter(openalex_id__in=paper_ids))

            job.status = "completed"
            job.review = review
            job.result_text = f"Generated review from {len(paper_snippets)} papers."
            job.completed_at = timezone.now()
            job.save()

        logger.info(f"Job {job_id}: Literature review generation completed successfully")
        return {"success": True, "review_id": review.id}

    except Exception as e:
        logger.exception(f"LLM task failed for Job {job_id}: {e}")
        if job:
            job.mark_failed(str(e))
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e)
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------
# Maintenance tasks
# ---------------------------------------------------------------------

@shared_task(name="litapp.tasks.cleanup_old_pdfs_task")
def cleanup_old_pdfs_task(days_old: int = 30):
    """
    Removes cached PDFs older than `days_old`.
    """
    from litapp.utils import clean_old_cached_pdfs
    try:
        removed = clean_old_cached_pdfs(days_old)
        logger.info(f"Removed {removed} old cached PDFs")
        return {"success": True, "removed": removed}
    except Exception as e:
        logger.exception(f"Error in cleanup_old_pdfs_task: {e}")
        return {"success": False, "error": str(e)}


@shared_task(name="litapp.tasks.cleanup_stale_jobs_task")
def cleanup_stale_jobs_task():
    """
    Marks 'processing' jobs older than 2h as failed.
    """
    from datetime import timedelta
    try:
        threshold = timezone.now() - timedelta(hours=2)
        stale_jobs = LiteratureReviewJob.objects.filter(status="processing", updated_at__lt=threshold)
        count = stale_jobs.count()
        if count:
            stale_jobs.update(
                status="failed",
                error_message="Job timed out or worker stopped unexpectedly",
                completed_at=timezone.now(),
            )
            logger.warning(f"Marked {count} stale jobs as failed")
        return {"success": True, "count": count}
    except Exception as e:
        logger.exception(f"Error in cleanup_stale_jobs_task: {e}")
        return {"success": False, "error": str(e)}
