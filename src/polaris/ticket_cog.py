import asyncio
import os
import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional

from polaris.ticket_ui import (
    AssignView,
    CloseReasonModal,
    DescriptionModal,
    TicketView,
)

# Central schema: (param_name, clearable_with_none, create_description, update_description)
# Add new ticket type fields here — describe blocks and handler logic pick them up automatically.
_TICKET_TYPE_FIELDS: list[tuple[str, bool, str, str]] = [
    ("category",            False, "Discord category name for the ticket channel/thread",                      "New category name"),
    ("channel",             False, "Discord channel name used by this ticket type",                            "New channel name"),
    ("thread",              False, "Create tickets as private threads (default False)",                        "Change thread/channel mode"),
    ("update_nickname",     False, "Update raiser's nickname from IGN field (default False)",                  "Change nickname-update setting"),
    ("assign_role",         True,  "Role to assign the raiser when ticket is opened",                         "New role to assign on open (type 'none' to clear)"),
    ("classified",          False, "Only notify classified_role instead of generic roles (default False)",    "Override to exclusively notify classified_role instead of generic roles"),
    ("classified_role",     True,  "Role to exclusively notify when classified is True",                      "Role to exclusively notify when classified is True (type 'none' to clear)"),
    ("default_description", False, "Pre-filled text shown in modal (or static text if description is False)", "New default description"),
    ("description",         False, "Show description box in the modal (default True)",                        "Show description box in the modal (True/False)"),
    ("extra_info",          True,  "Info message appended to the ticket on creation",                         "New extra info message (type 'none' to clear)"),
    ("button_label",        True,  "Button label in the embed (defaults to title-cased type_key)",            "New button label (type 'none' to clear)"),
]

_CREATE_DESCRIBES = {f: c for f, _, c, _ in _TICKET_TYPE_FIELDS}
_UPDATE_DESCRIBES = {f: u for f, _, _, u in _TICKET_TYPE_FIELDS}


