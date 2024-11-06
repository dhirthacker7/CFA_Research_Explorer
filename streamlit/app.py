import os
import streamlit as st
import requests
import datetime
from streamlit_option_menu import option_menu
from dotenv import load_dotenv
from langchain_nvidia_ai_endpoints import ChatNVIDIA
import boto3
import tempfile
from langchain_community.document_loaders import PyPDFLoader
from RAG import run_rag_pipeline  # Correctly import your own module, assuming it's in the same directory
import snowflake.connector
import pinecone



# Load environment variables from the .env file
load_dotenv()

# FastAPI backend URL from .env file
FASTAPI_URL = os.getenv("FASTAPI_URL", "http://127.0.0.1:8000")  # Default to localhost if not found

# NVIDIA API Key for LLM
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
if not NVIDIA_API_KEY:
    raise ValueError("NVIDIA_API_KEY is not loaded. Please check your .env file.")
os.environ["NVIDIA_API_KEY"] = NVIDIA_API_KEY

# Initialize NVIDIA LLM
llm = ChatNVIDIA(model="mistralai/mixtral-8x7b-instruct-v0.1", max_tokens=1024)

# AWS S3 configurations
BUCKET_NAME = os.getenv("BUCKET_NAME")
S3_PDFS_FOLDER = os.getenv("S3_PDFS_FOLDER")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION")

# Initialize S3 client
s3_client = boto3.client(
    's3',
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_REGION
)

def create_snowflake_connection():
    try:
        conn = snowflake.connector.connect(
            user=os.getenv("SNOWFLAKE_USER"),
            password=os.getenv("SNOWFLAKE_PASSWORD"),
            account=os.getenv("SNOWFLAKE_ACCOUNT"),
            warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
            database=os.getenv("SNOWFLAKE_DATABASE"),
            schema=os.getenv("SNOWFLAKE_SCHEMA")
        )
        return conn
    except Exception as e:
        st.error(f"Error connecting to Snowflake: {e}")
        return None
    
# Function to register a new user
def register_user(username, password):
    response = requests.post(f"{FASTAPI_URL}/signup?username={username}&password={password}")
    return response.json()

# Function to login and retrieve JWT token
def login_user(username, password):
    response = requests.post(f"{FASTAPI_URL}/login?username={username}&password={password}")
    return response.json()

# Function to check if the session is expired
def is_session_expired():
    if "token_expiration" not in st.session_state:
        return True
    current_time = datetime.datetime.utcnow()
    return current_time >= st.session_state["token_expiration"]

# Function to fetch publications
def fetch_publications():
    response = requests.get(f"{FASTAPI_URL}/publications")
    return response.json().get("publications", [])

# List PDFs in S3
def list_pdfs_from_s3():
    response = s3_client.list_objects_v2(Bucket=BUCKET_NAME, Prefix=S3_PDFS_FOLDER)
    pdfs = [obj["Key"] for obj in response.get("Contents", []) if obj["Key"].endswith(".pdf")]
    return pdfs

# Load a PDF from S3
def load_pdf_from_s3(bucket_name: str, s3_key: str):
    pdf_obj = s3_client.get_object(Bucket=bucket_name, Key=s3_key)
    pdf_content = pdf_obj["Body"].read()

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_pdf:
        tmp_pdf.write(pdf_content)
        tmp_pdf_path = tmp_pdf.name

    loader = PyPDFLoader(tmp_pdf_path)
    documents = loader.load()
    os.remove(tmp_pdf_path)
    
    return documents

# Show the signup page
def show_signup_page():
    st.subheader("Signup Page")
    new_username = st.text_input("Create a Username")
    new_password = st.text_input("Create a Password", type="password")
    confirm_password = st.text_input("Confirm Password", type="password")

    if st.button("Signup"):
        if new_password == confirm_password:
            result = register_user(new_username, new_password)
            st.success(result.get("msg", "Signup successful"))
        else:
            st.warning("Passwords do not match!")

