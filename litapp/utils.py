import os
import hashlib
import logging
from typing import Optional
from urllib.parse import urlparse

import requests
import fitz  # PyMuPDF
from django.conf import settings
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# Ensure PDF storage folder exists
PDF_STORAGE_DIR = os.path.join(settings.MEDIA_ROOT, "pdfs")
os.makedirs(PDF_STORAGE_DIR, exist_ok=True)


def get_requests_session() -> requests.Session:
    """
    Returns a requests session with retry logic and timeouts.
    Handles transient failures gracefully.
    """
    session = requests.Session()

    # Retry strategy: retry on 500, 502, 503, 504 status codes
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,  # Wait 1, 2, 4 seconds between retries
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS"]
    )

    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    return session


def get_pdf_cache_path(pdf_url: str) -> str:
    """
    Returns a deterministic path for cached PDF based on SHA256 hash of the URL.
    """
    hashed_name = hashlib.sha256(pdf_url.encode()).hexdigest()
    return os.path.join(PDF_STORAGE_DIR, f"{hashed_name}.pdf")


def is_valid_pdf_url(url: str) -> bool:
    """
    Basic validation for PDF URLs.
    """
    if not url:
        return False

    try:
        parsed = urlparse(url)
        if not parsed.scheme in ['http', 'https']:
            return False
        if not parsed.netloc:
            return False
        return True
    except Exception:
        return False


