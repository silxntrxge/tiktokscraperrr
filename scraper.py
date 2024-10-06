import requests
from selenium import webdriver
import logging

logger = logging.getLogger(__name__)

def get_formatted_proxy_url(proxy):
    """Format proxy URL to include scheme"""
    if not proxy:
        return None
        
    proxy_url = proxy.proxy if hasattr(proxy, 'proxy') else proxy
    # If proxy URL doesn't start with http:// or https://, add http://
    if not proxy_url.startswith(('http://', 'https://')):
        proxy_url = f'http://{proxy_url}'
    
    return proxy_url

def setup_proxy_config(proxy):
    """Setup proxy configuration with proper URL schemes"""
    if not proxy:
        return None
        
    proxy_url = get_formatted_proxy_url(proxy)
    return {
        'http': proxy_url,
        'https': proxy_url
    }

def initialize_driver(proxy):
    options = webdriver.ChromeOptions()
    if proxy:
        proxy_url = get_formatted_proxy_url(proxy)
        options.add_argument(f'--proxy-server={proxy_url}')
    # ... rest of the function

def scrape_tiktok_profile(url, proxy=None):
    try:
        proxy_config = setup_proxy_config(proxy)
        response = requests.get(url, proxies=proxy_config)
        # Rest of the function...
    except requests.exceptions.RequestException as e:
        logger.error(f"Error scraping profile: {str(e)}")
        logger.error("Full traceback:", exc_info=True)