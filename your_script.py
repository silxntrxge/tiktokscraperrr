import os
import requests
import logging
from selenium import webdriver
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables
IS_REMOTE = os.environ.get('IS_REMOTE', 'false').lower() == 'true'
PROXY_HOST = os.environ.get('PROXY_HOST', 'localhost')
PROXY_PORT = int(os.environ.get('PROXY_PORT', '8081'))
WEBDRIVER_URL = os.environ.get('WEBDRIVER_URL', 'http://localhost:4444/wd/hub')

def create_proxy():
    try:
        proxy = requests.post(f'http://{PROXY_HOST}:{PROXY_PORT}/proxy').json()
        return proxy['port']
    except requests.RequestException as e:
        logger.error(f"Failed to create proxy: {e}")
        raise

def gather_xhr_with_browsermob(url):
    try:
        proxy_port = create_proxy()
        logger.info(f"Created proxy on port {proxy_port}")
        
        proxy_url = f"{PROXY_HOST}:{proxy_port}"
        
        chrome_options = webdriver.ChromeOptions()
        chrome_options.add_argument(f'--proxy-server={proxy_url}')
        
        if IS_REMOTE:
            logger.info(f"Using remote WebDriver at {WEBDRIVER_URL}")
            driver = webdriver.Remote(
                command_executor=WEBDRIVER_URL,
                options=chrome_options
            )
        else:
            logger.info("Using local WebDriver")
            driver = webdriver.Chrome(options=chrome_options)
        
        try:
            logger.info(f"Sending GET request to {url}")
            driver.get(url)
            # ... (rest of your code to gather XHR data)
        finally:
            driver.quit()
            logger.info(f"Stopping proxy on port {proxy_port}")
            requests.delete(f'http://{PROXY_HOST}:{PROXY_PORT}/proxy/{proxy_port}')
    except Exception as e:
        logger.error(f"Error in gather_xhr_with_browsermob: {e}")
        raise

if __name__ == "__main__":
    app_port = int(os.environ.get('PORT', '10000'))
    logger.info(f"Starting application on port {app_port}")
    
    # Your main application code here
    # For example:
    # app.run(host='0.0.0.0', port=app_port)
    
    # Example usage of gather_xhr_with_browsermob
    try:
        gather_xhr_with_browsermob("https://www.tiktok.com/@tiktok")
    except Exception as e:
        logger.error(f"Failed to gather XHR data: {e}")