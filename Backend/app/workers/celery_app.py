from celery import Celery
from kombu import Queue

from app.core.config import settings

celery_app = Celery(
    "payflow",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Reliability: ack only after the task finished; requeue if a worker dies mid-task.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    result_expires=3600,
    broker_connection_retry_on_startup=True,
    broker_connection_max_retries=3,
    task_default_queue="payflow",
    # A worker started without -Q consumes every queue listed here.
    task_queues=(
        Queue("payflow"),
        Queue("payments"),
        Queue("investigations"),
        Queue("actions"),
        Queue("notifications"),
    ),
    task_routes={
        "payflow.process_payment_event": {"queue": "payments"},
        "payflow.run_reconciliation": {"queue": "payments"},
        "payflow.run_job": {"queue": "payments"},
        "payflow.investigate_incident": {"queue": "investigations"},
        "payflow.execute_action": {"queue": "actions"},
        "payflow.send_notification": {"queue": "notifications"},
    },
)
