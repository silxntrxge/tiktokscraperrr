##stableversion:v0.1.3
##moreupdatesVersion:v.0.2.7
##moreupdatesVersion:v.0.3.0

import asyncio
import json
import logging
import sys
import os
from logging.handlers import RotatingFileHandler
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from playwright.async_api import async_playwright, Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.proxy import Proxy, ProxyType
from bs4 import BeautifulSoup
import time
from browsermobproxy import Server
import socket
import subprocess
import shutil
import random
import psutil

def setup_logger(name: str, log_file: str, level=logging.DEBUG, max_size=1048576, backup_count=5):
    """Function to setup loggers that output to both file and stdout"""
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # Rotating File Handler
    file_handler = RotatingFileHandler(log_file, maxBytes=max_size, backupCount=backup_count)
    file_handler.setFormatter(formatter)
    
    # Stream handler (for stdout)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    
    # Setup logger
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    
    return logger

# Initialize loggers
log_dir = "logs"
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

main_logger = setup_logger('main_logger', os.path.join(log_dir, 'main.log'))
scraper_logger = setup_logger('scraper_logger', os.path.join(log_dir, 'scraper.log'))

# Set logging levels
main_logger.setLevel(logging.DEBUG)
scraper_logger.setLevel(logging.DEBUG)

# Add this line to make scraper_logger available globally
globals()['scraper_logger'] = scraper_logger

# Add this function to check for write permissions
def check_log_permissions(log_dir):
    if not os.path.exists(log_dir):
        try:
            os.makedirs(log_dir)
        except PermissionError:
            print(f"Error: No permission to create log directory: {log_dir}")
            return False
    if not os.access(log_dir, os.W_OK):
        print(f"Error: No write permission for log directory: {log_dir}")
        return False
    return True

# Add a test logging function
def test_logging():
    main_logger.debug("This is a debug message")
    main_logger.info("This is an info message")
    main_logger.warning("This is a warning message")
    main_logger.error("This is an error message")
    main_logger.critical("This is a critical message")
    
    scraper_logger.debug("This is a debug message")
    scraper_logger.info("This is an info message")
    scraper_logger.warning("This is a warning message")
    scraper_logger.error("This is an error message")
    scraper_logger.critical("This is a critical message")

app = FastAPI()

js_scroll_function = """
function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function scrollToEnd() {
  let lastHeight = document.body.scrollHeight;
  for (let i = 0; i < 15; i++) {
    window.scrollTo(0, document.body.scrollHeight);
    await sleep(3000);
    if (document.body.scrollHeight === lastHeight) {
      console.log("Reached the bottom or no more content to load.");
      break;
    }
    lastHeight = document.body.scrollHeight;
  }
  console.log("Finished scrolling.");
}

scrollToEnd();
"""

def parse_channel(data):
    if "itemList" not in data:
        scraper_logger.warning("itemList not found in data")
        return []
    
    parsed_data = []
    for post in data["itemList"]:
        try:
            result = {
                "createTime": post.get("createTime"),
                "desc": post.get("desc"),
                "id": post.get("id"),
                "stats": post.get("stats"),
                "video": {
                    "duration": post.get("video", {}).get("duration"),
                    "ratio": post.get("video", {}).get("ratio"),
                    "cover": post.get("video", {}).get("cover"),
                    "playAddr": post.get("video", {}).get("playAddr"),
                    "downloadAddr": post.get("video", {}).get("downloadAddr")
                }
            }
            parsed_data.append(result)
        except Exception as e:
            scraper_logger.error(f"Error parsing post: {str(e)}")
    
    return parsed_data

async def load_page_with_retry(page, url, max_attempts=3):
    for attempt in range(1, max_attempts + 1):
        try:
            await page.goto(url, timeout=30000)
            main_logger.info(f"Successfully loaded page: {url}")
            return True
        except Exception as e:
            scraper_logger.warning(f"Error loading page. Retrying... Attempt {attempt}/{max_attempts}. Error: {str(e)}")
            await asyncio.sleep(2)  # Wait before retrying
    scraper_logger.error(f"Failed to load page after {max_attempts} attempts: {url}")
    return False

