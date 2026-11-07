import logging
import os
import threading
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


def _file_size(path: Path) -> int:
    """Size of a regular file, or -1 if it is missing / not a file / unreadable."""
    try:
        return path.stat().st_size if path.is_file() else -1
    except OSError:
        return -1


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
        # path -> (last_seen, last_size). Touched by the observer thread and the
        # scanner thread, so every access goes through _lock.
        self._pending: dict[str, tuple[float, int]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _is_direct_inbox_file(path: Path) -> bool:
        """True only for files sitting directly in data/inbox/ (not _rejected/ etc.)."""
        try:
            return path.resolve().parent == settings.inbox_dir.resolve()
        except OSError:
            return False

    def _touch(self, path_str: str) -> None:
        path = Path(path_str)
        if path.name.startswith("."):
            return  # ignore temp/dotfiles (uploads use .upload_*)
        if not self._is_direct_inbox_file(path):
            return  # ignore _rejected/ and anything outside the inbox
        with self._lock:
            self._pending[path_str] = (time.time(), _file_size(path))

    def scan_existing(self) -> None:
        """Queue every file already in the inbox (dropped while the app was off)."""
        try:
            entries = list(settings.inbox_dir.iterdir())
        except OSError:
            logger.exception("Could not list inbox for startup scan")
            return
        for entry in entries:
            if entry.is_file() and not entry.name.startswith("."):
                self._touch(str(entry))

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
        with self._lock:
            snapshot = list(self._pending.items())

        ready: list[Path] = []
        for path_str, (seen_at, size) in snapshot:
            path = Path(path_str)
            current = _file_size(path)

            # Size changed (or file appeared/vanished): restart the debounce window.
            if current != size:
                with self._lock:
                    if path_str in self._pending:
                        self._pending[path_str] = (now, current)
                continue
            if now - seen_at < _DEBOUNCE_SECONDS:
                continue

            with self._lock:
                self._pending.pop(path_str, None)
            if current >= 0 and not path.name.startswith("."):
                ready.append(path)

        for path in ready:
            self._ingest(path)

    def _ingest(self, path: Path) -> None:
        conn = connect()
        try:
            # inbox_path lets the job handler delete the file on success and move
            # it to _rejected/ on failure.
            ingest_file(path, conn, inbox_path=path)
        except DuplicateDocumentError as exc:
            # If the existing document is still queued/processing, its job will
            # clean the inbox file up itself. Otherwise this is a plain duplicate.
            if exc.existing.get("status") not in ("queued", "processing"):
                remove_from_inbox(path)
        except IngestError:
            move_to_rejected(path)
        except Exception:
            logger.exception("Inbox ingestion failed for %s", path)
            move_to_rejected(path)
        finally:
            conn.close()


def start_watchdog() -> WatchdogHandle:
    """Start watching data/inbox/ and return a handle that stops both threads."""
    inbox = settings.inbox_dir
    inbox.mkdir(parents=True, exist_ok=True)

    handler = _InboxHandler()
    handler.scan_existing()  # pick up files dropped while the app was off

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