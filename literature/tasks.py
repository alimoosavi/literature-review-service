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
MAX_WORKERS = 5  # Concurrency for PDF download and text extraction
MIN_REVIEW_WORDS = 3000  # Minimum words for literature review
PDF_MIN_SIZE = 50000  # Minimum PDF size in bytes
MAX_OPENAI_TOKENS = 4096  # Max tokens for OpenAI calls

# === Helper Functions ===
def sanitize_text(text):
    """Remove NUL characters and other problematic bytes from text."""
    if not text:
        return text
    return text.replace('\x00', '').replace('\u0000', '')

def update_task_progress(task):
    """Update progress percentage based on task stage."""
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
    """Download PDF in a blocking way (thread-safe)."""
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
    """Extract text from PDF using PyMuPDF."""
    if not paper.pdf_path or paper.extracted_text:
        return False
    try:
        # Handle FieldFile properly
        if hasattr(paper.pdf_path, 'path'):
            full_path = paper.pdf_path.path
        else:
            full_path = os.path.join(settings.MEDIA_ROOT, str(paper.pdf_path))

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

# === Summarization ===
def summarize_paper(client, paper, task):
    """Summarize paper using OpenAI."""
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

# === Main Celery Task ===
@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def generate_review_task(self, task_id):
    try:
        task = ReviewTask.objects.get(id=task_id)
        task.status = 'running'
        task.current_stage = 'searching_openalex'
        task.save()

        client = OpenAI(api_key=settings.OPENAI_API_KEY)

        # === Step 1: Search OpenAlex ===
        query = task.topic.replace(" ", "+")
        url = settings.OPENALEX_WORKS_URL
        params = {
            'search': query,
            'per_page': 30,
            'sort': 'cited_by_count:desc',
            'filter': 'has_abstract:true',
            'mailto': settings.OPENALEX_DEFAULT_MAILTO
        }
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        papers_data = response.json().get('results', [])

        task.papers_found = len(papers_data)
        task.total_papers_target = len(papers_data)
        task.save()
        update_task_progress(task)

        # === Step 2: Create/Update Papers ===
        pdf_dir = os.path.join(settings.MEDIA_ROOT, 'pdfs')
        os.makedirs(pdf_dir, exist_ok=True)
        paper_objs = []
        for p_data in papers_data:
            oa_id = p_data['id'].split('/')[-1]
            doi = p_data.get('doi')
            if doi:
                doi = doi.replace('https://doi.org/', '')
            paper, _ = Paper.objects.get_or_create(
                openalex_id=oa_id,
                defaults={
                    'doi': doi,
                    'title': p_data.get('title', 'Unknown Title'),
                    'authors': [a['author'].get('display_name', 'Unknown') for a in p_data.get('authorships', [])],
                    'year': p_data.get('publication_year'),
                    'pdf_url': p_data.get('open_access', {}).get('oa_url'),
                }
            )
            task.papers.add(paper)
            paper_objs.append(paper)

        # === Step 3: Download PDFs using threads ===
        task.current_stage = 'downloading_pdfs'
        task.save()
        update_task_progress(task)

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(download_pdf, p, pdf_dir) for p in paper_objs]
            download_count = sum(f.result() for f in futures)
        task.papers_downloaded = download_count
        task.save()
        update_task_progress(task)

        # === Step 4: Extract Text concurrently ===
        task.current_stage = 'extracting_text'
        task.save()
        update_task_progress(task)

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(extract_text_from_pdf, p) for p in paper_objs]
            extract_count = sum(f.result() for f in futures)
        task.papers_extracted = extract_count
        task.save()
        update_task_progress(task)

        # === Step 5: Summarize Papers sequentially ===
        task.current_stage = 'summarizing_papers'
        task.save()
        summarize_count = 0
        for p in paper_objs:
            if summarize_paper(client, p, task):
                summarize_count += 1
                task.papers_summarized = summarize_count
                task.save()
                update_task_progress(task)

        # === Step 6: Generate Literature Review ===
        task.current_stage = 'generating_review'
        task.save()
        update_task_progress(task)

        processed_papers = [
            {
                'title': p.title,
                'citation': f"({p.authors[0].split()[-1] if p.authors else 'Unknown'} et al., {p.year or 'n.d.'})",
                'doi': p.doi,
                'summary': p.summary
            } for p in paper_objs if p.summary and "[failed]" not in p.summary.lower()
        ]

        if not processed_papers:
            task.status = 'failed'
            task.error_message = "No papers were successfully processed."
            task.save()
            return

        context = "\n\n".join([f"[{p['citation']}] {p['title']}\nSummary: {p['summary']}" for p in processed_papers])

        # === Improved review prompt ===
        review_prompt = f"""
        Output Format
        The final literature review must:
        • Contain at least {MIN_REVIEW_WORDS} words
        • Include inline citations like (Smith et al., 2023) for each claim
        • End with a bibliography list of all cited papers
        • Follow APA or IEEE style (you may choose APA)
        • Be structured into these sections:
          1. Introduction
          2. Historical evolution / background
          3. Methods and approaches used in the papers
          4. Key findings and results
          5. Research gaps and challenges
          6. Future directions and opportunities
          7. Conclusion

        User Request:
        {task.prompt}

        Provided Papers ({len(processed_papers)}):
        {context}

        Instructions:
        - Use only the provided sources.
        - Analyze, synthesize, and critically evaluate the studies.
        - Highlight similarities, differences, and contradictions between studies.
        - Maintain a formal academic tone suitable for a journal article.
        - Include inline citations in the text and a complete reference list at the end.
        - Ensure logical flow between sections.
        """

        review_resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": review_prompt}],
            max_tokens=MAX_OPENAI_TOKENS,
            temperature=0.7
        )

        review_text = review_resp.choices[0].message.content.strip()
        if len(review_text.split()) < MIN_REVIEW_WORDS:
            review_text += f"\n\n[Note: Review is shorter than {MIN_REVIEW_WORDS} words due to limited source material.]"

        task.result = sanitize_text(review_text)
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