def parse_json_response(response_text):
    try:
        data = json.loads(response_text)
        # Validate if 'itemList' or 'userInfo' key exists
        if "itemList" in data or "userInfo" in data:
            main_logger.info("Successfully parsed JSON response")
            return data
        else:
            scraper_logger.error("JSON response does not contain 'itemList' or 'userInfo'")
            scraper_logger.debug(f"Response content: {response_text[:1000]}...")  # Log first 1000 characters
            return None
    except json.JSONDecodeError as e:
        scraper_logger.error(f"JSON decoding failed: {str(e)}")
        scraper_logger.debug(f"Response content: {response_text[:1000]}...")
        return None

def validate_data_structure(data):
    if not isinstance(data, dict):
        scraper_logger.error("Data is not a dictionary.")
        return False
    if "itemList" in data:
        required_keys = ["itemList", "hasMore", "cursor"]
    elif "userInfo" in data:
        required_keys = ["userInfo", "stats"]
    else:
        scraper_logger.error("Data does not contain 'itemList' or 'userInfo'")
        return False
    for key in required_keys:
        if key not in data:
            scraper_logger.error(f"Missing key in data: {key}")
            return False
    return True

def get_item_list(response_text):
    data = parse_json_response(response_text)
    if data and validate_data_structure(data):
        return data.get("itemList", [])
    return []

def get_user_info(response_text):
    data = parse_json_response(response_text)
    if data and validate_data_structure(data):
        return data.get("userInfo", {})
    return {}

def parse_api_response(response_json):
    """
    Parses the API response and assigns the raw itemList to 'videos'.
    
    Args:
        response_json (dict): The JSON response from the TikTok API.
    
    Returns:
        dict: Parsed data with videos containing raw itemList data.
    """
    main_logger.debug("Starting to parse API response.")
    
    if not response_json:
        scraper_logger.error("Empty response JSON.")
        return {
            "username": "",
            "follower_count": "",
            "videos": []
        }
    
    # Extract general information
    username = response_json.get('username', 'unknown')  # Adjust based on actual response
    follower_count = response_json.get('follower_count', '0')  # Adjust based on actual response
    
    # Assign 'itemList' directly to 'videos'
    item_list = response_json.get('itemList', [])
    
    if not item_list:
        scraper_logger.warning("itemList is empty in the response.")
    
    main_logger.debug(f"Parsed username: {username}, follower_count: {follower_count}, number of items: {len(item_list)}")
    
    return {
        "username": username,
        "follower_count": follower_count,
        "videos": item_list  # Directly assigning the raw itemList
    }

async def intercept_xhr(page):
    xhr_data_list = []

    async def handle_route(route, request):
        if "api/post/item_list" in request.url or "api/user/detail" in request.url:
            try:
                main_logger.debug(f"Intercepting XHR request: {request.url}")
                main_logger.debug(f"Request headers: {request.headers}")
                main_logger.debug(f"Request method: {request.method}")
                
                # Add a random delay before fetching to avoid rate limiting
                await asyncio.sleep(random.uniform(1, 3))
                
                # Modify headers to mimic a real browser more closely
                modified_headers = {
                    **request.headers,
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Sec-Fetch-Site": "same-origin",
                    "Sec-Fetch-Mode": "cors",
                    "Sec-Fetch-Dest": "empty",
                    "Referer": "https://www.tiktok.com/",
                }
                
                response = await route.fetch(headers=modified_headers)
                response_body = await response.text()
                
                main_logger.debug(f"Received XHR response with status: {response.status}")
                main_logger.debug(f"Response headers: {response.headers}")
                main_logger.debug(f"Response body (first 1000 chars): {response_body[:1000]}")
                
                if response_body:
                    try:
                        json_data = json.loads(response_body)
                        xhr_data = {
                            "url": request.url,
                            "method": request.method,
                            "request_headers": dict(modified_headers),
                            "response_status": response.status,
                            "response_headers": dict(response.headers),
                            "response_body": json_data
                        }
                        xhr_data_list.append(xhr_data)
                        main_logger.info(f"Successfully captured XHR data for: {request.url}")
                    except json.JSONDecodeError:
                        main_logger.error(f"Failed to parse JSON from response: {response_body[:1000]}")
                else:
                    main_logger.warning(f"Empty response body for URL: {request.url}")
            except Exception as e:
                main_logger.error(f"Error intercepting XHR {request.url}: {str(e)}")
        
        await route.continue_()
    
    await page.route("**/*", handle_route)
    return xhr_data_list

