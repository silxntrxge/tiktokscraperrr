def initialize_driver(proxy):
    options = webdriver.ChromeOptions()
    if proxy:
        # Add scheme if missing
        if not proxy.startswith(('http://', 'https://')):
            proxy = 'http://' + proxy
        options.add_argument(f'--proxy-server={proxy}')
    # ... rest of the function

def scrape_tiktok_profile(url, proxy=None):
    try:
        if proxy:
            if not proxy.proxy.startswith(('http://', 'https://')):
                proxy.proxy = 'http://' + proxy.proxy
            proxies = {'http': proxy.proxy, 'https': proxy.proxy}
        else:
            proxies = None
        
        response = requests.get(url, proxies=proxies)
        # Rest of the function...
    except requests.exceptions.RequestException as e:
        logger.error(f"Error scraping profile: {str(e)}")
        logger.error(f"Full traceback:", exc_info=True)