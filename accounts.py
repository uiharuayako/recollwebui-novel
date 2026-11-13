"""Local multi-account storage. Does not import Bottle or Recoll.

Initialize explicitly with ``python manage.py init`` before starting workers.
Each method opens its own short-lived connection; hashes run outside transactions.
"""
import base64
import contextlib
import fcntl
import hashlib
import json
import math
import os
import re
import secrets
import sqlite3
import time
import uuid
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

SCHEMA_VERSION = 1
DAY = 86400
PASSWORDS = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
USERNAME = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
FLAGS = ("search_history_enabled", "reading_history_enabled", "progress_sync_enabled")
EPOCHS = ("search_epoch", "reading_epoch", "progress_epoch")
FORMATS = {"txt", "epub", "chm", "pdf", "html", "htm", "xhtml", "xml", "md", "mhtml", "mobi", "azw", "azw3", "fb2", "docx", "cbz", "cbr", "cbt", "cb7"}
INTEGER_PREFERENCES = {"context": (0, 500), "stem": (0, 1), "dirdepth": (0, 5), "maxchars": (0, 20000), "maxresults": (0, 100000), "perpage": (0, 1000), "collapsedups": (0, 1), "shortenpaths": (0, 1), "font_size": (16, 32),
                       "reader_line_height": (140, 220), "reader_paragraph_spacing": (0, 30),
                       "reader_content_width": (480, 1040), "reader_indent": (0, 1), "reader_preserve_publisher": (0, 1)}
TEXT_PREFERENCES = {"title_link": {"download", "preview", "open", "reader"}, "defaultsort": {"relevancyrating", "mtime", "url", "filename", "fbytes", "author"},
                    "reader_theme": {"light", "sepia", "dark", "auto"}, "reader_font_family": {"serif", "sans", "system"},
                    "reader_text_align": {"left", "justify"}}


class SetupRequired(RuntimeError):
    pass


class ValidationError(ValueError):
    pass


class ConflictError(RuntimeError):
    def __init__(self, message, current=None):
        super().__init__(message)
        self.current = current


class NotFound(LookupError):
    pass


class AuthenticationError(RuntimeError):
    pass


def _id():
    return uuid.uuid4().hex


def _json(value, maximum=16384):
    try:
        result = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValidationError("Invalid JSON data") from exc
    if len(result.encode("utf-8")) > maximum:
        raise ValidationError("Data exceeds size limit")
    return result


def _password(value):
    if not isinstance(value, str) or not 15 <= len(value) <= 128:
        raise ValidationError("Password must contain 15–128 characters")
    return value


def _username(value):
    if not isinstance(value, str) or not USERNAME.fullmatch(value):
        raise ValidationError("Username must contain 3–32 ASCII letters, digits, dots, underscores or hyphens")
    return value.lower()


def _integer(value, minimum=0, maximum=2**53):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValidationError("Invalid integer")
    return value


def _safe_wal_version():
    # Upstream WAL-reset fixes. Unknown vendor backports conservatively use DELETE.
    version = sqlite3.sqlite_version_info
    return version >= (3, 51, 3) or ((3, 50, 7) <= version < (3, 51, 0)) or ((3, 44, 6) <= version < (3, 45, 0))


SCHEMA = """
CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at REAL NOT NULL);
CREATE TABLE users(id TEXT PRIMARY KEY, username_normalized TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','member')), status TEXT NOT NULL CHECK(status IN ('active','disabled')), must_change_password INTEGER NOT NULL, auth_version INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE sessions(id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, auth_version INTEGER NOT NULL, created_at REAL NOT NULL, last_seen_at REAL NOT NULL, idle_expires_at REAL NOT NULL, absolute_expires_at REAL NOT NULL, revoked_at REAL, device_label TEXT NOT NULL);
CREATE INDEX sessions_user ON sessions(user_id,absolute_expires_at);
CREATE TABLE user_settings(user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, preferences_json TEXT NOT NULL DEFAULT '{}', schema_version INTEGER NOT NULL DEFAULT 1, search_history_enabled INTEGER NOT NULL DEFAULT 1, reading_history_enabled INTEGER NOT NULL DEFAULT 1, progress_sync_enabled INTEGER NOT NULL DEFAULT 1, search_epoch INTEGER NOT NULL DEFAULT 1, reading_epoch INTEGER NOT NULL DEFAULT 1, progress_epoch INTEGER NOT NULL DEFAULT 1, updated_at REAL NOT NULL);
CREATE TABLE document_refs(id TEXT PRIMARY KEY, library_key TEXT NOT NULL, locator_key TEXT NOT NULL, source_url TEXT NOT NULL, source_path TEXT NOT NULL, ipath TEXT NOT NULL, rcludi TEXT NOT NULL, title TEXT NOT NULL, format TEXT NOT NULL, content_version TEXT NOT NULL, last_verified_at REAL NOT NULL, UNIQUE(library_key,locator_key));
CREATE TABLE search_history(id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, query_json TEXT NOT NULL, query_fingerprint TEXT NOT NULL, settings_snapshot TEXT NOT NULL, scope_version TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','success','failed')), history_epoch INTEGER NOT NULL, created_at REAL NOT NULL, completed_at REAL, result_count INTEGER);
CREATE INDEX search_user ON search_history(user_id,status,completed_at DESC,id DESC);
CREATE TABLE reading_history(id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, document_id TEXT NOT NULL REFERENCES document_refs(id), title_snapshot TEXT NOT NULL, first_opened_at REAL NOT NULL, last_opened_at REAL NOT NULL, UNIQUE(user_id,document_id));
CREATE INDEX reading_user ON reading_history(user_id,last_opened_at DESC,id DESC);
CREATE TABLE reading_progress(user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, document_id TEXT NOT NULL REFERENCES document_refs(id), position_json TEXT NOT NULL, position_version INTEGER NOT NULL DEFAULT 1, content_version TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0, writer_id TEXT NOT NULL DEFAULT '', writer_seq INTEGER NOT NULL DEFAULT 0, progress REAL, origin TEXT NOT NULL DEFAULT 'reading', imported_at REAL, updated_at REAL NOT NULL, PRIMARY KEY(user_id,document_id));
CREATE TABLE folder_resume(user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, folder_key TEXT NOT NULL, last_document_id TEXT NOT NULL REFERENCES document_refs(id), revision INTEGER NOT NULL DEFAULT 1, updated_at REAL NOT NULL, PRIMARY KEY(user_id,folder_key));
CREATE TABLE bookshelf(user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, document_id TEXT NOT NULL REFERENCES document_refs(id), added_at REAL NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY(user_id,document_id));
CREATE TABLE login_throttle(bucket_key TEXT PRIMARY KEY, window_start REAL NOT NULL, failure_count INTEGER NOT NULL DEFAULT 0, blocked_until REAL NOT NULL DEFAULT 0, expires_at REAL NOT NULL);
"""


