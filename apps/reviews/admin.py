from django.contrib import admin

from .models import Clinic, CrawlRun, Doctor, DoctorSource, PipelineJob, Review, SourceSite


class DoctorSourceInline(admin.TabularInline):
    model = DoctorSource
    extra = 0
    autocomplete_fields = ["doctor", "clinic", "site"]
    fields = (
        "site",
        "doctor",
        "clinic",
        "profile_url",
        "is_active",
        "last_status",
        "last_reviews_count",
        "last_checked_at",
    )
    readonly_fields = ("last_status", "last_reviews_count", "last_checked_at")


@admin.register(SourceSite)
class SourceSiteAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "code",
        "parser_key",
        "is_active",
        "use_browser_fallback",
        "manual_browser_assist",
    )
    list_filter = ("is_active", "use_browser_fallback", "manual_browser_assist")
    search_fields = ("name", "code", "parser_key", "base_url")
    inlines = [DoctorSourceInline]


@admin.register(Clinic)
class ClinicAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "address")
    search_fields = ("name", "city", "address", "normalized_key")


@admin.register(Doctor)
class DoctorAdmin(admin.ModelAdmin):
    list_display = ("last_name", "first_name", "middle_name", "full_name")
    search_fields = ("full_name", "first_name", "last_name", "middle_name", "normalized_name")
    inlines = [DoctorSourceInline]


@admin.register(DoctorSource)
class DoctorSourceAdmin(admin.ModelAdmin):
    list_display = (
        "doctor",
        "site",
        "clinic",
        "profile_url",
        "is_active",
        "last_status",
        "last_reviews_count",
        "last_checked_at",
        "consecutive_failures",
    )
    list_filter = ("site", "is_active", "last_status")
    search_fields = (
        "doctor__full_name",
        "site__name",
        "profile_url",
        "clinic__name",
        "source_file",
    )
    autocomplete_fields = ("doctor", "clinic", "site")
    readonly_fields = (
        "source_site",
        "last_checked_at",
        "last_success_at",
        "next_retry_at",
        "last_http_status",
        "consecutive_failures",
        "last_reviews_count",
    )


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("doctor_source", "published_at", "rating", "reviewer_name")
    list_filter = ("doctor_source__site", "rating")
    search_fields = (
        "doctor_source__doctor__full_name",
        "doctor_source__profile_url",
        "review_text",
        "reviewer_name",
        "external_id",
    )
    autocomplete_fields = ("doctor_source",)
    readonly_fields = ("content_hash",)


@admin.register(CrawlRun)
class CrawlRunAdmin(admin.ModelAdmin):
    list_display = ("source_site", "mode", "status", "started_at", "finished_at")
    list_filter = ("source_site", "mode", "status")
    search_fields = ("source_site",)


@admin.register(PipelineJob)
class PipelineJobAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "scope",
        "mode",
        "status",
        "skip_fetch",
        "browser_assisted",
        "processed_sources",
        "total_sources",
        "started_at",
        "finished_at",
    )
    list_filter = ("status", "scope", "mode", "skip_fetch", "browser_assisted")
    search_fields = ("config_path", "error_text")
    readonly_fields = (
        "summary",
        "recent_events",
        "error_text",
        "started_at",
        "finished_at",
        "current_stage",
        "current_doctor_name",
        "current_source_site",
        "current_profile_url",
    )
