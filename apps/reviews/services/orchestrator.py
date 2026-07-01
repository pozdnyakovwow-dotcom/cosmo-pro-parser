import asyncio
import hashlib
from datetime import timedelta
from pathlib import Path
from typing import Callable, Optional, Union

import yaml
from django.db.models import Q
from django.utils import timezone

from apps.reviews.models import (
    SOURCE_SITE_DEFAULTS,
    Clinic,
    CrawlRun,
    Doctor,
    DoctorSource,
    Review,
    SourceSite,
)
from parsers.base import SourceBlockedError, TemporaryFetchError
from parsers.docdoc import DocDocParser
from parsers.doctu import DoctuParser
from parsers.napopravku import NaPopravkuParser
from parsers.prodoctorov import ProdoctorovParser

from .exporters import export_to_csv, export_to_json, flatten_export_records
from .input_loader import CSVInputLoader, DoctorSeed, normalize_profile_url
from .logging_utils import get_logger
from .matching import group_seeds_by_doctor, normalize_display_name, normalize_text, split_full_name
from .validators import clean_datetime, clean_rating, validate_review_payload

logger = get_logger(__name__)

PARSERS = {
    "docdoc": DocDocParser,
    "doctu": DoctuParser,
    "napopravku": NaPopravkuParser,
    "prodoctorov": ProdoctorovParser,
}


