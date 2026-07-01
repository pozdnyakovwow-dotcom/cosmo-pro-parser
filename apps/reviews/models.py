from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


SOURCE_SITE_DEFAULTS = (
    ("docdoc", "DocDoc", "https://docdoc.ru"),
    ("doctu", "Doctu", "https://doctu.ru"),
    ("napopravku", "НаПоправку", "https://napopravku.ru"),
    ("prodoctorov", "ПроДокторов", "https://prodoctorov.ru"),
)


def normalize_stored_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlsplit(url.strip())
    normalized_query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    normalized_path = parsed.path.rstrip("/") or parsed.path or "/"
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            normalized_path,
            normalized_query,
            "",
        )
    )


class SourceSite(TimeStampedModel):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=64)
    base_url = models.URLField(blank=True)
    parser_key = models.CharField(max_length=32, blank=True)
    is_active = models.BooleanField(default=True)
    use_browser_fallback = models.BooleanField(default=False)
    manual_browser_assist = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Clinic(TimeStampedModel):
    name = models.CharField(max_length=255)
    city = models.CharField(max_length=128, blank=True)
    address = models.CharField(max_length=255, blank=True)
    normalized_key = models.CharField(max_length=255, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Doctor(TimeStampedModel):
    full_name = models.CharField(max_length=255)
    last_name = models.CharField(max_length=128, blank=True)
    first_name = models.CharField(max_length=128, blank=True)
    middle_name = models.CharField(max_length=128, blank=True)
    normalized_name = models.CharField(max_length=255, unique=True)

    class Meta:
        ordering = ["full_name"]

    def __str__(self) -> str:
        return self.full_name

    def save(self, *args, **kwargs):
        parts = [self.last_name, self.first_name, self.middle_name]
        rebuilt_full_name = " ".join(part for part in parts if part).strip()
        if rebuilt_full_name:
            self.full_name = rebuilt_full_name
        super().save(*args, **kwargs)


class DoctorSource(TimeStampedModel):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("ok", "OK"),
        ("failed", "Failed"),
        ("blocked", "Blocked"),
        ("skipped", "Skipped"),
    ]
    SOURCE_CHOICES = [
        ("docdoc", "DocDoc"),
        ("doctu", "Doctu"),
        ("napopravku", "NaPopravku"),
        ("prodoctorov", "ProDoctorov"),
    ]

    doctor = models.ForeignKey(
        Doctor,
        on_delete=models.CASCADE,
        related_name="sources",
    )
    clinic = models.ForeignKey(
        Clinic,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="doctor_sources",
    )
    site = models.ForeignKey(
        SourceSite,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="doctor_sources",
    )
    source_site = models.CharField(max_length=32, choices=SOURCE_CHOICES)
    profile_url = models.URLField()
    source_file = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    input_payload = models.JSONField(default=dict, blank=True)
    last_status = models.CharField(max_length=32, choices=STATUS_CHOICES, default="pending")
    last_error = models.TextField(blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    last_http_status = models.PositiveIntegerField(null=True, blank=True)
    consecutive_failures = models.PositiveIntegerField(default=0)
    last_reviews_count = models.PositiveIntegerField(default=0)
    crawl_priority = models.PositiveSmallIntegerField(default=100)

    class Meta:
        ordering = ["crawl_priority", "source_site", "profile_url"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_site", "profile_url"],
                name="uniq_source_profile",
            ),
            models.UniqueConstraint(
                fields=["site", "profile_url"],
                condition=Q(site__isnull=False),
                name="uniq_site_profile",
            ),
        ]

    def __str__(self) -> str:
        site_name = self.site.name if self.site else self.source_site
        return f"{self.doctor.full_name} [{site_name}]"

    @property
    def parser_code(self) -> str:
        if self.site and self.site.parser_key:
            return self.site.parser_key
        if self.site:
            return self.site.code
        return self.source_site

    def clean(self) -> None:
        if not self.site and not self.source_site:
            raise ValidationError("Укажите сайт или код сайта.")
        if self.site and self.source_site and self.site.code != self.source_site:
            raise ValidationError("Код сайта должен совпадать с привязанным сайтом.")

    def save(self, *args, **kwargs):
        if self.site and not self.source_site:
            self.source_site = self.site.code
        self.profile_url = normalize_stored_url(self.profile_url)
        super().save(*args, **kwargs)


class Review(TimeStampedModel):
    doctor_source = models.ForeignKey(
        DoctorSource,
        on_delete=models.CASCADE,
        related_name="reviews",
    )
    external_id = models.CharField(max_length=255, blank=True)
    content_hash = models.CharField(max_length=64, unique=True)
    review_text = models.TextField(blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    rating = models.DecimalField(
        max_digits=3,
        decimal_places=1,
        null=True,
        blank=True,
    )
    reviewer_name = models.CharField(max_length=255, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-published_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["doctor_source", "external_id"],
                condition=~Q(external_id=""),
                name="uniq_review_external_id",
            )
        ]

    def __str__(self) -> str:
        return f"{self.doctor_source} [{self.rating}]"


class CrawlRun(TimeStampedModel):
    STATUS_CHOICES = [
        ("started", "Started"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    ]

    source_site = models.CharField(max_length=32)
    mode = models.CharField(max_length=16, default="sync")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="started")
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"{self.source_site}:{self.mode}:{self.status}"


class PipelineJob(TimeStampedModel):
    SCOPE_CHOICES = [
        ("all", "All Sources"),
        ("manual_blocked", "Manual Blocked Queue"),
    ]
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("running", "Running"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    ]

    mode = models.CharField(max_length=16, default="sync")
    scope = models.CharField(max_length=32, choices=SCOPE_CHOICES, default="all")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="pending")
    skip_fetch = models.BooleanField(default=False)
    browser_assisted = models.BooleanField(default=False)
    config_path = models.CharField(max_length=255, default="config/pipeline.yaml")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    total_sources = models.PositiveIntegerField(default=0)
    processed_sources = models.PositiveIntegerField(default=0)
    success_sources = models.PositiveIntegerField(default=0)
    blocked_sources = models.PositiveIntegerField(default=0)
    failed_sources = models.PositiveIntegerField(default=0)
    skipped_sources = models.PositiveIntegerField(default=0)
    current_stage = models.CharField(max_length=64, blank=True)
    current_doctor_name = models.CharField(max_length=255, blank=True)
    current_source_site = models.CharField(max_length=64, blank=True)
    current_profile_url = models.URLField(blank=True)
    summary = models.JSONField(default=dict, blank=True)
    recent_events = models.JSONField(default=list, blank=True)
    error_text = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"pipeline:{self.mode}:{self.status}:{self.pk}"
