# literature/tasks.py
import logging
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import fitz  # PyMuPDF
from celery import shared_task
from django.conf import settings
from openai import OpenAI, APIError, RateLimitError
import requests

from .models import ReviewTask, Paper

logger = logging.getLogger(__name__)

# === Constants ===
MAX_WORKERS = 5
MIN_REVIEW_WORDS = 3000
PDF_MIN_SIZE = 50000
MAX_OPENAI_TOKENS = 4096
DESIRED_PDF_COUNT = 30
PER_PAGE = 30
MAX_PAGES = 5  # limit number of OpenAlex pages to fetch


# === Helper Functions ===
def sanitize_text(text):
    if not text:
        return text
    return text.replace('\x00', '').replace('\u0000', '')


def update_task_progress(task):
    if not task.total_papers_target or task.total_papers_target == 0:
        task.progress_percent = 0.0
        task.save(update_fields=['progress_percent'])
        return

    stages = {
        'searching_openalex': 5,
        'downloading_pdfs': 25,
        'extracting_text': 25,
        'summarizing_papers': 30,
        'generating_review': 15
    }

    progress = 0.0
    target = task.total_papers_target

    if task.papers_found > 0:
        progress += stages['searching_openalex']
    if task.papers_downloaded > 0:
        progress += (task.papers_downloaded / target) * stages['downloading_pdfs']
    if task.papers_extracted > 0:
        progress += (task.papers_extracted / target) * stages['extracting_text']
    if task.papers_summarized > 0:
        progress += (task.papers_summarized / target) * stages['summarizing_papers']
    if task.current_stage == 'generating_review':
        progress += stages['generating_review']

    task.progress_percent = min(progress, 99.0)
    task.save(update_fields=['progress_percent'])


# === PDF Download ===
def download_pdf(paper, pdf_dir):
    if not paper.pdf_url or paper.pdf_path:
        return False
    try:
        resp = requests.get(paper.pdf_url, timeout=30)
        if resp.status_code == 200 and len(resp.content) >= PDF_MIN_SIZE:
            pdf_filename = f"{uuid.uuid4()}.pdf"
            pdf_path = os.path.join(pdf_dir, pdf_filename)
            with open(pdf_path, 'wb') as f:
                f.write(resp.content)
            paper.pdf_path = os.path.join('pdfs', pdf_filename)
            paper.save()
            return True
    except Exception as e:
        logger.warning(f"Failed to download PDF for {paper.title}: {e}")
    return False


def extract_text_from_pdf(paper):
    if not paper.pdf_path or paper.extracted_text:
        return False
    try:
        full_path = getattr(paper.pdf_path, 'path', os.path.join(settings.MEDIA_ROOT, str(paper.pdf_path)))
        doc = fitz.open(full_path)
        text = "".join(page.get_text() for page in doc)
        doc.close()
        if len(text.strip()) > 200:
            paper.extracted_text = sanitize_text(text[:100000])
            paper.save()
            return True
    except Exception as e:
        logger.warning(f"Failed to extract text for {paper.title}: {e}")
    return False


def summarize_paper(client, paper, task):
    if not paper.extracted_text or paper.summary:
        return False
    try:
        summary_prompt = (
            f"Summarize this scientific paper in 250-300 words. Focus on: "
            f"1. Research gap and objective\n"
            f"2. Methods used\n"
            f"3. Key findings\n"
            f"4. Relevance to '{task.prompt}'\n\n"
            f"Title: {paper.title}\n\n"
            f"Text excerpt: {paper.extracted_text[:7000]}"
        )
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": summary_prompt}],
            max_tokens=400,
            temperature=0.5
        )
        summary = resp.choices[0].message.content.strip()
        if summary and len(summary) > 100:
            paper.summary = sanitize_text(summary)
        else:
            paper.summary = "[Summary too short or invalid]"
        paper.save()
        return True
    except (RateLimitError, APIError) as e:
        logger.error(f"OpenAI error for {paper.title}: {e}")
        paper.summary = "[OpenAI API error]"
        paper.save()
    except Exception as e:
        logger.error(f"Failed to summarize {paper.title}: {e}")
        paper.summary = "[Summary failed]"
        paper.save()
    return False


