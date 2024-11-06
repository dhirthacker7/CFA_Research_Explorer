import os
from dotenv import load_dotenv
from pinecone import Pinecone, ServerlessSpec
import logging
import pinecone


# Set up logging for better traceability
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Load environment variables
load_dotenv()

# Fetch Pinecone configuration from environment variables
API_KEY = os.getenv("PINECONE_API_KEY")
ENVIRONMENT = os.getenv("PINECONE_ENVIRONMENT", "us-east-1")
INDEX_NAME = os.getenv("PINECONE_INDEX_NAME", "embedding-index")
DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", 768))

# Initialize Pinecone client with error handling
if not API_KEY:
    raise EnvironmentError("PINECONE_API_KEY is not set in environment variables.")
pinecone_client = Pinecone(api_key=API_KEY)

# Clear all data with confirmation logging
def clear_index():
    """Deletes all data from the index with a logging prompt."""
    try:
        index = connect_to_index()
        logging.warning("Clearing all data from the index.")
        index.delete(delete_all=True)
    except Exception as e:
        logging.error(f"Error clearing index data: {e}")


# Function to initialize or connect to an index with logging
def connect_to_index(index_name=INDEX_NAME, dimension=DIMENSION):
    """Initializes or retrieves an index, creating it if necessary."""
    try:
        if index_name not in [idx.name for idx in pinecone_client.list_indexes()]:
            logging.info(f"Creating Pinecone index: {index_name}")
            pinecone_client.create_index(
                name=index_name,
                dimension=dimension,
                metric="cosine",
                spec=ServerlessSpec(cloud="aws", region=ENVIRONMENT)
            )
        return pinecone_client.index(index_name)
    except Exception as e:
        logging.error(f"Failed to create or connect to index: {e}")
        raise

# Store embeddings with more detailed metadata and logging
def insert_embeddings_with_logging(metadata, embedding_vector):
    """Inserts embeddings with additional checks and logging."""
    try:
        index = connect_to_index()
        doc_id = metadata.get("document_id", "default_id")
        logging.info(f"Inserting document {doc_id} into index.")
        index.upsert(vectors=[(doc_id, embedding_vector, metadata)])
    except Exception as e:
        logging.error(f"Error inserting embeddings: {e}")

# Retrieve embeddings with added validation
def fetch_embeddings_by_id(doc_id):
    """Fetches embeddings by document ID and logs the process."""
    try:
        index = connect_to_index()
        response = index.query(top_k=1, include_values=True, namespace="", id=doc_id)
        if response and response['matches']:
            logging.info(f"Embedding found for document ID {doc_id}")
            return response['matches'][0]['values']
        logging.warning(f"No embedding found for document ID {doc_id}")
    except Exception as e:
        logging.error(f"Error fetching embeddings: {e}")
    return None
