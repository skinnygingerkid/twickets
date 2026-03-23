import json
import os
import sqlite3
import yaml
from importlib.resources import files
from dotenv import load_dotenv
from pathlib import Path

_ENV_PATH = Path(__file__).parent.parent.parent / ".env"

CONFIG_DB_PATH = Path(__file__).parent.parent.parent / "config.db"


def get_token() -> str:
    load_dotenv(_ENV_PATH)
    return os.getenv("DISCORD_TOKEN")

def get_guild() -> str:
    load_dotenv(_ENV_PATH)
    return os.getenv("DISCORD_GUILD")


def load_config() -> dict:
    config_file = files("config").joinpath("config.yml")
    with config_file.open("r") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# SQLite config helpers
# ---------------------------------------------------------------------------

CREATE_CONFIG_TABLES = """
CREATE TABLE IF NOT EXISTS bot_settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS bot_ticketing_roles (
    role_name TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS ticket_types (
    type_key            TEXT PRIMARY KEY,
    category            TEXT,
    channel             TEXT,
    thread              INTEGER DEFAULT 0,
    update_nickname     INTEGER DEFAULT 0,
    assign_role         TEXT,
    classified          INTEGER DEFAULT 0,
    classified_role     TEXT,
    default_description TEXT,
    extra_info          TEXT,
    button_label        TEXT
);

CREATE TABLE IF NOT EXISTS ticket_type_fields (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type_key    TEXT NOT NULL,
    position    INTEGER NOT NULL,
    label       TEXT NOT NULL,
    placeholder TEXT,
    required    INTEGER DEFAULT 1,
    FOREIGN KEY (type_key) REFERENCES ticket_types (type_key)
);
"""


def init_config_db(db_path: Path = CONFIG_DB_PATH) -> None:
    """Create config tables if they don't already exist."""
    with sqlite3.connect(db_path) as con:
        con.executescript(CREATE_CONFIG_TABLES)


def load_config_from_db(db_path: Path = CONFIG_DB_PATH) -> dict:
    """
    Load config from a SQLite database and return a dict identical in
    structure to what load_config() returns from config.yml.
    """
    init_config_db(db_path)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        # --- bot section ---
        bot = {}
        for row in con.execute("SELECT key, value FROM bot_settings"):
            bot[row["key"]] = row["value"]
        roles = [row["role_name"] for row in con.execute(
            "SELECT role_name FROM bot_ticketing_roles ORDER BY role_name"
        )]
        if roles:
            bot["generic_ticketing_role"] = roles

        # --- ticket_types section ---
        ticket_types = {}
        for tt in con.execute("SELECT * FROM ticket_types ORDER BY type_key"):
            key = tt["type_key"]
            entry = {
                "category":            tt["category"],
                "channel":             tt["channel"],
                "thread":              bool(tt["thread"]),
                "update_nickname":     bool(tt["update_nickname"]),
                "default_description": tt["default_description"],
                "description":         bool(tt["description"]) if tt["description"] is not None else True,
            }
            if tt["assign_role"]:
                entry["assign_role"] = tt["assign_role"]
            if tt["classified"]:
                entry["classified"] = bool(tt["classified"])
            if tt["classified_role"]:
                entry["classified_role"] = tt["classified_role"]
            if tt["extra_info"]:
                entry["extra_info"] = tt["extra_info"]
            if tt["button_label"]:
                entry["button_label"] = tt["button_label"]

            fields = [
                {
                    "label":       row["label"],
                    "placeholder": row["placeholder"] or "",
                    "required":    bool(row["required"]),
                }
                for row in con.execute(
                    "SELECT * FROM ticket_type_fields WHERE type_key=? ORDER BY position",
                    (key,),
                )
            ]
            if fields:
                entry["fields"] = fields

            ticket_types[key] = entry

        return {"bot": bot, "ticket_types": ticket_types}
    finally:
        con.close()


def seed_db_from_config(config: dict, db_path: Path = CONFIG_DB_PATH) -> None:
    """
    Populate (or overwrite) the SQLite config database from a config dict.
    Useful for migrating from config.yml to DB-backed config.
    Call once: python -c "from twicketsbot.config import load_config, seed_db_from_config; seed_db_from_config(load_config())"
    """
    init_config_db(db_path)
    with sqlite3.connect(db_path) as con:
        # bot settings
        con.execute("DELETE FROM bot_settings")
        con.execute("DELETE FROM bot_ticketing_roles")
        bot = config.get("bot", {})
        for key, value in bot.items():
            if key == "generic_ticketing_role":
                continue
            con.execute(
                "INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)",
                (key, str(value)),
            )
        for role in bot.get("generic_ticketing_role") or []:
            con.execute(
                "INSERT OR REPLACE INTO bot_ticketing_roles (role_name) VALUES (?)",
                (role,),
            )

        # ticket types
        con.execute("DELETE FROM ticket_type_fields")
        con.execute("DELETE FROM ticket_types")
        for type_key, cfg in (config.get("ticket_types") or {}).items():
            con.execute(
                """
                INSERT OR REPLACE INTO ticket_types
                    (type_key, category, channel, thread, update_nickname,
                     assign_role, classified, classified_role, default_description, extra_info, button_label)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    type_key,
                    cfg.get("category"),
                    cfg.get("channel"),
                    int(bool(cfg.get("thread", False))),
                    int(bool(cfg.get("update_nickname", False))),
                    cfg.get("assign_role"),
                    int(bool(cfg.get("classified", False))),
                    cfg.get("classified_role"),
                    cfg.get("default_description"),
                    cfg.get("extra_info"),
                    cfg.get("button_label"),
                ),
            )
            for pos, field in enumerate(cfg.get("fields") or []):
                con.execute(
                    """
                    INSERT INTO ticket_type_fields
                        (type_key, position, label, placeholder, required)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        type_key,
                        pos,
                        field["label"],
                        field.get("placeholder", ""),
                        int(bool(field.get("required", True))),
                    ),
                )
        con.commit()
    print(f"[seed_db_from_config] Seeded config into {db_path}")


