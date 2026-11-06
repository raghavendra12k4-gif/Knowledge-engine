import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Thread

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from app.core.config import settings
from app.core.db import connect
from app.services.documents import DuplicateDocumentError
from app.services.ingest import IngestError, ingest_file, move_to_rejected, remove_from_inbox

_DEBOUNCE_SECONDS = 1.0
_SCAN_INTERVAL = 0.2
logger = logging.getLogger(__name__)


@dataclass
class WatchdogHandle:
    observer: BaseObserver
    scanner: Thread
    stop_event: Event

    def stop(self) -> None:
        self.stop_event.set()
        self.observer.stop()
        self.observer.join()
        self.scanner.join()


class _InboxHandler(FileSystemEventHandler):
    """Watchdog handler that debounces file writes.

    A file is only ingested once its size has stopped changing for
    _DEBOUNCE_SECONDS, avoiding partial-write ingestion.
    """

    def __init__(self) -> None:
        self._pending: dict[str, tuple[float, int]] = {}  # path -> (last_seen, last_size)

    def _touch(self, path_str: str) -> None:
        path = Path(path_str)
        if path.name.startswith("."):
            return  # ignore temp/dotfiles
        try:
            size = path.stat().st_size if path.exists() and path.is_file() else -1
        except OSError:
            size = -1
        self._pending[path_str] = (time.time(), size)

    def on_created(self, event) -> None:  # noqa: ANN001
        if not event.is_directory:
            self._touch(os.fsdecode(event.src_path))

    def on_modified(self, event) -> None:  # noqa: ANN001
        if not event.is_directory:
            self._touch(os.fsdecode(event.src_path))

    def on_moved(self, event) -> None:  # noqa: ANN001
        if not event.is_directory:
            self._touch(os.fsdecode(event.dest_path))

    def scan_pending(self) -> None:
        """Process files whose size has stabilized for _DEBOUNCE_SECONDS."""
        now = time.time()
        ready: list[Path] = []
        for path_str, (t, size) in list(self._pending.items()):
            path = Path(path_str)
            try:
                current_size = path.stat().st_size if path.exists() and path.is_file() else -1
            except OSError:
                current_size = -1

            # Size changed (or file gone): reset the debounce window.
            if current_size != size:
                self._pending[path_str] = (now, current_size)
                continue
            if now - t < _DEBOUNCE_SECONDS:
                continue

            # Verify stability one more time before ingestion.
            try:
                verify_size = path.stat().st_size if path.exists() and path.is_file() else -1
            except OSError:
                verify_size = -1
            if verify_size != size:
                self._pending[path_str] = (now, verify_size)
                continue

            del self._pending[path_str]
            try:
                if path.exists() and path.is_file() and not path.name.startswith("."):
                    ready.append(path)
            except OSError:
                pass

        for path in ready:
            conn = connect()
            try:
                try:
                    ingest_file(path, conn)
                    # The job handler removes the file from inbox on success.
                except DuplicateDocumentError:
                    # Already known; remove the duplicate from inbox.
                    remove_from_inbox(path)
                except IngestError:
                    # Validation failed; move to _rejected for inspection.
                    move_to_rejected(path)
                except Exception:
                    # Any other error; move to _rejected and keep queue alive.
                    logger.exception("Inbox ingestion failed for %s", path)
                    move_to_rejected(path)
            finally:
                conn.close()


def start_watchdog() -> WatchdogHandle:
    """Start watching data/inbox/ and return a handle that stops both threads."""
    inbox = settings.inbox_dir
    inbox.mkdir(parents=True, exist_ok=True)

    handler = _InboxHandler()
    observer = Observer()
    observer.schedule(handler, str(inbox), recursive=False)
    observer.start()

    stop_event = Event()

    def _scan_loop() -> None:
        while not stop_event.wait(_SCAN_INTERVAL):
            try:
                handler.scan_pending()
            except Exception:
                logger.exception("Inbox scan failed")

    scan_thread = Thread(target=_scan_loop, name="inbox-scan", daemon=True)
    scan_thread.start()

    return WatchdogHandle(observer, scan_thread, stop_event)