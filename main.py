import asyncio
import json
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from loguru import logger as log
from playwright.async_api import async_playwright
from playwright._impl._api_types import TimeoutError as PlaywrightTimeoutError

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

async def scrape_profile(username: str):
    xhr_data_list = []

    async def intercept_xhr(route, request):
        if "api/post/item_list" in request.url:
            response = await route.fetch()
            xhr_data_list.append({
                "url": request.url,
                "method": request.method,
                "body": await response.text(),
                "headers": dict(response.headers)
            })
        await route.continue_()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        page.on("console", lambda msg: log.debug(f"Browser console: {msg.text}"))

        await page.route("**/*", intercept_xhr)

        url = f"https://www.tiktok.com/@{username}"
        log.info(f"Attempting to navigate to: {url}")
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                await page.goto(url, timeout=60000)
                log.info(f"Successfully loaded page for username: {username}")
                break
            except PlaywrightTimeoutError:
                if attempt < max_retries - 1:
                    log.warning(f"Timeout occurred. Retrying... (Attempt {attempt + 1}/{max_retries})")
                else:
                    log.error(f"Max retries reached. Unable to load the page for username: {username}")
                    await browser.close()
                    return {"error": f"Unable to load the page for username: {username} after multiple attempts"}

        try:
            log.info("Starting scroll function")
            await page.evaluate(js_scroll_function)
            log.info(f"Finished scrolling for username: {username}")
            
            log.info("Waiting for additional XHR requests")
            await page.wait_for_timeout(10000)
            
        except Exception as e:
            log.error(f"Error during scrolling for username {username}: {str(e)}")

        page_content = await page.content()
        log.debug(f"Final page content sample: {page_content[:1000]}...")

        await browser.close()

    if xhr_data_list:
        log.info(f"Captured {len(xhr_data_list)} XHR requests for username: {username}")
        return {"xhr_data": xhr_data_list}
    else:
        log.error(f"No XHR data captured for username: {username}")
        return {"error": f"No XHR data captured for username: {username}"}

def parse_channel(data):
    if "itemList" not in data:
        return []
    
    parsed_data = []
    for post in data["itemList"]:
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
    
    return parsed_data

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