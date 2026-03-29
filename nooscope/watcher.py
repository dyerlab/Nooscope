from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from nooscope.backends.base import EmbeddingBackend
from nooscope.db import delete_document_by_path, get_watcher_state
from nooscope.indexer import index_file

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
        log.info("Created: %s", self._rel(event.src_path))
        try:
            index_file(self.conn, self.vault_id, event.src_path, self.vault_root, self.backends, self.config)
        except Exception as exc:
            log.error("Error indexing %s: %s", event.src_path, exc)

    def on_modified(self, event) -> None:
        if event.is_directory or not event.src_path.endswith(".md"):
            return
        log.info("Modified: %s", self._rel(event.src_path))
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