# === Main Task ===
@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def generate_review_task(self, task_id):
    try:
        task = ReviewTask.objects.get(id=task_id)
        task.status = 'running'
        task.current_stage = 'searching_openalex'
        task.save()

        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        pdf_dir = os.path.join(settings.MEDIA_ROOT, 'pdfs')
        os.makedirs(pdf_dir, exist_ok=True)

        paper_objs = []
        pdf_count = 0
        page = 1
        task.total_papers_target = 0

        # === Step 1: Fetch papers from OpenAlex ===
        while pdf_count < DESIRED_PDF_COUNT and page <= MAX_PAGES:
            url = settings.OPENALEX_WORKS_URL
            params = {
                'search': task.topic.replace(" ", "+"),
                'per_page': PER_PAGE,
                'page': page,
                'sort': 'cited_by_count:desc',
                'filter': 'has_abstract:true',
                'mailto': settings.OPENALEX_DEFAULT_MAILTO
            }
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            papers_data = response.json().get('results', [])

            if not papers_data:
                break

            for p_data in papers_data:
                oa_id = p_data['id'].split('/')[-1]
                doi = p_data.get('doi', '').replace('https://doi.org/', '')
                paper, _ = Paper.objects.get_or_create(
                    openalex_id=oa_id,
                    defaults={
                        'doi': doi,
                        'title': p_data.get('title', 'Unknown Title'),
                        'authors': [a['author'].get('display_name', 'Unknown') for a in p_data.get('authorships', [])],
                        'year': p_data.get('publication_year'),
                        'pdf_url': p_data.get('open_access', {}).get('oa_url')
                    }
                )
                paper.openalex_abstract = p_data.get('abstract_inverted_index', None)
                task.papers.add(paper)
                paper_objs.append(paper)
                task.total_papers_target += 1

                if download_pdf(paper, pdf_dir):
                    pdf_count += 1
                if pdf_count >= DESIRED_PDF_COUNT:
                    break

            page += 1
            task.papers_found = len(paper_objs)
            task.save()
            update_task_progress(task)

        # === Step 2: Extract Text concurrently ===
        task.current_stage = 'extracting_text'
        task.save()
        update_task_progress(task)

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(extract_text_from_pdf, p) for p in paper_objs]
            extract_count = sum(f.result() for f in futures)
        task.papers_extracted = extract_count
        task.save()
        update_task_progress(task)

        # === Step 3: Summarize Papers sequentially ===
        task.current_stage = 'summarizing_papers'
        task.save()
        summarize_count = 0
        for p in paper_objs:
            if summarize_paper(client, p, task):
                summarize_count += 1
                task.papers_summarized = summarize_count
                task.save()
                update_task_progress(task)

        # === Step 4: Generate Literature Review in batches ===
        task.current_stage = 'generating_review'
        task.save()
        update_task_progress(task)

        processed_papers = [
            {
                'title': p.title,
                'citation': f"({p.authors[0].split()[-1] if p.authors else 'Unknown'} et al., {p.year or 'n.d.'})",
                'summary': p.summary or getattr(p, 'openalex_abstract', None) or "[No text available]"
            } for p in paper_objs if p.summary or getattr(p, 'openalex_abstract', None)
        ]

        if not processed_papers:
            task.status = 'failed'
            task.error_message = "No papers were successfully processed."
            task.save()
            return

        # === Batch processing ===
        BATCH_SIZE = 6
        batches = [processed_papers[i:i + BATCH_SIZE] for i in range(0, len(processed_papers), BATCH_SIZE)]
        batch_reviews = []

        for idx, batch in enumerate(batches, start=1):
            batch_context = "\n\n".join(
                [f"[{p['citation']}] {p['title']}\nSummary: {p['summary']}" for p in batch]
            )
            batch_prompt = f"""
            Generate a detailed literature review section using the following papers (batch {idx} of {len(batches)}):

            User Request:
            {task.prompt}

            Provided Papers ({len(batch)}):
            {batch_context}

            Instructions:
            - Use only the provided sources.
            - Analyze, synthesize, and critically evaluate.
            - Include inline citations and maintain formal academic tone.
            """
            try:
                batch_resp = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": batch_prompt}],
                    max_tokens=MAX_OPENAI_TOKENS,
                    temperature=0.7
                )
                batch_text = batch_resp.choices[0].message.content.strip()
                batch_reviews.append(batch_text)
            except Exception as e:
                logger.error(f"Failed to generate review for batch {idx}: {e}")
                batch_reviews.append(f"[Batch {idx} failed to generate review]")

        final_review_text = "\n\n".join(batch_reviews)
        if len(final_review_text.split()) < MIN_REVIEW_WORDS:
            final_review_text += f"\n\n[Note: Review is shorter than {MIN_REVIEW_WORDS} words due to limited source material.]"

        task.result = sanitize_text(final_review_text)
        task.status = 'finished'
        task.current_stage = None
        task.progress_percent = 100.0
        task.save()

        logger.info(f"Review generated successfully for task {task.id}")

    except Exception as exc:
        task.status = 'failed'
        task.error_message = str(exc)
        task.current_stage = None
        task.save()
        logger.error(f"Task {task_id} failed: {exc}")
        raise exc
