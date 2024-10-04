import asyncio
import json
import logging
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from loguru import logger as log
from playwright.async_api import async_playwright, Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError

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
    for attempt in range(max_attempts):
        try:
            await page.goto(url, timeout=30000)
            return True
        except Exception as e:
            logger.warning(f"Error loading page. Retrying... Attempt {attempt + 1}/{max_attempts}")
            log.warning(f"Error loading page. Retrying... Attempt {attempt + 1}/{max_attempts}")
            await asyncio.sleep(2)  # Add delay between retries
    logger.error("Failed to load page after multiple attempts")
    log.error("Failed to load page after multiple attempts")
    return False

def parse_json_response(response_text):
    try:
        data = json.loads(response_text)
        # Validate required fields
        if "itemList" in data:
            logger.info("Successfully parsed JSON response with 'itemList'")
            log.info("Successfully parsed JSON response with 'itemList'")
            return data
        elif "userInfo" in data:
            logger.info("Successfully parsed JSON response with 'userInfo'")
            log.info("Successfully parsed JSON response with 'userInfo'")
            return data
        else:
            logger.error("JSON response does not contain 'itemList' or 'userInfo'")
            log.error("JSON response does not contain 'itemList' or 'userInfo'")
            logger.debug(f"Response content: {response_text[:1000]}...")
            log.debug(f"Response content: {response_text[:1000]}...")
            return None
    except json.JSONDecodeError as e:
        logger.error(f"JSON decoding failed: {e}")
        log.error(f"JSON decoding failed: {e}")
        logger.debug(f"Response content: {response_text[:1000]}...")
        log.debug(f"Response content: {response_text[:1000]}...")
        return None

async def scrape_profile(username: str):
    xhr_data_list = []

    async def intercept_xhr(route, request):
        if "api/post/item_list" in request.url or "api/user/detail" in request.url:
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
                    xhr_data["itemList"] = parse_channel(json_data)
                    logger.info(f"Successfully retrieved itemList with {len(xhr_data['itemList'])} items")
                    log.info(f"Successfully retrieved itemList with {len(xhr_data['itemList'])} items")
                elif "api/user/detail" in request.url:
                    xhr_data["userInfo"] = json_data.get("userInfo")
                    logger.info("Successfully retrieved user details")
                    log.info("Successfully retrieved user details")
            else:
                xhr_data["parse_error"] = "Failed to parse JSON or missing required fields"
            
            xhr_data_list.append(xhr_data)
            logger.debug(f"Intercepted XHR: {xhr_data['url']}")
            log.debug(f"Intercepted XHR: {xhr_data['url']}")
        
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

@app.post("/scrape")
async def scrape_tiktok(request: ScrapeRequest):
    logger.info(f"Received scrape request for username: {request.username}")
    log.info(f"Received scrape request for username: {request.username}")
    data = await scrape_profile(request.username)
    logger.info(f"Scraping completed for username: {request.username}")
    log.info(f"Scraping completed for username: {request.username}")
    return data

@app.get("/")
async def root():
    return {"message": "TikTok Scraper API is running. Use POST /scrape to scrape data."}

if __name__ == "__main__":
    logger.info("Starting TikTok Scraper API")
    log.info("Starting TikTok Scraper API")
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)