# Show the login page
def show_login_page():
    st.subheader("Login Page")
    username = st.text_input("Username")
    password = st.text_input("Password", type="password")

    if st.button("Login"):
        result = login_user(username, password)
        if "access_token" in result:
            st.success(f"Login Successful! Welcome, {username}!")
            st.session_state["access_token"] = result["access_token"]
            st.session_state["username"] = username
            st.session_state["token_expiration"] = datetime.datetime.utcnow() + datetime.timedelta(minutes=15)
            st.session_state["logged_in"] = True
            st.rerun()
        else:
            st.error(result.get("detail", "Login Failed"))

# Show the user profile page
def show_profile_page():
    st.subheader("User Profile")
    if "access_token" in st.session_state:
        profile_data = view_profile(st.session_state["access_token"])
        if "username" in profile_data:
            st.write(f"Username: {profile_data['username']}")
            st.write(f"Created At: {profile_data['created_at']}")
        else:
            st.error(profile_data.get("detail", "Could not retrieve profile"))
    else:
        st.warning("You need to login first.")

# Show the update password page
def show_update_password_page():
    st.subheader("Update Password")
    old_password = st.text_input("Old Password", type="password")
    new_password = st.text_input("New Password", type="password")

    if st.button("Update Password"):
        result = update_password(old_password, new_password, st.session_state["access_token"])
        if "msg" in result:
            st.success(result["msg"])
        else:
            st.error(result.get("detail", "Password update failed"))

# Show Explore Documents section
def show_explore_documents(publications):
    st.header("Explore Documents")
    
    # Custom CSS for image borders
    st.markdown("""
        <style>
            .bordered-image {
                border: 2px solid #4CAF50;  /* Green border color */
                border-radius: 5px;         /* Rounded corners */
                padding: 5px;               /* Space between border and image */
            }
            .default-image {
                width: 100px;               /* Width for the default image */
                height: 100px;              /* Height for the default image */
            }
        </style>
    """, unsafe_allow_html=True)
    
    # Default image URL (use an actual URL for a placeholder image)
    default_image_url = "/Users/nishitamatlani/Downloads/Assignment3_Nvidia/streamlit/no-pictures.png"  # Replace with a valid URL for the default image
    
    for pub in publications:
        if pub.get("pdf_link"):
            col1, col2 = st.columns([1, 3])
            with col1:
                if pub.get("image_link"):
                    # Use HTML to apply the CSS class to the image
                    st.markdown(f'<img class="bordered-image" src="{pub["image_link"]}" width="100" />', unsafe_allow_html=True)
                else:
                    # Display the default image if no image link is present
                    st.markdown(f'<img class="bordered-image default-image" src="{default_image_url}" />', unsafe_allow_html=True)
            with col2:
                st.write(f"**{pub['title']}**")
                overview = pub.get("brief_summary", "No summary available")
                st.write(overview[:100] + "..." if len(overview) > 100 else overview)
                if st.button("Read More", key=f"read_more_{pub['title']}"):
                    st.session_state[f"show_full_overview_{pub['title']}"] = True


def show_process_pdf_page():
    MAX_CONTEXT_LENGTH = 2000

    st.subheader("Process PDF Document")
    pdf_files = list_pdfs_from_s3()
    selected_pdf = st.selectbox("Select a PDF Document to Process", pdf_files)

    if selected_pdf:
        # Add a "View PDF" button for displaying the PDF in an iframe
        if st.button("View PDF"):
            # Generate a signed URL with inline content disposition
            pdf_url = s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': BUCKET_NAME,
                    'Key': selected_pdf,
                    'ResponseContentDisposition': 'inline'
                },
                ExpiresIn=3600  # URL is valid for 1 hour
            )
            # Embed the PDF in an iframe
            st.markdown(f'<iframe src="{pdf_url}" width="100%" height="600px"></iframe>', unsafe_allow_html=True)

        if st.button("Process PDF"):
            with st.spinner("Processing PDF..."):
                documents = load_pdf_from_s3(BUCKET_NAME, selected_pdf)
                context = "\n".join([doc.page_content for doc in documents])

                context_chunks = [context[i:i + MAX_CONTEXT_LENGTH] for i in range(0, len(context), MAX_CONTEXT_LENGTH)]
                st.session_state['context_chunks'] = context_chunks
                st.write("PDF processed successfully. Click the button below to get a summary of the entire document.")

    if 'context_chunks' in st.session_state:
        if st.button("Generate Summary "):
            combined_summary = []
            for chunk_index, chunk in enumerate(st.session_state['context_chunks']):
                prompt = f"Summarize this in six lines and not mre than that:\n{chunk}"
                response = llm.invoke(prompt)
                combined_summary.append(response.content if response else "No response")

            final_prompt = "Summarize the following:\n" + " ".join(combined_summary)
            final_response = llm.invoke(final_prompt)
            st.write("Summary:", final_response.content if final_response else "No final summary generated.")