async def scrape_profile_playwright(username: str):
    main_logger.info(f"Starting scrape_profile_playwright for username: {username}")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        )
        page = await context.new_page()

        page.on("console", lambda msg: scraper_logger.debug(f"Browser console: {msg.text}"))

        main_logger.info(f"Starting XHR interception for username: {username}")
        xhr_data_list = await intercept_xhr(page)

        url = f"https://www.tiktok.com/@{username}"
        main_logger.info(f"Attempting to navigate to: {url}")

        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)
            main_logger.info(f"Successfully loaded page: {url}")
        except PlaywrightTimeoutError:
            main_logger.warning(f"Timeout while loading page: {url}. Continuing with partial page load.")

        try:
            main_logger.info("Starting scroll function")
            await page.evaluate(js_scroll_function)
            main_logger.info(f"Finished scrolling for username: {username}")
        except PlaywrightError as e:
            main_logger.error(f"Error during scrolling for username {username}: {str(e)}")

        main_logger.info("Waiting for additional XHR requests")
        await page.wait_for_timeout(10000)

        await browser.close()

    if xhr_data_list:
        main_logger.info(f"Captured {len(xhr_data_list)} XHR requests for username: {username}")
        return {"xhr_data": xhr_data_list}
    else:
        main_logger.warning(f"No XHR data captured for username: {username}")
        return {"error": f"No XHR data captured for username: {username}"}

def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0

def verify_java_installation():
    try:
        result = subprocess.run(["java", "-version"], capture_output=True, text=True)
        if result.returncode == 0:
            java_version = result.stderr.split('\n')[0]
            main_logger.info(f"Java is installed: {java_version}")
            return True
        else:
            main_logger.error("Java is not installed or not in PATH")
            return False
    except Exception as e:
        main_logger.error(f"Error checking Java installation: {str(e)}")
        return False

def verify_proxy_executable():
    proxy_path = os.environ.get('BROWSERMOB_PROXY_PATH')
    main_logger.info(f"Initial BROWSERMOB_PROXY_PATH: {proxy_path}")

    if not proxy_path:
        main_logger.info("BROWSERMOB_PROXY_PATH not set, searching for executable...")
        possible_paths = [
            '/opt/browsermob-proxy/bin/browsermob-proxy',
            './browsermob-proxy',
            'browsermob-proxy',
            os.path.join(os.path.dirname(__file__), 'browsermob-proxy'),
            os.path.join(os.path.dirname(__file__), 'browsermob-proxy', 'bin', 'browsermob-proxy'),
        ]
        for path in possible_paths:
            main_logger.info(f"Checking path: {path}")
            if os.path.isfile(path) and os.access(path, os.X_OK):
                proxy_path = path
                main_logger.info(f"Found executable at: {proxy_path}")
                break
            elif os.path.isfile(path + '.bat'):  # For Windows
                proxy_path = path + '.bat'
                main_logger.info(f"Found Windows executable at: {proxy_path}")
                break
        
        if not proxy_path:
            main_logger.info("Executable not found in common locations, searching in PATH...")
            proxy_path = shutil.which('browsermob-proxy')
            if proxy_path:
                main_logger.info(f"Found executable in PATH: {proxy_path}")
    
    if not proxy_path:
        main_logger.error("BROWSERMOB_PROXY_PATH not set and executable not found")
        return False
    
    if not os.path.isfile(proxy_path):
        main_logger.error(f"Proxy executable not found at {proxy_path}")
        return False
    
    if not os.access(proxy_path, os.X_OK):
        main_logger.error(f"Proxy executable at {proxy_path} is not executable")
        return False
    
    main_logger.info(f"Proxy executable verified at {proxy_path}")
    os.environ['BROWSERMOB_PROXY_PATH'] = proxy_path  # Set the environment variable
    return True