def upsert_ticket_type(type_key: str, data: dict, db_path: Path = CONFIG_DB_PATH) -> None:
    """
    Insert or update a ticket type's scalar fields.
    Only keys present in `data` are changed; omitted keys keep their existing values.
    Pass a key with value None to explicitly clear that field.
    """
    init_config_db(db_path)
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        existing = con.execute(
            "SELECT * FROM ticket_types WHERE type_key=?", (type_key,)
        ).fetchone()
        if existing:
            base = dict(existing)
        else:
            base = {
                "type_key": type_key,
                "category": None,
                "channel": None,
                "thread": 0,
                "update_nickname": 0,
                "assign_role": None,
                "classified": 0,
                "classified_role": None,
                "default_description": None,
                "description": 1,
                "extra_info": None,
                "button_label": None,
            }
        # Override only keys explicitly supplied in data
        for k, v in data.items():
            base[k] = v
        con.execute(
            """
            INSERT OR REPLACE INTO ticket_types
                (type_key, category, channel, thread, update_nickname,
                 assign_role, classified, classified_role, default_description,
                 description, extra_info, button_label)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                type_key,
                base.get("category"),
                base.get("channel"),
                int(bool(base.get("thread", False))),
                int(bool(base.get("update_nickname", False))),
                base.get("assign_role"),
                int(bool(base.get("classified", False))),
                base.get("classified_role"),
                base.get("default_description"),
                int(bool(base.get("description", True))),
                base.get("extra_info"),
                base.get("button_label"),
            ),
        )
        con.commit()


def delete_ticket_type_from_db(type_key: str, db_path: Path = CONFIG_DB_PATH) -> bool:
    """Delete a ticket type and its modal fields. Returns True if a row was deleted."""
    with sqlite3.connect(db_path) as con:
        con.execute("DELETE FROM ticket_type_fields WHERE type_key=?", (type_key,))
        cur = con.execute("DELETE FROM ticket_types WHERE type_key=?", (type_key,))
        con.commit()
        return cur.rowcount > 0


def list_ticket_fields(type_key: str, db_path: Path = CONFIG_DB_PATH) -> list[dict]:
    """Return all fields for a ticket type ordered by position."""
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in con.execute(
                "SELECT * FROM ticket_type_fields WHERE type_key=? ORDER BY position",
                (type_key,),
            )
        ]


def upsert_ticket_field(
    type_key: str,
    label: str,
    data: dict,
    db_path: Path = CONFIG_DB_PATH,
) -> None:
    """
    Add or update a modal field (matched by current `label`) for a ticket type.
    data keys (all optional):
        label         — rename the field to this value
        placeholder   — hint text shown in the modal input
        required      — bool
        position      — 0-based order in the modal (auto-appended if omitted for new fields)
    """
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        existing = con.execute(
            "SELECT * FROM ticket_type_fields WHERE type_key=? AND label=?",
            (type_key, label),
        ).fetchone()
        if existing:
            base = dict(existing)
            base.update(data)
            con.execute(
                """
                UPDATE ticket_type_fields
                SET label=?, placeholder=?, required=?, position=?
                WHERE type_key=? AND id=?
                """,
                (
                    base["label"],
                    base.get("placeholder", ""),
                    int(bool(base.get("required", True))),
                    base["position"],
                    type_key,
                    base["id"],
                ),
            )
        else:
            if "position" not in data:
                max_pos = con.execute(
                    "SELECT COALESCE(MAX(position), -1) FROM ticket_type_fields WHERE type_key=?",
                    (type_key,),
                ).fetchone()[0]
                data["position"] = max_pos + 1
            new_label = data.get("label", label)
            con.execute(
                """
                INSERT INTO ticket_type_fields (type_key, position, label, placeholder, required)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    type_key,
                    data["position"],
                    new_label,
                    data.get("placeholder", ""),
                    int(bool(data.get("required", True))),
                ),
            )
        con.commit()


def delete_ticket_field(type_key: str, label: str, db_path: Path = CONFIG_DB_PATH) -> bool:
    """Remove a field by label from a ticket type. Returns True if deleted."""
    with sqlite3.connect(db_path) as con:
        cur = con.execute(
            "DELETE FROM ticket_type_fields WHERE type_key=? AND label=?",
            (type_key, label),
        )
        con.commit()
        return cur.rowcount > 0
