"""Composition root of the notification worker (taskiq): see app.worker.app."""

from app.worker.app import DELIVER_TASK_NAME, WorkerApp, create_worker_app, drain

__all__ = ["DELIVER_TASK_NAME", "WorkerApp", "create_worker_app", "drain"]
