import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import aiohttp
import requests
from bs4 import BeautifulSoup

from apps.reviews.services.browser import SeleniumBrowserFetcher


class FetchError(RuntimeError):
    def __init__(self, message: str, http_status: Optional[int] = None, raw_html: str = ""):
        super().__init__(message)
        self.http_status = http_status
        self.raw_html = raw_html


class TemporaryFetchError(FetchError):
    pass


class SourceBlockedError(FetchError):
    pass


@dataclass
class ParsedReview:
    review_text: str = ""
    review_published_at: str = ""
    review_rating: str = ""
    reviewer_name: str = ""
    external_id: str = ""
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParseResult:
    doctor_name: str = ""
    clinic: str = ""
    reviews: list[ParsedReview] = field(default_factory=list)
    raw_html: str = ""


class BaseDoctorParser:
    source_site = ""
    clinic_link_patterns: tuple[str, ...] = ()
    browser_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }

    def __init__(
        self,
        timeout: int = 25,
        retries: int = 3,
        rate_limit_seconds: float = 1.5,
        browser_enabled: bool = False,
        browser_config: Optional[dict] = None,
        use_browser_fallback: bool = False,
        manual_browser_assist: bool = False,
    ):
        self.timeout = timeout
        self.retries = retries
        self.rate_limit_seconds = rate_limit_seconds
        self.browser_enabled = browser_enabled
        self.browser_config = browser_config or {}
        self.use_browser_fallback = use_browser_fallback
        self.manual_browser_assist = manual_browser_assist

    def parse_sync(self, profile_url: str) -> ParseResult:
        if self._should_prefer_browser():
            html = self._fetch_with_browser(profile_url)
            return self.parse_html(html, profile_url)
        html = self._fetch_sync(profile_url)
        return self.parse_html(html, profile_url)

    async def parse_async(self, profile_url: str) -> ParseResult:
        if self._should_prefer_browser():
            html = await asyncio.to_thread(self._fetch_with_browser, profile_url)
            return self.parse_html(html, profile_url)
        html = await self._fetch_async(profile_url)
        return self.parse_html(html, profile_url)

    def parse_html(self, html: str, profile_url: str) -> ParseResult:
        soup = BeautifulSoup(html, "lxml")
        doctor_name = self._safe_text(soup.select_one("h1"))
        clinic = self._extract_clinic(soup)
        reviews = self._extract_reviews(soup)
        return ParseResult(
            doctor_name=doctor_name,
            clinic=clinic,
            reviews=reviews,
            raw_html=html,
        )

    def _fetch_sync(self, profile_url: str) -> str:
        last_error: Optional[Exception] = None
        session = requests.Session()
        for attempt in range(1, self.retries + 1):
            try:
                response = session.get(
                    profile_url,
                    headers=self.browser_headers,
                    timeout=self.timeout,
                    allow_redirects=True,
                )
                self._raise_for_bad_response(response.status_code, response.text, response.url)
                time.sleep(self.rate_limit_seconds)
                return response.text
            except SourceBlockedError as exc:
                if self._should_use_browser_fallback():
                    return self._fetch_with_browser(profile_url, blocked_exc=exc)
                raise
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(self.rate_limit_seconds * attempt)
            except TemporaryFetchError as exc:
                last_error = exc
                time.sleep(self.rate_limit_seconds * attempt)
        raise RuntimeError(f"{self.source_site} fetch failed: {last_error}") from last_error

    async def _fetch_async(self, profile_url: str) -> str:
        last_error: Optional[Exception] = None
        for attempt in range(1, self.retries + 1):
            try:
                timeout = aiohttp.ClientTimeout(total=self.timeout)
                async with aiohttp.ClientSession(timeout=timeout, headers=self.browser_headers) as session:
                    async with session.get(profile_url) as response:
                        html = await response.text()
                        self._raise_for_bad_response(response.status, html, str(response.url))
                        await asyncio.sleep(self.rate_limit_seconds)
                        return html
            except SourceBlockedError as exc:
                if self._should_use_browser_fallback():
                    return await asyncio.to_thread(self._fetch_with_browser, profile_url, exc)
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = exc
                await asyncio.sleep(self.rate_limit_seconds * attempt)
            except TemporaryFetchError as exc:
                last_error = exc
                await asyncio.sleep(self.rate_limit_seconds * attempt)
        raise RuntimeError(f"{self.source_site} fetch failed: {last_error}") from last_error

    def _extract_clinic(self, soup: BeautifulSoup) -> str:
        for href_part in self.clinic_link_patterns:
            anchor = soup.select_one(f'a[href*="{href_part}"]')
            if anchor:
                return self._safe_text(anchor)
        return ""

    def _extract_reviews(self, soup: BeautifulSoup) -> list[ParsedReview]:
        reviews = self._reviews_from_json_ld(soup)
        if reviews:
            return reviews
        return self._reviews_from_microdata(soup)

    def _reviews_from_json_ld(self, soup: BeautifulSoup) -> list[ParsedReview]:
        reviews: list[ParsedReview] = []
        for script in soup.select('script[type="application/ld+json"]'):
            text = script.string or script.get_text(strip=True)
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            for review in self._collect_review_nodes(payload):
                reviews.append(
                    ParsedReview(
                        review_text=str(review.get("reviewBody", "")),
                        review_published_at=str(review.get("datePublished", "")),
                        review_rating=str(
                            (review.get("reviewRating") or {}).get("ratingValue", "")
                        ),
                        reviewer_name=str((review.get("author") or {}).get("name", "")),
                        external_id=str(review.get("@id", "")),
                        raw_payload=review,
                    )
                )
        return [review for review in reviews if review.review_text or review.review_rating]

    def _collect_review_nodes(self, value: Any) -> list[dict[str, Any]]:
        nodes: list[dict[str, Any]] = []
        if isinstance(value, dict):
            if value.get("@type") == "Review":
                nodes.append(value)
            if "review" in value:
                nodes.extend(self._collect_review_nodes(value["review"]))
            for child in value.values():
                nodes.extend(self._collect_review_nodes(child))
        elif isinstance(value, list):
            for item in value:
                nodes.extend(self._collect_review_nodes(item))
        return nodes

    def _reviews_from_microdata(self, soup: BeautifulSoup) -> list[ParsedReview]:
        reviews: list[ParsedReview] = []
        for node in soup.select('[itemprop="review"], [itemtype*="Review"]'):
            reviews.append(
                ParsedReview(
                    review_text=self._safe_text(node.select_one('[itemprop="reviewBody"]')),
                    review_published_at=self._safe_text(
                        node.select_one('[itemprop="datePublished"], time')
                    ),
                    review_rating=self._safe_text(node.select_one('[itemprop="ratingValue"]')),
                    reviewer_name=self._safe_text(node.select_one('[itemprop="author"]')),
                )
            )
        return [review for review in reviews if review.review_text or review.review_rating]

    @staticmethod
    def _safe_text(node) -> str:
        if not node:
            return ""
        return " ".join(node.get_text(" ", strip=True).split())

    def _raise_for_bad_response(self, status_code: int, html: str, response_url: str) -> None:
        if status_code in (401, 403):
            raise SourceBlockedError(
                f"{self.source_site} blocked access with status {status_code}: {response_url}",
                http_status=status_code,
                raw_html=html,
            )
        if status_code in (408, 409, 425, 429, 500, 502, 503, 504):
            raise TemporaryFetchError(
                f"{self.source_site} temporary error with status {status_code}: {response_url}",
                http_status=status_code,
                raw_html=html,
            )
        if status_code >= 400:
            raise FetchError(
                f"{self.source_site} returned status {status_code}: {response_url}",
                http_status=status_code,
                raw_html=html,
            )
        if self._is_probably_blocked_html(html):
            raise SourceBlockedError(
                f"{self.source_site} returned an anti-bot or interstitial page: {response_url}",
                http_status=status_code,
                raw_html=html,
            )

    def _fetch_with_browser(self, profile_url: str, blocked_exc: Optional[SourceBlockedError] = None) -> str:
        try:
            fetcher = SeleniumBrowserFetcher(self.browser_config)
            html = fetcher.fetch_html(
                profile_url,
                is_html_ready=self._browser_html_is_ready if self.manual_browser_assist else None,
            )
            self._raise_for_bad_response(200, html, profile_url)
            time.sleep(self.rate_limit_seconds)
            return html
        except SourceBlockedError:
            raise
        except Exception as exc:
            raise TemporaryFetchError(
                f"{self.source_site} selenium fallback failed: {exc}",
                http_status=getattr(blocked_exc, "http_status", None),
                raw_html=getattr(blocked_exc, "raw_html", ""),
            ) from exc

    def _should_use_browser_fallback(self) -> bool:
        return self.browser_enabled and self.use_browser_fallback

    def _should_prefer_browser(self) -> bool:
        return self._should_use_browser_fallback() and self.browser_config.get(
            "force_for_blocked_sources",
            False,
        )

    def _is_probably_blocked_html(self, html: str) -> bool:
        lower_html = html.lower()
        blocked_markers = (
            "servicepipe.ru/loaders/default.js",
            "attention required",
            "captcha",
            "just a moment",
            "/exhkqyad",
            "enable javascript",
            "access denied",
        )
        return any(marker in lower_html for marker in blocked_markers)

    def _browser_html_is_ready(self, html: str) -> bool:
        if self._is_probably_blocked_html(html):
            return False
        return bool(BeautifulSoup(html, "lxml").select_one("h1"))
