import asyncio
import json
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from loguru import logger as log
from playwright.async_api import async_playwright

log.add("app.log", rotation="500 MB")

app = FastAPI()

class ScrapeRequest(BaseModel):
    username: str

js_scroll_function = """
function scrollToEnd(i) {
    if (window.innerHeight + window.scrollY >= document.body.scrollHeight) {
        console.log("Reached the bottom.");
        return;
    }
    window.scrollTo(0, document.body.scrollHeight);
    if (i < 15) {
        setTimeout(() => scrollToEnd(i + 1), 3000);
    } else {
        console.log("Reached the end of iterations.");
    }
}
scrollToEnd(0);
"""

async def scrape_profile(username: str):
    xhr_data_list = []

    async def intercept_xhr(route, request):
        if "api/post/item_list" in request.url:
            response = await route.fetch()
            body = await response.text()
            xhr_data_list.append(body)
        await route.continue_()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        await page.route("**/*", intercept_xhr)

        url = f"https://www.tiktok.com/@{username}"
        await page.goto(url)
        await page.evaluate(js_scroll_function)
        await page.wait_for_timeout(45000)  # Wait for 45 seconds to allow scrolling and XHR calls

        await browser.close()

    if xhr_data_list:
        try:
            parsed_data = []
            for xhr_data in xhr_data_list:
                json_data = json.loads(xhr_data)
                parsed_data.extend(parse_channel(json_data))
            return parsed_data
        except json.JSONDecodeError:
            log.error("Failed to parse XHR data as JSON")
            return {"error": "Failed to parse XHR data"}
    else:
        log.error("No XHR data captured")
        return {"error": "No XHR data captured"}

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
    data = await scrape_profile(request.username)
    return {"video_data": data}

@app.get("/")
async def root():
    return {"message": "TikTok Scraper API is running. Use POST /scrape to scrape data."}

if __name__ == "__main__":
    log.info("Starting TikTok Scraper API")
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)