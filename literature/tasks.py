# literature/tasks.py
import os
import uuid
import logging
import requests
import fitz  # PyMuPDF
from celery import shared_task
from django.conf import settings
from openai import OpenAI, APIError, RateLimitError
from .models import ReviewTask, Paper

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def generate_review_task(self, task_id):
    try:
        task = ReviewTask.objects.get(id=task_id)
        task.status = 'running'
        task.current_stage = 'searching_openalex'
        task.save()

        client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
        if not client.api_key:
            raise ValueError("OPENAI_API_KEY not set in environment.")

        # === Step 1: Search OpenAlex ===
        query = task.topic.replace(" ", "+")
        url = "https://api.openalex.org/works"
        params = {
            'search': query,
            'per_page': 30,
            'sort': 'cited_by_count:desc',
            'filter': 'has_abstract:true'
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

        # === Step 2: Process Papers (Reuse + Download + Extract + Summarize) ===
        task.current_stage = 'downloading_pdfs'
        task.save()

        pdf_dir = os.path.join(settings.MEDIA_ROOT, 'pdfs')
        os.makedirs(pdf_dir, exist_ok=True)

        processed_papers = []
        download_count = extract_count = summarize_count = 0

        for p_data in papers_data[:30]:
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
            paper.authors = [a['author'].get('display_name', '') for a in p_data.get('authorships', [])] or paper.authors
            paper.year = p_data.get('publication_year') or paper.year
            paper.pdf_url = p_data.get('open_access', {}).get('oa_url') or paper.pdf_url
            paper.save()

            task.papers.add(paper)

            # === Download PDF ===
            if paper.pdf_url and not paper.pdf_path:
                try:
                    pdf_resp = requests.get(paper.pdf_url, timeout=30)
                    if pdf_resp.status_code == 200 and len(pdf_resp.content) > 50000:  # Basic size check
                        pdf_filename = f"{uuid.uuid4()}.pdf"
                        pdf_path = os.path.join(pdf_dir, pdf_filename)
                        with open(pdf_path, 'wb') as f:
                            f.write(pdf_resp.content)
                        paper.pdf_path = os.path.join('pdfs', pdf_filename)
                        paper.save()
                        download_count += 1
                        logger.info(f"Downloaded PDF for: {paper.title}")
                except Exception as e:
                    logger.warning(f"PDF download failed for {paper.title}: {e}")

            # === Extract Text ===
            if paper.pdf_path and not paper.extracted_text:
                task.current_stage = 'extracting_text'
                task.save()

                try:
                    full_path = os.path.join(settings.MEDIA_ROOT, paper.pdf_path.path if hasattr(paper.pdf_path, 'path') else paper.pdf_path)
                    doc = fitz.open(full_path)
                    text = ""
                    for page in doc:
                        text += page.get_text()
                    doc.close()

                    if len(text.strip()) > 200:  # Valid text
                        paper.extracted_text = text[:100000]
                        paper.save()
                        extract_count += 1
                        logger.info(f"Extracted text for: {paper.title}")
                except Exception as e:
                    logger.warning(f"Text extraction failed for {paper.title}: {e}")

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
                        paper.summary = summary
                        summarize_count += 1
                        logger.info(f"Summarized: {paper.title}")
                    else:
                        paper.summary = "[Summary too short or invalid]"
                    paper.save()
                except (RateLimitError, APIError) as e:
                    logger.error(f"OpenAI error for {paper.title}: {e}")
                    paper.summary = "[OpenAI API error]"
                    paper.save()
                except Exception as e:
                    logger.error(f"Summarization failed for {paper.title}: {e}")
                    paper.summary = "[Summary failed]"
                    paper.save()

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

        # === Step 5: Generate Review (Allow Partial Success) ===
        if not processed_papers:
            error_msg = (
                f"No papers could be summarized. "
                f"Downloaded: {download_count}, "
                f"Extracted: {extract_count}, "
                f"Summarized: {summarize_count}"
            )
            logger.error(error_msg)
            raise ValueError(error_msg)

        task.current_stage = 'generating_review'
        task.save()

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
            raise ValueError(f"Failed to generate final review: {str(e)}")

        if len(review_text.split()) < 3000:
            review_text += "\n\n[Note: Review is shorter than 3000 words due to limited source material.]"

        task.result = review_text
        task.status = 'finished'
        task.current_stage = None
        task.save()

        logger.info(f"Review generated successfully. {len(processed_papers)} papers used.")

    except Exception as exc:
        task.status = 'failed'
        task.error_message = str(exc)
        task.current_stage = None
        task.save()
        logger.error(f"Task failed: {exc}")
        raise exc