class PipelineOrchestrator:
    def __init__(
        self,
        config_path: Union[str, Path],
        runtime_overrides: Optional[dict] = None,
        progress_callback: Optional[Callable[[dict], None]] = None,
        source_scope: str = "all",
    ):
        self.config_path = Path(config_path)
        self.config = self._load_config(self.config_path)
        self.runtime_overrides = runtime_overrides or {}
        self._apply_runtime_overrides()
        self.network = self.config["network"]
        self.browser = self.config.get("browser", {})
        self.progress_callback = progress_callback
        self.source_scope = source_scope

    def run(self, mode: str = "sync", fetch_pages: bool = True) -> dict:
        self._ensure_source_sites()
        self._deduplicate_sources_in_db()
        seeds = CSVInputLoader(self.config["project"]["docs_dir"]).load()
        grouped = group_seeds_by_doctor(seeds)
        self._bootstrap_sources(grouped)
        self._deduplicate_sources_in_db()
        process_sources = list(self._sources_to_process()) if fetch_pages else []
        summary = {
            "doctors": len(grouped),
            "sources": len(seeds),
            "fetched": 0,
            "scope": self.source_scope,
            "queued_sources": len(process_sources),
            "manual_blocked_candidates": self._manual_blocked_candidates_count(),
        }
        self._emit_progress(
            event="initialized",
            stage="Подготовка входных данных завершена",
            summary=summary,
            total_sources=len(process_sources),
        )
        if fetch_pages:
            if mode == "async":
                summary["fetched"] = asyncio.run(self._run_async(process_sources))
            else:
                summary["fetched"] = self._run_sync(process_sources)
        export_paths = self._export()
        summary.update(export_paths)
        summary["manual_blocked_candidates"] = self._manual_blocked_candidates_count()
        self._emit_progress(
            event="finished",
            stage="Экспорт завершен",
            summary=summary,
        )
        return summary

    def _bootstrap_sources(self, grouped: dict[str, list[DoctorSeed]]) -> None:
        for normalized_name, seeds in grouped.items():
            canonical_name = normalize_display_name(seeds[0].doctor_name)
            last_name, first_name, middle_name = split_full_name(canonical_name)
            doctor, _ = Doctor.objects.get_or_create(
                normalized_name=normalized_name,
                defaults={
                    "full_name": canonical_name,
                    "last_name": last_name,
                    "first_name": first_name,
                    "middle_name": middle_name,
                },
            )
            changed_fields = []
            if last_name and doctor.last_name != last_name:
                doctor.last_name = last_name
                changed_fields.append("last_name")
            if first_name and doctor.first_name != first_name:
                doctor.first_name = first_name
                changed_fields.append("first_name")
            if middle_name and doctor.middle_name != middle_name:
                doctor.middle_name = middle_name
                changed_fields.append("middle_name")
            if changed_fields:
                doctor.save(update_fields=changed_fields + ["full_name", "updated_at"])
            for seed in seeds:
                clinic = self._get_or_create_clinic(seed.clinic_label)
                site = self._get_or_create_site(seed.source_site)
                source, created = DoctorSource.objects.get_or_create(
                    source_site=seed.source_site,
                    profile_url=normalize_profile_url(seed.profile_url),
                    defaults={
                        "site": site,
                        "doctor": doctor,
                        "clinic": clinic,
                        "source_file": seed.source_file,
                        "input_payload": seed.input_payload,
                    },
                )
                if not created:
                    changed_fields = self._merge_existing_source(source, doctor, clinic, site, seed)
                    if changed_fields:
                        source.save(update_fields=changed_fields + ["updated_at"])

    def _run_sync(self, sources: list[DoctorSource]) -> int:
        fetched = 0
        total_sources = len(sources)
        for index, source in enumerate(sources, start=1):
            self._emit_progress(
                event="source_started",
                stage="Парсинг ссылки",
                index=index,
                total_sources=total_sources,
                source=source,
            )
            parser = self._build_parser(source.parser_code)
            crawl_run = CrawlRun.objects.create(source_site=source.parser_code, mode="sync")
            try:
                result = parser.parse_sync(source.profile_url)
                self._persist_parse_result(source, result.raw_html, result.clinic, result.reviews)
                crawl_run.status = "completed"
                crawl_run.details = {"reviews": len(result.reviews)}
                fetched += 1
                self._emit_progress(
                    event="source_completed",
                    stage="Ссылка обработана",
                    index=index,
                    total_sources=total_sources,
                    source=source,
                    reviews_count=len(result.reviews),
                )
            except SourceBlockedError as exc:
                self._mark_source_error(source, exc, blocked=True)
                crawl_run.status = "failed"
                crawl_run.details = {"error": str(exc), "kind": "blocked"}
                if getattr(exc, "raw_html", ""):
                    self._save_raw_html(source, exc.raw_html)
                self._emit_progress(
                    event="source_blocked",
                    stage="Источник заблокирован",
                    index=index,
                    total_sources=total_sources,
                    source=source,
                    error=str(exc),
                )
            except Exception as exc:
                self._mark_source_error(
                    source,
                    exc,
                    blocked=False,
                    temporary=isinstance(exc, TemporaryFetchError),
                )
                crawl_run.status = "failed"
                crawl_run.details = {"error": str(exc)}
                logger.exception("Sync parsing failed for %s", source.profile_url)
                self._emit_progress(
                    event="source_failed",
                    stage="Ошибка при парсинге",
                    index=index,
                    total_sources=total_sources,
                    source=source,
                    error=str(exc),
                )
            crawl_run.finished_at = timezone.now()
            crawl_run.save(update_fields=["status", "finished_at", "details", "updated_at"])
        return fetched

    async def _run_async(self, sources: list[DoctorSource]) -> int:
        results = await asyncio.gather(
            *(self._parse_source_async(source, index + 1, len(sources)) for index, source in enumerate(sources)),
            return_exceptions=True,
        )
        return sum(1 for result in results if result is True)

    async def _parse_source_async(self, source: DoctorSource, index: int, total_sources: int) -> bool:
        self._emit_progress(
            event="source_started",
            stage="Парсинг ссылки",
            index=index,
            total_sources=total_sources,
            source=source,
        )
        parser = self._build_parser(source.parser_code)
        crawl_run = CrawlRun.objects.create(source_site=source.parser_code, mode="async")
        try:
            result = await parser.parse_async(source.profile_url)
            self._persist_parse_result(source, result.raw_html, result.clinic, result.reviews)
            crawl_run.status = "completed"
            crawl_run.details = {"reviews": len(result.reviews)}
            self._emit_progress(
                event="source_completed",
                stage="Ссылка обработана",
                index=index,
                total_sources=total_sources,
                source=source,
                reviews_count=len(result.reviews),
            )
            return True
        except SourceBlockedError as exc:
            self._mark_source_error(source, exc, blocked=True)
            crawl_run.status = "failed"
            crawl_run.details = {"error": str(exc), "kind": "blocked"}
            if getattr(exc, "raw_html", ""):
                self._save_raw_html(source, exc.raw_html)
            self._emit_progress(
                event="source_blocked",
                stage="Источник заблокирован",
                index=index,
                total_sources=total_sources,
                source=source,
                error=str(exc),
            )
            return False
        except Exception as exc:
            self._mark_source_error(
                source,
                exc,
                blocked=False,
                temporary=isinstance(exc, TemporaryFetchError),
            )
            crawl_run.status = "failed"
            crawl_run.details = {"error": str(exc)}
            logger.exception("Async parsing failed for %s", source.profile_url)
            self._emit_progress(
                event="source_failed",
                stage="Ошибка при парсинге",
                index=index,
                total_sources=total_sources,
                source=source,
                error=str(exc),
            )
            return False
        finally:
            crawl_run.finished_at = timezone.now()
            crawl_run.save(update_fields=["status", "finished_at", "details", "updated_at"])

    def _persist_parse_result(self, source: DoctorSource, raw_html: str, clinic_name: str, reviews: list) -> None:
        source.last_status = "ok"
        source.last_error = ""
        source.last_checked_at = timezone.now()
        source.last_success_at = source.last_checked_at
        source.next_retry_at = None
        source.last_http_status = 200
        source.consecutive_failures = 0
        source.last_reviews_count = len(reviews)
        if clinic_name and source.clinic and not source.clinic.address:
            source.clinic.address = clinic_name
            source.clinic.save(update_fields=["address", "updated_at"])
        source.save(
            update_fields=[
                "last_status",
                "last_error",
                "last_checked_at",
                "last_success_at",
                "next_retry_at",
                "last_http_status",
                "consecutive_failures",
                "last_reviews_count",
                "updated_at",
            ]
        )
        self._save_raw_html(source, raw_html)
        for review in reviews:
            review_payload = {
                "doctor_name": source.doctor.full_name,
                "profile_url": source.profile_url,
                "review_text": review.review_text,
                "published_at": review.review_published_at,
                "rating": review.review_rating,
            }
            validation_errors = validate_review_payload(review_payload)
            if validation_errors:
                logger.warning(
                    "Review skipped for %s because of validation errors: %s",
                    source.profile_url,
                    ", ".join(validation_errors),
                )
                continue
            content_hash = self._build_review_hash(source, review)
            defaults = {
                "doctor_source": source,
                "external_id": review.external_id,
                "review_text": review.review_text,
                "published_at": clean_datetime(review.review_published_at),
                "rating": clean_rating(review.review_rating),
                "reviewer_name": review.reviewer_name,
                "raw_payload": review.raw_payload,
            }
            if review.external_id:
                review_object = Review.objects.filter(
                    doctor_source=source,
                    external_id=review.external_id,
                ).first()
                if review_object:
                    for field_name, field_value in defaults.items():
                        setattr(review_object, field_name, field_value)
                    if review_object.content_hash != content_hash:
                        review_object.content_hash = content_hash
                    review_object.save()
                    continue
            Review.objects.update_or_create(
                content_hash=content_hash,
                defaults=defaults,
            )

    def _export(self) -> dict[str, str]:
        records = []
        sources = DoctorSource.objects.select_related("doctor", "clinic", "site").prefetch_related("reviews")
        for source in sources:
            records.append(
                {
                    "clinic": source.clinic.name if source.clinic else "",
                    "doctor": source.doctor.full_name,
                    "source_site": source.site.name if source.site else source.source_site,
                    "doctor_profile_url": source.profile_url,
                    "reviews": [
                        {
                            "review_text": review.review_text,
                            "review_published_at": review.published_at.isoformat() if review.published_at else "",
                            "review_rating": str(review.rating) if review.rating is not None else "",
                        }
                        for review in source.reviews.all()
                    ],
                }
            )
        rows = flatten_export_records(
            records,
            include_empty_reviews=self.config["project"]["include_doctors_without_reviews"],
        )
        export_dir = Path(self.config["project"]["export_dir"])
        csv_path = export_to_csv(rows, export_dir / self.config["export"]["csv_name"])
        json_path = export_to_json(rows, export_dir / self.config["export"]["json_name"])
        return {"csv": str(csv_path), "json": str(json_path)}

    def _get_or_create_clinic(self, clinic_label: str) -> Optional[Clinic]:
        if not clinic_label:
            return None
        normalized_key = normalize_text(clinic_label)
        clinic, _ = Clinic.objects.get_or_create(
            normalized_key=normalized_key,
            defaults={"name": clinic_label},
        )
        return clinic

    def _get_or_create_site(self, source_code: str) -> SourceSite:
        source_map = {
            code: {"name": name, "base_url": base_url}
            for code, name, base_url in SOURCE_SITE_DEFAULTS
        }
        defaults = source_map.get(source_code, {})
        site, _ = SourceSite.objects.get_or_create(
            code=source_code,
            defaults={
                "name": defaults.get("name", source_code),
                "base_url": defaults.get("base_url", ""),
                "parser_key": source_code,
                "use_browser_fallback": self._source_browser_fallback(source_code),
                "manual_browser_assist": self._source_manual_browser_assist(source_code),
            },
        )
        return site

    def _build_parser(self, source_site: str):
        parser_class = PARSERS[source_site]
        site = SourceSite.objects.filter(code=source_site).first()
        return parser_class(
            timeout=self.network["request_timeout_seconds"],
            retries=self.network["retries"],
            rate_limit_seconds=self.network["rate_limit_seconds"],
            browser_enabled=self.browser.get("enabled", False),
            browser_config=self.browser,
            use_browser_fallback=bool(site.use_browser_fallback) if site else False,
            manual_browser_assist=bool(site.manual_browser_assist) if site else False,
        )

    def _ensure_source_sites(self) -> None:
        for code, name, base_url in SOURCE_SITE_DEFAULTS:
            site, created = SourceSite.objects.get_or_create(
                code=code,
                defaults={
                    "name": name,
                    "base_url": base_url,
                    "parser_key": code,
                    "use_browser_fallback": self._source_browser_fallback(code),
                    "manual_browser_assist": self._source_manual_browser_assist(code),
                },
            )
            if not created:
                updated_fields = []
                if not site.parser_key:
                    site.parser_key = code
                    updated_fields.append("parser_key")
                if not site.base_url:
                    site.base_url = base_url
                    updated_fields.append("base_url")
                desired_fallback = self._source_browser_fallback(code)
                if site.use_browser_fallback != desired_fallback:
                    site.use_browser_fallback = desired_fallback
                    updated_fields.append("use_browser_fallback")
                desired_manual_assist = self._source_manual_browser_assist(code)
                if site.manual_browser_assist != desired_manual_assist:
                    site.manual_browser_assist = desired_manual_assist
                    updated_fields.append("manual_browser_assist")
                if updated_fields:
                    site.save(update_fields=updated_fields + ["updated_at"])

    def _load_config(self, path: Path) -> dict:
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)

    def _save_raw_html(self, source: DoctorSource, raw_html: str) -> None:
        raw_dir = Path(self.config["project"]["raw_dir"])
        raw_dir.mkdir(parents=True, exist_ok=True)
        filename = hashlib.md5(source.profile_url.encode("utf-8")).hexdigest() + ".html"
        (raw_dir / filename).write_text(raw_html, encoding="utf-8")

    def _sources_to_process(self):
        queryset = DoctorSource.objects.select_related("doctor", "clinic", "site").filter(is_active=True)
        now = timezone.now()
        if self.source_scope == "manual_blocked":
            queryset = queryset.filter(last_status="blocked", site__manual_browser_assist=True)
        else:
            queryset = queryset.filter(Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now))
        return queryset.order_by(
            "crawl_priority",
            "updated_at",
        )

    def _merge_existing_source(
        self,
        source: DoctorSource,
        doctor: Doctor,
        clinic: Optional[Clinic],
        site: SourceSite,
        seed: DoctorSeed,
    ) -> list[str]:
        changed_fields: list[str] = []
        if source.site_id != site.id:
            source.site = site
            changed_fields.append("site")
        if source.doctor_id != doctor.id:
            source.doctor = doctor
            changed_fields.append("doctor")
        if clinic and source.clinic_id != clinic.id:
            source.clinic = clinic
            changed_fields.append("clinic")
        if seed.source_file and source.source_file != seed.source_file:
            source.source_file = seed.source_file
            changed_fields.append("source_file")
        merged_payload = dict(source.input_payload or {})
        incoming_payload = dict(seed.input_payload or {})
        if incoming_payload:
            payload_list = list(merged_payload.get("seed_payloads", []))
            if incoming_payload not in payload_list:
                payload_list.append(incoming_payload)
            merged_payload["seed_payloads"] = payload_list
        source_files = list(merged_payload.get("source_files", []))
        if seed.source_file and seed.source_file not in source_files:
            source_files.append(seed.source_file)
        merged_payload["source_files"] = source_files
        if merged_payload != (source.input_payload or {}):
            source.input_payload = merged_payload
            changed_fields.append("input_payload")
        return changed_fields

    def _mark_source_error(
        self,
        source: DoctorSource,
        exc: Exception,
        blocked: bool,
        temporary: bool = False,
    ) -> None:
        source.last_status = "blocked" if blocked else "failed"
        source.last_error = str(exc)
        source.last_checked_at = timezone.now()
        source.consecutive_failures += 1
        source.last_http_status = getattr(exc, "http_status", None)
        if blocked:
            source.next_retry_at = timezone.now() + timedelta(hours=12)
        elif temporary:
            delay_minutes = min(60, 5 * source.consecutive_failures)
            source.next_retry_at = timezone.now() + timedelta(minutes=delay_minutes)
        else:
            source.next_retry_at = timezone.now() + timedelta(minutes=30)
        source.save(
            update_fields=[
                "last_status",
                "last_error",
                "last_checked_at",
                "consecutive_failures",
                "last_http_status",
                "next_retry_at",
                "updated_at",
            ]
        )

    def _build_review_hash(self, source: DoctorSource, review) -> str:
        return hashlib.sha256(
            "|".join(
                [
                    source.parser_code,
                    normalize_profile_url(source.profile_url),
                    review.external_id or "",
                    review.review_text.strip(),
                    review.review_published_at.strip(),
                    review.review_rating.strip(),
                ]
            ).encode("utf-8")
        ).hexdigest()

    def _deduplicate_sources_in_db(self) -> None:
        grouped: dict[tuple[str, str], list[DoctorSource]] = {}
        queryset = DoctorSource.objects.select_related("site", "clinic", "doctor").prefetch_related("reviews")
        for source in queryset.order_by("id"):
            key = (source.parser_code, normalize_profile_url(source.profile_url))
            grouped.setdefault(key, []).append(source)
        for duplicates in grouped.values():
            if len(duplicates) < 2:
                continue
            primary = self._select_primary_source(duplicates)
            changed_fields: set[str] = set()
            for duplicate in duplicates:
                if duplicate.id == primary.id:
                    continue
                if duplicate.site_id and not primary.site_id:
                    primary.site = duplicate.site
                    changed_fields.add("site")
                if duplicate.clinic_id and not primary.clinic_id:
                    primary.clinic = duplicate.clinic
                    changed_fields.add("clinic")
                if duplicate.source_file and not primary.source_file:
                    primary.source_file = duplicate.source_file
                    changed_fields.add("source_file")
                merged_payload = self._merge_payload_values(primary.input_payload, duplicate.input_payload)
                if merged_payload != (primary.input_payload or {}):
                    primary.input_payload = merged_payload
                    changed_fields.add("input_payload")
                for review in duplicate.reviews.all():
                    collision = Review.objects.filter(content_hash=review.content_hash).exclude(pk=review.pk).exists()
                    if collision:
                        review.delete()
                        continue
                    review.doctor_source = primary
                    review.save(update_fields=["doctor_source", "updated_at"])
                duplicate.delete()
            normalized_url = normalize_profile_url(primary.profile_url)
            if primary.profile_url != normalized_url:
                primary.profile_url = normalized_url
                changed_fields.add("profile_url")
            if changed_fields:
                primary.save(update_fields=sorted(changed_fields) + ["updated_at"])

    def _select_primary_source(self, duplicates: list[DoctorSource]) -> DoctorSource:
        return sorted(
            duplicates,
            key=lambda source: (
                0 if source.site_id else 1,
                0 if source.last_success_at else 1,
                -(source.last_reviews_count or source.reviews.count()),
                source.id,
            ),
        )[0]

    def _merge_payload_values(self, left_payload: Optional[dict], right_payload: Optional[dict]) -> dict:
        merged = dict(left_payload or {})
        for key, value in (right_payload or {}).items():
            if key not in merged:
                merged[key] = value
                continue
            if merged[key] == value:
                continue
            if not isinstance(merged[key], list):
                merged[key] = [merged[key]]
            if isinstance(value, list):
                for item in value:
                    if item not in merged[key]:
                        merged[key].append(item)
            elif value not in merged[key]:
                merged[key].append(value)
        return merged

    def _source_browser_fallback(self, source_code: str) -> bool:
        source_config = self.config.get("sources", {}).get(source_code, {})
        return bool(source_config.get("use_browser_fallback", False))

    def _source_manual_browser_assist(self, source_code: str) -> bool:
        source_config = self.config.get("sources", {}).get(source_code, {})
        return bool(source_config.get("manual_browser_assist", False))

    def _apply_runtime_overrides(self) -> None:
        browser_config = self.config.setdefault("browser", {})
        if "headless" in self.runtime_overrides:
            browser_config["headless"] = self.runtime_overrides["headless"]
        if "assisted_mode" in self.runtime_overrides:
            browser_config["assisted_mode"] = self.runtime_overrides["assisted_mode"]

    def _emit_progress(self, **payload) -> None:
        if not self.progress_callback:
            return
        self.progress_callback(payload)

    def _manual_blocked_candidates_count(self) -> int:
        return DoctorSource.objects.filter(
            is_active=True,
            last_status="blocked",
            site__manual_browser_assist=True,
        ).count()
