import logging

from apscheduler.schedulers.background import BackgroundScheduler

from backend.app.core.config import Settings
from backend.app.domain.schemas import SynchronizationStatus
from backend.app.services.synchronization_service import SynchronizationService

logger = logging.getLogger(__name__)


class SynchronizationScheduler:
    def __init__(self, settings: Settings, service: SynchronizationService) -> None:
        self._settings = settings
        self._service = service
        self._scheduler = BackgroundScheduler(timezone="UTC")

    def start(self) -> None:
        if self._scheduler.running:
            return

        self._scheduler.add_job(
            self.run_once,
            trigger="interval",
            hours=self._settings.era5_sync_interval_hours,
            id="era5-near-real-time-sync",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self._scheduler.start()
        logger.info(
            "ERA5 near-real-time synchronization scheduler started with interval=%s hours",
            self._settings.era5_sync_interval_hours,
        )

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("ERA5 near-real-time synchronization scheduler stopped")

    def run_once(self) -> None:
        try:
            state = self._service.synchronize_configured(self._settings)
            if state.status == SynchronizationStatus.QUEUED:
                self._service.run_synchronization(state.sync_id)
        except Exception:
            logger.exception("ERA5 near-real-time synchronization failed")
