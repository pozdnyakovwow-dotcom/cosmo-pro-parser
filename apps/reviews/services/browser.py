import time
from pathlib import Path
from typing import Callable, Optional

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service


class SeleniumBrowserFetcher:
    def __init__(self, browser_config: Optional[dict] = None):
        self.browser_config = browser_config or {}

    def fetch_html(
        self,
        url: str,
        is_html_ready: Optional[Callable[[str], bool]] = None,
    ) -> str:
        driver = None
        try:
            driver = self._build_driver()
            driver.get(url)
            time.sleep(float(self.browser_config.get("wait_after_load_seconds", 4)))
            html = driver.page_source
            if is_html_ready and not is_html_ready(html):
                html = self._wait_until_ready(driver, is_html_ready)
            return html
        except TimeoutException as exc:
            raise RuntimeError(f"Selenium page load timeout for {url}") from exc
        except WebDriverException as exc:
            raise RuntimeError(f"Selenium driver error for {url}: {exc}") from exc
        finally:
            if driver is not None:
                driver.quit()

    def _wait_until_ready(self, driver, is_html_ready: Callable[[str], bool]) -> str:
        deadline = time.time() + float(self.browser_config.get("interaction_timeout_seconds", 180))
        poll = float(self.browser_config.get("interaction_poll_seconds", 2))
        last_html = driver.page_source
        while time.time() < deadline:
            current_html = driver.page_source
            last_html = current_html
            if is_html_ready(current_html):
                return current_html
            time.sleep(poll)
        return last_html

    def _build_driver(self):
        options = Options()
        if self.browser_config.get("headless", True):
            options.add_argument("--headless=new")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--no-sandbox")
        options.add_argument("--window-size={width},{height}".format(
            width=self.browser_config.get("window_width", 1440),
            height=self.browser_config.get("window_height", 2200),
        ))
        options.add_argument("--lang=ru-RU")
        options.add_argument(
            "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )
        chrome_binary_path = self.browser_config.get("chrome_binary_path")
        if chrome_binary_path:
            options.binary_location = chrome_binary_path
        user_data_dir = self.browser_config.get("user_data_dir")
        if user_data_dir:
            Path(user_data_dir).mkdir(parents=True, exist_ok=True)
            options.add_argument(f"--user-data-dir={user_data_dir}")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        service = Service()
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(int(self.browser_config.get("page_load_timeout_seconds", 45)))
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": """
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    Object.defineProperty(navigator, 'platform', {get: () => 'MacIntel'});
                    Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU', 'ru', 'en-US', 'en']});
                    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                    window.chrome = { runtime: {} };
                """
            },
        )
        return driver
