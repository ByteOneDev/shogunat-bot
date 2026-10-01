"""Tickets (fils privés), FAQ et suggestions."""
import logging
from typing import Optional

import discord
from discord import app_commands

from . import db

log = logging.getLogger("shogunat.interactions")

SAKURA = 0xFF5FAE
COULEURS_SUGGESTION = {"en_attente": 0xB84DFF, "acceptee": 0x4ADE80, "refusee": 0xB3291C, "faite": 0x2980B9}
LIBELLES_SUGGESTION = {"en_attente": "⏳ En attente", "acceptee": "✅ Acceptée", "refusee": "❌ Refusée",
                       "faite": "🎉 Ajoutée au serveur"}
TYPES_SUGGESTION = {"mod": "🧩 Mod", "fonctionnalite": "✨ Fonctionnalité"}

CATEGORIES_TICKET = [
    ("aide", "🆘", "Besoin d'aide", "Installation, connexion, gameplay"),
    ("joueur", "⚠️", "Signaler un joueur", "Triche, grief, comportement"),
    ("bug", "🐛", "Bug", "Un problème sur le serveur ou un mod"),
    ("boutique", "💰", "Boutique / dons", "Paiement, récompense non reçue"),
    ("autre", "📨", "Autre", "Toute autre demande"),
]
TICKETS_MAX_PAR_JOUEUR = 2


# --- tickets ------------------------------------------------------------------------------

