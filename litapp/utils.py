import os
import hashlib
import requests
import fitz  # PyMuPDF
from django.conf import settings

# Ensure PDF storage folder exists
PDF_STORAGE_DIR = os.path.join(settings.MEDIA_ROOT, "pdfs")
os.makedirs(PDF_STORAGE_DIR, exist_ok=True)


def get_pdf_cache_path(pdf_url: str) -> str:
    """
    Returns a deterministic path for cached PDF based on SHA256 hash of the URL.
    """
    hashed_name = hashlib.sha256(pdf_url.encode()).hexdigest()
    return os.path.join(PDF_STORAGE_DIR, f"{hashed_name}.pdf")


def download_pdf(pdf_url: str, openalex_id: str = None) -> str | None:
    """
    Downloads a PDF from the given URL and saves it in MEDIA_ROOT/pdfs/.
    Returns the absolute path to the cached file. If already cached, returns the path.
    """
    if not pdf_url:
        return None

    cache_path = get_pdf_cache_path(pdf_url)

    # Return cached file if it already exists
    if os.path.exists(cache_path):
        return cache_path

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; litRevAI/1.0; +https://example.com)"
        }
        response = requests.get(pdf_url, headers=headers, timeout=20)
        response.raise_for_status()

        with open(cache_path, "wb") as f:
            f.write(response.content)

        return cache_path
    except Exception as e:
        print(f"[download_pdf] Failed to download {pdf_url}: {e}")
        return None


def extract_text_from_pdf(pdf_path: str) -> str:
    """
    Extracts all text from a PDF file using PyMuPDF.
    Returns extracted text as a single string.
    """
    if not pdf_path or not os.path.exists(pdf_path):
        return ""

    text = ""
    try:
        doc = fitz.open(pdf_path)
        for page in doc:
            text += page.get_text("text") + "\n"
        doc.close()
    except Exception as e:
        print(f"[extract_text_from_pdf] Failed to extract text from {pdf_path}: {e}")

    return text.strip()


def fetch_openalex_works_data(topic: str, per_page: int = 30) -> list:
    """
    Fetches works data from OpenAlex API using a direct requests call.
    Implements search by topic and filters for open access journal articles.
    """
    # Use URL-encoding for the search query
    search_query = topic.replace(" ", "%20")

    # Define the parameters for the API call
    params = {
        'search': search_query,
        'filter': 'is_oa:true,type:journal-article',  # Only Open Access journal articles
        'sort': 'cited_by_count:desc',  # Highest cited first
        'per-page': per_page,
        'mailto': settings.OPENALEX_DEFAULT_MAILTO  # Polite pool email
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; LitRevGenerator/1.0; mailto:{DEFAULT_MAILTO})"
    }

    try:
        response = requests.get(settings.OPENALEX_WORKS_URL, params=params, headers=headers, timeout=30)
        response.raise_for_status()

        data = response.json()
        # OpenAlex returns the list of works in the 'results' key
        return data.get('results', [])

    except requests.exceptions.RequestException as e:
        print(f"[OpenAlex API] Request Failed for topic '{topic}': {e}")
        return []
