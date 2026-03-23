# twickets

A Discord ticketing bot built with `discord.py`. Supports private threads and private channels, configurable ticket types with custom modal fields, role assignment, nickname updates, and full admin management via slash commands. Config can be loaded from a YAML file or a SQLite database.

---

## Requirements

- Python 3.11+
- [`uv`](https://github.com/astral-sh/uv) (or `pip`)
- A Discord bot token

Install dependencies:
```bash
uv sync
# or: pip install -e .
```

---

## Setup

### 1. Create `.env`

```env
DISCORD_TOKEN=your_bot_token_here
DISCORD_GUILD=your_guild_id_here
CONFIG_SOURCE=db   # or for testing: yaml
```

`.env` is gitignored — never commit it.

### 2. Configure the bot

**YAML mode**: edit `config/config.yml`.

**DB mode** (default) either:
- Manage ticket types entirely via `/ticket_type` slash commands, or
- Manage ticket types via sqlite command line

The config database is written to `config.db` at the project root.

### 3. Run the bot

```bash
PYTHONPATH=src:. uv run python -m twicketsbot.main
```

### 4. Deploy channels and embeds

In Discord, run `/setup` in the channel where you want the ticket buttons to appear. This creates all required categories and channels and posts the embed with ticket buttons.

---

## Config structure (`config.yml`)

```yaml
bot:
  token: "YOUR_DISCORD_TOKEN_HERE"
  generic_call_channel: "🎫-create-a-ticket"      # name of the default ticket button channel
  generic_call_category: "Support"                 # category for the above channel
  generic_archive_category: "Ticket Graveyard"     # closed channel-tickets are moved here
  generic_ticketing_role:                          # roles pinged on every non-classified ticket
    - "Staff"
    - "Moderator"

ticket_types:
  join-clan:                                       # unique key used as the ticket type identifier
    category: "Tickets"                            # Discord category for the ticket channel/thread
    channel: "🎟️-join-clan"                        # channel name (create-on-demand for channel mode; parent for thread mode)
    thread: true                                   # true = private thread, false = private channel
    update_nickname: true                          # update raiser's server nickname from the "IGN" modal field
    assign_role: "member"                          # role assigned to the raiser when ticket is opened
    classified: false                              # if true, only notifies classified_role (bypasses generic roles)
    classified_role: ""                            # role to exclusively notify when classified is true
    description: true                              # show the description text box in the modal (false = hide it)
    default_description: "I'd like to join."       # pre-filled text in the description box; used as static text if description is false
    extra_info: "Staff will be with you shortly."  # extra message appended to the ticket on creation
    button_label: "Join Clan"                      # label on the embed button (defaults to title-cased key)
    fields:                                        # additional modal inputs (max 4 if description is shown, 5 if hidden)
      - label: "IGN"
        placeholder: "Your exact in-game name"
        required: true
```

### Ticket type field reference

| Field | Type | Default | Description |
|---|---|---|---|
| `category` | string | — | Discord category the ticket channel/thread lives in |
| `channel` | string | — | Channel name — parent channel for threads, or the ticket channel for channel mode |
| `thread` | bool | `false` | `true` = private thread, `false` = private channel |
| `update_nickname` | bool | `false` | Renames the raiser's server nickname to the value of the `IGN` modal field |
| `assign_role` | string | — | Role name to assign to the raiser on ticket open |
| `classified` | bool | `false` | Bypasses generic ticketing roles — only notifies `classified_role` |
| `classified_role` | string | — | Role to notify when `classified` is `true` |
| `description` | bool | `true` | Show a free-text description box in the modal |
| `default_description` | string | — | Pre-fills the description box; used as static description text when `description: false` |
| `extra_info` | string | — | Informational message appended to the ticket message on creation |
| `button_label` | string | title-cased key | Label shown on the embed button |
| `fields` | list | — | Additional modal inputs (label, placeholder, required); Discord maximum of 5 inputs total per modal |

---

## Slash commands

### `/setup`

Deploys a ticket embed with buttons in the current channel. Creates any missing categories and channels.

| Parameter | Required | Description |
|---|---|---|
| `ticket_types` | No | Comma-separated list of ticket types to include (e.g. `join-clan, join-as-guest`). Autocomplete supported. Omit for all types. |
| `deploy_generic` | No | Also deploy the embed to the configured `generic_call_channel`. Default `false`. |

---

### `/ticket`

Opens a ticket via a modal. The raiser selects the ticket type and fills in any configured fields.

| Parameter | Required | Description |
|---|---|---|
| `ticket_type` | Yes | The type of ticket to raise. Autocomplete supported. |

---

### `/assign`

Assigns the current ticket to yourself. Updates the ticket message, disables the assign button, and grants explicit channel/thread access.

Must be run inside a ticket channel or thread.

---

### `/close_ticket`

Prompts for a close reason then archives the ticket.

- **Thread tickets**: locked and archived.
- **Channel tickets**: permissions stripped and moved to the archive category.

Can be run by the ticket raiser, the assigned staff member, or any administrator.

The ticket embed also has **📥 Assign to Me** and **🔒 Close Ticket** buttons that perform the same actions.

---

### `/ticket_type` group

Admin-only commands (`Administrator` permission required). Only available when `CONFIG_SOURCE=db`.

#### `/ticket_type create`

Creates a new ticket type. All fields from the [ticket type field reference](#ticket-type-field-reference) are available as parameters.

| Parameter | Required |
|---|---|
| `type_key` | Yes |
| `category` | Yes |
| `channel` | Yes |
| All other fields | No |

#### `/ticket_type update`

Updates one or more fields on an existing ticket type. Only the parameters you provide are changed. For clearable text fields (`assign_role`, `classified_role`, `extra_info`, `button_label`), type `none` to set the value to null.

#### `/ticket_type delete`

Deletes a ticket type and all its modal fields from the database.

#### `/ticket_type list`

Lists all configured ticket types with a summary of their mode, location, and settings.

#### `/ticket_type field_add`

Adds a modal input field to an existing ticket type.

| Parameter | Required | Description |
|---|---|---|
| `type_key` | Yes | Ticket type to add the field to |
| `label` | Yes | Field label shown in the modal |
| `placeholder` | No | Hint text inside the input box |
| `required` | No | Whether the field is required (default `true`) |
| `position` | No | 0-based position in the modal (appended at end if omitted) |

#### `/ticket_type field_edit`

Edits an existing modal field. Autocomplete available for both `type_key` and `label`. Type `none` for `placeholder` to clear it.

| Parameter | Description |
|---|---|
| `type_key` | Ticket type that owns the field |
| `label` | Current label of the field to edit |
| `new_label` | Rename the field |
| `placeholder` | New placeholder text |
| `required` | Change required setting |
| `position` | New position in the modal |

#### `/ticket_type field_remove`

Removes a modal field from a ticket type.

#### `/ticket_type field_list`

Lists all modal fields for a ticket type with their positions, required status, and placeholder text.

---

## How tickets work

1. A user clicks a button in the ticket embed (deployed by `/setup`) or runs `/ticket`.
2. If the ticket type has modal inputs, a modal is shown. If there are no inputs (`description: false` and no fields), the ticket is created directly without a modal.
3. On submission, the bot creates either a **private thread** or a **private channel** depending on the ticket type config.
4. The ticket message is posted with the raiser's details, their modal responses, any `extra_info`, and a ping to the relevant staff roles.
5. If `update_nickname` is set and an `IGN` field was filled in, the raiser's server nickname is updated.
6. If `assign_role` is set, the raiser is assigned that role immediately.
7. Staff can assign themselves via the button or `/assign`.
8. The ticket is closed via the button or `/close_ticket`, which prompts for a reason.

---

## Adding a new ticket type field to the schema

The ticket type field schema is centralised in `_TICKET_TYPE_FIELDS` at the top of `src/twicketsbot/ticket_cog.py`. To add a new field that is managed via slash commands:

1. Add a column to the `ticket_types` table in `CREATE_CONFIG_TABLES` in `config.py`.
2. Add a row to `_TICKET_TYPE_FIELDS` in `ticket_cog.py` — the describe text for both `/ticket_type create` and `/ticket_type update` is defined here.
3. Add the parameter to both `ticket_type_create` and `ticket_type_update` function signatures.
4. Handle the new field in `upsert_ticket_type` and `load_config_from_db` in `config.py`.

