import asyncio
import json
import logging
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from loguru import logger as log
from playwright.async_api import async_playwright, Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from bs4 import BeautifulSoup
import time
import undetected_chromedriver as uc

# Set up standard logging
def setup_logger():
    logger = logging.getLogger("tiktok_scraper")
    logger.setLevel(logging.DEBUG)

    fh = logging.FileHandler("scraper.log")
    fh.setLevel(logging.DEBUG)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)

    formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(message)s')
    fh.setFormatter(formatter)
    ch.setFormatter(formatter)

    logger.addHandler(fh)
    logger.addHandler(ch)

    return logger

logger = setup_logger()

# Configure loguru logger
log.add("app.log", rotation="500 MB", level="DEBUG")

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
        logger.warning("itemList not found in data")
        log.warning("itemList not found in data")
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
            logger.error(f"Error parsing post: {str(e)}")
            log.error(f"Error parsing post: {str(e)}")
    
    return parsed_data

async def load_page_with_retry(page, url, max_attempts=3):
    for attempt in range(1, max_attempts + 1):
        try:
            await page.goto(url, timeout=30000)
            logger.info(f"Successfully loaded page: {url}")
            log.info(f"Successfully loaded page: {url}")
            return True
        except Exception as e:
            logger.warning(f"Error loading page. Retrying... Attempt {attempt}/{max_attempts}. Error: {str(e)}")
            log.warning(f"Error loading page. Retrying... Attempt {attempt}/{max_attempts}. Error: {str(e)}")
            await asyncio.sleep(2)  # Wait before retrying
    logger.error(f"Failed to load page after {max_attempts} attempts: {url}")
    log.error(f"Failed to load page after {max_attempts} attempts: {url}")
    return False

def parse_json_response(response_text):
    try:
        data = json.loads(response_text)
        # Validate if 'itemList' or 'userInfo' key exists
        if "itemList" in data or "userInfo" in data:
            logger.info("Successfully parsed JSON response")
            log.info("Successfully parsed JSON response")
            return data
        else:
            logger.error("JSON response does not contain 'itemList' or 'userInfo'")
            log.error("JSON response does not contain 'itemList' or 'userInfo'")
            logger.debug(f"Response content: {response_text[:1000]}...")  # Log first 1000 characters
            log.debug(f"Response content: {response_text[:1000]}...")
            return None
    except json.JSONDecodeError as e:
        logger.error(f"JSON decoding failed: {str(e)}")
        log.error(f"JSON decoding failed: {str(e)}")
        logger.debug(f"Response content: {response_text[:1000]}...")
        log.debug(f"Response content: {response_text[:1000]}...")
        return None

def validate_data_structure(data):
    if not isinstance(data, dict):
        logger.error("Data is not a dictionary.")
        log.error("Data is not a dictionary.")
        return False
    if "itemList" in data:
        required_keys = ["itemList", "hasMore", "cursor"]
    elif "userInfo" in data:
        required_keys = ["userInfo", "stats"]
    else:
        logger.error("Data does not contain 'itemList' or 'userInfo'")
        log.error("Data does not contain 'itemList' or 'userInfo'")
        return False
    for key in required_keys:
        if key not in data:
            logger.error(f"Missing key in data: {key}")
            log.error(f"Missing key in data: {key}")
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