class PanneauTickets(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(custom_id="ticket:categorie", placeholder="🎫 Ouvrir un ticket…",
                       options=[discord.SelectOption(label=nom, value=cle, emoji=emoji, description=desc)
                                for cle, emoji, nom, desc in CATEGORIES_TICKET])
    async def choisir(self, inter: discord.Interaction, select: discord.ui.Select):
        ouverts = db.un("SELECT COUNT(*) AS n FROM tickets WHERE user_id = ? AND statut = 'ouvert'", inter.user.id)["n"]
        if ouverts >= TICKETS_MAX_PAR_JOUEUR:
            await inter.response.send_message("Tu as déjà des tickets ouverts : réponds dans ceux-là d'abord 🙏",
                                              ephemeral=True)
            return
        await inter.response.send_modal(ModalTicket(select.values[0]))


class ModalTicket(discord.ui.Modal):
    sujet = discord.ui.TextInput(label="Sujet", max_length=100, placeholder="En quelques mots")
    details = discord.ui.TextInput(label="Explique ton problème", style=discord.TextStyle.paragraph, max_length=1500)

    def __init__(self, categorie):
        self.categorie = next(c for c in CATEGORIES_TICKET if c[0] == categorie)
        super().__init__(title=f"Ticket : {self.categorie[2]}")

    async def on_submit(self, inter: discord.Interaction):
        bot = inter.client
        salon = bot.salon("tickets")
        if not salon:
            await inter.response.send_message("Les tickets ne sont pas encore configurés.", ephemeral=True)
            return
        cle, emoji, nom, _ = self.categorie
        fil = await salon.create_thread(name=f"{emoji} {inter.user.display_name} · {nom}"[:100],
                                        type=discord.ChannelType.private_thread, invitable=False)
        await fil.add_user(inter.user)
        tid = db.executer("INSERT INTO tickets (fil_id, user_id, user_nom, categorie, sujet, ouvert_le) "
                          "VALUES (?, ?, ?, ?, ?, ?)", fil.id, inter.user.id, inter.user.display_name, cle,
                          str(self.sujet), db.maintenant())
        embed = discord.Embed(title=f"{emoji} {self.sujet}", description=str(self.details), color=SAKURA)
        embed.set_author(name=inter.user.display_name, icon_url=inter.user.display_avatar.url)
        embed.set_footer(text=f"Ticket n°{tid} · {nom}")
        staff = bot.role_staff()
        await fil.send(content=f"{inter.user.mention} {staff.mention if staff else ''}\nUn membre du staff va te répondre ici.",
                       embed=embed, view=VueTicket(),
                       allowed_mentions=discord.AllowedMentions(users=True, roles=True))
        await bot.journal(f"🎫 Nouveau ticket n°{tid} ({nom}) de **{inter.user.display_name}** : {fil.mention}")
        await inter.response.send_message(f"Ton ticket est ouvert : {fil.mention}", ephemeral=True)


class VueTicket(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Fermer le ticket", emoji="🔒", style=discord.ButtonStyle.secondary,
                       custom_id="ticket:fermer")
    async def fermer(self, inter: discord.Interaction, _):
        ticket = db.un("SELECT * FROM tickets WHERE fil_id = ?", inter.channel_id)
        if not ticket or ticket["statut"] != "ouvert":
            await inter.response.send_message("Ce ticket est déjà fermé.", ephemeral=True)
            return
        if inter.user.id != ticket["user_id"] and not await inter.client.est_admin(inter.user.id):
            await inter.response.send_message("Seul l'auteur du ticket ou le staff peut le fermer.", ephemeral=True)
            return
        await inter.response.send_message(f"🔒 Ticket fermé par {inter.user.mention}.")
        await fermer_ticket(inter.client, ticket, inter.user.display_name)


async def fermer_ticket(bot, ticket, par):
    db.maj("tickets", "id", ticket["id"], {"statut": "ferme", "ferme_le": db.maintenant(), "ferme_par": par})
    fil = bot.guild.get_thread(ticket["fil_id"]) if bot.guild else None
    if fil is None and bot.guild:
        try:
            fil = await bot.fetch_channel(ticket["fil_id"])
        except discord.HTTPException:
            return
    if fil:
        try:
            await fil.edit(archived=True, locked=True)
        except discord.HTTPException:
            await fil.edit(archived=True)


async def publier_panneau_tickets(bot):
    embed = discord.Embed(
        title="🎫 Support du Shogunat",
        description="Choisis une catégorie dans le menu ci-dessous : un fil privé s'ouvre entre toi et le staff.\n\n"
                    + "\n".join(f"{emoji} **{nom}** : {desc}" for _, emoji, nom, desc in CATEGORIES_TICKET),
        color=SAKURA)
    await bot.message_permanent("tickets", "msg_tickets", embed=embed, view=PanneauTickets())


# --- FAQ ----------------------------------------------------------------------------------

def embeds_faq():
    questions = db.tous("SELECT * FROM faq ORDER BY ordre, id")
    if not questions:
        return [discord.Embed(title="❓ FAQ", description="Aucune question pour l'instant.", color=SAKURA)]
    embeds = []
    for i in range(0, len(questions), 8):
        e = discord.Embed(title="❓ Questions fréquentes" if i == 0 else None, color=SAKURA)
        for q in questions[i:i + 8]:
            e.add_field(name=q["question"][:256], value=q["reponse"][:1024], inline=False)
        embeds.append(e)
    return embeds


async def publier_faq(bot):
    """Remplace les messages de #faq par la FAQ à jour."""
    salon = bot.salon("faq")
    if not salon:
        raise RuntimeError("salon #faq non configuré (onglet Structure du panneau)")
    for mid in db.reglage("msg_faq", []):
        try:
            await (await salon.fetch_message(mid)).delete()
        except discord.HTTPException:
            pass
    ids = []
    embeds = embeds_faq()
    for i in range(0, len(embeds), 10):
        ids.append((await salon.send(embeds=embeds[i:i + 10])).id)
    db.definir("msg_faq", ids)


# --- suggestions --------------------------------------------------------------------------

def embed_suggestion(s):
    e = discord.Embed(title=f"{TYPES_SUGGESTION.get(s['type'], s['type'])} · {s['titre']}",
                      description=s["description"], color=COULEURS_SUGGESTION.get(s["statut"], SAKURA))
    if s.get("lien"):
        e.add_field(name="Lien", value=s["lien"][:1024], inline=False)
    e.add_field(name="Statut", value=LIBELLES_SUGGESTION.get(s["statut"], s["statut"]))
    if s.get("note_admin"):
        e.add_field(name="Réponse du staff", value=s["note_admin"][:1024], inline=False)
    e.set_footer(text=f"Suggestion n°{s['id']} de {s['user_nom']} · vote avec 👍 / 👎")
    return e


class ModalSuggestion(discord.ui.Modal):
    titre = discord.ui.TextInput(label="Titre", max_length=100, placeholder="Ex. : ajouter le mod Create Aeronautics")
    description = discord.ui.TextInput(label="Pourquoi ?", style=discord.TextStyle.paragraph, max_length=1500)
    lien = discord.ui.TextInput(label="Lien (CurseForge, Modrinth…) — facultatif", required=False, max_length=300)

    def __init__(self, type_):
        self.type_ = type_
        super().__init__(title="Proposer un mod" if type_ == "mod" else "Proposer une fonctionnalité")

    async def on_submit(self, inter: discord.Interaction):
        bot = inter.client
        salon = bot.salon("suggestions")
        if not salon:
            await inter.response.send_message("Les suggestions ne sont pas encore configurées.", ephemeral=True)
            return
        sid = db.executer("INSERT INTO suggestions (user_id, user_nom, type, titre, description, lien, creee_le) "
                          "VALUES (?, ?, ?, ?, ?, ?, ?)", inter.user.id, inter.user.display_name, self.type_,
                          str(self.titre), str(self.description), str(self.lien) or None, db.maintenant())
        s = db.un("SELECT * FROM suggestions WHERE id = ?", sid)
        msg = await salon.send(embed=embed_suggestion(s))
        for emoji in ("👍", "👎"):
            await msg.add_reaction(emoji)
        db.maj("suggestions", "id", sid, {"message_id": msg.id})
        await bot.journal(f"💡 Nouvelle suggestion n°{sid} de **{inter.user.display_name}** : {s['titre']} {msg.jump_url}")
        await inter.response.send_message(f"Merci ! Ta suggestion est publiée : {msg.jump_url}", ephemeral=True)


async def maj_suggestion(bot, sid, statut, note):
    db.maj("suggestions", "id", sid, {"statut": statut, "note_admin": note or None})
    s = db.un("SELECT * FROM suggestions WHERE id = ?", sid)
    salon = bot.salon("suggestions")
    if salon and s["message_id"]:
        try:
            await (await salon.fetch_message(s["message_id"])).edit(embed=embed_suggestion(s))
        except discord.HTTPException as e:
            log.warning("suggestion %s : %s", sid, e)
    try:  # prévenir l'auteur en message privé (peut être refusé par ses réglages)
        user = await bot.fetch_user(s["user_id"])
        await user.send(f"Ta suggestion « {s['titre']} » sur le Shogunat : **{LIBELLES_SUGGESTION[statut]}**"
                        + (f"\n> {note}" if note else ""))
    except discord.HTTPException:
        pass
    return s


# --- enregistrement ------------------------------------------------------------------------

def enregistrer(bot):
    bot.add_view(PanneauTickets())
    bot.add_view(VueTicket())

    async def autocomplete_faq(_inter: discord.Interaction, courant: str):
        questions = db.tous("SELECT id, question FROM faq ORDER BY ordre, id")
        courant = courant.lower()
        return [app_commands.Choice(name=q["question"][:100], value=str(q["id"]))
                for q in questions if courant in q["question"].lower()][:25]

    @bot.tree.command(name="faq", description="Questions fréquentes")
    @app_commands.describe(question="Cherche une question")
    @app_commands.autocomplete(question=autocomplete_faq)
    async def cmd_faq(inter: discord.Interaction, question: Optional[str] = None):
        q = db.un("SELECT * FROM faq WHERE id = ?", int(question)) if question and question.isdigit() else None
        if q:
            await inter.response.send_message(embed=discord.Embed(title=f"❓ {q['question']}", description=q["reponse"],
                                                                  color=SAKURA))
        else:
            await inter.response.send_message(embeds=embeds_faq()[:10], ephemeral=True)

    @bot.tree.command(name="suggestion", description="Proposer un mod ou une fonctionnalité au staff")
    @app_commands.describe(type="Ce que tu proposes")
    @app_commands.choices(type=[app_commands.Choice(name="Un mod", value="mod"),
                                app_commands.Choice(name="Une fonctionnalité", value="fonctionnalite")])
    async def cmd_suggestion(inter: discord.Interaction, type: app_commands.Choice[str]):  # noqa: A002
        await inter.response.send_modal(ModalSuggestion(type.value))

    async def compter_vote(payload: discord.RawReactionActionEvent, delta):
        if payload.user_id == bot.user.id or str(payload.emoji) not in ("👍", "👎"):
            return
        s = db.un("SELECT id FROM suggestions WHERE message_id = ?", payload.message_id)
        if s:
            col = "pour" if str(payload.emoji) == "👍" else "contre"
            db.executer(f"UPDATE suggestions SET {col} = MAX(0, {col} + ?) WHERE id = ?", delta, s["id"])

    @bot.event
    async def on_raw_reaction_add(payload):
        await compter_vote(payload, 1)

    @bot.event
    async def on_raw_reaction_remove(payload):
        await compter_vote(payload, -1)