class Store:
    def __init__(self, path=None):
        raw = path or os.environ.get("RECOLL_WEBUI_DB_PATH") or "~/.local/share/recoll-webui/app.sqlite3"
        self.path = os.path.abspath(os.path.expanduser(os.fspath(raw)))
        self.secret_path = self.path + ".secret"

    @contextlib.contextmanager
    def _connection(self, write=False):
        if not os.path.isfile(self.path):
            raise SetupRequired("Initialize the application database before starting workers")
        connection = sqlite3.connect(Path(self.path).as_uri() + "?mode=rw", uri=True, timeout=3, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=3000")
            try:
                row = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
            except sqlite3.DatabaseError as exc:
                raise SetupRequired("Database schema is not initialized") from exc
            if not row or row[0] != SCHEMA_VERSION:
                raise SetupRequired("Database schema does not match this application version")
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write:
                connection.commit()
        except BaseException:
            if write:
                connection.rollback()
            raise
        finally:
            connection.close()

    @contextlib.contextmanager
    def _maintenance_lock(self):
        parent = os.path.dirname(self.path)
        os.makedirs(parent, mode=0o700, exist_ok=True)
        descriptor = os.open(self.path + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

    def initialized(self):
        try:
            with self._connection():
                return True
        except (SetupRequired, sqlite3.DatabaseError):
            return False

    def initialize(self):
        with self._maintenance_lock():
            if os.path.exists(self.path) and os.path.getsize(self.path):
                if not self.initialized():
                    raise SetupRequired("Existing database has an unsupported schema; explicit migration is required")
            else:
                descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
                os.close(descriptor)
                connection = sqlite3.connect(self.path)
                try:
                    connection.execute("PRAGMA journal_mode=" + ("WAL" if _safe_wal_version() else "DELETE"))
                    connection.executescript("BEGIN IMMEDIATE;" + SCHEMA + "INSERT INTO schema_migrations VALUES (1, strftime('%s','now'));COMMIT;")
                finally:
                    connection.close()
            if not os.path.exists(self.secret_path):
                descriptor = os.open(self.secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(secrets.token_bytes(64))
                    stream.flush()
                    os.fsync(stream.fileno())
            os.chmod(self.path, 0o600)
            os.chmod(self.secret_path, 0o600)
        return {"schema_version": SCHEMA_VERSION, "sqlite_version": sqlite3.sqlite_version, "journal_mode": "wal" if _safe_wal_version() else "delete"}

    def secret(self):
        try:
            with open(self.secret_path, "rb") as stream:
                value = stream.read()
        except OSError as exc:
            raise SetupRequired("Persistent signing secret is unavailable; run the local initialization command") from exc
        if len(value) != 64:
            raise SetupRequired("Persistent signing secret is invalid")
        return value

    @staticmethod
    def _user(row):
        result = {key: row[key] for key in ("id", "display_name", "role", "status", "must_change_password", "auth_version", "created_at", "updated_at")}
        result["username"] = row["username_normalized"]
        result["must_change_password"] = bool(result["must_change_password"])
        return result

    @staticmethod
    def _require_user(connection, user_id):
        row = connection.execute("SELECT * FROM users WHERE id=? AND status='active'", (user_id,)).fetchone()
        if not row:
            raise NotFound("Account not found")
        return row

    def create_user(self, username, password, display_name="", role="member", must_change_password=True):
        username = _username(username)
        if role not in ("admin", "member") or not isinstance(display_name, str) or len(display_name) > 100:
            raise ValidationError("Invalid role or display name")
        password_hash = PASSWORDS.hash(_password(password))
        identifier, now = _id(), time.time()
        try:
            with self._connection(write=True) as connection:
                connection.execute("INSERT INTO users VALUES (?,?,?,?,?,'active',?,1,?,?)", (identifier, username, display_name, password_hash, role, int(bool(must_change_password)), now, now))
                connection.execute("INSERT INTO user_settings(user_id,updated_at) VALUES (?,?)", (identifier, now))
                row = connection.execute("SELECT * FROM users WHERE id=?", (identifier,)).fetchone()
        except sqlite3.IntegrityError as exc:
            raise ConflictError("Username is already in use") from exc
        return self._user(row)

    def list_users(self):
        with self._connection() as connection:
            return [self._user(row) for row in connection.execute("SELECT * FROM users ORDER BY username_normalized")]

    def update_user(self, actor_id, target_id, action, **values):
        action = {"set-role": "role", "reset_password": "reset-password", "set_role": "role"}.get(action, action)
        if action not in {"role", "enable", "disable", "delete", "reset-password", "update"}:
            raise ValidationError("Unknown account action")
        password_hash = PASSWORDS.hash(_password(values.get("password", values.get("new_password")))) if action == "reset-password" else None
        with self._connection(write=True) as connection:
            if actor_id is not None:
                actor = self._require_user(connection, actor_id)
                if actor["role"] != "admin":
                    raise AuthenticationError("Administrator access required")
            target = connection.execute("SELECT * FROM users WHERE id=?", (target_id,)).fetchone()
            if not target:
                raise NotFound("Account not found")
            role = values.get("role", target["role"])
            if role not in {"admin", "member"}:
                raise ValidationError("Invalid role")
            status = "disabled" if action == "disable" else "active" if action == "enable" else target["status"]
            removing_admin = target["role"] == "admin" and target["status"] == "active" and (action == "delete" or status != "active" or (action in {"role", "update"} and role != "admin"))
            if removing_admin and connection.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND status='active'").fetchone()[0] <= 1:
                raise ConflictError("The last active administrator cannot be removed, disabled or demoted")
            if action == "delete":
                connection.execute("DELETE FROM users WHERE id=?", (target_id,))
                return None
            fields = {"updated_at": time.time()}
            revoke = False
            if action in {"role", "update"} and role != target["role"]:
                fields["role"], revoke = role, True
            if action in {"enable", "disable"}:
                fields["status"], revoke = status, True
            if action == "reset-password":
                fields.update(password_hash=password_hash, must_change_password=1)
                revoke = True
            if "display_name" in values:
                name = values["display_name"]
                if not isinstance(name, str) or len(name) > 100:
                    raise ValidationError("Invalid display name")
                fields["display_name"] = name
            if revoke:
                fields["auth_version"] = target["auth_version"] + 1
                connection.execute("DELETE FROM sessions WHERE user_id=?", (target_id,))
            connection.execute("UPDATE users SET " + ",".join(key + "=?" for key in fields) + " WHERE id=?", tuple(fields.values()) + (target_id,))
            return self._user(connection.execute("SELECT * FROM users WHERE id=?", (target_id,)).fetchone())

    def _settings(self, connection, user_id):
        row = connection.execute("SELECT * FROM user_settings WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            raise NotFound("Account settings not found")
        return dict(preferences=json.loads(row["preferences_json"]), **{key: bool(row[key]) for key in FLAGS}, **{key: row[key] for key in EPOCHS})

    def get_settings(self, user_id):
        with self._connection() as connection:
            self._require_user(connection, user_id)
            return self._settings(connection, user_id)

    @staticmethod
    def _preferences(preferences):
        if not isinstance(preferences, dict):
            raise ValidationError("Preferences must be an object")
        clean = {}
        for key, value in preferences.items():
            if key in INTEGER_PREFERENCES:
                clean[key] = _integer(value, *INTEGER_PREFERENCES[key])
            elif key in TEXT_PREFERENCES and isinstance(value, str) and value in TEXT_PREFERENCES[key]:
                clean[key] = value
            elif key == "timefmt" and isinstance(value, str) and len(value) <= 100:
                clean[key] = value
            elif key == "csvfields" and isinstance(value, str) and len(value) <= 1000 and all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", field) for field in value.split()):
                clean[key] = value
            else:
                raise ValidationError("Unsupported preference: " + str(key))
        _json(clean)
        return clean

    def save_settings(self, user_id, preferences=None, flags=None):
        clean = self._preferences(preferences) if preferences is not None else None
        if flags is not None and (not isinstance(flags, dict) or any(key not in FLAGS or not isinstance(value, bool) for key, value in flags.items())):
            raise ValidationError("Invalid recording switches")
        with self._connection(write=True) as connection:
            self._require_user(connection, user_id)
            current = self._settings(connection, user_id)
            merged = current["preferences"]
            if clean is not None:
                merged.update(clean)
            fields = {"preferences_json": _json(merged), "updated_at": time.time()}
            for flag, value in (flags or {}).items():
                fields[flag] = int(bool(value))
                if bool(value) != current[flag]:
                    epoch = EPOCHS[FLAGS.index(flag)]
                    fields[epoch] = current[epoch] + 1
            connection.execute("UPDATE user_settings SET " + ",".join(key + "=?" for key in fields) + " WHERE user_id=?", tuple(fields.values()) + (user_id,))
            return self._settings(connection, user_id)

    @staticmethod
    def _buckets(username, address):
        return [hashlib.sha256((kind + ":" + str(value)).encode()).hexdigest() for kind, value in (("user", username), ("address", address or "unknown"))]

    def authenticate(self, username, password, address="", device_label="", remember=False):
        normalized = str(username).lower()[:256]
        keys, now = self._buckets(normalized, address), time.time()
        with self._connection() as connection:
            for key in keys:
                throttle = connection.execute("SELECT blocked_until FROM login_throttle WHERE bucket_key=? AND expires_at>?", (key, now)).fetchone()
                if throttle and throttle[0] > now:
                    raise AuthenticationError("Invalid credentials or too many attempts; try again later")
            row = connection.execute("SELECT * FROM users WHERE username_normalized=?", (normalized,)).fetchone()
        # Always perform one expensive verification, including nonexistent users.
        encoded = row["password_hash"] if row else "$argon2id$v=19$m=19456,t=2,p=1$c29tZXNhbHRmb3JkdW1teQ$aoNvJ1xQgwj7wGoA7N6TWc3bwpPlvH7ko0dMPnSwcRA"
        try:
            valid = isinstance(password, str) and len(password) <= 128 and PASSWORDS.verify(encoded, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            valid = False
        valid = valid and row is not None and row["status"] == "active"
        new_hash = PASSWORDS.hash(password) if valid and PASSWORDS.check_needs_rehash(encoded) else None
        token, session_id = secrets.token_urlsafe(48), _id()
        with self._connection(write=True) as connection:
            if valid:
                fresh = connection.execute("SELECT * FROM users WHERE id=? AND status='active' AND auth_version=? AND password_hash=?", (row["id"], row["auth_version"], encoded)).fetchone()
                valid = fresh is not None
            if not valid:
                for key in keys:
                    current = connection.execute("SELECT * FROM login_throttle WHERE bucket_key=?", (key,)).fetchone()
                    count = current["failure_count"] + 1 if current and current["window_start"] > now - 900 else 1
                    start = current["window_start"] if current and current["window_start"] > now - 900 else now
                    delay = min(300, 2 ** min(count - 5, 9)) if count >= 5 else 0
                    connection.execute("INSERT OR REPLACE INTO login_throttle VALUES(?,?,?,?,?)", (key, start, count, now + delay, now + 900))
            else:
                connection.execute("DELETE FROM login_throttle WHERE bucket_key=?", (keys[0],))
                if new_hash:
                    connection.execute("UPDATE users SET password_hash=? WHERE id=?", (new_hash, row["id"]))
                connection.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,NULL,?)", (session_id, hashlib.sha256(token.encode()).hexdigest(), row["id"], row["auth_version"], now, now, now + 7 * DAY, now + 30 * DAY, str(device_label)[:200]))
                connection.execute("DELETE FROM sessions WHERE user_id=? AND id NOT IN (SELECT id FROM sessions WHERE user_id=? ORDER BY created_at DESC LIMIT 20)", (row["id"], row["id"]))
        if not valid:
            raise AuthenticationError("Invalid credentials or too many attempts; try again later")
        return token, self.get_session(token)

    def get_session(self, raw_token):
        if not isinstance(raw_token, str) or not 20 <= len(raw_token) <= 256:
            return None
        now = time.time()
        digest = hashlib.sha256(raw_token.encode()).hexdigest()
        with self._connection() as connection:
            row = connection.execute("SELECT s.* FROM sessions s JOIN users u ON u.id=s.user_id AND u.auth_version=s.auth_version WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.idle_expires_at>? AND s.absolute_expires_at>? AND u.status='active'", (digest, now, now)).fetchone()
            if not row:
                return None
            user = connection.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
            result = {key: row[key] for key in ("id", "user_id", "created_at", "last_seen_at", "idle_expires_at", "absolute_expires_at", "device_label", "auth_version")}
            result.update(user=self._user(user), settings=self._settings(connection, row["user_id"]))
        if row["last_seen_at"] < now - 300:
            with self._connection(write=True) as connection:
                connection.execute("UPDATE sessions SET last_seen_at=?,idle_expires_at=MIN(?,absolute_expires_at) WHERE id=? AND revoked_at IS NULL AND last_seen_at<?", (now, now + 7 * DAY, row["id"], now - 300))
        return result

    def list_sessions(self, user_id):
        now = time.time()
        with self._connection() as connection:
            self._require_user(connection, user_id)
            return [dict(row) for row in connection.execute("SELECT id,created_at,last_seen_at,device_label,absolute_expires_at,idle_expires_at FROM sessions WHERE user_id=? AND revoked_at IS NULL AND absolute_expires_at>? AND idle_expires_at>? ORDER BY last_seen_at DESC", (user_id, now, now))]

    def revoke_session(self, user_id, session_id=None):
        with self._connection(write=True) as connection:
            if session_id is None:
                connection.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            else:
                cursor = connection.execute("DELETE FROM sessions WHERE user_id=? AND id=?", (user_id, session_id))
                if not cursor.rowcount:
                    raise NotFound("Session not found")

    def change_password(self, user_id, old_password, new_password):
        with self._connection() as connection:
            user = self._require_user(connection, user_id)
        try:
            PASSWORDS.verify(user["password_hash"], old_password)
        except (VerificationError, InvalidHashError, TypeError) as exc:
            raise AuthenticationError("Current password is incorrect") from exc
        encoded = PASSWORDS.hash(_password(new_password))
        with self._connection(write=True) as connection:
            changed = connection.execute("UPDATE users SET password_hash=?,must_change_password=0,auth_version=auth_version+1,updated_at=? WHERE id=? AND password_hash=? AND status='active'", (encoded, time.time(), user_id, user["password_hash"]))
            if not changed.rowcount:
                raise AuthenticationError("Account changed; sign in again")
            connection.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))

    def begin_search(self, user_id, query, settings_snapshot, scope_version):
        query_json, snapshot = _json(query), _json(settings_snapshot)
        if not isinstance(query, (str, dict)) or not isinstance(settings_snapshot, dict):
            raise ValidationError("Invalid search data")
        if not (query.strip() if isinstance(query, str) else any(query.get(key) for key in ("query", "dir", "before", "after"))):
            return None
        identifier, now = _id(), time.time()
        with self._connection(write=True) as connection:
            self._require_user(connection, user_id)
            settings = self._settings(connection, user_id)
            if not settings["search_history_enabled"]:
                return None
            connection.execute("INSERT INTO search_history VALUES(?,?,?,?,?,?,'pending',?,?,NULL,NULL)", (identifier, user_id, query_json, hashlib.sha256(query_json.encode()).hexdigest(), snapshot, str(scope_version)[:200], settings["search_epoch"], now))
            self._cap(connection, "search_history", user_id, "created_at", 100, "status='pending'")
        return identifier

    @staticmethod
    def _search(row):
        result = dict(row)
        result["query"] = json.loads(result.pop("query_json"))
        result["settings_snapshot"] = json.loads(result["settings_snapshot"])
        return result

    def get_search(self, user_id, search_id):
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM search_history WHERE user_id=? AND id=? AND ((status='success' AND completed_at>?) OR (status IN ('pending','failed') AND created_at>?))", (user_id, search_id, time.time() - 90 * DAY, time.time() - DAY)).fetchone()
            if not row:
                raise NotFound("Search not found")
            return self._search(row)

    def complete_search(self, user_id, search_id, query, settings_snapshot, result_count):
        encoded, snapshot = _json(query), _json(settings_snapshot)
        _integer(result_count)
        with self._connection(write=True) as connection:
            settings = self._settings(connection, user_id)
            if not settings["search_history_enabled"]:
                return False
            # The original semantic query must match; snapshots may reflect current hard limits.
            changed = connection.execute("UPDATE search_history SET status='success',completed_at=?,result_count=?,settings_snapshot=? WHERE id=? AND user_id=? AND status='pending' AND history_epoch=? AND query_json=? AND created_at>?", (time.time(), result_count, snapshot, search_id, user_id, settings["search_epoch"], encoded, time.time() - DAY))
            if changed.rowcount:
                self._cap(connection, "search_history", user_id, "completed_at", 500, "status='success'")
            return bool(changed.rowcount)

    def fail_search(self, user_id, search_id):
        with self._connection(write=True) as connection:
            connection.execute("UPDATE search_history SET status='failed' WHERE user_id=? AND id=? AND status='pending'", (user_id, search_id))

    @staticmethod
    def _page(limit, cursor):
        limit = _integer(limit, 1, 100)
        if not cursor:
            return limit, None
        try:
            decoded = json.loads(base64.urlsafe_b64decode(str(cursor) + "=" * (-len(str(cursor)) % 4)))
            if not isinstance(decoded, list) or len(decoded) != 2 or not isinstance(decoded[0], (float, int)) or not math.isfinite(decoded[0]) or not isinstance(decoded[1], str) or len(decoded[1]) > 100:
                raise ValueError()
            return limit, decoded
        except (ValueError, TypeError, OverflowError) as exc:
            raise ValidationError("Invalid cursor") from exc

    @staticmethod
    def _paged(rows, limit, time_key, convert):
        has_more = len(rows) > limit
        items = [convert(row) for row in rows[:limit]]
        cursor = base64.urlsafe_b64encode(_json([items[-1][time_key], items[-1]["id"]]).encode()).decode().rstrip("=") if has_more else None
        return {"items": items, "next_cursor": cursor}

    def list_searches(self, user_id, limit=20, cursor=None):
        limit, after = self._page(limit, cursor)
        where = " AND (completed_at<? OR (completed_at=? AND id<?))" if after else ""
        arguments = (user_id, time.time() - 90 * DAY) + ((after[0], after[0], after[1]) if after else ()) + (limit + 1,)
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM search_history WHERE user_id=? AND status='success' AND completed_at>?" + where + " ORDER BY completed_at DESC,id DESC LIMIT ?", arguments).fetchall()
        return self._paged(rows, limit, "completed_at", self._search)

    def delete_search(self, user_id, search_id=None):
        with self._connection(write=True) as connection:
            self._require_user(connection, user_id)
            if search_id is None:
                connection.execute("DELETE FROM search_history WHERE user_id=?", (user_id,))
            elif not connection.execute("DELETE FROM search_history WHERE user_id=? AND id=?", (user_id, search_id)).rowcount:
                raise NotFound("Search not found")
            connection.execute("UPDATE user_settings SET search_epoch=search_epoch+1,updated_at=? WHERE user_id=?", (time.time(), user_id))
            # Pending requests belonging to any previous generation cannot complete.

    def register_document(self, library_key, source_url, source_path, ipath="", rcludi="", title="", format="", content_version=""):
        if not all(isinstance(value, str) for value in (library_key, source_url, source_path, ipath, rcludi, title, format, content_version)) or not library_key or not (source_url or source_path):
            raise ValidationError("Invalid document locator")
        if source_path:
            source_path = os.path.realpath(source_path)
            identity = [source_path, ipath]
        else:
            identity = [source_url, ipath]
        locator_key = hashlib.sha256(_json(identity).encode()).hexdigest()
        with self._connection(write=True) as connection:
            row = connection.execute("SELECT * FROM document_refs WHERE library_key=? AND locator_key=?", (library_key, locator_key)).fetchone()
            if row:
                actual = [row["source_path"], row["ipath"]] if row["source_path"] else [row["source_url"], row["ipath"]]
                if actual != identity:
                    raise ConflictError("Document locator digest collision")
                identifier = row["id"]
                connection.execute("UPDATE document_refs SET source_url=?,rcludi=?,title=?,format=?,content_version=?,last_verified_at=? WHERE id=?", (source_url, rcludi, title, format.lower(), content_version, time.time(), identifier))
            else:
                identifier = _id()
                connection.execute("INSERT INTO document_refs VALUES(?,?,?,?,?,?,?,?,?,?,?)", (identifier, library_key, locator_key, source_url, source_path, ipath, rcludi, title, format.lower(), content_version, time.time()))
            return dict(connection.execute("SELECT * FROM document_refs WHERE id=?", (identifier,)).fetchone())

    def get_document(self, document_id):
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM document_refs WHERE id=?", (document_id,)).fetchone()
            if not row:
                raise NotFound("Document not found")
            return dict(row)

    @staticmethod
    def _progress(row):
        if not row:
            return None
        result = dict(row)
        result["position"] = json.loads(result.pop("position_json"))
        return result

    def _get_progress(self, connection, user_id, document_id):
        return self._progress(connection.execute("SELECT p.* FROM reading_progress p WHERE p.user_id=? AND p.document_id=? AND (p.updated_at>? OR EXISTS(SELECT 1 FROM bookshelf b WHERE b.user_id=p.user_id AND b.document_id=p.document_id))", (user_id, document_id, time.time() - 365 * DAY)).fetchone())

    def get_progress(self, user_id, document_id):
        with self._connection() as connection:
            self._require_user(connection, user_id)
            return self._get_progress(connection, user_id, document_id)

    @staticmethod
    def _cap(connection, table, user_id, time_key, count, condition="1=1"):
        # Identifiers and conditions originate exclusively from this module.
        if table == "reading_progress":
            condition = "(" + condition + ") AND NOT EXISTS(SELECT 1 FROM bookshelf b WHERE b.user_id=reading_progress.user_id AND b.document_id=reading_progress.document_id)"
        connection.execute("DELETE FROM " + table + " WHERE user_id=? AND " + condition + " AND rowid NOT IN (SELECT rowid FROM " + table + " WHERE user_id=? AND " + condition + " ORDER BY " + time_key + " DESC,rowid DESC LIMIT ?)", (user_id, user_id, count))

    def open_reading(self, user_id, document_id, reading_epoch, progress_epoch, folder_key=None):
        _integer(reading_epoch)
        _integer(progress_epoch)
        if folder_key is not None and (not isinstance(folder_key, str) or not folder_key or len(folder_key) > 4096):
            raise ValidationError("Invalid folder identity")
        now = time.time()
        with self._connection(write=True) as connection:
            self._require_user(connection, user_id)
            settings = self._settings(connection, user_id)
            document = connection.execute("SELECT * FROM document_refs WHERE id=?", (document_id,)).fetchone()
            if not document:
                raise NotFound("Document not found")
            reading_valid = settings["reading_history_enabled"] and reading_epoch == settings["reading_epoch"]
            progress_valid = settings["progress_sync_enabled"] and progress_epoch == settings["progress_epoch"]
            if reading_valid:
                connection.execute("INSERT INTO reading_history VALUES(?,?,?,?,?,?) ON CONFLICT(user_id,document_id) DO UPDATE SET title_snapshot=excluded.title_snapshot,last_opened_at=excluded.last_opened_at", (_id(), user_id, document_id, document["title"], now, now))
                self._cap(connection, "reading_history", user_id, "last_opened_at", 1000)
            if progress_valid:
                # Expired rows must not restore old progress or block a fresh explicit open.
                connection.execute("DELETE FROM reading_progress WHERE user_id=? AND document_id=? AND updated_at<=? AND NOT EXISTS(SELECT 1 FROM bookshelf b WHERE b.user_id=reading_progress.user_id AND b.document_id=reading_progress.document_id)", (user_id, document_id, now - 365 * DAY))
                connection.execute("INSERT OR IGNORE INTO reading_progress(user_id,document_id,position_json,content_version,updated_at) VALUES(?,?,'{}',?,?)", (user_id, document_id, document["content_version"], now))
                self._cap(connection, "reading_progress", user_id, "updated_at", 1000)
                if folder_key:
                    connection.execute("INSERT INTO folder_resume VALUES(?,?,?,1,?) ON CONFLICT(user_id,folder_key) DO UPDATE SET last_document_id=excluded.last_document_id,revision=folder_resume.revision+1,updated_at=excluded.updated_at", (user_id, folder_key, document_id, now))
                    self._cap(connection, "folder_resume", user_id, "updated_at", 1000)
            return {"progress": self._get_progress(connection, user_id, document_id), "settings": settings, "reading_recorded": reading_valid, "progress_enabled": progress_valid}

    @staticmethod
    def _position(position, document_format):
        if not isinstance(position, dict):
            raise ValidationError("Position must be a structured object")
        if document_format and document_format not in FORMATS:
            raise ValidationError("This document format does not support progress")
        if "version" in position:
            _integer(position["version"], 1, 10)
        # Never accept a document source or executable object as a position.
        blocked = {"source_path", "source_url", "path", "url", "__proto__", "constructor", "prototype"}
        def inspect(value, depth=0):
            if depth > 8:
                raise ValidationError("Position nesting exceeds limit")
            if isinstance(value, dict):
                for key, child in value.items():
                    if not isinstance(key, str) or key in blocked or len(key) > 100:
                        raise ValidationError("Invalid position field")
                    inspect(child, depth + 1)
            elif isinstance(value, list):
                if len(value) > 256:
                    raise ValidationError("Position list exceeds limit")
                for child in value:
                    inspect(child, depth + 1)
            elif isinstance(value, str):
                if len(value) > 4096 or "\x00" in value:
                    raise ValidationError("Position text exceeds limit")
            elif value is not None and not isinstance(value, (int, float, bool)):
                raise ValidationError("Unsupported position value")
        inspect(position)
        return _json(position)

    def save_progress(self, user_id, document_id, position, revision, writer_id, writer_seq, epoch, content_version="", progress=None):
        _integer(revision)
        _integer(writer_seq, 1)
        _integer(epoch)
        if not isinstance(writer_id, str) or not 1 <= len(writer_id) <= 128:
            raise ValidationError("Invalid writer identity")
        if progress is not None and (isinstance(progress, bool) or not isinstance(progress, (int, float)) or not math.isfinite(progress) or not 0 <= progress <= 100):
            raise ValidationError("Invalid progress")
        # JSON validation occurs before the write transaction.
        preliminary = self.get_document(document_id)
        encoded = self._position(position, preliminary["format"])
        with self._connection(write=True) as connection:
            self._require_user(connection, user_id)
            settings = self._settings(connection, user_id)
            current = self._get_progress(connection, user_id, document_id)
            if not settings["progress_sync_enabled"] or settings["progress_epoch"] != epoch:
                raise ConflictError("Progress synchronization context expired; reopen the document", current=current)
            if current is None:
                raise ConflictError("Open the document again before saving progress", current=None)
            if current["writer_id"] == writer_id and writer_seq == current["writer_seq"] and encoded == _json(current["position"]) and revision + 1 == current["revision"]:
                return current  # An identical network retry is idempotent.
            if revision != current["revision"] or (current["writer_id"] == writer_id and writer_seq <= current["writer_seq"]):
                raise ConflictError("Progress has changed on another page or device", current=current)
            document = connection.execute("SELECT content_version FROM document_refs WHERE id=?", (document_id,)).fetchone()
            if content_version and content_version != document["content_version"]:
                raise ConflictError("Document content changed; reopen before saving", current=current)
            changed = connection.execute("UPDATE reading_progress SET position_json=?,position_version=?,content_version=?,revision=revision+1,writer_id=?,writer_seq=?,progress=?,origin='reading',updated_at=? WHERE user_id=? AND document_id=? AND revision=?", (encoded, position.get("version", 1), document["content_version"], writer_id, writer_seq, progress, time.time(), user_id, document_id, revision))
            if not changed.rowcount:
                raise ConflictError("Progress changed while saving", current=self._get_progress(connection, user_id, document_id))
            return self._get_progress(connection, user_id, document_id)

    def list_readings(self, user_id, limit=20, cursor=None):
        limit, after = self._page(limit, cursor)
        where = " AND (r.last_opened_at<? OR (r.last_opened_at=? AND r.id<?))" if after else ""
        now = time.time()
        arguments = (now - 365 * DAY, user_id, now - 180 * DAY) + ((after[0], after[0], after[1]) if after else ()) + (limit + 1,)
        with self._connection() as connection:
            rows = connection.execute("SELECT r.*,d.title,d.format,d.content_version,p.progress,p.revision FROM reading_history r JOIN document_refs d ON d.id=r.document_id LEFT JOIN reading_progress p ON p.user_id=r.user_id AND p.document_id=r.document_id AND (p.updated_at>? OR EXISTS(SELECT 1 FROM bookshelf b WHERE b.user_id=p.user_id AND b.document_id=p.document_id)) WHERE r.user_id=? AND r.last_opened_at>?" + where + " ORDER BY r.last_opened_at DESC,r.id DESC LIMIT ?", arguments).fetchall()
        return self._paged(rows, limit, "last_opened_at", dict)

    def delete_reading(self, user_id, document_id=None, clear_progress=False):
        with self._connection(write=True) as connection:
            self._require_user(connection, user_id)
            if document_id is None:
                connection.execute("DELETE FROM reading_history WHERE user_id=?", (user_id,))
            elif not connection.execute("DELETE FROM reading_history WHERE user_id=? AND document_id=?", (user_id, document_id)).rowcount:
                raise NotFound("Reading history not found")
            connection.execute("UPDATE user_settings SET reading_epoch=reading_epoch+1,updated_at=? WHERE user_id=?", (time.time(), user_id))
            if clear_progress:
                self._clear_progress(connection, user_id, document_id)

    @staticmethod
    def _clear_progress(connection, user_id, document_id):
        if document_id is None:
            connection.execute("DELETE FROM reading_progress WHERE user_id=?", (user_id,))
            connection.execute("DELETE FROM folder_resume WHERE user_id=?", (user_id,))
        else:
            connection.execute("DELETE FROM reading_progress WHERE user_id=? AND document_id=?", (user_id, document_id))
            connection.execute("DELETE FROM folder_resume WHERE user_id=? AND last_document_id=?", (user_id, document_id))
        connection.execute("UPDATE user_settings SET progress_epoch=progress_epoch+1,updated_at=? WHERE user_id=?", (time.time(), user_id))

    def clear_progress(self, user_id, document_id=None):
        with self._connection(write=True) as connection:
            self._require_user(connection, user_id)
            self._clear_progress(connection, user_id, document_id)

    def get_folder_resume(self, user_id, folder_key):
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM folder_resume WHERE user_id=? AND folder_key=? AND updated_at>?", (user_id, folder_key, time.time() - 365 * DAY)).fetchone()
            return dict(row) if row else None

    def add_to_shelf(self, user_id, document_id):
        now = time.time()
        with self._connection(write=True) as connection:
            self._require_user(connection, user_id)
            if not connection.execute("SELECT 1 FROM document_refs WHERE id=?", (document_id,)).fetchone():
                raise NotFound("Document not found")
            connection.execute("INSERT INTO bookshelf VALUES(?,?,?,?) ON CONFLICT(user_id,document_id) DO NOTHING", (user_id, document_id, now, now))
        return {"document_id": document_id, "on_shelf": True}

    def remove_from_shelf(self, user_id, document_id):
        with self._connection(write=True) as connection:
            self._require_user(connection, user_id)
            if not connection.execute("DELETE FROM bookshelf WHERE user_id=? AND document_id=?", (user_id, document_id)).rowcount:
                raise NotFound("Book is not on this account's shelf")
        return {"document_id": document_id, "on_shelf": False}

    def is_on_shelf(self, user_id, document_id):
        with self._connection() as connection:
            return bool(connection.execute("SELECT 1 FROM bookshelf WHERE user_id=? AND document_id=?", (user_id, document_id)).fetchone())

    def list_bookshelf(self, user_id, limit=20, cursor=None):
        limit, after = self._page(limit, cursor)
        where = " AND (b.added_at<? OR (b.added_at=? AND b.document_id<?))" if after else ""
        arguments = (user_id,) + ((after[0], after[0], after[1]) if after else ()) + (limit + 1,)
        with self._connection() as connection:
            self._require_user(connection, user_id)
            rows = connection.execute("SELECT b.document_id AS id,b.*,d.title,d.format,d.content_version,p.progress,p.revision,p.position_json,r.last_opened_at FROM bookshelf b JOIN document_refs d ON d.id=b.document_id LEFT JOIN reading_progress p ON p.user_id=b.user_id AND p.document_id=b.document_id LEFT JOIN reading_history r ON r.user_id=b.user_id AND r.document_id=b.document_id WHERE b.user_id=?" + where + " ORDER BY b.added_at DESC,b.document_id DESC LIMIT ?", arguments).fetchall()
        def convert(row):
            result = dict(row)
            result["position"] = json.loads(result.pop("position_json")) if result.get("position_json") else None
            return result
        return self._paged(rows, limit, "added_at", convert)

    def import_progress(self, user_id, document_id, position, epoch, content_version="", overwrite=False):
        document = self.get_document(document_id)
        encoded, now = self._position(position, document["format"]), time.time()
        _integer(epoch)
        with self._connection(write=True) as connection:
            self._require_user(connection, user_id)
            settings = self._settings(connection, user_id)
            current = self._get_progress(connection, user_id, document_id)
            if not settings["progress_sync_enabled"] or epoch != settings["progress_epoch"]:
                raise ConflictError("Progress synchronization context expired", current=current)
            if current and not overwrite:
                raise ConflictError("Existing server progress is preserved", current=current)
            if content_version and content_version != document["content_version"]:
                raise ConflictError("Document changed before import", current=current)
            connection.execute("INSERT INTO reading_progress(user_id,document_id,position_json,content_version,revision,origin,imported_at,updated_at) VALUES(?,?,?,?,?,'import',?,?) ON CONFLICT(user_id,document_id) DO UPDATE SET position_json=excluded.position_json,content_version=excluded.content_version,revision=reading_progress.revision+1,writer_id='',writer_seq=0,origin='import',imported_at=excluded.imported_at,updated_at=excluded.updated_at", (user_id, document_id, encoded, document["content_version"], 1, now, now))
            self._cap(connection, "reading_progress", user_id, "updated_at", 1000)
            return self._get_progress(connection, user_id, document_id)

    def cleanup(self):
        now, removed = time.time(), {}
        # Bounded batches keep each transaction short; the scheduler is the single caller.
        tasks = [("sessions", "revoked_at IS NOT NULL OR idle_expires_at<=? OR absolute_expires_at<=?", (now, now)), ("search_history", "(status='success' AND completed_at<=?) OR (status!='success' AND created_at<=?)", (now - 90 * DAY, now - DAY)), ("reading_history", "last_opened_at<=?", (now - 180 * DAY,)), ("reading_progress", "updated_at<=? AND NOT EXISTS(SELECT 1 FROM bookshelf b WHERE b.user_id=reading_progress.user_id AND b.document_id=reading_progress.document_id)", (now - 365 * DAY,)), ("folder_resume", "updated_at<=?", (now - 365 * DAY,)), ("login_throttle", "expires_at<=?", (now,))]
        with self._maintenance_lock():
            for table, where, arguments in tasks:
                removed[table] = 0
                while True:
                    with self._connection(write=True) as connection:
                        count = connection.execute("DELETE FROM " + table + " WHERE rowid IN (SELECT rowid FROM " + table + " WHERE " + where + " LIMIT 250)", arguments).rowcount
                    removed[table] += count
                    if count < 250:
                        break
            with self._connection(write=True) as connection:
                removed["document_refs"] = connection.execute("DELETE FROM document_refs WHERE id IN (SELECT d.id FROM document_refs d WHERE last_verified_at<? AND NOT EXISTS(SELECT 1 FROM reading_history r WHERE r.document_id=d.id) AND NOT EXISTS(SELECT 1 FROM reading_progress p WHERE p.document_id=d.id) AND NOT EXISTS(SELECT 1 FROM folder_resume f WHERE f.last_document_id=d.id) AND NOT EXISTS(SELECT 1 FROM bookshelf b WHERE b.document_id=d.id) LIMIT 250)", (now - DAY,)).rowcount
        return removed

    def backup(self, path):
        target = os.path.abspath(os.path.expanduser(os.fspath(path)))
        if target == self.path or os.path.exists(target):
            raise ValidationError("Backup destination must be a new file")
        os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        try:
            with self._connection() as source:
                destination = sqlite3.connect(target)
                try:
                    source.backup(destination, pages=128, sleep=0.05)
                finally:
                    destination.close()
        except BaseException:
            os.unlink(target)
            raise
        return target

    def restore(self, path):
        source_path = os.path.abspath(os.path.expanduser(os.fspath(path)))
        if source_path == self.path or not os.path.isfile(source_path):
            raise ValidationError("A separate existing backup file is required")
        # Operator must stop workers first; the CLI never claims a live restore is safe.
        with self._maintenance_lock():
            source = sqlite3.connect(Path(source_path).as_uri() + "?mode=ro", uri=True)
            try:
                if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or source.execute("PRAGMA foreign_key_check").fetchone():
                    raise ValidationError("Backup integrity check failed")
                if source.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] != SCHEMA_VERSION:
                    raise SetupRequired("Backup schema does not match this application")
                # Copy into a private staging DB, invalidate all restored credentials and
                # contexts, then atomically replace only after every check succeeds.
                staging = self.path + ".restore-" + _id()
                descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(descriptor)
                destination = sqlite3.connect(staging)
                try:
                    source.backup(destination)
                    destination.execute("PRAGMA journal_mode=DELETE")
                    destination.execute("DELETE FROM sessions")
                    destination.execute("DELETE FROM search_history WHERE status!='success'")
                    destination.execute("UPDATE users SET auth_version=auth_version+1")
                    # Random epoch shift prevents matching post-backup contexts as well.
                    shift = secrets.randbelow(2**40) + 2**40
                    destination.execute("UPDATE user_settings SET search_epoch=search_epoch+?,reading_epoch=reading_epoch+?,progress_epoch=progress_epoch+?", (shift, shift, shift))
                    destination.commit()
                finally:
                    destination.close()
                for suffix in ("-wal", "-shm", "-journal"):
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(self.path + suffix)
                os.replace(staging, self.path)
            except sqlite3.DatabaseError as exc:
                raise ValidationError("Backup is not a valid application database") from exc
            finally:
                source.close()
                if 'staging' in locals():
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(staging)
        return {"restored": True, "sessions_revoked": True}
