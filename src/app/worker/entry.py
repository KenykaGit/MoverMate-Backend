"""Entry points for the taskiq CLI (one image, two processes):

    taskiq worker app.worker.entry:broker --no-configure-logging --workers 1
    taskiq scheduler app.worker.entry:scheduler --no-configure-logging

Settings and logging are set up on import: the CLI imports this module in every
process it starts.
"""

from app.config import Settings
from app.infrastructure.logging import setup_logging
from app.worker.app import create_worker_app

settings = Settings()
setup_logging(settings.log_level, json_logs=settings.log_json)

_app = create_worker_app(settings)
broker = _app.broker
scheduler = _app.scheduler
