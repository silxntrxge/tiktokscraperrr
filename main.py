##stableversion:v0.1.3

import asyncio
import json
import logging
import sys
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from playwright.async_api import async_playwright, Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
import time

def setup_logger(name: str, log_file: str, level=logging.DEBUG):
    """Function to setup loggers that output to both file and stdout"""
    formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
    
    # File handler
    file_handler = logging.FileHandler(log_file)
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

# Set up loggers
main_logger = setup_logger('main_logger', 'main.log')
scraper_logger = setup_logger('scraper_logger', 'scraper.log')

app = FastAPI()

class ScrapeRequest(BaseModel):
    username: str

js_scroll_function = """
async function scrollToEnd() {
    let lastHeight = document.body.scrollHeight;
    for (let i = 0; i < 15; i++) {
        window.scrollTo(0, document.body.scrollHeight);
        await new Promise(resolve => setTimeout(resolve, 3000));
        if (document.body.scrollHeight === lastHeight) {
            console.log("Reached the bottom or no more content to load.");
            break;
        }
        lastHeight = document.body.scrollHeight;
    }
    console.log("Finished scrolling.");
}
await scrollToEnd();
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

async def intercept_xhr(page):
    xhr_data_list = []

    async def handle_route(route, request):
        if "api/post/item_list" in request.url or "api/user/detail" in request.url:
            try:
                main_logger.debug(f"Intercepting XHR request: {request.url}")
                response = await route.fetch()
                response_body = await response.text()
                main_logger.debug(f"Received XHR response with status: {response.status}")
                
                xhr_data = {
                    "url": request.url,
                    "method": request.method,
                    "headers": dict(request.headers),
                    "response_status": response.status,
                    "response_headers": dict(response.headers)
                }
                
                json_data = parse_json_response(response_body)
                if json_data:
                    if "api/post/item_list" in request.url:
                        xhr_data["itemList"] = json_data.get("itemList", [])
                        main_logger.info(f"Successfully intercepted itemList XHR: {request.url}")
                        main_logger.debug(f"ItemList Data (first 2 items): {json_data.get('itemList', [])[:2]}")
                    elif "api/user/detail" in request.url:
                        xhr_data["userInfo"] = json_data.get("userInfo", {})
                        main_logger.info(f"Successfully intercepted userInfo XHR: {request.url}")
                        main_logger.debug(f"UserInfo Data: {json_data.get('userInfo', {})}")
                else:
                    xhr_data["parse_error"] = "Failed to parse JSON or missing required fields"
                    scraper_logger.error(f"Failed to parse JSON from XHR: {request.url}")
                    scraper_logger.debug(f"Raw response body: {response_body[:1000]}...")  # Log first 1000 characters
                
                xhr_data_list.append(xhr_data)
            except Exception as e:
                scraper_logger.error(f"Error intercepting XHR {request.url}: {str(e)}")
        
        await route.continue_()
    
    await page.route("**/*", handle_route)
    return xhr_data_list

async def scrape_profile_playwright(username: str):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        page.on("console", lambda msg: scraper_logger.debug(f"Browser console: {msg.text}"))

        main_logger.info(f"Starting XHR interception for username: {username}")
        xhr_data_list = await intercept_xhr(page)

        url = f"https://www.tiktok.com/@{username}"
        main_logger.info(f"Attempting to navigate to: {url}")

        if not await load_page_with_retry(page, url):
            scraper_logger.error(f"Failed to load page for username: {username}")
            return {"error": "Failed to load page after multiple attempts"}

        try:
            main_logger.info("Starting scroll function")
            await asyncio.wait_for(page.evaluate(js_scroll_function), timeout=120.0)
            main_logger.info(f"Finished scrolling for username: {username}")
        except PlaywrightTimeoutError:
            scraper_logger.warning(f"Scrolling timed out for username: {username}. Continuing with data collection.")
        except Exception as e:
            scraper_logger.error(f"Error during scrolling for username {username}: {str(e)}")

        main_logger.info("Waiting for additional XHR requests")
        await page.wait_for_timeout(10000)

        await browser.close()

    if xhr_data_list:
        main_logger.info(f"Captured {len(xhr_data_list)} XHR requests for username: {username}")
        return {"xhr_data": xhr_data_list}
    else:
        scraper_logger.error(f"No XHR data captured for username: {username}")
        return {"error": f"No XHR data captured for username: {username}"}

def initialize_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    
    # Use the default ChromeDriver installed in the Docker image
    service = Service()
    
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

def scrape_profile_selenium(username):
    driver = initialize_driver()
    try:
        driver.get(f"https://www.tiktok.com/@{username}")
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        page_source = driver.page_source
        return parse_profile_html(page_source)
    except Exception as e:
        scraper_logger.error(f"Error scraping profile with Selenium: {e}")
        return None
    finally:
        driver.quit()

def parse_profile_html(html):
    soup = BeautifulSoup(html, 'html.parser')
    profile_data = {}
    
    # Extract username
    username_tag = soup.find('h1', {'data-e2e': 'user-title'})
    if username_tag:
        profile_data['username'] = username_tag.text.strip()
    else:
        scraper_logger.error("Username not found in the profile HTML.")
    
    # Extract follower count
    follower_count = soup.find('strong', {'data-e2e': 'followers-count'})
    if follower_count:
        profile_data['follower_count'] = follower_count.text.strip()
    else:
        scraper_logger.error("Follower count not found in the profile HTML.")
    
    # Extract video list
    video_items = soup.find_all('div', {'data-e2e': 'user-post-item'})
    profile_data['videos'] = []
    for item in video_items:
        video_data = {}
        video_link = item.find('a')
        if video_link:
            video_data['link'] = video_link.get('href')
        video_desc = item.find('div', {'data-e2e': 'user-post-item-desc'})
        if video_desc:
            video_data['description'] = video_desc.text.strip()
        profile_data['videos'].append(video_data)
    
    return profile_data

@app.post("/scrape")
async def scrape_tiktok(request: ScrapeRequest):
    main_logger.info(f"Received scrape request for username: {request.username}")
    
    # Try Selenium first
    selenium_data = scrape_profile_selenium(request.username)
    if selenium_data:
        main_logger.info(f"Successfully scraped data using Selenium for username: {request.username}")
        return selenium_data
    
    # Fallback to Playwright if Selenium fails
    scraper_logger.warning(f"Selenium scraping failed for username: {request.username}. Falling back to Playwright.")
    playwright_data = await scrape_profile_playwright(request.username)
    
    main_logger.info(f"Scraping completed for username: {request.username}")
    return playwright_data

@app.get("/")
async def root():
    return {"message": "TikTok Scraper API is running. Use POST /scrape to scrape data."}

if __name__ == "__main__":
    main_logger.info("Starting TikTok Scraper API")
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)