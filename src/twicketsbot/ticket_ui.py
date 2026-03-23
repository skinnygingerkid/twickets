import discord


class DescriptionModal(discord.ui.Modal):
    """
    Modal presented to the user when opening a ticket.
    Dynamically builds inputs from config: one description field plus any
    extra fields defined under ticket_types[type].fields (max 5 total).
    """

    def __init__(self, ticket_type: str, cog):
        cfg = cog.config["ticket_types"][ticket_type]
        label = cfg.get("button_label", ticket_type.replace("-", " ").title())
        super().__init__(title=f"Open {label} Ticket")
        self.ticket_type = ticket_type
        self.cog = cog

        default_desc = cfg.get("default_description", "")
        show_description = cfg.get("description", True)
        if show_description:
            self.description_input = discord.ui.TextInput(
                label="Description",
                placeholder=default_desc or "Describe your issue...",
                default=default_desc,
                required=not bool(default_desc),
                style=discord.TextStyle.paragraph,
            )
            self.add_item(self.description_input)
        else:
            self.description_input = None

        # Add a TextInput for each configured field (Discord max is 5 total inputs)
        self.field_inputs: list[tuple[str, discord.ui.TextInput]] = []
        for field in cfg.get("fields", []):
            inp = discord.ui.TextInput(
                label=field["label"],
                placeholder=field.get("placeholder", ""),
                required=field.get("required", True),
                style=discord.TextStyle.short,
            )
            self.add_item(inp)
            self.field_inputs.append((field["label"], inp))

    async def on_submit(self, interaction: discord.Interaction):
        if self.description_input is not None:
            description = self.description_input.value or self.cog.config["ticket_types"][self.ticket_type].get("default_description")
        else:
            description = self.cog.config["ticket_types"][self.ticket_type].get("default_description", "")
        fields = {label: inp.value for label, inp in self.field_inputs if inp.value}
        await interaction.response.defer(ephemeral=True)
        await self.cog._create_ticket(interaction, self.ticket_type, description, fields)

    def is_empty(self) -> bool:
        """Returns True if the modal has no input components (nothing to show Discord)."""
        return self.description_input is None and not self.field_inputs

class TicketButton(discord.ui.Button):
    """
    A button representing a single ticket type, shown in call-channel embeds.
    Clicking it opens the DescriptionModal for that type.
    """

    def __init__(self, ticket_type: str, cog):
        cfg = cog.config["ticket_types"][ticket_type]
        label = cfg.get("button_label", ticket_type.replace("-", " ").title())
        super().__init__(
            label=label,
            style=discord.ButtonStyle.primary,
            custom_id=f"ticket_btn_{ticket_type}",
        )
        self.ticket_type = ticket_type
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        modal = DescriptionModal(self.ticket_type, self.cog)
        if modal.is_empty():
            # No inputs to collect — skip the modal and create the ticket directly
            await interaction.response.defer(ephemeral=True)
            cfg = self.cog.config["ticket_types"][self.ticket_type]
            description = cfg.get("default_description", "")
            await self.cog._create_ticket(interaction, self.ticket_type, description)
        else:
            await interaction.response.send_modal(modal)


class TicketView(discord.ui.View):
    """
    Persistent view holding one TicketButton per ticket type.
    Deployed into call-channel embeds by /setup.
    """

    def __init__(self, ticket_types: list[str], cog):
        super().__init__(timeout=None)
        for t in ticket_types:
            self.add_item(TicketButton(t, cog))


class AssignView(discord.ui.View):
    """
    Persistent view attached to every ticket opening message.
    Provides an 'Assign to Me' button and a 'Close Ticket' button.
    """

    def __init__(self, cog=None):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="📥 Assign to Me", style=discord.ButtonStyle.success, custom_id="ticket_assign")
    async def assign(self, interaction: discord.Interaction, button: discord.ui.Button):
        msg = interaction.message
        content = msg.content or ""

        if f"Ticket raised by {interaction.user.mention}" in content:
            await interaction.response.send_message("You cannot assign a ticket you raised.", ephemeral=True)
            return

        if f"**Assigned to:** {interaction.user.mention}" in content:
            await interaction.response.send_message("You already have this ticket assigned to you.", ephemeral=True)
            return

        lines = [line for line in content.splitlines() if not line.startswith("**Assigned to:**")]
        lines.append(f"**Assigned to:** {interaction.user.mention}")
        new_content = "\n".join(lines)

        button.label = f"📥 Assigned to {interaction.user.display_name}"
        button.style = discord.ButtonStyle.secondary
        button.disabled = True
        await msg.edit(content=new_content, view=self)

        channel = interaction.channel
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

    @discord.ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.danger, custom_id="ticket_close")
    async def close_ticket_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.cog:
            await interaction.response.send_modal(CloseReasonModal(self.cog))
        else:
            await interaction.response.send_message(
                "Bot was restarted — use `/close_ticket` to close this ticket.", ephemeral=True
            )


class CloseReasonModal(discord.ui.Modal):
    """
    Modal that prompts for a mandatory close reason before archiving the ticket.
    Submitted by both the Close Ticket button and the /close_ticket slash command.
    """

    def __init__(self, cog):
        super().__init__(title="Close Ticket")
        self.cog = cog
        self.reason_input = discord.ui.TextInput(
            label="Reason for closing",
            placeholder="Briefly describe why this ticket is being closed...",
            required=True,
            style=discord.TextStyle.paragraph,
        )
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.cog._do_close_ticket(interaction, self.reason_input.value)
