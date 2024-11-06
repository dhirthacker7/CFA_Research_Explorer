import os
import logging
import time
from airflow import DAG
from airflow.operators.python import PythonOperator
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from webdriver_manager.chrome import ChromeDriverManager

from extraction_files.datascrapingandupload import (
    scrape_all_publication_links_with_clicking,
    download_content_and_upload_to_s3,
)
from extraction_files.scrapetosnowflake import (
    list_s3_files,
    find_s3_file_from_extracted_name,
    find_s3_image_from_name,
    insert_data_to_snowflake,
    get_pdf_filename_from_html,
    get_image_name_from_html,
    extract_overview_from_html
)

# Default arguments
default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'start_date': datetime(2024, 10, 1),
    'retries': 3,
    'retry_delay': timedelta(minutes=5),
}

# Define the DAG
dag = DAG(
    'full_dag',
    default_args=default_args,
    description='Scrape CFA Institute publications, upload PDFs/images to S3, and insert metadata into Snowflake',
    schedule_interval='@daily',
    catchup=False,
)

# Function to set up Chrome WebDriver with headless mode
def get_chrome_driver():
    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920x1080")
    return webdriver.Remote(f'selenium_remote:4444/wd/hub',options=chrome_options)

def scrape_links(**kwargs):
    landing_page_url = "https://rpc.cfainstitute.org/en/research-foundation/publications#sort=%40officialz32xdate%20descending&f:SeriesContent=[Research%20Foundation]"
    links = scrape_all_publication_links_with_clicking(landing_page_url)
    return links

def download_and_upload_to_s3(**kwargs):
    ti = kwargs['ti']
    publication_links = ti.xcom_pull(task_ids='scrape_links_task')
    if not publication_links:
        raise ValueError("No publication links found to process.")
    download_content_and_upload_to_s3(publication_links)

def download_content_and_insert_to_snowflake(**kwargs):
    ti = kwargs['ti']
    publication_links = ti.xcom_pull(task_ids='scrape_links_task')
    if not publication_links:
        raise ValueError("No publication links found to process.")
    
    # List existing files in S3 to find references
    image_files = list_s3_files("images1/")
    pdf_files = list_s3_files("pdfs1/")
    
    # Use the function to download content and integrate with S3 and Snowflake
    driver = get_chrome_driver()  # Use standardized driver setup

    for link in publication_links:
        try:
            driver.get(link)
            time.sleep(3)
            
            # Extract data using helper functions
            title = None
            try:
                title_element = driver.find_element(By.CSS_SELECTOR, 'h1.spotlight-hero__title.spotlight-max-width-item')
                title = title_element.text.strip()
                logging.info(f"Title: {title}")
            except NoSuchElementException:
                logging.warning("Title not found.")

            # Extract short description and overview
            short_description = None
            try:
                description_element = driver.find_element(By.CSS_SELECTOR, 'p.article-description')
                short_description = description_element.text.strip()
            except NoSuchElementException:
                logging.warning("Short description not found.")

            overview = extract_overview_from_html(driver)

            # Extract S3 image and PDF links
            image_name = get_image_name_from_html(driver)
            image_s3_link = find_s3_image_from_name(image_name, image_files)

            pdf_name = get_pdf_filename_from_html(driver)
            pdf_s3_link = find_s3_file_from_extracted_name(pdf_name, pdf_files)

            # Combine information to be inserted into Snowflake
            brief_summary = "\n".join(filter(None, [
                f"Short Description: {short_description}" if short_description else "",
                f"Overview: {overview}" if overview else ""
            ]))

            # Insert into Snowflake
            insert_data_to_snowflake(title, brief_summary, image_s3_link, pdf_s3_link)

        except Exception as e:
            logging.error(f"Error processing link {link}: {e}")

    driver.quit()

# Define tasks for the Airflow DAG
scrape_links_task = PythonOperator(
    task_id='scrape_links_task',
    python_callable=scrape_links,
    dag=dag,
    execution_timeout=timedelta(minutes=30)
)

download_and_upload_task = PythonOperator(
    task_id='download_and_upload_task',
    python_callable=download_and_upload_to_s3,
    dag=dag,
    execution_timeout=timedelta(minutes=60),
    provide_context=True
)

# Task to insert processed data into Snowflake
insert_into_snowflake_task = PythonOperator(
    task_id='insert_into_snowflake_task',
    python_callable=download_content_and_insert_to_snowflake,
    dag=dag,
    execution_timeout=timedelta(minutes=60),
    provide_context=True
)

# Set the task dependencies
scrape_links_task >> download_and_upload_task >> insert_into_snowflake_task
