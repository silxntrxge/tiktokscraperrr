##stableversion:v0.1.3
##moreupdatesVersion:v.0.2.7
##moreupdatesVersion:v.0.3.0
##showshtmlbutparsingerrorv0.3.5

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
from browsermobproxy import Server  # Changed from browsermob_proxy to browsermobproxy
import socket
import subprocess
import shutil
import random
import psutil
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import requests
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities
from selenium.common.exceptions import TimeoutException
from scraper import get_formatted_proxy_url, setup_proxy_config, scrape_tiktok_profile

# Add these environment variable definitions near the top of the file, after the imports
IS_REMOTE = os.environ.get('IS_REMOTE', 'false').lower() == 'true'
PROXY_HOST = os.environ.get('PROXY_HOST', 'localhost')
PROXY_PORT = int(os.environ.get('PROXY_PORT', '8081'))
WEBDRIVER_URL = os.environ.get('WEBDRIVER_URL', 'http://localhost:4444/wd/hub')

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
    
    # Add these options to ignore SSL errors
    chrome_options.add_argument('--ignore-certificate-errors')
    chrome_options.add_argument('--ignore-ssl-errors')
    
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
    
    # Add these desired capabilities to ignore SSL errors
    chrome_options.set_capability('acceptInsecureCerts', True)
    
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

def setup_browsermob_proxy():
    try:
        proxy_path = os.environ.get('BROWSERMOB_PROXY_PATH', '/opt/browsermob-proxy/bin/browsermob-proxy')
        port = int(os.environ.get('PORT', 10000))
        main_logger.info(f"Attempting to start Browsermob-Proxy server on port {port}")
        main_logger.info(f"Using proxy path: {proxy_path}")
        
        # Check if the proxy executable exists
        if not os.path.exists(proxy_path):
            main_logger.error(f"Browsermob-Proxy executable not found at {proxy_path}")
            return None, None
        
        # Check Java version
        java_version = subprocess.check_output(['java', '-version'], stderr=subprocess.STDOUT).decode()
        main_logger.info(f"Java version: {java_version}")
        
        server = Server(proxy_path, options={'port': port})
        server.start()
        main_logger.info(f"Browsermob-Proxy server started on port {port}")
        
        proxy = server.create_proxy()
        main_logger.info(f"Proxy instance created on port {proxy.port}")
        
        # Test proxy connection
        test_url = "http://example.com"
        try:
            response = requests.get(test_url, proxies={'http': f'http://localhost:{proxy.port}', 'https': f'http://localhost:{proxy.port}'}, timeout=10)
            main_logger.info(f"Proxy test connection successful. Status code: {response.status_code}")
        except requests.RequestException as e:
            main_logger.error(f"Proxy test connection failed: {e}")
        
        return server, proxy
    except Exception as e:
        main_logger.error(f"Failed to set up Browsermob-Proxy: {e}")
        main_logger.exception("Full traceback:")
        return None, None

def create_proxy():
    try:
        proxy = requests.post(f'http://{PROXY_HOST}:{PROXY_PORT}/proxy').json()
        return proxy['port']
    except requests.RequestException as e:
        logger.error(f"Failed to create proxy: {e}")
        raise

def gather_xhr_with_browsermob(proxy, url):
    try:
        proxy_port = proxy.port  # Use the existing proxy instead of creating a new one
        main_logger.info(f"Using proxy on port {proxy_port}")
        
        proxy_url = f"{proxy.host}:{proxy_port}"
        
        driver = setup_selenium_with_proxy(proxy)
        
        try:
            main_logger.info(f"Sending GET request to {url}")
            driver.get(url)
            # Add code here to extract XHR data
            xhr_data = []  # Replace this with actual XHR data extraction
            success = True
        finally:
            driver.quit()
            main_logger.info(f"Closed WebDriver")
        
        return xhr_data, success
    except Exception as e:
        main_logger.error(f"Error in gather_xhr_with_browsermob: {e}")
        return None, False

