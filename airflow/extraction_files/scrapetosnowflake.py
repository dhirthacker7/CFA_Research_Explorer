import os
import requests
import boto3
import snowflake.connector
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.chrome.options import Options
from dotenv import load_dotenv
import time
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)

# Load environment variables from .env file
load_dotenv()

# AWS S3 Configuration
s3_bucket_name = os.getenv("S3_BUCKET_NAME")
s3_client = boto3.client(
    "s3",
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=os.getenv("AWS_REGION")
)

# Snowflake Configuration
snowflake_conn = snowflake.connector.connect(
    user=os.getenv("SNOWFLAKE_USER"),
    password=os.getenv("SNOWFLAKE_PASSWORD"),
    account=os.getenv("SNOWFLAKE_ACCOUNT"),
    warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
    database=os.getenv("SNOWFLAKE_DATABASE"),
    schema=os.getenv("SNOWFLAKE_SCHEMA")
)

# Function to get Chrome WebDriver with the correct options
def get_chrome_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920x1080")
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)

# Function to list existing files in S3 bucket
def list_s3_files(prefix):
    response = s3_client.list_objects_v2(Bucket=s3_bucket_name, Prefix=prefix)
    if 'Contents' in response:
        return [obj['Key'] for obj in response['Contents']]
    return []

# Function to find S3 file link based on exact extracted filename
def find_s3_file_from_extracted_name(extracted_name, file_list):
    for file in file_list:
        if extracted_name == file.split("/")[-1]:  # Match against only the file name
            return f"https://{s3_bucket_name}.s3.us-east-2.amazonaws.com/{file}"
    return None

# Function to find S3 image link based on title pattern or extracted image name
def find_s3_image_from_name(image_name, file_list):
    if not image_name:
        return None
    for file in file_list:
        if image_name in file:
            return f"https://{s3_bucket_name}.s3.us-east-2.amazonaws.com/{file}"
    return None

# Function to insert data into Snowflake
def insert_data_to_snowflake(title, brief_summary, image_link, pdf_link):
    try:
        cursor = snowflake_conn.cursor()
        insert_query = """
        INSERT INTO PUBLICATION_DATA_BACKUP (TITLE, BRIEF_SUMMARY, IMAGE_LINK, PDF_LINK)
        VALUES (%s, %s, %s, %s)
        """
        cursor.execute(insert_query, (title, brief_summary, image_link, pdf_link))
        logging.info(f"Inserted data for title: {title}")
        cursor.close()
    except Exception as e:
        logging.error(f"Failed to insert data into Snowflake: {e}")

# Function to extract PDF filename from HTML
def get_pdf_filename_from_html(driver):
    try:
        pdf_element = driver.find_element(By.CSS_SELECTOR, 'a.content-asset--primary')
        pdf_href = pdf_element.get_attribute('href')
        extracted_pdf_name = pdf_href.split("/")[-1]
        return extracted_pdf_name
    except NoSuchElementException:
        return None

# Function to extract the image name from the image tag
def get_image_name_from_html(driver):
    try:
        img_element = driver.find_element(By.CSS_SELECTOR, 'section.book__cover-image img')
        img_src = img_element.get_attribute('src')
        image_name = img_src.split("/")[-1].split("?")[0]  # Removing URL parameters if any
        return image_name
    except NoSuchElementException:
        return None

# Enhanced Function to extract content from <span>, <p>, and <div> elements
def extract_overview_from_html(driver):
    overview = []
    
    # Extract content from <span> with class 'overview__content'
    try:
        overview_span = driver.find_element(By.CSS_SELECTOR, 'span.overview__content')
        overview.append(overview_span.text.strip())
    except NoSuchElementException:
        pass
    
    # Extract all <p> tags within <div> under the <article> with the class 'grid__item--article-body'
    try:
        overview_paragraphs = driver.find_elements(By.CSS_SELECTOR, 'article.grid__item--article-body div p')
        for p in overview_paragraphs:
            if p.text.strip():
                overview.append(p.text.strip())
    except NoSuchElementException:
        pass
    
    # Extract any direct content within <div> under 'grid__item--article-body'
    try:
        overview_divs = driver.find_elements(By.CSS_SELECTOR, 'article.grid__item--article-body div')
        for div in overview_divs:
            if div.text.strip() and not div.find_elements(By.CSS_SELECTOR, 'p'):  # Avoid duplicate text already in <p>
                overview.append(div.text.strip())
    except NoSuchElementException:
        pass

    return " ".join(overview) if overview else "No overview found."