def fetch_pdf_details(pdf_filename):
    # Construct the expected PDF link
    pdf_link = f"https://bdiaassignment3.s3.us-east-2.amazonaws.com/pdfs1/{pdf_filename}"

    conn = create_snowflake_connection()  # Create a connection to Snowflake
    if not conn:
        return ("Unknown PDF", "No summary available.", "https://example.com/default_image.jpg", pdf_link)

    cursor = conn.cursor()
    try:
        # Query to fetch PDF details based on the constructed link
        query = """
            SELECT TITLE, BRIEF_SUMMARY, IMAGE_LINK, PDF_LINK 
            FROM PUBLICATION_DATA 
            WHERE PDF_LINK = %s
        """
        cursor.execute(query, (pdf_link,))
        result = cursor.fetchone()  # Fetch a single record

        if result:
            return result  # Returns a tuple (title, brief_summary, image_link, pdf_link)
        else:
            return ("Unknown PDF", "No summary available.", "https://example.com/default_image.jpg", pdf_link)
    except Exception as e:
        st.error(f"Error retrieving PDF details: {e}")
        return ("Unknown PDF", "Error occurred while fetching data.", "https://example.com/default_image.jpg", pdf_link)
    finally:
        cursor.close()
        conn.close()


def show_pdf_qna_page():
    st.title("PDF Q&A")

    st.subheader("Process PDF Document")
    pdf_files = list_pdfs_from_s3()
    selected_pdf = st.selectbox("Select a PDF Document to Process", pdf_files)

    if selected_pdf:
        # Generate a signed URL with inline content disposition
        pdf_url = s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': BUCKET_NAME,
                'Key': selected_pdf,
                'ResponseContentDisposition': 'inline'
            },
            ExpiresIn=3600  # URL is valid for 1 hour
        )

        user_query = st.text_input("Enter your question:")
        
        if st.button("Get Answer"):
            if user_query:
                answer = run_rag_pipeline(pdf_url, user_query)  # Use the generated PDF link and user query
                st.write("Answer:", answer)
            else:
                st.warning("Please enter a question.")


# Function to display PDF details
def display_pdf_details(pdf_data):
    pdf_name, brief_summary, image_link, pdf_link = pdf_data
    st.title(f"Details of {pdf_name}")
    st.image(image_link, width=400)
    st.write(f"**Summary**: {brief_summary}")
    st.markdown(f"[Open PDF]({pdf_link})", unsafe_allow_html=True)


# Handle user logout
def handle_logout():
    st.session_state.clear()
    st.success("You have been logged out successfully!")
    st.session_state["logged_in"] = False
    st.rerun()

def main():
    st.title("Document Exploration App")

    if "access_token" in st.session_state and not is_session_expired():
        publications = fetch_publications()
    else:
        publications = []

    menu_options = ["Login", "Signup", "Process and Summarize PDF", "Explore Documents", "PDF Q&A", "Logout"] if "access_token" in st.session_state else ["Login", "Signup"]

    with st.sidebar:
        choice = option_menu("Menu", menu_options, icons=["box-arrow-in-right", "person-plus", "file-earmark", "folder", "question-circle", "box-arrow-right"])

    if choice == "Signup":
        show_signup_page()
    elif choice == "Login":
        show_login_page()
    elif choice == "Process and Summarize PDF":
        show_process_pdf_page()
    elif choice == "Explore Documents" and "access_token" in st.session_state:
        show_explore_documents(publications)
    elif choice == "PDF Q&A":
        show_pdf_qna_page()  # New page for Q&A
    elif choice == "Logout":
        handle_logout()

if __name__ == "__main__":
    main()

