"""Composition root of the HTTP app.

Run with `uvicorn app.main:create_app --factory`.
"""

from dishka.integrations.fastapi import FastapiProvider
from fastapi import FastAPI

from app.config import Settings
from app.infrastructure.logging import setup_logging
from app.ioc.config_provider import http_settings_from
from app.ioc.container import create_container
from app.presentation.http.app import create_app as create_http_app


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    setup_logging(settings.log_level, json_logs=settings.log_json)
    container = create_container(settings, FastapiProvider())
    return create_http_app(container, http_settings_from(settings))
