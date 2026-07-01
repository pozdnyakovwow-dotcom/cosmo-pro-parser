from django.core.management.base import BaseCommand

from apps.reviews.services.orchestrator import PipelineOrchestrator


class Command(BaseCommand):
    help = "Run doctor review parsing pipeline"

    def add_arguments(self, parser):
        parser.add_argument(
            "--config",
            default="config/pipeline.yaml",
            help="Path to YAML config file",
        )
        parser.add_argument(
            "--mode",
            choices=["sync", "async"],
            default="sync",
            help="Network execution mode",
        )
        parser.add_argument(
            "--skip-fetch",
            action="store_true",
            help="Load input files and export current DB state without fetching pages",
        )
        parser.add_argument(
            "--browser-assisted",
            action="store_true",
            help="Run blocked sources in visible Chrome and wait for local manual page confirmation",
        )

    def handle(self, *args, **options):
        runtime_overrides = {}
        if options["browser_assisted"]:
            runtime_overrides["headless"] = False
            runtime_overrides["assisted_mode"] = True
        orchestrator = PipelineOrchestrator(
            options["config"],
            runtime_overrides=runtime_overrides,
        )
        summary = orchestrator.run(
            mode=options["mode"],
            fetch_pages=not options["skip_fetch"],
        )
        self.stdout.write(self.style.SUCCESS(str(summary)))