class TicketCog(commands.Cog):
    """
    Main cog for the ticketing system.

    Slash commands:
        /setup         — create categories/channels and deploy call-channel embeds.
        /ticket        — open a ticket via modal (validates the invoking channel).
        /assign        — assign the current ticket to yourself.
        /close_ticket  — prompt for a reason then archive/lock the ticket.

    Config keys (config.yml):
        bot.generic_call_channel      — channel name for any-type ticket buttons.
        bot.generic_call_category     — parent category for the generic call channel.
        bot.generic_archive_category  — category closed channel-tickets are moved to.
        bot.generic_ticketing_role    — list of role names notified on every ticket.
        ticket_types[*].classified      — true = override generic roles with classified_role only.
        ticket_types[*].classified_role  — the role used when classified is true.
        ticket_types[*].thread          — true = private thread; false = private channel.
    """

    def __init__(self, bot, config: dict):
        self.bot = bot
        self.config = config
        print(f"TicketCog initialized with config: {self.config}")

    def _is_db_mode(self) -> bool:
        return os.getenv("CONFIG_SOURCE", "yaml").lower() == "db"

    def _reload_config(self) -> None:
        """Reload self.config from its source after a DB change."""
        if self._is_db_mode():
            from polaris.config import load_config_from_db
            self.config = load_config_from_db()
        else:
            from polaris.config import load_config
            self.config = load_config()

    def _resolve_staff_roles(self, guild: discord.Guild, ticket_cfg: dict) -> list[discord.Role]:
        """Return discord.Role objects for staff to ping.
        If the ticket type is classified, only the classified_role is returned.
        Otherwise returns generic ticketing roles.
        """
        if ticket_cfg.get("classified") and ticket_cfg.get("classified_role"):
            role = discord.utils.get(guild.roles, name=ticket_cfg["classified_role"])
            return [role] if role else []
        names = list(self.config["bot"].get("generic_ticketing_role") or [])
        return [r for name in names if (r := discord.utils.get(guild.roles, name=name))]

    async def _create_ticket(self, interaction: discord.Interaction, resolved_type: str, description: str, fields: dict = None):
        """Core ticket creation — shared by slash command and button flow."""
        try:
            ticket_cfg = self.config["ticket_types"][resolved_type]
            use_thread = ticket_cfg.get("thread", False)
            ticket_channel_name = ticket_cfg["channel"]
            ticket_cat_name = ticket_cfg["category"]

            category = discord.utils.get(interaction.guild.categories, name=ticket_cat_name)
            if category is None:
                await interaction.followup.send(
                    f"Ticket category **{ticket_cat_name}** not found. Run `/setup` first.", ephemeral=True
                )
                return

            ticket_channel = discord.utils.get(category.channels, name=ticket_channel_name)
            if ticket_channel is None:
                await interaction.followup.send(
                    f"Ticket channel **{ticket_channel_name}** not found. Run `/setup` first.", ephemeral=True
                )
                return

            staff_roles = self._resolve_staff_roles(interaction.guild, ticket_cfg)
            staff_ping = " ".join(r.mention for r in staff_roles) if staff_roles else ""

            field_text = ""
            if fields:
                field_text = "\n" + "\n".join(f"**{k}:** {v}" for k, v in fields.items())

            # Update the opener's server nickname if configured and IGN was provided
            nickname_warning = None
            if ticket_cfg.get("update_nickname") and fields and "IGN" in fields:
                ign = fields["IGN"]
                if interaction.user.id == interaction.guild.owner_id:
                    nickname_warning = f"⚠️ Nickname could not be updated to **{ign}** — Discord does not allow bots to change the server owner's nickname."
                    print(f"[_create_ticket] Skipping nickname update for server owner {interaction.user} (IGN: {ign})")
                else:
                    try:
                        await interaction.user.edit(nick=ign, reason="Nickname updated from ticket IGN field")
                        print(f"[_create_ticket] Nickname updated for {interaction.user} → {ign}")
                    except discord.Forbidden:
                        nickname_warning = f"⚠️ Nickname could not be updated to **{ign}** — the bot lacks `Manage Nicknames` permission."
                        print(f"[_create_ticket] Forbidden: could not update nickname for {interaction.user} → {ign}")
                    except discord.HTTPException as e:
                        nickname_warning = f"⚠️ Nickname could not be updated to **{ign}** — Discord returned an error: {e}"
                        print(f"[_create_ticket] HTTPException updating nickname for {interaction.user} → {ign}: {e}")

            msg_body = (
                f"**Ticket raised by {interaction.user.mention}**\n"
                f"**Type:** {resolved_type}\n"
                f"**Description:** {description}"
                f"{field_text}\n\n"
                f"Use the 🔒 Close Ticket button or `/close_ticket` to close this ticket."
            )
            if staff_ping:
                msg_body += f"\n\n📢 {staff_ping}"
            extra_info = ticket_cfg.get("extra_info")
            if extra_info:
                msg_body += f"\n\nℹ️ {extra_info}"
            if nickname_warning:
                msg_body += f"\n\n{nickname_warning}"

            # Assign a role to the opener if configured
            role_warning = None
            assign_role_name = ticket_cfg.get("assign_role")
            if assign_role_name:
                assign_role = discord.utils.get(interaction.guild.roles, name=assign_role_name)
                if assign_role is None:
                    role_warning = f"⚠️ Could not assign role **{assign_role_name}** — role not found on this server."
                    print(f"[_create_ticket] Role '{assign_role_name}' not found in guild")
                else:
                    try:
                        await interaction.user.add_roles(assign_role, reason=f"Assigned via {resolved_type} ticket")
                        print(f"[_create_ticket] Assigned role '{assign_role_name}' to {interaction.user}")
                    except discord.Forbidden:
                        role_warning = f"⚠️ Could not assign role **{assign_role_name}** — the bot lacks `Manage Roles` permission."
                        print(f"[_create_ticket] Forbidden: could not assign role '{assign_role_name}' to {interaction.user}")
                    except discord.HTTPException as e:
                        role_warning = f"⚠️ Could not assign role **{assign_role_name}** — Discord returned an error: {e}"
                        print(f"[_create_ticket] HTTPException assigning role '{assign_role_name}' to {interaction.user}: {e}")
            if role_warning:
                msg_body += f"\n\n{role_warning}"

            if use_thread:
                thread_name = f"{resolved_type}-{interaction.user.display_name}"
                thread = await ticket_channel.create_thread(
                    name=thread_name,
                    type=discord.ChannelType.private_thread,
                    reason=f"Ticket raised by {interaction.user}",
                )
                await thread.add_user(interaction.user)
                for role in staff_roles:
                    for member in role.members:
                        await thread.add_user(member)
                await thread.send(msg_body, view=AssignView(self))
                await interaction.followup.send(
                    f"Your ticket has been created: {thread.mention}", ephemeral=True
                )
            else:
                overwrites = {
                    interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                    interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
                }
                for role in staff_roles:
                    overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
                channel = await interaction.guild.create_text_channel(
                    name=f"{resolved_type}-{interaction.user.display_name}",
                    category=category,
                    overwrites=overwrites,
                )
                await channel.send(msg_body, view=AssignView(self))
                await interaction.followup.send(
                    f"Your ticket has been created: {channel.mention}", ephemeral=True
                )
        except Exception as e:
            import traceback
            print(f"[_create_ticket error] {traceback.format_exc()}")
            try:
                await interaction.followup.send(f"Error creating ticket: {e}", ephemeral=True)
            except Exception:
                pass

    async def _deploy_embed_to_channel(self, channel: discord.TextChannel, ticket_types: list[str]):
        """Post or update a ticket embed with buttons in a call channel."""

        # Embed is defined here
        embed = discord.Embed(
            title="🎟️ Open a Ticket",
            description="Click a button below to raise a ticket.",
            color=discord.Color.blurple(),
        )
        for t in ticket_types:
            cfg = self.config["ticket_types"][t]
            label = cfg.get("button_label", t.replace("-", " ").title())
            embed.add_field(name=label, value=cfg.get("default_description", "No description set."), inline=False)

        # Buttons
        view = TicketView(ticket_types, self)

        # Edit existing bot embed message if present, otherwise post new
        existing_msg = None
        async for msg in channel.history(limit=50):
            if msg.author == channel.guild.me and msg.embeds:
                existing_msg = msg
                break

        if existing_msg:
            await existing_msg.edit(embed=embed, view=view)
        else:
            await channel.send(embed=embed, view=view)

    async def setup_ticket_type_autocomplete(self, interaction: discord.Interaction, current: str):
        """Autocomplete for /setup: supports comma-separated ticket types, completing the last token."""
        all_types = list(self.config.get("ticket_types", {}).keys())
        # Split on comma and work on the last token being typed
        parts = [p.strip() for p in current.split(",")]
        already_chosen = set(parts[:-1])  # everything before the last comma
        typing = parts[-1].lower()
        prefix = ", ".join(parts[:-1])
        if prefix:
            prefix += ", "
        return [
            app_commands.Choice(name=f"{prefix}{t}", value=f"{prefix}{t}")
            for t in all_types
            if t not in already_chosen and typing in t.lower()
        ][:25]

    @app_commands.command(name="setup", description="Deploy a ticket embed in this channel")
    @app_commands.describe(ticket_types="Comma-separated ticket types to show (omit for all types), e.g. join-clan, join-as-guest")
    @app_commands.autocomplete(ticket_types=setup_ticket_type_autocomplete)
    @app_commands.checks.has_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction, ticket_types: str = None, deploy_generic: bool = False):
        print(f"[setup] invoked by {interaction.user} in #{interaction.channel.name} ticket_types={ticket_types!r} deploy_generic={deploy_generic}")
        await interaction.response.defer(ephemeral=True)
        try:
            guild = interaction.guild
            current_channel = interaction.channel
            bot_cfg = self.config.get("bot", {})
            ticket_cfg = self.config.get("ticket_types", {})

            # Parse and validate the comma-separated ticket_types string
            if ticket_types:
                requested = [t.strip() for t in ticket_types.split(",") if t.strip()]
                invalid = [t for t in requested if t not in ticket_cfg]
                if invalid:
                    valid = ", ".join(f"`{t}`" for t in ticket_cfg)
                    await interaction.followup.send(
                        f"Unknown ticket type(s): {', '.join(f'`{t}`' for t in invalid)}. Valid options: {valid}",
                        ephemeral=True,
                    )
                    return
            else:
                requested = None

            created = []
            existing = []

            async def ensure_category(name: str) -> discord.CategoryChannel:
                cat = discord.utils.get(guild.categories, name=name)
                if cat is None:
                    cat = await guild.create_category(name)
                    await asyncio.sleep(0.5)
                    created.append(f"Category: **{name}**")
                else:
                    existing.append(f"Category: **{name}**")
                return cat

            async def ensure_channel(name: str, category: discord.CategoryChannel) -> discord.TextChannel:
                ch = discord.utils.get(category.channels, name=name)
                if ch is None:
                    ch = await guild.create_text_channel(name, category=category)
                    await asyncio.sleep(0.5)
                    created.append(f"Channel: **{name}** in {category.name}")
                else:
                    existing.append(f"Channel: **{name}** in {category.name}")
                return ch

            # 1. Deploy embed in the current channel (locked to requested types or all)
            types_for_current = requested if requested else list(ticket_cfg.keys())
            await self._deploy_embed_to_channel(current_channel, types_for_current)

            # 2. Ensure generic call category + channel exist; deploy all types there
            #    (skip if the current channel IS the generic call channel — already done above)
            #   deploy_generic flag can be set to true to also deploy to the generic channel/cat
            generic_cat_name = bot_cfg.get("generic_call_category")
            generic_ch_name = bot_cfg.get("generic_call_channel")
            if deploy_generic:
                if generic_cat_name:
                    generic_cat = await ensure_category(generic_cat_name)
                    if generic_ch_name:
                        generic_call_channel = await ensure_channel(generic_ch_name, generic_cat)
                        if generic_call_channel.id != current_channel.id:
                            await self._deploy_embed_to_channel(generic_call_channel, list(ticket_cfg.keys()))

            # 3. Ensure archive category exists
            # This will always run
            archive_cat_name = bot_cfg.get("generic_archive_category")
            if archive_cat_name:
                await ensure_category(archive_cat_name)

            # 4. Ensure ticket landing categories + channels exist for every ticket type
            # This will always run
            ticket_cats: dict[str, discord.CategoryChannel] = {}
            for tt, cfg in ticket_cfg.items():
                cat_name = cfg["category"]
                if cat_name not in ticket_cats:
                    ticket_cats[cat_name] = await ensure_category(cat_name)
                
                if cfg.get("thread", False):
                    # Thread-based tickets attach to a parent channel — it must exist
                    await ensure_channel(cfg["channel"], ticket_cats[cat_name])
                # Channel-based tickets create their own channel on demand — nothing to pre-create

            scope = ", ".join(f"`{t}`" for t in requested) if requested else "all ticket types"
            lines = [f"**Embed deployed in {current_channel.mention}** — {scope}."]
            if created:
                lines.append("**Created:**\n" + "\n".join(f"  + {c}" for c in created))
            if existing:
                lines.append("**Already existed:**\n" + "\n".join(f"  ✓ {e}" for e in existing))

            await interaction.followup.send("\n\n".join(lines), ephemeral=True)

        except Exception as e:
            import traceback
            print(f"[setup error] {traceback.format_exc()}")
            await interaction.followup.send(f"Setup failed with error:\n```{e}```", ephemeral=True)

    @setup.error
    async def setup_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        import traceback
        print(f"[setup_error] {type(error).__name__}: {error}")
        traceback.print_exc()
        msg = "You need Administrator permission to run setup." if isinstance(error, app_commands.MissingPermissions) else f"Setup error: {error}"
        try:
            await interaction.response.send_message(msg, ephemeral=True)
        except discord.InteractionResponded:
            await interaction.followup.send(msg, ephemeral=True)

    async def ticket_type_autocomplete(self, interaction: discord.Interaction, current: str):
        choices = list(self.config.get("ticket_types", {}).keys())
        return [
            app_commands.Choice(name=t, value=t)
            for t in choices
            if current.lower() in t.lower()
        ]

    @app_commands.command(name="ticket", description="Open a new support ticket")
    @app_commands.describe(
        ticket_type="Type of ticket to raise",
    )
    @app_commands.autocomplete(ticket_type=ticket_type_autocomplete)
    async def ticket(self, interaction: discord.Interaction, ticket_type: str):
        print(f"[ticket] invoked by {interaction.user} in channel '{interaction.channel.name}' ticket_type='{ticket_type}'")
        try:
            if ticket_type not in self.config.get("ticket_types", {}):
                valid = ", ".join(f"`{t}`" for t in self.config.get("ticket_types", {}))
                await interaction.response.send_message(
                    f"Unknown ticket type `{ticket_type}`. Valid options: {valid}", ephemeral=True
                )
                return

            await interaction.response.send_modal(DescriptionModal(ticket_type, self))

        except Exception as e:
            import traceback
            print(f"[ticket error] {traceback.format_exc()}")
            try:
                await interaction.response.send_message(f"Error: {e}", ephemeral=True)
            except discord.InteractionResponded:
                await interaction.followup.send(f"Error: {e}", ephemeral=True)
            except discord.InteractionResponded:
                await interaction.followup.send(f"Error: {e}", ephemeral=True)

    async def _do_close_ticket(self, interaction: discord.Interaction, reason: str):
        """Shared close logic — called by CloseReasonModal after user provides a reason."""
        channel = interaction.channel
        guild = interaction.guild

        try:
            is_thread = isinstance(channel, discord.Thread)

            opening_msg = None
            async for msg in channel.history(limit=30, oldest_first=True):
                if msg.author == guild.me and "Ticket raised by" in (msg.content or ""):
                    opening_msg = msg
                    break

            if opening_msg is None:
                await interaction.followup.send(
                    "Could not find the ticket opening message. Are you in a ticket?", ephemeral=True
                )
                return

            content = opening_msg.content or ""

            raiser_mention = None
            for line in content.splitlines():
                if line.startswith("**Ticket raised by"):
                    raiser_mention = line.split("**Ticket raised by ")[1].rstrip("**").strip()
                    break

            assigned_mention = None
            for line in content.splitlines():
                if line.startswith("**Assigned to:**"):
                    assigned_mention = line.split("**Assigned to:** ")[1].strip()
                    break

            user_mention = interaction.user.mention
            is_raiser = raiser_mention == user_mention
            is_assigned = assigned_mention == user_mention
            is_admin = interaction.user.guild_permissions.administrator

            if not (is_raiser or is_assigned or is_admin):
                await interaction.followup.send(
                    "Only the ticket raiser, the assigned staff member, or an administrator can close this ticket.",
                    ephemeral=True,
                )
                return

            closer = interaction.user.mention

            if is_thread:
                await channel.send(f"🔒 Ticket closed by {closer}.\n**Reason:** {reason}")
                await interaction.followup.send("Ticket closed and thread archived.", ephemeral=True)
                await channel.edit(
                    locked=True,
                    archived=True,
                    reason=f"Ticket closed by {interaction.user}",
                )
            else:
                archive_cat_name = self.config["bot"].get("generic_archive_category")
                archive_cat = None
                if archive_cat_name:
                    archive_cat = discord.utils.get(guild.categories, name=archive_cat_name)

                await channel.send(f"🔒 Ticket closed by {closer}.\n**Reason:** {reason}")
                edit_kwargs = {
                    "overwrites": {
                        guild.default_role: discord.PermissionOverwrite(view_channel=False),
                    },
                    "reason": f"Ticket closed by {interaction.user}",
                }
                if archive_cat:
                    edit_kwargs["category"] = archive_cat
                await channel.edit(**edit_kwargs)
                await interaction.followup.send("Ticket closed and channel archived.", ephemeral=True)

        except Exception as e:
            import traceback
            print(f"[close_ticket error] {traceback.format_exc()}")
            await interaction.followup.send(f"Error closing ticket: {e}", ephemeral=True)

    @app_commands.command(name="close_ticket", description="Close the current ticket")
    async def close_ticket(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CloseReasonModal(self))

    @app_commands.command(name="assign", description="Assign this ticket to yourself")
    async def assign(self, interaction: discord.Interaction):
        """
        Slash-command alternative to the Assign button.
        Finds the bot's opening message, updates the assigned-to line,
        and grants explicit permissions on the thread or channel.
        """
        channel = interaction.channel

        # Find the bot's opening message with the AssignView
        target_msg = None
        async for msg in channel.history(limit=20, oldest_first=True):
            if msg.author == interaction.guild.me and msg.components:
                target_msg = msg
                break

        if target_msg is None:
            await interaction.response.send_message("Could not find the ticket message to assign.", ephemeral=True)
            return

        content = target_msg.content or ""
        if f"Ticket raised by {interaction.user.mention}" in content:
            await interaction.response.send_message("You cannot assign a ticket you raised.", ephemeral=True)
            return

        if f"**Assigned to:** {interaction.user.mention}" in content:
            await interaction.response.send_message("You already have this ticket assigned to you.", ephemeral=True)
            return

        lines = [line for line in content.splitlines() if not line.startswith("**Assigned to:**")]
        lines.append(f"**Assigned to:** {interaction.user.mention}")
        new_content = "\n".join(lines)

        view = AssignView(self)
        view.assign.label = f"📥 Assigned to {interaction.user.display_name}"
        view.assign.style = discord.ButtonStyle.secondary
        view.assign.disabled = True
        await target_msg.edit(content=new_content, view=view)

        # Grant the assigned staff member explicit access to the ticket
        if isinstance(channel, discord.Thread):
            await channel.add_user(interaction.user)
        else:
            await channel.set_permissions(
                interaction.user,
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            )

        await interaction.response.send_message("✅ You have been assigned to this ticket.", ephemeral=True)

    # ------------------------------------------------------------------ #
    #  /ticket_type group — create, update, delete  (DB mode only)        #
    # ------------------------------------------------------------------ #

    # Derived from module-level _TICKET_TYPE_FIELDS — handlers use this for data dict construction.
    _TICKET_TYPE_SCHEMA: list[tuple[str, bool]] = [(f, cl) for f, cl, _, _ in _TICKET_TYPE_FIELDS]

    ticket_type_group = app_commands.Group(
        name="ticket_type",
        description="Create, update or delete ticket types (DB mode only)",
        default_permissions=discord.Permissions(administrator=True),
    )

    @ticket_type_group.command(name="create", description="Add a new ticket type")
    @app_commands.describe(type_key="Unique key for this ticket type, e.g. join-clan", **_CREATE_DESCRIBES)
    async def ticket_type_create(
        self,
        interaction: discord.Interaction,
        type_key: str,
        category: str,
        channel: str,
        thread: bool = False,
        update_nickname: bool = False,
        assign_role: Optional[str] = None,
        classified: bool = False,
        classified_role: Optional[str] = None,
        default_description: Optional[str] = None,
        description: bool = True,
        extra_info: Optional[str] = None,
        button_label: Optional[str] = None,
    ):
        if not self._is_db_mode():
            await interaction.response.send_message(
                "⚠️ Ticket type management requires `CONFIG_SOURCE=db`.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        if type_key in self.config.get("ticket_types", {}):
            await interaction.followup.send(
                f"Ticket type `{type_key}` already exists. Use `/ticket_type update` to modify it.",
                ephemeral=True,
            )
            return
        from polaris.config import upsert_ticket_type
        _p = locals()
        upsert_ticket_type(type_key, {field: _p[field] for field, _ in self._TICKET_TYPE_SCHEMA})
        self._reload_config()
        await interaction.followup.send(f"✅ Ticket type `{type_key}` created.", ephemeral=True)
        print(f"[ticket_type create] {interaction.user} created '{type_key}'")

    @ticket_type_group.command(name="update", description="Update fields on an existing ticket type")
    @app_commands.describe(type_key="Ticket type to update", **_UPDATE_DESCRIBES)
    @app_commands.autocomplete(type_key=ticket_type_autocomplete)
    async def ticket_type_update(
        self,
        interaction: discord.Interaction,
        type_key: str,
        category: Optional[str] = None,
        channel: Optional[str] = None,
        thread: Optional[bool] = None,
        update_nickname: Optional[bool] = None,
        assign_role: Optional[str] = None,
        classified: Optional[bool] = None,
        classified_role: Optional[str] = None,
        default_description: Optional[str] = None,
        description: Optional[bool] = None,
        extra_info: Optional[str] = None,
        button_label: Optional[str] = None,
    ):
        if not self._is_db_mode():
            await interaction.response.send_message(
                "⚠️ Ticket type management requires `CONFIG_SOURCE=db`.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        if type_key not in self.config.get("ticket_types", {}):
            await interaction.followup.send(
                f"Ticket type `{type_key}` not found. Use `/ticket_type create` to add it.",
                ephemeral=True,
            )
            return
        # Build a dict of only the fields the user explicitly provided.
        # Typing 'none' for an optional text field clears it (sets to NULL).
        def _clear(v): return None if isinstance(v, str) and v.lower() == "none" else v
        _p = locals()
        data = {
            field: (_clear(_p[field]) if clearable else _p[field])
            for field, clearable in self._TICKET_TYPE_SCHEMA
            if _p[field] is not None
        }
        if not data:
            await interaction.followup.send("No fields provided — nothing to update.", ephemeral=True)
            return
        from polaris.config import upsert_ticket_type
        upsert_ticket_type(type_key, data)
        self._reload_config()
        changed = ", ".join(f"`{k}`" for k in data)
        await interaction.followup.send(
            f"✅ Ticket type `{type_key}` updated ({changed}).", ephemeral=True
        )
        print(f"[ticket_type update] {interaction.user} updated '{type_key}': {data}")

    @ticket_type_group.command(name="delete", description="Delete a ticket type")
    @app_commands.describe(type_key="Ticket type to delete")
    @app_commands.autocomplete(type_key=ticket_type_autocomplete)
    async def ticket_type_delete(
        self,
        interaction: discord.Interaction,
        type_key: str,
    ):
        if not self._is_db_mode():
            await interaction.response.send_message(
                "⚠️ Ticket type management requires `CONFIG_SOURCE=db`.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        if type_key not in self.config.get("ticket_types", {}):
            await interaction.followup.send(f"Ticket type `{type_key}` not found.", ephemeral=True)
            return
        from polaris.config import delete_ticket_type_from_db
        deleted = delete_ticket_type_from_db(type_key)
        if deleted:
            self._reload_config()
            await interaction.followup.send(f"🗑️ Ticket type `{type_key}` deleted.", ephemeral=True)
            print(f"[ticket_type delete] {interaction.user} deleted '{type_key}'")
        else:
            await interaction.followup.send(
                f"Could not delete `{type_key}` — not found in database.", ephemeral=True
            )

    # ---- field subcommands ---- #

    async def _field_label_autocomplete(self, interaction: discord.Interaction, current: str):
        """Autocomplete field labels for the type_key already entered."""
        type_key = getattr(interaction.namespace, "type_key", None)
        if not type_key:
            return []
        fields = self.config.get("ticket_types", {}).get(type_key, {}).get("fields", [])
        return [
            app_commands.Choice(name=f["label"], value=f["label"])
            for f in fields
            if current.lower() in f["label"].lower()
        ][:25]

    @ticket_type_group.command(name="list", description="List all configured ticket types")
    async def ticket_type_list(self, interaction: discord.Interaction):
        ticket_types = self.config.get("ticket_types", {})
        if not ticket_types:
            await interaction.response.send_message("No ticket types configured.", ephemeral=True)
            return
        lines = ["**Configured ticket types:**"]
        for key, cfg in ticket_types.items():
            thread_mode = "thread" if cfg.get("thread") else "channel"
            fields = cfg.get("fields") or []
            field_names = ", ".join(f"`{f['label']}`" for f in fields) or "—"
            extras = []
            if cfg.get("assign_role"):     extras.append(f"role: {cfg['assign_role']}")
            if cfg.get("classified"):      extras.append(f"classified → {cfg.get('classified_role') or '?'}")
            if cfg.get("update_nickname"): extras.append("updates nickname")
            extra_str = f" | {', '.join(extras)}" if extras else ""
            lines.append(
                f"  **`{key}`** — {thread_mode} in `{cfg.get('category', '?')} / {cfg.get('channel', '?')}`{extra_str}\n"
                f"    fields: {field_names}"
            )
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @ticket_type_group.command(name="field_list", description="List all modal fields for a ticket type")
    @app_commands.describe(type_key="Ticket type to inspect")
    @app_commands.autocomplete(type_key=ticket_type_autocomplete)
    async def ticket_type_field_list(
        self,
        interaction: discord.Interaction,
        type_key: str,
    ):
        if type_key not in self.config.get("ticket_types", {}):
            await interaction.response.send_message(
                f"Ticket type `{type_key}` not found.", ephemeral=True
            )
            return
        fields = self.config["ticket_types"][type_key].get("fields") or []
        if not fields:
            await interaction.response.send_message(
                f"No fields configured for `{type_key}`.", ephemeral=True
            )
            return
        lines = [f"**Fields for `{type_key}`:**"]
        for i, f in enumerate(fields):
            req = "✅ required" if f.get("required", True) else "⬜ optional"
            lines.append(f"  `{i}` **{f['label']}** — {req} | placeholder: *{f.get('placeholder') or '—'}*")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @ticket_type_group.command(name="field_add", description="Add a modal field to a ticket type")
    @app_commands.describe(
        type_key="Ticket type to add the field to",
        label="Field label shown in the modal",
        placeholder="Hint text inside the input box",
        required="Whether the field is required (default True)",
        position="0-based position in the modal (appended at end if omitted)",
    )
    @app_commands.autocomplete(type_key=ticket_type_autocomplete)
    async def ticket_type_field_add(
        self,
        interaction: discord.Interaction,
        type_key: str,
        label: str,
        placeholder: Optional[str] = None,
        required: bool = True,
        position: Optional[int] = None,
    ):
        if not self._is_db_mode():
            await interaction.response.send_message(
                "⚠️ Field management requires `CONFIG_SOURCE=db`.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        if type_key not in self.config.get("ticket_types", {}):
            await interaction.followup.send(f"Ticket type `{type_key}` not found.", ephemeral=True)
            return
        existing_labels = [f["label"] for f in self.config["ticket_types"][type_key].get("fields") or []]
        if label in existing_labels:
            await interaction.followup.send(
                f"Field `{label}` already exists on `{type_key}`. Use `/ticket_type field_edit` to modify it.",
                ephemeral=True,
            )
            return
        if len(existing_labels) >= 5:
            await interaction.followup.send(
                "Discord modals support a maximum of **5 fields**. Remove one first.", ephemeral=True
            )
            return
        data = {"placeholder": placeholder or "", "required": required}
        if position is not None:
            data["position"] = position
        from polaris.config import upsert_ticket_field
        upsert_ticket_field(type_key, label, data)
        self._reload_config()
        await interaction.followup.send(
            f"✅ Field `{label}` added to `{type_key}`.", ephemeral=True
        )
        print(f"[field_add] {interaction.user} added field '{label}' to '{type_key}'")

    @ticket_type_group.command(name="field_edit", description="Edit an existing modal field")
    @app_commands.describe(
        type_key="Ticket type that owns the field",
        label="Current label of the field to edit",
        new_label="Rename the field to this label",
        placeholder="New placeholder text (type 'none' to clear)",
        required="Change required setting",
        position="New 0-based position in the modal",
    )
    @app_commands.autocomplete(type_key=ticket_type_autocomplete, label=_field_label_autocomplete)
    async def ticket_type_field_edit(
        self,
        interaction: discord.Interaction,
        type_key: str,
        label: str,
        new_label: Optional[str] = None,
        placeholder: Optional[str] = None,
        required: Optional[bool] = None,
        position: Optional[int] = None,
    ):
        if not self._is_db_mode():
            await interaction.response.send_message(
                "⚠️ Field management requires `CONFIG_SOURCE=db`.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        if type_key not in self.config.get("ticket_types", {}):
            await interaction.followup.send(f"Ticket type `{type_key}` not found.", ephemeral=True)
            return
        existing_labels = [f["label"] for f in self.config["ticket_types"][type_key].get("fields") or []]
        if label not in existing_labels:
            await interaction.followup.send(
                f"Field `{label}` not found on `{type_key}`. Use `/ticket_type field_list` to see current fields.",
                ephemeral=True,
            )
            return
        data = {}
        if new_label is not None:   data["label"]       = new_label
        if placeholder is not None: data["placeholder"] = None if placeholder.lower() == "none" else placeholder
        if required is not None:    data["required"]    = required
        if position is not None:    data["position"]    = position
        if not data:
            await interaction.followup.send("No fields provided — nothing to update.", ephemeral=True)
            return
        from polaris.config import upsert_ticket_field
        upsert_ticket_field(type_key, label, data)
        self._reload_config()
        changed = ", ".join(f"`{k}`" for k in data)
        await interaction.followup.send(
            f"✅ Field `{label}` on `{type_key}` updated ({changed}).", ephemeral=True
        )
        print(f"[field_edit] {interaction.user} edited field '{label}' on '{type_key}': {data}")

    @ticket_type_group.command(name="field_remove", description="Remove a modal field from a ticket type")
    @app_commands.describe(
        type_key="Ticket type to remove the field from",
        label="Label of the field to remove",
    )
    @app_commands.autocomplete(type_key=ticket_type_autocomplete, label=_field_label_autocomplete)
    async def ticket_type_field_remove(
        self,
        interaction: discord.Interaction,
        type_key: str,
        label: str,
    ):
        if not self._is_db_mode():
            await interaction.response.send_message(
                "⚠️ Field management requires `CONFIG_SOURCE=db`.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        if type_key not in self.config.get("ticket_types", {}):
            await interaction.followup.send(f"Ticket type `{type_key}` not found.", ephemeral=True)
            return
        from polaris.config import delete_ticket_field
        deleted = delete_ticket_field(type_key, label)
        if deleted:
            self._reload_config()
            await interaction.followup.send(
                f"🗑️ Field `{label}` removed from `{type_key}`.", ephemeral=True
            )
            print(f"[field_remove] {interaction.user} removed field '{label}' from '{type_key}'")
        else:
            await interaction.followup.send(
                f"Field `{label}` not found on `{type_key}`.", ephemeral=True
            )