# Function to scrape all publication links
def scrape_all_publication_links_with_clicking(landing_url):
    driver = get_chrome_driver()  # Use the new standardized driver setup
    driver.get(landing_url)
    all_publication_links = []
    
    while True:
        try:
            elements = WebDriverWait(driver, 15).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, 'a[href*="/research/foundation"]'))
            )
            for elem in elements:
                link = elem.get_attribute('href')
                if link and link not in all_publication_links:
                    all_publication_links.append(link)
        except Exception as e:
            logging.error(f"Error finding publication links: {e}")
        
        try:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1)
            next_button = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, '//li[@aria-label="Next"]'))
            )
            logging.info("Clicking the 'Next' button...")
            driver.execute_script("arguments[0].click();", next_button)
            time.sleep(3)
        except (NoSuchElementException, TimeoutException):
            logging.info("No more pages or failed to click 'Next'. Exiting loop.")
            break
    
    driver.quit()
    return all_publication_links

# Function to visit each publication page and extract information
def download_content_and_save_text(publication_links):
    # List existing files in S3
    image_files = list_s3_files("images1/")
    pdf_files = list_s3_files("pdfs1/")

    driver = get_chrome_driver()  # Use the standardized driver setup
    
    for link in publication_links:
        try:
            driver.get(link)
            time.sleep(3)
            
            # Extract the title
            title = None
            try:
                title_element = driver.find_element(By.CSS_SELECTOR, 'h1.spotlight-hero__title.spotlight-max-width-item')
                title = title_element.text.strip()
                logging.info(f"Title: {title}")
            except NoSuchElementException:
                pass
            
            # Extract the short article description
            short_description = None
            try:
                description_element = driver.find_element(By.CSS_SELECTOR, 'p.article-description')
                short_description = description_element.text.strip()
            except NoSuchElementException:
                pass

            # Extract the overview using enhanced method
            overview = extract_overview_from_html(driver)

            # Extract the article paragraph
            article_paragraph = None
            try:
                WebDriverWait(driver, 20).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, 'div.article__paragraph'))
                )
                article_paragraph = driver.execute_script("""
                    var paragraphs = document.querySelectorAll('div.article__paragraph p');
                    var contentArray = [];
                    paragraphs.forEach(function(paragraph) {
                        contentArray.push(paragraph.textContent.trim());
                    });
                    return contentArray.join("\\n");
                """)
            except Exception as e:
                logging.error(f"Failed to extract article paragraphs: {e}")
            
            # Extract image name and find in S3
            image_name = get_image_name_from_html(driver)
            image_s3_link = find_s3_image_from_name(image_name, image_files)

            # Extract PDF filename from HTML
            extracted_pdf_name = get_pdf_filename_from_html(driver)
            pdf_s3_link = find_s3_file_from_extracted_name(extracted_pdf_name, pdf_files)

            # Combine short description, overview, and article paragraph
            brief_summary = "\n".join(filter(None, [
                f"Short Description: {short_description}" if short_description else "",
                f"Overview: {overview}" if overview else "",
                f"Article Paragraph: {article_paragraph}" if article_paragraph else ""
            ]))

            # Insert into Snowflake
            insert_data_to_snowflake(title, brief_summary, image_s3_link, pdf_s3_link)

        except Exception as e:
            logging.error(f"Error accessing or downloading content on {link}: {e}")
    
    driver.quit()

# This block will only run if the script is executed directly, not when imported by Airflow
if __name__ == "__main__":
    landing_page_url = "https://rpc.cfainstitute.org/en/research-foundation/publications#sort=%40officialz32xdate%20descending&f:SeriesContent=[Research%20Foundation]"
    publication_links = scrape_all_publication_links_with_clicking(landing_page_url)

    # Download PDFs and images, and upload directly to S3 bucket if available
    download_content_and_save_text(publication_links)

    # Close the Snowflake connection
    snowflake_conn.close()
    