async def scrape_profile_playwright(username: str):
    xhr_data_list = []

    async def intercept_xhr(route, request):
        if "api/post/item_list" in request.url or "api/user/detail" in request.url:
            try:
                response = await route.fetch()
                response_body = await response.text()
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
                        logger.info(f"Successfully retrieved itemList with {len(xhr_data['itemList'])} items")
                        log.info(f"Successfully retrieved itemList with {len(xhr_data['itemList'])} items")
                    elif "api/user/detail" in request.url:
                        xhr_data["userInfo"] = json_data.get("userInfo", {})
                        logger.info("Successfully retrieved user details")
                        log.info("Successfully retrieved user details")
                else:
                    xhr_data["parse_error"] = "Failed to parse JSON or missing required fields"
                
                xhr_data_list.append(xhr_data)
                logger.debug(f"Intercepted XHR: {xhr_data['url']}")
                log.debug(f"Intercepted XHR: {xhr_data['url']}")
            except Exception as e:
                logger.error(f"Error intercepting XHR: {str(e)}")
                log.error(f"Error intercepting XHR: {str(e)}")
        
        await route.continue_()

    async def console_handler(msg):
        ignore_messages = [
            "Starling ICU Warning",
            "missing key",
            "[TikTok desktop app SDK Monitor]",
            "Failed to parse video contentType",
            "loadable: `loadableReady()`",
            "[i18n] missing key"
        ]
        if not any(ignore_msg in msg.text for ignore_msg in ignore_messages):
            logger.debug(f"Browser console: {msg.text}")
            log.debug(f"Browser console: {msg.text}")
        if "error getItemList" in msg.text:
            logger.error(f"getItemList error detected: {msg.text}")
            log.error(f"getItemList error detected: {msg.text}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        page.on("console", console_handler)

        await page.route("**/*", intercept_xhr)

        url = f"https://www.tiktok.com/@{username}"
        logger.info(f"Attempting to navigate to: {url}")
        log.info(f"Attempting to navigate to: {url}")
        
        if not await load_page_with_retry(page, url):
            return {"error": "Failed to load page after multiple attempts"}

        try:
            logger.info("Starting scroll function")
            log.info("Starting scroll function")
            await asyncio.wait_for(page.evaluate(js_scroll_function), timeout=120.0)
            logger.info(f"Finished scrolling for username: {username}")
            log.info(f"Finished scrolling for username: {username}")
        except PlaywrightTimeoutError:
            logger.warning(f"Scrolling timed out for username: {username}. Continuing with data collection.")
            log.warning(f"Scrolling timed out for username: {username}. Continuing with data collection.")
        except Exception as e:
            logger.error(f"Error during scrolling for username {username}: {str(e)}")
            log.error(f"Error during scrolling for username {username}: {str(e)}")
            # Continue with the scraping process even if scrolling fails

        logger.info("Waiting for additional XHR requests")
        log.info("Waiting for additional XHR requests")
        await page.wait_for_timeout(10000)

        await browser.close()

    if xhr_data_list:
        logger.info(f"Captured {len(xhr_data_list)} XHR requests for username: {username}")
        log.info(f"Captured {len(xhr_data_list)} XHR requests for username: {username}")
        return {"xhr_data": xhr_data_list}
    else:
        logger.error(f"No XHR data captured for username: {username}")
        log.error(f"No XHR data captured for username: {username}")
        return {"error": f"No XHR data captured for username: {username}"}

def initialize_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    driver = webdriver.Chrome(options=chrome_options)
    return driver

def initialize_undetected_driver():
    options = uc.ChromeOptions()
    options.headless = True
    driver = uc.Chrome(options=options)
    return driver

def scrape_profile_selenium(username):
    driver = initialize_driver()
    try:
        driver.get(f"https://www.tiktok.com/@{username}")
        time.sleep(5)  # Wait for the page to load completely
        page_source = driver.page_source
        return parse_profile_html(page_source)
    except Exception as e:
        logger.error(f"Error scraping profile with Selenium: {e}")
        log.error(f"Error scraping profile with Selenium: {e}")
        return None
    finally:
        driver.quit()

def scrape_profile_undetected(username):
    driver = initialize_undetected_driver()
    try:
        driver.get(f"https://www.tiktok.com/@{username}")
        # Add necessary waits
        driver.implicitly_wait(10)
        page_source = driver.page_source
        return parse_profile_html(page_source)
    except Exception as e:
        logger.error(f"Error scraping with undetected Chromedriver: {e}")
        log.error(f"Error scraping with undetected Chromedriver: {e}")
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
        logger.error("Username not found in the profile HTML.")
        log.error("Username not found in the profile HTML.")
    
    # Extract follower count
    follower_count = soup.find('strong', {'data-e2e': 'followers-count'})
    if follower_count:
        profile_data['follower_count'] = follower_count.text.strip()
    else:
        logger.error("Follower count not found in the profile HTML.")
        log.error("Follower count not found in the profile HTML.")
    
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
    logger.info(f"Received scrape request for username: {request.username}")
    log.info(f"Received scrape request for username: {request.username}")
    
    # Try undetected_chromedriver first
    undetected_data = scrape_profile_undetected(request.username)
    if undetected_data:
        logger.info(f"Successfully scraped data using undetected_chromedriver for username: {request.username}")
        log.info(f"Successfully scraped data using undetected_chromedriver for username: {request.username}")
        return undetected_data
    
    # Try Selenium if undetected_chromedriver fails
    selenium_data = scrape_profile_selenium(request.username)
    if selenium_data:
        logger.info(f"Successfully scraped data using Selenium for username: {request.username}")
        log.info(f"Successfully scraped data using Selenium for username: {request.username}")
        return selenium_data
    
    # Fallback to Playwright if both undetected_chromedriver and Selenium fail
    logger.warning(f"Undetected_chromedriver and Selenium scraping failed for username: {request.username}. Falling back to Playwright.")
    log.warning(f"Undetected_chromedriver and Selenium scraping failed for username: {request.username}. Falling back to Playwright.")
    playwright_data = await scrape_profile_playwright(request.username)
    
    logger.info(f"Scraping completed for username: {request.username}")
    log.info(f"Scraping completed for username: {request.username}")
    return playwright_data

@app.get("/")
async def root():
    return {"message": "TikTok Scraper API is running. Use POST /scrape to scrape data."}

if __name__ == "__main__":
    logger.info("Starting TikTok Scraper API")
    log.info("Starting TikTok Scraper API")
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)