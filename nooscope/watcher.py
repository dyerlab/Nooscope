from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from nooscope.backends.base import EmbeddingBackend
from nooscope.db import delete_document_by_path, get_watcher_state
from nooscope.indexer import index_file, is_ignored

log = logging.getLogger(__name__)


class VaultEventHandler(FileSystemEventHandler):
    def __init__(self, conn, vault_id: int, vault_root: str, backends: dict[str, EmbeddingBackend], config):
        super().__init__()
        self.conn = conn
        self.vault_id = vault_id
        self.vault_root = vault_root
        self.backends = backends
        self.config = config

    def _rel(self, path: str) -> str:
        return os.path.relpath(path, self.vault_root)

    def on_created(self, event) -> None:
        if event.is_directory or not event.src_path.endswith(".md"):
            return
        rel = self._rel(event.src_path)
        ignore_patterns = getattr(
            next((v for v in self.config.vaults if v.path == self.vault_root), None),
            "ignore", []
        )
        if is_ignored(rel, ignore_patterns):
            return
        log.info("Created: %s", rel)
        try:
            index_file(self.conn, self.vault_id, event.src_path, self.vault_root, self.backends, self.config)
        except Exception as exc:
            log.error("Error indexing %s: %s", event.src_path, exc)

        # If a daily note just appeared, flush any pending log entries that were
        # waiting for it — Obsidian just created the note from its template.
        daily_folder = Path(self.vault_root) / self.config.capture.daily_notes_folder
        if Path(event.src_path).parent == daily_folder:
            try:
                from nooscope.capture import flush_log_entries
                results = flush_log_entries(self.conn, self.vault_root, self.config, poll=False)
                if results["written"]:
                    log.info("Flushed %d pending log entry(ies) to %s", results["written"],
                             Path(event.src_path).name)
            except Exception as exc:
                log.error("Error flushing log entries: %s", exc)

    def on_modified(self, event) -> None:
        if event.is_directory or not event.src_path.endswith(".md"):
            return
        rel = self._rel(event.src_path)
        ignore_patterns = getattr(
            next((v for v in self.config.vaults if v.path == self.vault_root), None),
            "ignore", []
        )
        if is_ignored(rel, ignore_patterns):
            return
        log.info("Modified: %s", rel)
        try:
            index_file(self.conn, self.vault_id, event.src_path, self.vault_root, self.backends, self.config)
        except Exception as exc:
            log.error("Error indexing %s: %s", event.src_path, exc)

    def on_deleted(self, event) -> None:
        if event.is_directory or not event.src_path.endswith(".md"):
            return
        rel = self._rel(event.src_path)
        log.info("Deleted: %s", rel)
        delete_document_by_path(self.conn, self.vault_id, rel)


def watch_vault(
    conn,
    vault_id: int,
    vault_root: str,
    backends: dict[str, EmbeddingBackend],
    config,
) -> None:
    handler = VaultEventHandler(conn, vault_id, vault_root, backends, config)
    observer = Observer()
    observer.schedule(handler, vault_root, recursive=True)
    observer.start()
    log.info("Watching vault: %s", vault_root)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
