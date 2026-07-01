from django.urls import path

from . import views


app_name = "reviews"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("run/", views.run_pipeline_view, name="run"),
    path("partials/stats/", views.stats_partial, name="stats_partial"),
    path("partials/active-job/", views.active_job_partial, name="active_job_partial"),
    path("partials/recent-jobs/", views.recent_jobs_partial, name="recent_jobs_partial"),
    path("partials/blocked-queue/", views.blocked_queue_partial, name="blocked_queue_partial"),
    path("partials/source-health/", views.source_health_partial, name="source_health_partial"),
    path("exports/<str:fmt>/", views.download_export, name="download_export"),
]
