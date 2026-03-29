from __future__ import annotations

import argparse
import logging
import os
import sys


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(prog="nooscope")
    parser.add_argument("--config", default=None, help="Path to nooscope.yaml")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("rebuild", help="Full vault reindex")
    subparsers.add_parser("watch", help="Start incremental file watcher")
    subparsers.add_parser("serve", help="Start MCP server")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    from nooscope.config import load_config
    from nooscope.db import init_db, upsert_vault

    if args.config:
        os.environ["NOOSCOPE_CONFIG"] = args.config

    config = load_config()

    def build_backends():
        from nooscope.backends.ollama import OllamaBackend
        from nooscope.backends.openai import OpenAIBackend
        from nooscope.backends.mlx import MLXBackend
        from nooscope.backends.apple_nl import AppleNLBackend
        from nooscope.backends.fdl import FDLBackend

        _map = {
            "ollama": OllamaBackend,
            "openai": OpenAIBackend,
            "mlx": MLXBackend,
            "apple_nl": AppleNLBackend,
            "fdl": FDLBackend,
        }
        backends = {}
        for etype, ecfg in config.embedding_types.items():
            cls = _map.get(ecfg.backend)
            if cls:
                backends[etype] = cls(model=ecfg.model, dimensions=ecfg.dimensions)
        return backends

    if args.command == "rebuild":
        from nooscope.indexer import rebuild_vault
        backends = build_backends()
        for vault_cfg in config.vaults:
            conn = init_db(vault_cfg.db_path)
            vault_id = upsert_vault(conn, vault_cfg.name, vault_cfg.path)
            logging.info("Rebuilding vault '%s' at %s", vault_cfg.name, vault_cfg.path)
            results = rebuild_vault(conn, vault_id, vault_cfg.path, backends, config)
            logging.info(
                "Done: %d reindexed, %d skipped, %d errors",
                results["reindexed"],
                results["skipped"],
                len(results["errors"]),
            )
            for err in results["errors"]:
                logging.error("  %s: %s", err["file"], err["error"])
            conn.close()

    elif args.command == "watch":
        from nooscope.watcher import watch_vault
        backends = build_backends()
        if not config.vaults:
            logging.error("No vaults configured")
            sys.exit(1)
        vault_cfg = config.vaults[0]
        conn = init_db(vault_cfg.db_path)
        vault_id = upsert_vault(conn, vault_cfg.name, vault_cfg.path)
        watch_vault(conn, vault_id, vault_cfg.path, backends, config)

    elif args.command == "serve":
        from nooscope.mcp_server import main as mcp_main
        mcp_main()


if __name__ == "__main__":
    main()
