import asyncio
import json
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from loguru import logger as log
from playwright.async_api import async_playwright, Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError

log.add("app.log", rotation="500 MB")

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
            log.error(f"Error parsing post: {str(e)}")
    
    return parsed_data

async def load_page_with_retry(page, url, max_attempts=3):
    for attempt in range(max_attempts):
        try:
            await page.goto(url, timeout=30000)
            return True
        except Exception as e:
            log.warning(f"Error loading page. Retrying... Attempt {attempt + 1}/{max_attempts}")
            await asyncio.sleep(2)  # Add delay between retries
    log.error("Failed to load page after multiple attempts")
    return False

def parse_json_response(response_text):
    try:
        return json.loads(response_text)
    except json.JSONDecodeError as e:
        log.error(f"Failed to parse JSON from response: {str(e)}")
        log.debug(f"Response content: {response_text[:1000]}...")  # Log first 1000 characters
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
                    log.info(f"Successfully retrieved itemList with {len(xhr_data['itemList'])} items")
                elif "api/user/detail" in request.url:
                    xhr_data["userInfo"] = json_data.get("userInfo")
                    log.info("Successfully retrieved user details")
            else:
                xhr_data["parse_error"] = "Failed to parse JSON"
            
            xhr_data_list.append(xhr_data)
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
            log.debug(f"Browser console: {msg.text}")
        if "error getItemList" in msg.text:
            log.error(f"getItemList error detected: {msg.text}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        page.on("console", console_handler)

        await page.route("**/*", intercept_xhr)

        url = f"https://www.tiktok.com/@{username}"
        log.info(f"Attempting to navigate to: {url}")
        
        if not await load_page_with_retry(page, url):
            return {"error": "Failed to load page after multiple attempts"}

        try:
            log.info("Starting scroll function")
            await asyncio.wait_for(page.evaluate(js_scroll_function), timeout=120.0)
            log.info(f"Finished scrolling for username: {username}")
        except PlaywrightTimeoutError:
            log.warning(f"Scrolling timed out for username: {username}. Continuing with data collection.")
        except Exception as e:
            log.error(f"Error during scrolling for username {username}: {str(e)}")
            # Continue with the scraping process even if scrolling fails

        log.info("Waiting for additional XHR requests")
        await page.wait_for_timeout(10000)

        await browser.close()

    if xhr_data_list:
        log.info(f"Captured {len(xhr_data_list)} XHR requests for username: {username}")
        return {"xhr_data": xhr_data_list}
    else:
        log.error(f"No XHR data captured for username: {username}")
        return {"error": f"No XHR data captured for username: {username}"}

@app.post("/scrape")
async def scrape_tiktok(request: ScrapeRequest):
    log.info(f"Received scrape request for username: {request.username}")
    data = await scrape_profile(request.username)
    log.info(f"Scraping completed for username: {request.username}")
    return data

@app.get("/")
async def root():
    return {"message": "TikTok Scraper API is running. Use POST /scrape to scrape data."}

if __name__ == "__main__":
    log.info("Starting TikTok Scraper API")
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)