def gather_xhr_with_selenium(driver, url, timeout=30):
    try:
        main_logger.info(f"Navigating to {url} with Selenium")
        driver.get(url)
        
        # Wait for the body element to be present
        WebDriverWait(driver, timeout).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        
        main_logger.info("Page loaded, executing JavaScript to capture XHR")
        
        # Set a script timeout
        driver.set_script_timeout(timeout)
        
        # Execute JavaScript to capture XHR with a timeout
        xhr_data = driver.execute_async_script("""
            var callback = arguments[arguments.length - 1];
            var xhrData = [];
            var open = XMLHttpRequest.prototype.open;
            XMLHttpRequest.prototype.open = function() {
                this.addEventListener('load', function() {
                    xhrData.push({
                        url: this.responseURL,
                        method: arguments[0],
                        data: this.response
                    });
                });
                open.apply(this, arguments);
            };
            
            // Set a timeout to return data even if no XHR requests are made
            setTimeout(function() {
                callback(xhrData);
            }, 10000);  // Wait for 10 seconds
        """)
        
        main_logger.info(f"Captured {len(xhr_data)} XHR requests")
        return xhr_data
    except TimeoutException:
        main_logger.error(f"Timeout while loading page or executing script: {url}")
        return None
    except Exception as e:
        main_logger.error(f"Error gathering XHR with Selenium: {e}")
        return None

def setup_selenium_with_proxy(proxy):
    options = Options()
    if proxy:
        options.add_argument(f'--proxy-server={proxy.proxy}')
    options.add_argument('--ignore-certificate-errors')
    options.add_argument('--ignore-ssl-errors')
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--remote-debugging-port=9222')  # This can help with DevTools issues
    options.add_argument('--disable-extensions')
    options.add_argument('--disable-software-rasterizer')
    
    # Set the binary location only if it exists
    chrome_binary = "/opt/google/chrome/chrome"
    if os.path.exists(chrome_binary):
        options.binary_location = chrome_binary
    else:
        main_logger.warning(f"Chrome binary not found at {chrome_binary}. Using default location.")
    
    try:
        # Try the newer Selenium version syntax
        service = Service(executable_path="/usr/local/bin/chromedriver")
        driver = webdriver.Chrome(service=service, options=options)
    except Exception as e:
        main_logger.error(f"Error creating Chrome driver with service: {e}")
        try:
            # Fall back to older Selenium version syntax
            driver = webdriver.Chrome(options=options)
        except Exception as e:
            main_logger.error(f"Error creating Chrome driver without service: {e}")
            raise
    
    return driver

def setup_and_scrape(username):
    server, proxy = setup_browsermob_proxy()
    try:
        return scrape_tiktok_profile(username, server, proxy)
    finally:
        if server:
            main_logger.info("Stopping Browsermob-Proxy server")
            try:
                server.stop()
            except Exception as e:
                main_logger.error(f"Error stopping Browsermob-Proxy server: {e}")
        if proxy:
            main_logger.info("Closing proxy")
            try:
                proxy.close()
            except Exception as e:
                main_logger.error(f"Error closing proxy: {e}")

@retry(stop=stop_after_attempt(3), 
       wait=wait_exponential(multiplier=1, min=4, max=10),
       retry=retry_if_exception_type((requests.RequestException, WebDriverException, Exception)))
async def scrape_tiktok(request: ScrapeRequest):
    main_logger.info(f"Received scrape request for username: {request.username}")
    
    # Check if there's a saved state for this username
    saved_state = load_scraping_state(request.username)
    
    try:
        if saved_state:
            main_logger.info(f"Resuming scraping for {request.username} from saved state")
            result = resume_scraping(request.username, saved_state)
        else:
            result = setup_and_scrape(request.username)
        
        if result:
            main_logger.info(f"Successfully scraped data for username: {request.username}")
            # Clear the saved state after successful scraping
            clear_scraping_state(request.username)
            return result
        else:
            raise HTTPException(status_code=500, detail="Failed to scrape TikTok profile")
    except Exception as e:
        main_logger.error(f"Error during scraping: {str(e)}")
        # Save the current state before raising the exception
        save_scraping_state(request.username, get_current_state())
        raise HTTPException(status_code=500, detail=f"Error during scraping: {str(e)}")

def load_scraping_state(username):
    # Implement logic to load saved state from a file or database
    # Return None if no saved state exists
    pass

def save_scraping_state(username, state):
    # Implement logic to save the current scraping state
    pass

def clear_scraping_state(username):
    # Implement logic to clear the saved state after successful scraping
    pass

def get_current_state():
    # Implement logic to capture the current scraping state
    pass

def resume_scraping(username, saved_state):
    # Implement logic to resume scraping from the saved state
    pass

@retry(stop=stop_after_attempt(2), 
       wait=wait_exponential(multiplier=1, min=4, max=10),
       retry=retry_if_exception_type((requests.RequestException, WebDriverException, Exception)))
