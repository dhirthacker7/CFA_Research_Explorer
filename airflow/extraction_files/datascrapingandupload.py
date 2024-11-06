import os
import requests
import boto3
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from dotenv import load_dotenv
import time
import logging

# Load environment variables from .env file
load_dotenv()

# S3 Configuration
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
S3_PDFS_FOLDER = os.getenv("S3_PDFS_FOLDER")
S3_IMAGES_FOLDER = os.getenv("S3_IMAGES_FOLDER")

# AWS Credentials (configured via environment variables)
s3_client = boto3.client(
    's3',
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=os.getenv("AWS_REGION")
)

# Set up logging
logging.basicConfig(level=logging.INFO)

def get_chrome_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920x1080")
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)

def upload_to_s3(file_content, s3_bucket, s3_key):
    try:
        s3_client.put_object(Bucket=s3_bucket, Key=s3_key, Body=file_content)
        logging.info(f"Uploaded to s3://{s3_bucket}/{s3_key}")
    except Exception as e:
        logging.error(f"Failed to upload to S3: {e}")

def scrape_all_publication_links_with_clicking(landing_url):
    driver = get_chrome_driver()
    driver.get(landing_url)
    all_publication_links = []

    try:
        while True:
            elements = WebDriverWait(driver, 15).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, 'a[href*="/research/foundation"]'))
            )
            for elem in elements:
                link = elem.get_attribute('href')
                if link and link not in all_publication_links:
                    all_publication_links.append(link)

            try:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1)
                next_button = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, '//li[@aria-label="Next"]'))
                )
                logging.info("Clicking 'Next' button...")
                driver.execute_script("arguments[0].click();", next_button)
                time.sleep(3)
            except (NoSuchElementException, TimeoutException):
                logging.info("No more pages or failed to click 'Next'. Exiting loop.")
                break

    finally:
        driver.quit()

    return all_publication_links

def download_content_and_upload_to_s3(publication_links):
    driver = get_chrome_driver()  # Ensure consistent driver setup
    
    for link in publication_links:
        try:
            driver.get(link)
            time.sleep(3)  # Allow time for the page to load

            # Extract the title
            try:
                title_element = driver.find_element(By.CSS_SELECTOR, 'h1.spotlight-hero__title.spotlight-max-width-item')
                title = title_element.text.strip()
                logging.info(f"Processing publication: {title}")
            except NoSuchElementException:
                title = "No title found."
                logging.warning("Title not found.")

            # Locate and check for the PDF element
            try:
                pdf_element = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, 'a.content-asset--primary'))
                )
                pdf_url = pdf_element.get_attribute('href')

                if pdf_url and pdf_url.endswith('.pdf'):
                    pdf_name = pdf_url.split("/")[-1]

                    # Download the PDF directly to memory
                    response = requests.get(pdf_url)
                    if response.status_code == 200:
                        # Upload PDF content directly to S3
                        s3_key = f"{S3_PDFS_FOLDER}{pdf_name}"  # Assuming `S3_PDFS_FOLDER` is defined
                        upload_to_s3(response.content, S3_BUCKET_NAME, s3_key)  # Assuming `S3_BUCKET_NAME` is defined
                        logging.info(f"Uploaded PDF to S3: {pdf_name}")
                    else:
                        logging.error(f"Failed to download PDF: {pdf_name}")
                else:
                    logging.info("No valid PDF found, skipping download.")
            
            except TimeoutException:
                logging.warning("PDF link not found or not a valid PDF, skipping download.")

            # Locate and download the image (if needed)
            try:
                image_element = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, 'section.book__cover-image img'))
                )
                image_url = image_element.get_attribute('src')
                image_name = image_url.split("/")[-1].split("?")[0]

                # Download the image directly to memory
                img_response = requests.get(image_url)
                if img_response.status_code == 200:
                    # Upload Image content directly to S3
                    s3_key = f"{S3_IMAGES_FOLDER}{image_name}"  # Assuming `S3_IMAGES_FOLDER` is defined
                    upload_to_s3(img_response.content, S3_BUCKET_NAME, s3_key)
                    logging.info(f"Uploaded image to S3: {image_name}")
                else:
                    logging.error(f"Failed to download Image: {image_name}")
            except TimeoutException:
                logging.warning("Image not found, skipping download.")

        except Exception as e:
            logging.error(f"Error accessing or downloading content from {link}: {e}")

    driver.quit()


# This block will only run if the script is executed directly, not when imported by Airflow
if __name__ == "__main__":
    landing_page_url = "https://rpc.cfainstitute.org/en/research-foundation/publications#sort=%40officialz32xdate%20descending&f:SeriesContent=[Research%20Foundation]"
    publication_links = scrape_all_publication_links_with_clicking(landing_page_url)

    # Download PDFs and images, and upload directly to S3 bucket if available
    download_content_and_upload_to_s3(publication_links)
