import threading

from django.db import close_old_connections
from django.utils import timezone

from apps.reviews.models import PipelineJob
from apps.reviews.services.orchestrator import PipelineOrchestrator


_job_lock = threading.Lock()
_active_job_ids: set[int] = set()


def start_pipeline_job(job: PipelineJob) -> bool:
    with _job_lock:
        if job.pk in _active_job_ids:
            return False
        _active_job_ids.add(job.pk)
    thread = threading.Thread(
        target=_run_pipeline_job,
        args=(job.pk,),
        name=f"pipeline-job-{job.pk}",
        daemon=True,
    )
    thread.start()
    return True


def _run_pipeline_job(job_id: int) -> None:
    close_old_connections()
    try:
        job = PipelineJob.objects.get(pk=job_id)
        job.status = "running"
        job.started_at = timezone.now()
        job.error_text = ""
        job.current_stage = "Инициализация"
        job.total_sources = 0
        job.processed_sources = 0
        job.success_sources = 0
        job.blocked_sources = 0
        job.failed_sources = 0
        job.skipped_sources = 0
        job.current_doctor_name = ""
        job.current_source_site = ""
        job.current_profile_url = ""
        job.recent_events = []
        job.summary = {}
        job.save(
            update_fields=[
                "status",
                "started_at",
                "error_text",
                "current_stage",
                "total_sources",
                "processed_sources",
                "success_sources",
                "blocked_sources",
                "failed_sources",
                "skipped_sources",
                "current_doctor_name",
                "current_source_site",
                "current_profile_url",
                "recent_events",
                "summary",
                "updated_at",
            ]
        )

        runtime_overrides = {}
        if job.browser_assisted:
            runtime_overrides["headless"] = False
            runtime_overrides["assisted_mode"] = True

        orchestrator = PipelineOrchestrator(
            job.config_path,
            runtime_overrides=runtime_overrides,
            progress_callback=lambda payload: _update_job_progress(job_id, payload),
            source_scope=job.scope,
        )
        summary = orchestrator.run(mode=job.mode, fetch_pages=not job.skip_fetch)

        job.summary = summary
        job.status = "completed"
        job.finished_at = timezone.now()
        job.current_stage = "Завершено"
        job.current_doctor_name = ""
        job.current_source_site = ""
        job.current_profile_url = ""
        job.save(
            update_fields=[
                "summary",
                "status",
                "finished_at",
                "current_stage",
                "current_doctor_name",
                "current_source_site",
                "current_profile_url",
                "updated_at",
            ]
        )
    except Exception as exc:
        PipelineJob.objects.filter(pk=job_id).update(
            status="failed",
            error_text=str(exc),
            finished_at=timezone.now(),
            current_stage="Ошибка выполнения",
            updated_at=timezone.now(),
        )
    finally:
        close_old_connections()
        with _job_lock:
            _active_job_ids.discard(job_id)


def _update_job_progress(job_id: int, payload: dict) -> None:
    job = PipelineJob.objects.get(pk=job_id)
    event = payload.get("event", "")
    source = payload.get("source")
    summary = payload.get("summary")
    total_sources = payload.get("total_sources")
    stage = payload.get("stage", "")
    error_text = payload.get("error", "")
    reviews_count = payload.get("reviews_count")

    if total_sources is not None:
        job.total_sources = int(total_sources)
    if stage:
        job.current_stage = stage
    if summary:
        job.summary = {**(job.summary or {}), **summary}

    if source is not None:
        job.current_doctor_name = source.doctor.full_name
        job.current_source_site = source.site.name if source.site_id else source.source_site
        job.current_profile_url = source.profile_url

    if event == "source_completed":
        job.processed_sources += 1
        job.success_sources += 1
    elif event == "source_blocked":
        job.processed_sources += 1
        job.blocked_sources += 1
    elif event == "source_failed":
        job.processed_sources += 1
        job.failed_sources += 1

    event_row = _format_event_row(payload, error_text=error_text, reviews_count=reviews_count)
    if event_row:
        recent_events = list(job.recent_events or [])
        recent_events.insert(0, event_row)
        job.recent_events = recent_events[:20]

    job.save(
        update_fields=[
            "total_sources",
            "processed_sources",
            "success_sources",
            "blocked_sources",
            "failed_sources",
            "current_stage",
            "current_doctor_name",
            "current_source_site",
            "current_profile_url",
            "summary",
            "recent_events",
            "updated_at",
        ]
    )


def _format_event_row(payload: dict, error_text: str = "", reviews_count=None) -> dict:
    event = payload.get("event", "")
    source = payload.get("source")
    if not event:
        return {}
    label = payload.get("stage", event)
    if source is not None:
        label = f"{label}: {source.doctor.full_name} / {source.parser_code}"
    if reviews_count is not None:
        label = f"{label} / отзывов: {reviews_count}"
    if error_text:
        label = f"{label} / {error_text}"
    return {
        "timestamp": timezone.localtime(timezone.now()).strftime("%d.%m.%Y %H:%M:%S"),
        "event": event,
        "label": label,
    }