def check_network_connectivity():
    try:
        socket.create_connection(("www.google.com", 80))
        main_logger.info("Network connectivity: OK")
        return True
    except OSError:
        main_logger.error("Network connectivity: Failed")
        return False

def monitor_resource_usage():
    cpu_percent = psutil.cpu_percent()
    memory_percent = psutil.virtual_memory().percent
    main_logger.info(f"CPU usage: {cpu_percent}%, Memory usage: {memory_percent}%")

def setup_proxy():
    if not verify_java_installation():
        return None, None

    if not verify_proxy_executable():
        return None, None

    proxy_path = os.environ.get('BROWSERMOB_PROXY_PATH')
    proxy_port = 8080
    max_retries = 3
    retry_delay = 5

    for attempt in range(max_retries):
        try:
            main_logger.info(f"Attempt {attempt + 1} to start Browsermob-Proxy server")
            server = Server(proxy_path)
            server.start(options={'port': proxy_port})
            main_logger.info("Browsermob-Proxy server started successfully")
            
            time.sleep(2)
            
            try:
                proxy = server.create_proxy()
                main_logger.info(f"Proxy created successfully on port {proxy.port}")
                return server, proxy
            except Exception as e:
                main_logger.error(f"Failed to create proxy: {str(e)}")
                server.stop()
                if attempt == max_retries - 1:
                    return None, None
        except Exception as e:
            main_logger.error(f"Failed to start Browsermob-Proxy server: {str(e)}")
            
            if attempt < max_retries - 1:
                main_logger.info(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
            else:
                main_logger.error("Max retries reached. Unable to start Browsermob-Proxy.")
                return None, None

    return None, None

def initialize_driver(proxy):
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    
    # Set up proxy
    chrome_options.add_argument(f'--proxy-server={proxy.proxy}')
    
    # Use the default ChromeDriver installed in the Docker image
    service = Service()
    
    # Create a Proxy object and set it in chrome_options
    selenium_proxy = Proxy()
    selenium_proxy.http_proxy = proxy.proxy
    selenium_proxy.ssl_proxy = proxy.proxy
    selenium_proxy.proxy_type = ProxyType.MANUAL
    
    chrome_options.proxy = selenium_proxy
    
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

def scrape_profile_selenium(username):
    server, proxy = setup_proxy()
    if not proxy:
        main_logger.error("Failed to set up proxy. Aborting Selenium scraping.")
        return None

    try:
        main_logger.info(f"Creating new HAR for {username}")
        proxy.new_har(options={'captureHeaders': True, 'captureContent': True})
        
        main_logger.info("Initializing Selenium WebDriver")
        driver = initialize_driver(proxy)
        
        try:
            url = f"https://www.tiktok.com/@{username}"
            main_logger.info(f"Navigating to {url}")
            driver.get(url)
            
            main_logger.info("Waiting for body element to be present")
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            
            main_logger.info("Scrolling page to trigger XHR requests")
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(5)  # Wait for XHR requests to complete
            
            main_logger.info("Capturing page source and HAR data")
            page_source = driver.page_source
            har_data = proxy.har
            
            main_logger.info("Processing HAR data")
            xhr_data = process_har_data(har_data)
            
            main_logger.info("Parsing profile HTML")
            profile_data = parse_profile_html(page_source)
            profile_data['xhr_data'] = xhr_data
            
            main_logger.info(f"Scraping completed for {username}")
            return profile_data
        except Exception as e:
            main_logger.error(f"Error scraping profile with Selenium: {e}")
            main_logger.exception("Full traceback:")
            return None
        finally:
            main_logger.info("Closing Selenium WebDriver")
            driver.quit()
    finally:
        if server:
            main_logger.info("Stopping Browsermob-Proxy server")
            server.stop()
        main_logger.info("Closing proxy")
        proxy.close()

def process_har_data(har_data):
    xhr_data = []
    for entry in har_data['log']['entries']:
        if 'xhr' in entry['request']['method'].lower():
            response_content = entry['response']['content'].get('text', '')
            try:
                # Attempt to parse the response as JSON
                json_response = json.loads(response_content)
            except json.JSONDecodeError:
                json_response = None

            xhr_data.append({
                'url': entry['request']['url'],
                'method': entry['request']['method'],
                'response': json_response if json_response else response_content
            })
    
    # Print the raw XHR data
    main_logger.info("Raw XHR Data:")
    for data in xhr_data:
        main_logger.info(f"URL: {data['url']}")
        main_logger.info(f"Method: {data['method']}")
        main_logger.info(f"Response: {json.dumps(data['response'], indent=2)}")
        main_logger.info("---")

    return xhr_data

def parse_profile_html(html):
    soup = BeautifulSoup(html, 'html.parser')
    profile_data = {}
    
    # Updated selectors based on the latest TikTok profile page structure
    username_tag = soup.find('h1', {'data-testid': 'user-title'})
    if username_tag:
        profile_data['username'] = username_tag.text.strip()
    else:
        main_logger.error("Username not found in the profile HTML.")
    
    follower_count = soup.find('strong', {'data-testid': 'followers-count'})
    if follower_count:
        profile_data['follower_count'] = follower_count.text.strip()
    else:
        main_logger.error("Follower count not found in the profile HTML.")
    
    video_items = soup.find_all('div', {'data-testid': 'user-post-item'})
    profile_data['videos'] = []
    for item in video_items:
        video_data = {}
        video_link = item.find('a')
        if video_link:
            video_data['link'] = video_link.get('href')
        video_desc = item.find('div', {'data-testid': 'user-post-item-desc'})
        if video_desc:
            video_data['description'] = video_desc.text.strip()
        profile_data['videos'].append(video_data)
    
    return profile_data

def perform_setup_verification():
    main_logger.info("Performing setup verification...")
    if not verify_java_installation():
        return False
    if not verify_proxy_executable():
        return False
    if not check_network_connectivity():
        return False
    monitor_resource_usage()
    main_logger.info("Setup verification completed successfully")
    return True

class ScrapeRequest(BaseModel):
    username: str

@app.post("/scrape")
async def scrape_tiktok(request: ScrapeRequest):
    main_logger.info(f"Received scrape request for username: {request.username}")
    
    # Try Selenium first
    main_logger.info(f"Attempting to scrape with Selenium for username: {request.username}")
    selenium_data = scrape_profile_selenium(request.username)
    if selenium_data:
        main_logger.info(f"Successfully scraped data using Selenium for username: {request.username}")
        return selenium_data
    
    # Fallback to Playwright if Selenium fails
    main_logger.warning(f"Selenium scraping failed for username: {request.username}. Falling back to Playwright.")
    playwright_data = await scrape_profile_playwright(request.username)
    
    main_logger.info(f"Scraping completed for username: {request.username}")
    return playwright_data

@app.get("/")
async def root():
    return {"message": "TikTok Scraper API is running. Use POST /scrape to scrape data."}

if __name__ == "__main__":
    log_dir = "logs"
    if check_log_permissions(log_dir):
        if perform_setup_verification():
            main_logger.info("Starting TikTok Scraper API")
            import uvicorn
            uvicorn.run(app, host="0.0.0.0", port=8000)
        else:
            main_logger.error("Setup verification failed. Exiting.")
            sys.exit(1)
    else:
        print("Error: Unable to set up logging due to permission issues.")
        sys.exit(1)