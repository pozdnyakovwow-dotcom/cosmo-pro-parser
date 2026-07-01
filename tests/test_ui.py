import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "parser_project.settings")
django.setup()

from django.test import Client, TestCase  # noqa: E402

class DashboardViewTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_dashboard_page_renders(self):
        response = self.client.get("/")
        assert response.status_code == 200
        assert "Панель парсера" in response.content.decode("utf-8")

    def test_partials_render(self):
        assert self.client.get("/partials/stats/").status_code == 200
        assert self.client.get("/partials/active-job/").status_code == 200
        assert self.client.get("/partials/recent-jobs/").status_code == 200
        assert self.client.get("/partials/blocked-queue/").status_code == 200
        assert self.client.get("/partials/source-health/").status_code == 200
