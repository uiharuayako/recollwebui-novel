#!/usr/bin/env python3
"""Explicit local setup and maintenance for Recoll WebUI accounts."""
import argparse
import getpass
import json
import sqlite3
import sys

from accounts import (AuthenticationError, ConflictError, NotFound, SetupRequired,
                      Store, ValidationError)


def read_password(arguments):
    if arguments.password_stdin:
        # One password line, without echoing secrets into shell history/process args.
        line = sys.stdin.readline(1024)
        return line.rstrip("\r\n")
    if not sys.stdin.isatty():
        raise ValidationError("Use --password-stdin when standard input is not a terminal")
    password = getpass.getpass("Password (15–128 characters): ")
    if getpass.getpass("Repeat password: ") != password:
        raise ValidationError("Passwords do not match")
    return password


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--db", help="Absolute database path (default: RECOLL_WEBUI_DB_PATH or ~/.local/share/recoll-webui/app.sqlite3)")
    commands = root.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Initialize schema, signing key, and the first administrator")
    init.add_argument("--username", default="admin")
    init.add_argument("--display-name", default="Administrator")
    init.add_argument("--password-stdin", action="store_true")
    create = commands.add_parser("create-user", help="Create a local account; its initial password must be changed after login")
    create.add_argument("username")
    create.add_argument("--display-name", default="")
    create.add_argument("--role", choices=("admin", "member"), default="member")
    create.add_argument("--password-stdin", action="store_true")
    reset = commands.add_parser("reset-password", help="Reset a password and revoke all account sessions")
    reset.add_argument("username")
    reset.add_argument("--enable", action="store_true", help="Also enable the account")
    reset.add_argument("--admin", action="store_true", help="Also grant administrator role for local recovery")
    reset.add_argument("--password-stdin", action="store_true")
    commands.add_parser("cleanup", help="Remove expired sessions and unreferenced historical data in bounded batches")
    backup = commands.add_parser("backup", help="Create an online consistent SQLite backup (signing key is backed up separately)")
    backup.add_argument("destination")
    restore = commands.add_parser("restore", help="Restore a database after stopping every application worker")
    restore.add_argument("source")
    restore.add_argument("--workers-stopped", action="store_true", required=True, help="Confirm all standalone and WSGI workers are stopped")
    return root


def main(argv=None):
    arguments = parser().parse_args(argv)
    store = Store(arguments.db)
    try:
        if arguments.command == "init":
            result = store.initialize()
            administrators = [user for user in store.list_users() if user["role"] == "admin" and user["status"] == "active"]
            if not administrators:
                result["administrator"] = store.create_user(arguments.username, read_password(arguments), arguments.display_name, "admin")["username"]
            result["database"] = store.path
            result["signing_key"] = store.secret_path
        elif arguments.command == "create-user":
            user = store.create_user(arguments.username, read_password(arguments), arguments.display_name, arguments.role)
            result = {"username": user["username"], "role": user["role"], "must_change_password": True}
        elif arguments.command == "reset-password":
            users = [user for user in store.list_users() if user["username"] == arguments.username.lower()]
            if not users:
                raise NotFound("Account not found")
            identifier = users[0]["id"]
            store.update_user(None, identifier, "reset-password", password=read_password(arguments))
            if arguments.enable:
                store.update_user(None, identifier, "enable")
            if arguments.admin:
                store.update_user(None, identifier, "role", role="admin")
            result = {"username": users[0]["username"], "sessions_revoked": True, "must_change_password": True}
        elif arguments.command == "cleanup":
            result = store.cleanup()
        elif arguments.command == "backup":
            result = {"backup": store.backup(arguments.destination), "signing_key": store.secret_path}
        elif arguments.command == "restore":
            result = store.restore(arguments.source)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValidationError, ConflictError, NotFound, SetupRequired, AuthenticationError, sqlite3.DatabaseError, OSError) as exc:
        print("Management operation failed: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
