import logging
import os
from urllib.parse import urlparse

import fitz  # PyMuPDF
import openai
import requests
from celery import shared_task
from django.conf import settings
from django.core.files.base import ContentFile

from .models import SearchQuery, Paper, TaskStatus, LiteratureReview

logger = logging.getLogger(__name__)

# Configure OpenAI API key
openai.api_key = settings.OPENAI_API_KEY


def search_openalex(topic, max_results=30):
    """
    Search OpenAlex for papers on a given topic.
    Returns a list of dicts containing paper metadata.
    """
    url = settings.OPENALEX_WORKS_URL
    params = {
        "search": topic,
        "mailto": settings.OPENALEX_DEFAULT_MAILTO,
        "per-page": max_results,
    }

    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()

    papers_data = []
    for item in data.get("results", []):
        papers_data.append({
            "openalex_id": item.get("id"),
            "title": item.get("title"),
            "authors": ", ".join([a["author"]["display_name"] for a in item.get("authorships", [])]),
            "year": item.get("publication_year"),
            "doi": item.get("doi"),
            "url": item.get("id"),
            "open_access_pdf": item.get("open_access", {}).get("url_for_pdf"),
        })
    return papers_data


def download_pdf_for_paper(paper: Paper):
    """
    Download the PDF for a given Paper if open_access_pdf exists.
    Saves the file to the Paper.pdf_file field.
    """
    if not paper.open_access_pdf:
        logger.warning(f"No Open Access PDF URL for paper '{paper.title}'")
        paper.mark_failed("No Open Access PDF URL")
        return

    try:
        response = requests.get(paper.open_access_pdf, stream=True, timeout=30)
        response.raise_for_status()

        parsed_url = urlparse(paper.open_access_pdf)
        filename = os.path.basename(parsed_url.path)
        if not filename.lower().endswith(".pdf"):
            filename = f"{paper.id}.pdf"

        paper.pdf_file.save(filename, ContentFile(response.content), save=False)
        paper.mark_downloaded()
        logger.info(f"Downloaded PDF for paper '{paper.title}'")
    except Exception as e:
        paper.mark_failed(f"PDF download failed: {str(e)}")
        logger.error(f"Error downloading PDF for paper '{paper.title}': {e}")


def extract_text_from_pdf(paper: Paper):
    """
    Extract text from the PDF file of a Paper using PyMuPDF.
    """
    if not paper.pdf_file or not os.path.exists(paper.pdf_file.path):
        paper.mark_failed("PDF file missing for extraction")
        return None

    try:
        doc = fitz.open(paper.pdf_file.path)
        text = "\n".join([page.get_text() for page in doc])
        paper.mark_extracted(text)
        logger.info(f"Extracted text for paper '{paper.title}'")
        return text
    except Exception as e:
        paper.mark_failed(f"Text extraction failed: {str(e)}")
        logger.error(f"Error extracting text for paper '{paper.title}': {e}")
        return None


def generate_ai_review(papers_content, user_prompt):
    """
    Generate a literature review using OpenAI GPT-4 from paper contents.
    """
    content_text = "\n\n".join([f"Paper {i+1}:\n{content}" for i, content in enumerate(papers_content)])
    prompt = (
        f"You are an expert academic writer. "
        f"Generate a structured literature review (≥3000 words) "
        f"based on the following papers content:\n\n{content_text}\n\n"
        f"User request: {user_prompt}\n\n"
        "Include inline citations like (Author et al., Year) and a bibliography at the end in APA style."
    )

    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You are a scientific literature review assistant."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.3,
        max_tokens=4000
    )

    review_text = response.choices[0].message.content
    word_count = len(review_text.split())
    return review_text, word_count, "gpt-4"


@shared_task
def process_search_query(query_id):
    """
    Celery task to process a SearchQuery end-to-end:
    1. Search OpenAlex
    2. Download PDFs
    3. Extract text
    4. Generate AI review
    """
    try:
        query = SearchQuery.objects.get(id=query_id)

        # Create TaskStatus objects if they don't exist
        for task_type in ["search", "download", "extract", "generate"]:
            TaskStatus.objects.get_or_create(search_query=query, task_type=task_type)

        # ---------- 1. Search OpenAlex ----------
        search_task = query.tasks.get(task_type="search")
        search_task.status = "running"
        search_task.save(update_fields=["status", "timestamp"])
        logger.info(f"Started OpenAlex search for query '{query.topic}'")

        papers_data = search_openalex(query.topic)

        search_task.status = "completed"
        search_task.progress = 100
        search_task.save(update_fields=["status", "progress", "timestamp"])
        logger.info(f"Completed OpenAlex search for query '{query.topic}'")

        # ---------- 2. Download PDFs ----------
        download_task = query.tasks.get(task_type="download")
        download_task.status = "running"
        download_task.save(update_fields=["status", "timestamp"])
        logger.info(f"Started PDF download for query '{query.topic}'")

        for paper_data in papers_data:
            paper = Paper.objects.create(
                search_query=query,
                title=paper_data["title"],
                authors=paper_data["authors"],
                year=paper_data.get("year"),
                doi=paper_data.get("doi"),
                url=paper_data.get("url"),
                openalex_id=paper_data.get("openalex_id"),
                open_access_pdf=paper_data.get("open_access_pdf"),
                status="pending"
            )
            download_pdf_for_paper(paper)

        download_task.status = "completed"
        download_task.progress = 100
        download_task.save(update_fields=["status", "progress", "timestamp"])
        logger.info(f"Completed PDF download for query '{query.topic}'")

        # ---------- 3. Extract Text ----------
        extract_task = query.tasks.get(task_type="extract")
        extract_task.status = "running"
        extract_task.save(update_fields=["status", "timestamp"])
        logger.info(f"Started text extraction for query '{query.topic}'")

        papers_content = []
        for paper in query.papers.filter(status="downloaded"):
            text = extract_text_from_pdf(paper)
            if text:
                papers_content.append(text)

        extract_task.status = "completed"
        extract_task.progress = 100
        extract_task.save(update_fields=["status", "progress", "timestamp"])
        logger.info(f"Completed text extraction for query '{query.topic}'")

        # ---------- 4. Generate Literature Review ----------
        generate_task = query.tasks.get(task_type="generate")
        generate_task.status = "running"
        generate_task.save(update_fields=["status", "timestamp"])
        logger.info(f"Started AI review generation for query '{query.topic}'")

        if papers_content:
            review_text, word_count, model_name = generate_ai_review(papers_content, query.user_prompt)
            LiteratureReview.objects.create(
                search_query=query,
                generated_text=review_text,
                word_count=word_count,
                model_name=model_name,
                bibliography="(Automatically generated bibliography included in review text)"
            )

        generate_task.status = "completed"
        generate_task.progress = 100
        generate_task.save(update_fields=["status", "progress", "timestamp"])
        logger.info(f"Completed AI review generation for query '{query.topic}'")

    except Exception as e:
        logger.error(f"Error processing query {query_id}: {e}", exc_info=True)
        for task in query.tasks.all():
            task.status = "failed"
            task.message = str(e)
            task.save(update_fields=["status", "message", "timestamp"])