def scrape_tiktok_profile(username, server, proxy):
    driver = None
    try:
        url = f"https://www.tiktok.com/@{username}"
        
        if proxy:
            main_logger.info(f"Attempting to gather XHR with Browsermob for {username}")
            xhr_data, success = gather_xhr_with_browsermob(proxy, url)
        else:
            success = False
            main_logger.warning("Browsermob-Proxy not available, falling back to Selenium")

        if not success:
            main_logger.info("Attempting with Selenium")
            try:
                driver = setup_selenium_with_proxy(proxy) if proxy else setup_selenium_with_proxy(None)
                xhr_data = gather_xhr_with_selenium(driver, url)
                if xhr_data is None:
                    main_logger.warning("Failed to gather XHR data with Selenium")
                    success = False
                else:
                    success = True
            except Exception as e:
                main_logger.error(f"Error setting up or using Selenium: {e}")
                success = False

        if not success:
            main_logger.error("Failed to gather XHR data with both Browsermob and Selenium")
            return None

        main_logger.info("Capturing page source")
        if driver:
            html_content = driver.page_source
        else:
            proxy_config = setup_proxy_config(proxy)
            response = requests.get(url, proxies=proxy_config)
            html_content = response.text

        main_logger.info("Parsing profile HTML")
        profile_data = parse_profile_html(html_content)
        
        profile_data['xhr_data'] = xhr_data
        
        main_logger.info(f"Scraping completed for {username}")
        return profile_data
    except Exception as e:
        main_logger.error(f"Error scraping profile: {e}")
        main_logger.exception("Full traceback:")
        raise
    finally:
        if driver:
            main_logger.info("Closing Selenium WebDriver")
            driver.quit()

def extract_xhr_data(har_data):
    xhr_entries = [entry for entry in har_data['log']['entries'] 
                   if entry['request']['method'] == 'POST' and 
                   'application/json' in entry['request'].get('mimeType', '')]
    
    processed_xhr_data = []
    for entry in xhr_entries:
        try:
            request_url = entry['request']['url']
            response_content = entry['response']['content'].get('text', '')
            
            try:
                json_response = json.loads(response_content)
            except json.JSONDecodeError:
                json_response = None
            
            processed_xhr_data.append({
                'url': request_url,
                'method': entry['request']['method'],
                'response': json_response if json_response else response_content
            })
        except Exception as e:
            main_logger.error(f"Error processing XHR entry: {str(e)}")
    
    main_logger.info(f"Processed {len(processed_xhr_data)} XHR entries")
    return processed_xhr_data

def parse_profile_html(html):
    soup = BeautifulSoup(html, 'html.parser')
    profile_data = {}
    
    # Log the entire HTML structure for debugging
    main_logger.debug(f"Full HTML structure: {soup.prettify()}")
    
    # Try multiple potential selectors
    username_selectors = ['h1[data-testid="user-title"]', 'h1.tiktok-1d3iqmy-H1ShareTitle']
    follower_selectors = ['strong[data-testid="followers-count"]', 'strong[title="Followers"]']
    
    for selector in username_selectors:
        username_tag = soup.select_one(selector)
        if username_tag:
            profile_data['username'] = username_tag.text.strip()
            main_logger.info(f"Username found using selector: {selector}")
            break
    else:
        main_logger.error("Username not found in the profile HTML.")
    
    for selector in follower_selectors:
        follower_count = soup.select_one(selector)
        if follower_count:
            profile_data['follower_count'] = follower_count.text.strip()
            main_logger.info(f"Follower count found using selector: {selector}")
            break
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
    
    try:
        result = setup_and_scrape(request.username)
        if result:
            main_logger.info(f"Successfully scraped data for username: {request.username}")
            return result
        else:
            raise HTTPException(status_code=500, detail="Failed to scrape TikTok profile")
    except Exception as e:
        main_logger.error(f"Error during scraping: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error during scraping: {str(e)}")

@app.get("/")
async def root():
    return {"message": "TikTok Scraper API is running. Use POST /scrape to scrape data."}

def check_proxy_settings(proxy):
    main_logger.info("Checking proxy settings")
    main_logger.info(f"Proxy port: {proxy.port}")
    main_logger.info(f"Proxy host: {proxy.host}")
    main_logger.info(f"Proxy har: {proxy.har}")

if __name__ == "__main__":
    app_port = int(os.environ.get('PORT', '10000'))
    main_logger.info(f"Starting application on port {app_port}")
    
    # Your main application code here
    # For example:
    # app.run(host='0.0.0.0', port=app_port)
    
    # Example usage of gather_xhr_with_browsermob
    try:
        gather_xhr_with_browsermob("https://www.tiktok.com/@tiktok")
    except Exception as e:
        main_logger.error(f"Failed to gather XHR data: {e}")