def download_pdf(pdf_url: str, openalex_id: Optional[str] = None) -> Optional[str]:
    """
    Downloads a PDF from the given URL and saves it in MEDIA_ROOT/pdfs/.
    Returns the absolute path to the cached file. If already cached, returns the path.

    Args:
        pdf_url: URL of the PDF to download
        openalex_id: OpenAlex ID for logging purposes

    Returns:
        Absolute path to cached PDF file, or None on failure
    """
    if not is_valid_pdf_url(pdf_url):
        logger.warning(f"Invalid PDF URL: {pdf_url}")
        return None

    cache_path = get_pdf_cache_path(pdf_url)

    # Return cached file if it already exists and is valid
    if os.path.exists(cache_path):
        # Verify file is not empty and appears to be a PDF
        if os.path.getsize(cache_path) > 1024:  # At least 1KB
            logger.debug(f"Using cached PDF: {cache_path}")
            return cache_path
        else:
            # Remove corrupted cache
            logger.warning(f"Removing corrupted cache file: {cache_path}")
            os.remove(cache_path)

    try:
        session = get_requests_session()
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; LitRevAI/1.0; +https://example.com)",
            "Accept": "application/pdf"
        }

        logger.info(f"Downloading PDF from: {pdf_url[:100]}...")

        response = session.get(
            pdf_url,
            headers=headers,
            timeout=30,
            stream=True  # Stream large files
        )
        response.raise_for_status()

        # Verify content type
        content_type = response.headers.get('content-type', '').lower()
        if 'pdf' not in content_type and 'octet-stream' not in content_type:
            logger.warning(
                f"Unexpected content type for {pdf_url}: {content_type}"
            )
            # Continue anyway as some servers don't set correct content-type

        # Check file size (skip if > 50MB)
        content_length = response.headers.get('content-length')
        if content_length and int(content_length) > 50 * 1024 * 1024:
            logger.warning(f"PDF too large ({content_length} bytes): {pdf_url}")
            return None

        # Write to file in chunks
        with open(cache_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        file_size = os.path.getsize(cache_path)
        logger.info(
            f"Successfully downloaded PDF ({file_size} bytes) "
            f"for OpenAlex ID: {openalex_id}"
        )

        return cache_path

    except requests.exceptions.Timeout:
        logger.error(f"Timeout downloading PDF: {pdf_url}")
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error downloading PDF {pdf_url}: {e}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Request error downloading PDF {pdf_url}: {e}")
    except IOError as e:
        logger.error(f"IO error saving PDF to {cache_path}: {e}")
    except Exception as e:
        logger.exception(f"Unexpected error downloading PDF {pdf_url}: {e}")

    # Clean up partial downloads
    if os.path.exists(cache_path):
        try:
            os.remove(cache_path)
        except Exception:
            pass

    return None


def extract_text_from_pdf(pdf_path: str) -> str:
    """
    Extracts all text from a PDF file using PyMuPDF.
    Returns extracted text as a single string.

    Args:
        pdf_path: Absolute path to the PDF file

    Returns:
        Extracted text, or empty string on failure
    """
    if not pdf_path or not os.path.exists(pdf_path):
        logger.warning(f"PDF file not found: {pdf_path}")
        return ""

    # Check file size
    file_size = os.path.getsize(pdf_path)
    if file_size == 0:
        logger.warning(f"Empty PDF file: {pdf_path}")
        return ""

    if file_size > 100 * 1024 * 1024:  # 100MB limit
        logger.warning(f"PDF too large for extraction ({file_size} bytes): {pdf_path}")
        return ""

    text_parts = []

    try:
        doc = fitz.open(pdf_path)

        # Limit to first 100 pages to avoid excessive processing
        max_pages = min(len(doc), 100)

        for page_num in range(max_pages):
            try:
                page = doc[page_num]
                page_text = page.get_text("text")

                if page_text:
                    text_parts.append(page_text)

            except Exception as e:
                logger.warning(f"Error extracting page {page_num} from {pdf_path}: {e}")
                continue

        doc.close()

        full_text = "\n".join(text_parts).strip()

        if full_text:
            logger.info(
                f"Successfully extracted {len(full_text)} characters "
                f"from {max_pages} pages of {pdf_path}"
            )
        else:
            logger.warning(f"No text extracted from PDF: {pdf_path}")

        return full_text

    except fitz.FileDataError:
        logger.error(f"Corrupted or invalid PDF file: {pdf_path}")
    except fitz.EmptyFileError:
        logger.error(f"Empty PDF file: {pdf_path}")
    except Exception as e:
        logger.exception(f"Unexpected error extracting text from {pdf_path}: {e}")

    return ""


def fetch_openalex_works_data(topic: str, per_page: int = 30) -> list:
    """
    Fetches works data from OpenAlex API using direct requests call.
    Implements search by topic and filters for open access journal articles.

    Args:
        topic: Search topic/query
        per_page: Number of results to return (max 200)

    Returns:
        List of paper dictionaries from OpenAlex API
    """
    if not topic or not topic.strip():
        logger.error("Empty topic provided to fetch_openalex_works_data")
        return []

    # Validate per_page parameter
    per_page = min(max(1, per_page), 200)  # OpenAlex max is 200

    # Build query parameters
    params = {
        'search': topic.strip(),
        'filter': 'is_oa:true,type:journal-article',  # Open Access only
        'sort': 'cited_by_count:desc',  # Most cited first
        'per-page': per_page,
        # 'mailto': getattr(settings, 'OPENALEX_DEFAULT_MAILTO', 'admin@example.com')
    }

    headers = {
        # "User-Agent": f"Mozilla/5.0 (compatible; LitRevGenerator/1.0; mailto:{params['mailto']})"
        "User-Agent": f"Mozilla/5.0 (compatible; LitRevGenerator/1.0)"
    }

    try:
        session = get_requests_session()

        api_url = getattr(
            settings,
            'OPENALEX_WORKS_URL',
            'https://api.openalex.org/works'
        )

        logger.info(f"Querying OpenAlex API for topic: '{topic}' (per_page={per_page})")

        response = session.get(
            api_url,
            params=params,
            headers=headers,
            timeout=30
        )
        response.raise_for_status()

        data = response.json()
        results = data.get('results', [])

        logger.info(
            f"OpenAlex API returned {len(results)} results for topic '{topic}'. "
            f"Total available: {data.get('meta', {}).get('count', 'unknown')}"
        )

        return results

    except requests.exceptions.Timeout:
        logger.error(f"Timeout querying OpenAlex API for topic '{topic}'")
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error from OpenAlex API for topic '{topic}': {e}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Request error querying OpenAlex API for topic '{topic}': {e}")
    except ValueError as e:
        logger.error(f"JSON decode error from OpenAlex API response: {e}")
    except Exception as e:
        logger.exception(f"Unexpected error querying OpenAlex API for topic '{topic}': {e}")

    return []


def clean_old_cached_pdfs(days_old: int = 30) -> int:
    """
    Utility function to clean up old cached PDFs.
    Can be called from a management command or periodic task.

    Args:
        days_old: Remove PDFs older than this many days

    Returns:
        Number of files removed
    """
    import time

    removed_count = 0
    cutoff_time = time.time() - (days_old * 86400)

    try:
        for filename in os.listdir(PDF_STORAGE_DIR):
            filepath = os.path.join(PDF_STORAGE_DIR, filename)

            if os.path.isfile(filepath):
                file_mtime = os.path.getmtime(filepath)

                if file_mtime < cutoff_time:
                    try:
                        os.remove(filepath)
                        removed_count += 1
                        logger.debug(f"Removed old cached PDF: {filename}")
                    except Exception as e:
                        logger.warning(f"Failed to remove {filepath}: {e}")

        logger.info(f"Cleaned up {removed_count} cached PDFs older than {days_old} days")

    except Exception as e:
        logger.exception(f"Error during PDF cleanup: {e}")

    return removed_count