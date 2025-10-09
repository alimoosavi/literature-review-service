import logging
import os

from celery import shared_task
from django.conf import settings
from django.db import transaction  # Important for atomic updates

from litapp.models import Paper, LiteratureReviewJob, LiteratureReview
# Import utility functions for API calls and file handling
from litapp.utils import download_pdf, extract_text_from_pdf, fetch_openalex_works_data

logger = logging.getLogger(__name__)


# --- Helper Functions ---

def create_or_update_paper(paper_data: dict) -> Paper:
    """
    Creates or updates a Paper object using raw JSON data received from the
    OpenAlex API works endpoint.
    """
    # Safely extract all required fields from the raw API response (dict)
    openalex_id = paper_data.get("id")
    title = paper_data.get("display_name")
    year = paper_data.get("publication_year")
    doi = paper_data.get("doi")

    # Extract authors, handling the nested list structure
    authors_list = [
        a.get("author", {}).get("display_name")
        for a in paper_data.get("authorships", [])
        if a.get("author", {}).get("display_name")
    ]
    authors = ", ".join(authors_list)

    # The permanent URL for the OpenAlex work page
    url = openalex_id

    # Find the best Open Access PDF location
    best_oa_location = paper_data.get("open_access", {}).get("best_oa_location")
    pdf_url = best_oa_location.get("pdf_url") if best_oa_location else None

    # Check for mandatory fields before creating/updating
    if not openalex_id or not title or not year:
        # Log and raise an error for papers missing critical metadata
        raise ValueError("Missing required fields (ID, Title, or Year) in paper data.")

    # Create or update the paper record atomically
    paper, created = Paper.objects.get_or_create(
        openalex_id=openalex_id,
        defaults={
            "title": title,
            "authors": authors,
            "year": year,
            "doi": doi,
            "url": url,
            "pdf_url": pdf_url,
        }
    )
    return paper


def process_pdf_and_extract_text(paper: Paper):
    """
    Downloads the PDF if a URL is present and no text has been extracted yet.
    Caches the file locally and extracts the full text.
    """
    if paper.pdf_url and not paper.text:
        # download_pdf returns the absolute path to the cached PDF
        pdf_path = download_pdf(paper.pdf_url, openalex_id=paper.openalex_id)

        if pdf_path:
            # 1. Extract text
            paper.text = extract_text_from_pdf(pdf_path)

            # 2. Set the relative file path for the Django FileField
            # We use os.path.relpath to correctly store the path relative to MEDIA_ROOT
            relative_path = os.path.relpath(pdf_path, settings.MEDIA_ROOT)

            paper.cached_file = relative_path
            paper.save()


# --- Celery Tasks ---

@shared_task(name="litapp.tasks.llm_generation_task")
def llm_generation_task(job_id: int, paper_texts: list[str], prompt: str, topic: str):
    """
    MOCK: This task represents the second step: generating the review using an LLM.
    It takes the extracted texts and the user's prompt to synthesize the review.
    """
    try:
        job = LiteratureReviewJob.objects.get(pk=job_id)

        # --- LLM Simulation ---
        # In a real scenario, this is where you would call the Gemini API
        # with the prompt and the concatenated paper texts as context.

        context_length = sum(len(text) for text in paper_texts)
        if context_length < 1000:
            mock_content = f"The generated review is brief because only partial context ({context_length} chars) was available. Topic: {topic}. Prompt: {prompt}"
        else:
            mock_content = (
                f"## Literature Review on: {topic}\n\n"
                f"**Based on the user prompt:** *{prompt}*\n\n"
                "This is a high-quality, AI-generated literature review synthesized "
                f"from {len(paper_texts)} successfully extracted full-text documents. "
                "The review provides a comprehensive analysis of key themes, methodologies, "
                "and findings in the field, structured logically to address the user's query. "
                f"Total context used: {context_length} characters. [Citations would appear here]"
            )
        # --- End LLM Simulation ---

        # Create the final LiteratureReview object
        review = LiteratureReview.objects.create(
            user=job.user,
            topic=topic,
            prompt=prompt,
            content=mock_content,
            # Mock citation data
            citations={"paper-1": "Mock Citation (2024)", "paper-2": "Mock Citation (2023)"}
        )

        # Update the job status and link the review
        job.status = "completed"
        job.review = review
        job.result_text = "Literature review successfully generated."
        job.save()

        logger.info(f"LLM task for Job {job_id} successfully completed.")

    except Exception as e:
        logger.exception(f"Error in LLM generation for Job {job_id}: {e}")
        job.status = "failed"
        job.error_message = str(e)
        job.save()


@shared_task(bind=True, name="litapp.tasks.generate_literature_review_job")
def generate_literature_review_job(self, job_id: int):
    """
    STEP 1: Fetches papers, downloads PDFs, caches files, and extracts full text.
    On successful completion, it chains to the llm_generation_task (Step 2).
    """
    try:
        job = LiteratureReviewJob.objects.get(pk=job_id)
    except LiteratureReviewJob.DoesNotExist:
        logger.error(f"LiteratureReviewJob {job_id} does not exist.")
        return

    # Use a transaction for safety, although Celery tasks already handle atomic operations
    # within the task scope.
    with transaction.atomic():
        job.status = "processing"
        job.save()

    try:
        # Step 1: Search OpenAlex and attempt to download 30 papers
        papers_data = fetch_openalex_works_data(job.topic, per_page=30)

        extracted_texts = []
        downloaded_count = 0

        for raw_paper_data in papers_data:
            try:
                paper = create_or_update_paper(raw_paper_data)
                process_pdf_and_extract_text(paper)

                if paper.text:
                    extracted_texts.append(paper.text)

                if paper.cached_file:
                    downloaded_count += 1

            except Exception as e:
                # Log non-critical errors (like a single bad paper) and continue
                logger.warning(f"Skipping paper {raw_paper_data.get('id')} due to error: {e}")
                continue

        # Check if enough text was extracted to proceed
        if not extracted_texts:
            job.status = "failed"
            job.error_message = "No full-text could be extracted from the search results."
            job.save()
            return

        # Log completion of data retrieval phase
        log_message = (f"Data retrieval complete for Job {job_id}. "
                       f"Found {len(papers_data)} results, extracted text from {len(extracted_texts)}.")
        logger.info(log_message)

        # Update job status and result text before chaining to LLM task
        job.result_text = log_message
        job.save()

        # Step 2: Chain to the LLM generation task for synthesis
        llm_generation_task.delay(
            job.id,
            extracted_texts,
            job.prompt,
            job.topic
        )

        # NOTE: The job status will be marked 'completed' by llm_generation_task.

        return {"job_id": job_id, "papers_found": len(papers_data), "pdfs_extracted": len(extracted_texts)}

    except Exception as e:
        logger.exception(f"CRITICAL Error during data fetching for Job {job_id}: {e}")

        # Use transaction for rollback safety in case of severe failure
        with transaction.atomic():
            job.status = "failed"
            job.error_message = str(e)
            job.save()
            return
