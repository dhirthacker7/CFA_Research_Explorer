import base64
import hashlib
import os
import time
import requests
import tempfile
import re
import fitz  # PyMuPDF for PDF extraction
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer
from langchain.text_splitter import RecursiveCharacterTextSplitter
from pinecone import Pinecone, ServerlessSpec
import sys
import logging

# Extend the path for module imports
sys.path.append('/Users/nishitamatlani/Downloads/Assignment3_Nvidia')

# Load environment variables securely
load_dotenv()

# Configure logging for better debugging and traceability
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Pinecone configuration
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_REGION = "us-east-1"
DEFAULT_INDEX_NAME = "document-embeddings-index"

if not PINECONE_API_KEY:
    raise EnvironmentError("PINECONE_API_KEY is missing from the environment variables.")

pinecone_client = Pinecone(api_key=PINECONE_API_KEY)
_index_cache = {}

# Function to initialize or connect to a Pinecone index
def connect_or_create_index(index_name, dimension=384, metric='cosine'):
    if index_name in _index_cache:
        logging.info(f"Using cached index: {index_name}")
    else:
        existing_indexes = [idx.name for idx in pinecone_client.list_indexes()]
        if index_name not in existing_indexes:
            logging.info(f"Creating new Pinecone index: {index_name}")
            pinecone_client.create_index(
                name=index_name,
                dimension=dimension,
                metric=metric,
                spec=ServerlessSpec(cloud='aws', region=PINECONE_REGION)
            )
            time.sleep(3)  # Ensure index is properly set up before use
        _index_cache[index_name] = pinecone_client.Index(index_name)
    return _index_cache[index_name]

# Function to load a sentence transformer model securely
def load_model(model_type='sentence-transformers'):
    try:
        if model_type == 'sentence-transformers':
            return SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')
        else:
            raise ValueError("Invalid model type. Only 'sentence-transformers' is supported.")
    except Exception as e:
        logging.error(f"Model loading failed: {e}")
        raise

def create_chunk_embeddings(chunks, model):
    """Encodes chunks into embeddings with error handling."""
    try:
        return [model.encode(chunk).tolist() for chunk in chunks]
    except Exception as e:
        logging.error(f"Failed to create embeddings: {e}")
        return []

def generate_index_name_from_file(file_path):
    """Generates a unique index name based on file hash."""
    try:
        with open(file_path, "rb") as f:
            file_hash = hashlib.md5(f.read()).hexdigest()
        index_name = f"index-{file_hash[:8]}"
        return re.sub(r'[^a-z0-9-]', '-', index_name)
    except Exception as e:
        logging.error(f"Error generating index name: {e}")
        raise

# Redesigned function to download and verify PDF content
def download_pdf_file(url):
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_pdf:
            temp_pdf.write(response.content)
        logging.info(f"PDF downloaded successfully to {temp_pdf.name}")
        return temp_pdf.name
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to download PDF: {e}")
        raise ValueError("Unable to download PDF. Please verify the URL.")

# Extract text using PyMuPDF with added checks for text extraction
def extract_clean_text_from_pdf(pdf_path):
    text_content = []
    try:
        with fitz.open(pdf_path) as pdf:
            for page_number in range(pdf.page_count):
                page_text = sanitize_text(pdf[page_number].get_text())
                if page_text:
                    text_content.append(page_text)
        logging.info(f"Extracted text from {len(text_content)} pages.")
        return ' '.join(text_content)
    except Exception as e:
        logging.error(f"Error extracting text from PDF: {e}")
        return ''

def split_text_into_chunks(text, max_length=600, overlap=50):
    splitter = RecursiveCharacterTextSplitter(chunk_size=max_length, chunk_overlap=overlap)
    return [sanitize_text(chunk) for chunk in splitter.split_text(text)]

def upload_chunks_with_metadata(chunks, embeddings, pinecone_index):
    if not chunks or not embeddings:
        logging.warning("No chunks or embeddings to upload.")
        return

    data_records = [
        {"id": f"chunk-{i}", "values": embedding, "metadata": {"content": chunk}}
        for i, (embedding, chunk) in enumerate(zip(embeddings, chunks)) if embedding and chunk
    ]
    if data_records:
        logging.info(f"Uploading {len(data_records)} chunks to Pinecone index.")
        pinecone_index.upsert(vectors=data_records)

def sanitize_text(text):
    return re.sub(r'[^\x00-\x7F]+', ' ', text).replace('\n', ' ').strip()

# Query the index and return answers
def find_best_match(query, index, model):
    try:
        query_vector = model.encode(query).tolist()
        results = index.query(vector=query_vector, top_k=3, include_metadata=True)
        if results and "matches" in results:
            return "\n\n".join(
                match.get("metadata", {}).get("content", "").strip()
                for match in results["matches"] if match.get("metadata", {}).get("content", "")
            ) or "No relevant answer found."
    except Exception as e:
        logging.error(f"Error during query: {e}")
    return "No matches found."

# Full RAG process with modular design
def run_rag_pipeline(pdf_url, user_query, model_type='sentence-transformers'):
    pdf_path = download_pdf_file(pdf_url)
    raw_text = extract_clean_text_from_pdf(pdf_path)
    text_chunks = split_text_into_chunks(raw_text)
    model = load_model(model_type)
    chunk_embeddings = create_chunk_embeddings(text_chunks, model)
    index_name = generate_index_name_from_file(pdf_path)
    pinecone_index = connect_or_create_index(index_name)
    upload_chunks_with_metadata(text_chunks, chunk_embeddings, pinecone_index)
    answer = find_best_match(user_query, pinecone_index, model)
    os.remove(pdf_path)
    return answer
