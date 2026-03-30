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

    cap_parser = subparsers.add_parser("capture", help="Queue a new capture for later flush")
    cap_parser.add_argument("content", help="Note content")
    cap_parser.add_argument("--title", default=None)
    cap_parser.add_argument("--tags", default=None, help="Comma-separated tags")
    cap_parser.add_argument("--source", default="cli")

    subparsers.add_parser("queue", help="List pending captures")

    flush_parser = subparsers.add_parser("flush", help="Flush pending captures to Obsidian")
    flush_parser.add_argument("--dry-run", action="store_true", help="Preview without writing")

    log_parser = subparsers.add_parser("log", help="Queue a logger:: entry for today's daily note")
    log_parser.add_argument("text", help="Log entry text")
    log_parser.add_argument("--refs", default=None, help="Comma-separated wikilink targets e.g. 'Nooscope,Alice'")
    log_parser.add_argument("--date", default=None, help="Target date YYYY-MM-DD (default: today)")

    subparsers.add_parser("flush-logs", help="Retry pending log entries against their target daily notes")

    agenda_parser = subparsers.add_parser("inject-agenda", help="Inject today's calendar events into a daily note")
    agenda_parser.add_argument("--date", default=None, help="Target date YYYY-MM-DD (default: today)")
    agenda_parser.add_argument("--dry-run", action="store_true", help="Print agenda lines without writing")

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

    elif args.command == "capture":
        from nooscope.capture import queue_capture
        if not config.vaults:
            logging.error("No vaults configured")
            sys.exit(1)
        vault_cfg = config.vaults[0]
        conn = init_db(vault_cfg.db_path)
        upsert_vault(conn, vault_cfg.name, vault_cfg.path)
        tags = [t.strip() for t in args.tags.split(",")] if args.tags else []
        capture_id = queue_capture(conn, args.content, title=args.title, tags=tags, source=args.source)
        logging.info("Queued capture #%d", capture_id)
        conn.close()

    elif args.command == "queue":
        from nooscope.db import list_pending_captures
        if not config.vaults:
            logging.error("No vaults configured")
            sys.exit(1)
        vault_cfg = config.vaults[0]
        conn = init_db(vault_cfg.db_path)
        upsert_vault(conn, vault_cfg.name, vault_cfg.path)
        pending = list_pending_captures(conn)
        if not pending:
            print("No pending captures.")
        else:
            for c in pending:
                tags_str = ", ".join(c["tags"]) if c["tags"] else ""
                title_str = f" [{c['title']}]" if c["title"] else ""
                print(f"#{c['id']}{title_str} ({c['source']}) {tags_str}")
                print(f"  {c['content'][:80]}{'…' if len(c['content']) > 80 else ''}")
        conn.close()

    elif args.command == "log":
        from nooscope.capture import log_entry
        if not config.vaults:
            logging.error("No vaults configured")
            sys.exit(1)
        vault_cfg = config.vaults[0]
        conn = init_db(vault_cfg.db_path)
        upsert_vault(conn, vault_cfg.name, vault_cfg.path)
        refs = [r.strip() for r in args.refs.split(",")] if args.refs else []
        from datetime import date as _date
        target_date = _date.fromisoformat(args.date) if args.date else None
        result = log_entry(conn, vault_cfg.path, args.text, refs, config, today=target_date)
        if result["status"] == "written":
            logging.info("Written to %s", result["file"])
            logging.info("  %s", result["entry"])
        else:
            logging.info("Queued as #%d — daily note not yet available, will retry", result["id"])
        conn.close()

    elif args.command == "flush-logs":
        from nooscope.capture import flush_log_entries
        if not config.vaults:
            logging.error("No vaults configured")
            sys.exit(1)
        vault_cfg = config.vaults[0]
        conn = init_db(vault_cfg.db_path)
        upsert_vault(conn, vault_cfg.name, vault_cfg.path)
        results = flush_log_entries(conn, vault_cfg.path, config, poll=True)
        logging.info("Written: %d, still pending: %d", results["written"], results["still_pending"])
        for err in results["errors"]:
            logging.error("  #%s: %s", err["id"], err["error"])
        conn.close()

    elif args.command == "inject-agenda":
        from nooscope.calendar_reader import get_events_for_date
        from nooscope.agenda_injector import inject_agenda
        from datetime import date as _date
        from pathlib import Path

        if not config.vaults:
            logging.error("No vaults configured")
            sys.exit(1)
        vault_cfg = config.vaults[0]
        target_date = _date.fromisoformat(args.date) if args.date else _date.today()

        cap_cfg = config.capture
        filename = target_date.strftime(cap_cfg.daily_notes_format) + ".md"
        daily_path = Path(vault_cfg.path) / cap_cfg.daily_notes_folder / filename

        if args.dry_run:
            from nooscope.agenda_injector import _build_agenda_lines, _recent_notes, _generate_refresher
            events = get_events_for_date(target_date, calendars=config.calendar.calendars or None)
            print(f"## Agenda ({target_date})")
            if not events:
                recent = _recent_notes(vault_cfg.path, config)
                if recent:
                    refresher = _generate_refresher(recent, config.calendar.anthropic_api_key)
                    print(f"- No scheduled events today. {refresher}")
                else:
                    print("- No scheduled events today.")
            else:
                agenda_lines = _build_agenda_lines(events, target_date, vault_cfg.path, config, dry_run=True)
                for line in agenda_lines:
                    print(line)
        elif not daily_path.exists():
            logging.error("Daily note not found: %s", daily_path)
            sys.exit(1)
        else:
            lines = daily_path.read_text(encoding="utf-8").splitlines(keepends=True)
            new_lines = inject_agenda(lines, target_date, vault_cfg.path, config)
            if new_lines != lines:
                daily_path.write_text("".join(new_lines), encoding="utf-8")
                logging.info("Agenda injected into %s", daily_path.name)
            else:
                logging.warning("Nothing injected — check that calendar.enabled is true in nooscope.yaml")

    elif args.command == "flush":
        from nooscope.capture import flush_captures
        if not config.vaults:
            logging.error("No vaults configured")
            sys.exit(1)
        vault_cfg = config.vaults[0]
        conn = init_db(vault_cfg.db_path)
        upsert_vault(conn, vault_cfg.name, vault_cfg.path)
        if args.dry_run:
            from nooscope.db import list_pending_captures
            from nooscope.capture import _note_filename
            pending = list_pending_captures(conn)
            if not pending:
                print("No pending captures.")
            else:
                print(f"{len(pending)} pending capture(s) would be flushed via '{config.capture.flush_method}':")
                for c in pending:
                    print(f"  #{c['id']} → {config.capture.inbox_folder}/{_note_filename(c)}")
        else:
            results = flush_captures(conn, config)
            logging.info(
                "Flushed %d, failed %d",
                results["flushed"],
                results["failed"],
            )
            for err in results["errors"]:
                logging.error("  #%s: %s", err["id"], err["error"])
        conn.close()


if __name__ == "__main__":
    main()
