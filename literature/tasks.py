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
MAX_PAGES = 5
BATCH_SIZE = 6  # number of papers per batch


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


# === PDF Text Extraction ===
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


# === Paper Summarization ===
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
        paper.summary = sanitize_text(summary) if summary and len(summary) > 100 else "[Summary too short or invalid]"
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

        # === Step 2: Extract text concurrently ===
        task.current_stage = 'extracting_text'
        task.save()
        update_task_progress(task)

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(extract_text_from_pdf, p) for p in paper_objs]
            extract_count = sum(f.result() for f in futures)
        task.papers_extracted = extract_count
        task.save()
        update_task_progress(task)

        # === Step 3: Summarize papers sequentially ===
        task.current_stage = 'summarizing_papers'
        task.save()
        summarize_count = 0
        for p in paper_objs:
            if summarize_paper(client, p, task):
                summarize_count += 1
                task.papers_summarized = summarize_count
                task.save()
                update_task_progress(task)

        # === Step 4: Batch processing ===
        task.current_stage = 'generating_review'
        task.save()
        update_task_progress(task)

        processed_papers = [
            {
                'title': p.title,
                'authors': p.authors,
                'year': p.year,
                'doi': p.doi,
                'citation': f"({p.authors[0].split()[-1] if p.authors else 'Unknown'} et al., {p.year or 'n.d.'})",
                'summary': p.summary or getattr(p, 'openalex_abstract', None) or "[No text available]"
            } for p in paper_objs if p.summary or getattr(p, 'openalex_abstract', None)
        ]

        if not processed_papers:
            task.status = 'failed'
            task.error_message = "No papers were successfully processed."
            task.save()
            return

        batches = [processed_papers[i:i + BATCH_SIZE] for i in range(0, len(processed_papers), BATCH_SIZE)]
        batch_summaries = []

        for idx, batch in enumerate(batches, start=1):
            batch_context = "\n\n".join(
                [
                    f"[{p['citation']}] {p['title']}\nAuthors: {', '.join(p['authors'])}\nYear: {p['year']}\nDOI: {p['doi']}\nSummary: {p['summary']}"
                    for p in batch]
            )
            batch_prompt = f"""
Generate a structured summary for this batch of papers (batch {idx} of {len(batches)}).

User Request:
{task.prompt}

Provided Papers:
{batch_context}

Instructions:
- Include a synthesized summary for the batch.
- Maintain formal academic tone.
- Include inline citations.
- Preserve paper list with title, authors, year, DOI.
"""
            try:
                batch_resp = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": batch_prompt}],
                    max_tokens=MAX_OPENAI_TOKENS,
                    temperature=0.7
                )
                batch_summaries.append(batch_resp.choices[0].message.content.strip())
            except Exception as e:
                logger.error(f"Failed to generate review for batch {idx}: {e}")
                batch_summaries.append(f"[Batch {idx} failed to generate review]")

        # === Step 5: Final Aggregation using provided prompt ===
        final_context = "\n\n".join(batch_summaries)
        final_prompt = f"""
        You are tasked with generating a comprehensive, structured literature review from provided batch summaries of scientific papers.

        Output Format:
        - The final review must contain at least {MIN_REVIEW_WORDS} words.
        - Include inline citations like (Smith et al., 2023) for every claim.
        - Include a bibliography / reference list at the end.
        - Follow APA or IEEE citation style consistently.
        - Structure the review into the following sections:
          1. Introduction
          2. Historical evolution / Background
          3. Methods and approaches
          4. Key findings and results
          5. Research gaps and challenges
          6. Future directions
          7. Conclusion
        - Include the full list of all papers used with their title, authors, year, and DOI.

        Few-shot Examples:

        Example 1:
        User Input:
        Search topic: "Machine learning for catalyst design"
        User request: "Focus the review on recent deep learning approaches for optimizing catalytic reactions."

        Generated Review Excerpt:
        "Recent progress in deep learning has accelerated catalyst discovery (Li & Chen, 2022). Graph neural networks are increasingly used for activity prediction (Zhao et al., 2023)..."

        References:
        Li, J., & Chen, Y. (2022). Graph neural networks for catalytic site prediction. *Journal of Catalysis, 414*, 210-225.
        Zhao, K., et al. (2023). Deep learning for catalyst design. *Nature Communications, 14*(5), 2345.

        Example 2:
        User Input:
        Search topic: "CRISPR gene editing in agriculture"
        User request: "Focus on recent breakthroughs and their impact on crop yield and disease resistance."

        Generated Review Excerpt:
        "CRISPR-Cas9 has revolutionized plant genetic engineering, enabling precise genome edits to improve crop resistance (Smith et al., 2021). Recent studies demonstrate increased yield and pathogen resistance in edited rice and tomato varieties (Wang et al., 2022)..."

        References:
        Smith, A., et al. (2021). CRISPR-Cas9 in crop improvement. *Plant Biotechnology Journal, 19*(8), 1602-1615.
        Wang, B., et al. (2022). CRISPR-mediated disease resistance in crops. *Nature Plants, 8*, 456-467.

        Instructions:
        - Use only the information provided in the batch summaries below.
        - Synthesize, analyze, and critically evaluate the content.
        - Maintain formal academic tone throughout.
        - Include inline citations wherever necessary.
        - Produce at least {MIN_REVIEW_WORDS} words.
        - Conclude with a reference list of all papers used.

        User Request:
        {task.prompt}

        Batch Summaries:
        {final_context}
        """

        try:
            final_resp = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": final_prompt}],
                max_tokens=MAX_OPENAI_TOKENS,
                temperature=0.7
            )
            final_review_text = final_resp.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Failed to generate final review: {e}")
            final_review_text = final_context + f"\n\n[Final review generation failed, showing batch summaries]"

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
