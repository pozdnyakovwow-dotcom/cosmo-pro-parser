from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponseBadRequest
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import Doctor, DoctorSource, PipelineJob, Review, SourceSite
from .services.pipeline_jobs import start_pipeline_job


def dashboard(request):
    return render(
        request,
        "reviews/dashboard.html",
        {
            "active_job": _get_active_job(),
            "export_files": _get_export_files(),
        },
    )


@require_POST
def run_pipeline_view(request):
    active_job = _get_active_job()
    message = ""
    if active_job:
        message = f"Уже выполняется запуск #{active_job.pk}. Дождитесь завершения текущей задачи."
    else:
        mode = request.POST.get("mode", "sync")
        if mode not in {"sync", "async"}:
            return HttpResponseBadRequest("Недопустимый режим запуска.")
        scope = request.POST.get("scope", "all")
        if scope not in {"all", "manual_blocked"}:
            return HttpResponseBadRequest("Недопустимая область запуска.")
        browser_assisted = request.POST.get("browser_assisted") == "on"
        skip_fetch = request.POST.get("skip_fetch") == "on"
        if scope == "manual_blocked":
            mode = "sync"
            browser_assisted = True
            skip_fetch = False
        job = PipelineJob.objects.create(
            mode=mode,
            scope=scope,
            skip_fetch=skip_fetch,
            browser_assisted=browser_assisted,
            config_path="config/pipeline.yaml",
        )
        if start_pipeline_job(job):
            active_job = job
            message = f"Запуск #{job.pk} поставлен в очередь."
        else:
            job.status = "failed"
            job.error_text = "Не удалось запустить фоновую задачу."
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "error_text", "finished_at", "updated_at"])
            active_job = job
            message = f"Запуск #{job.pk} не был запущен."

    response = render(
        request,
        "reviews/partials/active_job.html",
        {
            "job": active_job,
            "message": message,
        },
    )
    response["HX-Trigger"] = "refresh-dashboard"
    return response


@require_GET
def stats_partial(request):
    return render(
        request,
        "reviews/partials/stats.html",
        _build_stats_context(),
    )


@require_GET
def active_job_partial(request):
    job = _get_active_job() or PipelineJob.objects.order_by("-created_at").first()
    return render(
        request,
        "reviews/partials/active_job.html",
        {
            "job": job,
        },
    )


@require_GET
def recent_jobs_partial(request):
    jobs = PipelineJob.objects.order_by("-created_at")[:8]
    return render(
        request,
        "reviews/partials/recent_jobs.html",
        {
            "jobs": jobs,
        },
    )


@require_GET
def source_health_partial(request):
    sources = (
        DoctorSource.objects.select_related("doctor", "site", "clinic")
        .order_by("-updated_at")[:25]
    )
    return render(
        request,
        "reviews/partials/source_health.html",
        {
            "sources": sources,
        },
    )


@require_GET
def blocked_queue_partial(request):
    blocked_sources = (
        DoctorSource.objects.select_related("doctor", "site", "clinic")
        .filter(last_status="blocked", site__manual_browser_assist=True, is_active=True)
        .order_by("-updated_at")[:25]
    )
    return render(
        request,
        "reviews/partials/blocked_queue.html",
        {
            "blocked_sources": blocked_sources,
            "active_job": _get_active_job(),
        },
    )


@require_GET
def download_export(request, fmt: str):
    export_files = _get_export_files()
    export_file = export_files.get(fmt)
    if not export_file or not export_file["exists"]:
        raise Http404("Экспортный файл не найден.")
    path = Path(export_file["path"])
    return FileResponse(path.open("rb"), as_attachment=True, filename=path.name)


def _build_stats_context() -> dict:
    active_job = _get_active_job()
    last_job = PipelineJob.objects.order_by("-created_at").first()
    export_files = _get_export_files()
    return {
        "doctors_count": Doctor.objects.count(),
        "sites_count": SourceSite.objects.count(),
        "sources_count": DoctorSource.objects.count(),
        "reviews_count": Review.objects.count(),
        "ok_sources_count": DoctorSource.objects.filter(last_status="ok").count(),
        "blocked_sources_count": DoctorSource.objects.filter(last_status="blocked").count(),
        "failed_sources_count": DoctorSource.objects.filter(last_status="failed").count(),
        "manual_blocked_sources_count": DoctorSource.objects.filter(
            last_status="blocked",
            site__manual_browser_assist=True,
            is_active=True,
        ).count(),
        "active_job": active_job,
        "last_job": last_job,
        "export_files": export_files,
        "generated_at": timezone.localtime(timezone.now()),
    }


def _get_active_job():
    return PipelineJob.objects.filter(status__in=["pending", "running"]).order_by("-created_at").first()


def _get_export_files() -> dict[str, dict]:
    export_map = {
        "csv": settings.EXPORT_DIR / "doctor_reviews_export.csv",
        "json": settings.EXPORT_DIR / "doctor_reviews_export.json",
    }
    result = {}
    for fmt, path in export_map.items():
        exists = path.exists()
        stat = path.stat() if exists else None
        result[fmt] = {
            "path": str(path),
            "exists": exists,
            "size": stat.st_size if stat else 0,
            "modified_at": timezone.localtime(datetime.fromtimestamp(stat.st_mtime, tz=timezone.get_current_timezone()))
            if stat
            else None,
        }
    return result
