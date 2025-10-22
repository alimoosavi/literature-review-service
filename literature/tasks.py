# literature/tasks.py
import logging
import os
import uuid

import fitz  # PyMuPDF
import requests
from celery import shared_task
from django.conf import settings
from openai import OpenAI, APIError, RateLimitError

from .models import ReviewTask, Paper

logger = logging.getLogger(__name__)


def sanitize_text(text):
    """Remove NUL characters and other problematic bytes from text."""
    if not text:
        return text
    # Remove NUL characters that PostgreSQL can't handle
    return text.replace('\x00', '').replace('\u0000', '')


def update_task_progress(task):
    """Calculate and update progress percentage based on current stage."""
    if not task.total_papers_target or task.total_papers_target == 0:
        task.progress_percent = 0.0
        return

    # Define stage weights (total = 100%)
    stages = {
        'searching_openalex': 5,
        'downloading_pdfs': 25,
        'extracting_text': 25,
        'summarizing_papers': 30,
        'generating_review': 15
    }

    progress = 0.0
    target = task.total_papers_target

    # Searching complete
    if task.papers_found > 0:
        progress += stages['searching_openalex']

    # Downloading progress
    if task.papers_downloaded > 0:
        download_progress = (task.papers_downloaded / target) * stages['downloading_pdfs']
        progress += download_progress

    # Extraction progress
    if task.papers_extracted > 0:
        extract_progress = (task.papers_extracted / target) * stages['extracting_text']
        progress += extract_progress

    # Summarization progress
    if task.papers_summarized > 0:
        summarize_progress = (task.papers_summarized / target) * stages['summarizing_papers']
        progress += summarize_progress

    # Final generation
    if task.current_stage == 'generating_review':
        progress += stages['generating_review']

    task.progress_percent = min(progress, 99.0)  # Cap at 99% until fully complete
    task.save(update_fields=['progress_percent'])


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def generate_review_task(self, task_id):
    try:
        task = ReviewTask.objects.get(id=task_id)
        task.status = 'running'
        task.current_stage = 'searching_openalex'
        task.save()

        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        if not client.api_key:
            raise ValueError("OPENAI_API_KEY not set in Django settings.")

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

        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            papers_data = response.json().get('results', [])
            if not papers_data:
                raise ValueError("OpenAlex returned no results for the topic.")
        except Exception as e:
            logger.error(f"OpenAlex search failed: {e}")
            raise ValueError(f"Failed to search OpenAlex: {str(e)}")

        logger.info(f"Found {len(papers_data)} papers on OpenAlex.")
        task.papers_found = len(papers_data)
        task.total_papers_target = len(papers_data)
        task.save()
        update_task_progress(task)

        # === Step 2: Process Papers ===
        task.current_stage = 'downloading_pdfs'
        task.save()

        pdf_dir = os.path.join(settings.MEDIA_ROOT, 'pdfs')
        os.makedirs(pdf_dir, exist_ok=True)

        processed_papers = []
        download_count = extract_count = summarize_count = 0
        paper_errors = []  # Track individual paper failures

        for idx, p_data in enumerate(papers_data[:30]):
            paper = None
            try:
                oa_id = p_data['id'].split('/')[-1]
                doi = p_data.get('doi')
                if doi:
                    doi = doi.replace('https://doi.org/', '')

                # Get or create Paper
                paper, created = Paper.objects.get_or_create(
                    openalex_id=oa_id,
                    defaults={
                        'doi': doi,
                        'title': p_data.get('title', 'Unknown Title'),
                        'authors': [a['author'].get('display_name', 'Unknown') for a in p_data.get('authorships', [])],
                        'year': p_data.get('publication_year'),
                        'pdf_url': p_data.get('open_access', {}).get('oa_url'),
                    }
                )

                # Update metadata
                paper.title = p_data.get('title', paper.title)
                paper.authors = [a['author'].get('display_name', '') for a in
                                 p_data.get('authorships', [])] or paper.authors
                paper.year = p_data.get('publication_year') or paper.year
                paper.pdf_url = p_data.get('open_access', {}).get('oa_url') or paper.pdf_url
                paper.save()

                task.papers.add(paper)

                # === Download PDF ===
                if paper.pdf_url and not paper.pdf_path:
                    try:
                        pdf_resp = requests.get(paper.pdf_url, timeout=30)
                        if pdf_resp.status_code == 200 and len(pdf_resp.content) > 50000:
                            pdf_filename = f"{uuid.uuid4()}.pdf"
                            pdf_path = os.path.join(pdf_dir, pdf_filename)
                            with open(pdf_path, 'wb') as f:
                                f.write(pdf_resp.content)
                            paper.pdf_path = os.path.join('pdfs', pdf_filename)
                            paper.save()
                            download_count += 1
                            logger.info(f"Downloaded PDF for: {paper.title}")
                    except Exception as e:
                        error_msg = f"PDF download failed for {paper.title}: {e}"
                        logger.warning(error_msg)
                        paper_errors.append(error_msg)

                task.papers_downloaded = download_count
                task.save()
                update_task_progress(task)

                # === Extract Text ===
                if paper.pdf_path and not paper.extracted_text:
                    task.current_stage = 'extracting_text'
                    task.save()

                    try:
                        full_path = os.path.join(settings.MEDIA_ROOT,
                                                 paper.pdf_path if not hasattr(paper.pdf_path, 'path')
                                                 else paper.pdf_path.path)
                        doc = fitz.open(full_path)
                        text = ""
                        for page in doc:
                            text += page.get_text()
                        doc.close()

                        if len(text.strip()) > 200:
                            # Sanitize text before saving
                            sanitized_text = sanitize_text(text[:100000])
                            paper.extracted_text = sanitized_text
                            paper.save()
                            extract_count += 1
                            logger.info(f"Extracted text for: {paper.title}")
                    except Exception as e:
                        error_msg = f"Text extraction failed for {paper.title}: {e}"
                        logger.warning(error_msg)
                        paper_errors.append(error_msg)

                task.papers_extracted = extract_count
                task.save()
                update_task_progress(task)

                # === Summarize ===
                if paper.extracted_text and not paper.summary:
                    task.current_stage = 'summarizing_papers'
                    task.save()

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
                            # Sanitize summary too
                            paper.summary = sanitize_text(summary)
                            summarize_count += 1
                            logger.info(f"Summarized: {paper.title}")
                        else:
                            paper.summary = "[Summary too short or invalid]"
                        paper.save()
                    except (RateLimitError, APIError) as e:
                        error_msg = f"OpenAI error for {paper.title}: {e}"
                        logger.error(error_msg)
                        paper.summary = "[OpenAI API error]"
                        paper.save()
                        paper_errors.append(error_msg)
                    except Exception as e:
                        error_msg = f"Summarization failed for {paper.title}: {e}"
                        logger.error(error_msg)
                        paper.summary = "[Summary failed]"
                        paper.save()
                        paper_errors.append(error_msg)

                task.papers_summarized = summarize_count
                task.save()
                update_task_progress(task)

                # === Collect for Review ===
                if paper.summary and "[failed]" not in paper.summary.lower() and len(paper.summary) > 100:
                    first_author = paper.authors[0].split()[-1] if paper.authors else "Unknown"
                    year = paper.year or "n.d."
                    citation = f"({first_author} et al., {year})"
                    processed_papers.append({
                        'title': paper.title,
                        'citation': citation,
                        'doi': paper.doi,
                        'summary': paper.summary
                    })

            except Exception as e:
                # Catch any unexpected errors for individual papers
                paper_title = paper.title if paper else f"Paper {idx + 1}"
                error_msg = f"Failed to process {paper_title}: {str(e)}"
                logger.error(error_msg)
                paper_errors.append(error_msg)
                # Continue with next paper instead of failing entire task

        # === Step 5: Generate Review (Allow Partial Success) ===
        if not processed_papers:
            error_summary = (
                f"No papers could be successfully processed for review generation.\n"
                f"Downloaded: {download_count}/{len(papers_data)}, "
                f"Extracted: {extract_count}/{download_count}, "
                f"Summarized: {summarize_count}/{extract_count}\n"
                f"Errors encountered: {len(paper_errors)}"
            )
            if paper_errors:
                error_summary += f"\n\nSample errors:\n" + "\n".join(paper_errors[:5])

            logger.error(error_summary)
            task.error_message = error_summary
            task.status = 'failed'
            task.save()
            return

        task.current_stage = 'generating_review'
        task.save()
        update_task_progress(task)

        context = "\n\n".join([
            f"[{p['citation']}] {p['title']}\nSummary: {p['summary']}"
            for p in processed_papers
        ])

        review_prompt = f"""
You are an expert academic writer. Write a **comprehensive literature review** of **at least 3000 words** based on:

**User Request**: {task.prompt}

**Available Sources** ({len(processed_papers)} papers):
{context}

**Requirements**:
- Use **only** the provided sources.
- Include **inline citations** like (Smith et al., 2023) after every claim.
- Structure: Introduction, Evolution, Methods, Findings, Challenges, Future Directions, Conclusion.
- End with **APA Bibliography**.
- Academic tone, logical flow, critical analysis.

Even if sources are limited, expand with analysis and synthesis.
"""

        try:
            review_resp = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": review_prompt}],
                max_tokens=4096,
                temperature=0.7
            )
            review_text = review_resp.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Final review generation failed: {e}")
            task.error_message = f"Failed to generate final review: {str(e)}"
            task.status = 'failed'
            task.save()
            return

        if len(review_text.split()) < 3000:
            review_text += "\n\n[Note: Review is shorter than 3000 words due to limited source material.]"

        # Add processing summary if there were errors
        if paper_errors:
            review_text += (
                f"\n\n---\n**Processing Notes**: "
                f"{len(processed_papers)} papers successfully processed out of {len(papers_data)} found. "
                f"{len(paper_errors)} papers encountered errors during processing."
            )

        task.result = sanitize_text(review_text)
        task.status = 'finished'
        task.current_stage = None
        task.progress_percent = 100.0
        task.save()

        logger.info(
            f"Review generated successfully. "
            f"{len(processed_papers)} papers used, "
            f"{len(paper_errors)} papers failed."
        )

    except Exception as exc:
        task.status = 'failed'
        task.error_message = str(exc)
        task.current_stage = None
        task.save()
        logger.error(f"Task {task_id} failed catastrophically: {exc}")
        raise exc