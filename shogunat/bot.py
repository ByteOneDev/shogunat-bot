"""Bot principal : mascotte, structure du serveur Discord, statut, classements, annonces."""
import logging
from datetime import datetime, timezone
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import tasks

from . import config, db, minecraft

log = logging.getLogger("shogunat.bot")

SAKURA = 0xF07AB2
OR = 0xCF9D3F
ROUGE = 0xB3291C

# Salons créés par « Créer la structure ». La clé sert dans db.reglage("salons").
# lecture_seule : les membres lisent mais n'écrivent pas (le bot publie).
STRUCTURE = [
    ("⛩・Shogunat", [
        ("annonces", "📢・annonces", "Annonces officielles du Shogunat.", True),
        ("statut", "🟢・statut", "État du serveur Minecraft, mis à jour chaque minute.", True),
        ("classements", "🏆・classements", "Classements des joueurs et des clans.", True),
        ("faq", "❓・faq", "Questions fréquentes. Tape /faq pour chercher.", True),
        ("suggestions", "💡・suggestions", "Propose un mod ou une fonctionnalité avec /suggestion, vote avec 👍 / 👎.", True),
        ("tickets", "🎫・tickets", "Besoin d'aide ? Ouvre un ticket avec le menu ci-dessous.", True),
    ]),
]
CATEGORIE_STAFF = ("🔒・Staff", "staff", "📋・journal-staff", "Nouveaux tickets et suggestions (staff uniquement).")
CATEGORIE_CLANS = "⚔・Clans"

# Permissions dont le bot a besoin au quotidien (lien d'invitation « normal »)
PERMS_QUOTIDIEN = discord.Permissions(
    view_channel=True, send_messages=True, embed_links=True, attach_files=True, read_message_history=True,
    add_reactions=True, use_external_emojis=True, create_private_threads=True, send_messages_in_threads=True,
    manage_threads=True, change_nickname=True,
)
# En plus, seulement le temps de « Créer la structure » (salons, rôles, webhooks des clans)
PERMS_STRUCTURE = discord.Permissions(manage_channels=True, manage_roles=True, manage_webhooks=True)


class ShogunatBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()  # aucun intent privilégié nécessaire
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.dernier_statut = None
        self.session_http = None

    # --- démarrage ------------------------------------------------------------------

    async def setup_hook(self):
        from . import interactions
        self.session_http = aiohttp.ClientSession()
        interactions.enregistrer(self)
        enregistrer_commandes(self)
        guild = discord.Object(id=config.GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        self.boucle_statut.start()
        self.boucle_stats.start()
        self.boucle_annonces.start()

    async def close(self):
        if self.session_http:
            await self.session_http.close()
        await super().close()

    async def on_ready(self):
        log.info("Connecté en tant que %s", self.user)

    @property
    def guild(self) -> Optional[discord.Guild]:
        return self.get_guild(config.GUILD_ID)

    def salon(self, cle) -> Optional[discord.TextChannel]:
        sid = (db.reglage("salons") or {}).get(cle)
        return self.guild.get_channel(sid) if (sid and self.guild) else None

    def role_staff(self) -> Optional[discord.Role]:
        rid = config.ADMIN_ROLE_ID or db.reglage("role_staff")
        return self.guild.get_role(rid) if (rid and self.guild) else None

    async def est_admin(self, user_id) -> bool:
        guild = self.guild
        if not guild:
            return False
        try:
            membre = guild.get_member(user_id) or await guild.fetch_member(user_id)
        except discord.HTTPException:
            return False
        if membre.guild_permissions.manage_guild or membre.guild_permissions.administrator:
            return True
        staff = self.role_staff()
        return bool(staff and staff in membre.roles)

    async def journal(self, texte=None, embed=None):
        """Message dans le salon réservé au staff."""
        salon = self.salon("staff")
        if salon:
            try:
                await salon.send(content=texte, embed=embed, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException as e:
                log.warning("journal staff : %s", e)

    async def message_permanent(self, cle_salon, cle_message, **contenu):
        """Édite le message du bot gardé en mémoire, ou en publie un nouveau."""
        salon = self.salon(cle_salon)
        if not salon:
            return
        mid = db.reglage(cle_message)
        if mid:
            try:
                msg = await salon.fetch_message(mid)
                await msg.edit(**contenu)
                return
            except discord.NotFound:
                pass
        msg = await salon.send(**contenu)
        db.definir(cle_message, msg.id)

    # --- statut du serveur Minecraft ------------------------------------------------

    @tasks.loop(seconds=60)
    async def boucle_statut(self):
        st = await minecraft.statut()
        self.dernier_statut = st
        if st["en_ligne"]:
            activite = discord.Activity(type=discord.ActivityType.watching,
                                        name=f"{st['joueurs']}/{st['max']} joueurs en ligne")
            etat = discord.Status.online
        else:
            activite = discord.Activity(type=discord.ActivityType.watching, name="le serveur endormi")
            etat = discord.Status.idle
        await self.change_presence(status=etat, activity=activite)
        try:
            await self.message_permanent("statut", "msg_statut", embed=embed_statut(st))
        except discord.HTTPException as e:
            log.warning("message de statut : %s", e)

    @boucle_statut.before_loop
    async def _attendre(self):
        await self.wait_until_ready()

    # --- statistiques et classements ------------------------------------------------

    @tasks.loop(minutes=2)
    async def boucle_stats(self):
        if not (config.RCON_PORT and config.RCON_PASSWORD):
            return
        try:
            await minecraft.rafraichir_stats()
        except (minecraft.RconErreur, ValueError) as e:
            log.info("stats non rafraîchies : %s", e)
            return
        if self.boucle_stats.current_loop % 5 == 0:  # message #classements toutes les 10 minutes
            await self.publier_classements()

    @boucle_stats.before_loop
    async def _attendre_stats(self):
        await self.wait_until_ready()

    async def publier_classements(self):
        try:
            await self.message_permanent("classements", "msg_classements", embeds=embeds_classements())
        except discord.HTTPException as e:
            log.warning("message des classements : %s", e)

    # --- annonces (créées depuis le panneau) ------------------------------------------

    @tasks.loop(seconds=20)
    async def boucle_annonces(self):
        dues = db.tous("SELECT * FROM annonces WHERE envoyee_le IS NULL AND erreur IS NULL "
                       "AND (prevue_le IS NULL OR prevue_le <= ?) ORDER BY id", db.maintenant())
        for a in dues:
            await self.envoyer_annonce(a)

    @boucle_annonces.before_loop
    async def _attendre_annonces(self):
        await self.wait_until_ready()

    async def envoyer_annonce(self, a):
        erreurs = []
        embed = discord.Embed(title=a["titre"], description=a["contenu"], color=SAKURA)
        if a.get("image_url"):
            embed.set_image(url=a["image_url"])
        mention = {"everyone": "@everyone", "here": "@here"}.get(a["mention"])
        autorise = discord.AllowedMentions(everyone=bool(mention))
        try:
            if a.get("clan"):
                clan = db.un("SELECT * FROM clans WHERE slug = ?", a["clan"])
                await self.parler_en_clan(clan, contenu=mention, embed=embed, mentions=autorise)
            else:
                salon = self.salon("annonces")
                if not salon:
                    raise RuntimeError("salon #annonces non configuré (onglet Structure du panneau)")
                embed.set_footer(text=self.user.display_name if self.user else "Shogunat")
                await salon.send(content=mention, embed=embed, allowed_mentions=autorise)
        except Exception as e:  # noqa: BLE001 — l'erreur est montrée dans le panneau
            erreurs.append(f"Discord : {e}")
        if a["en_jeu"]:
            try:
                await minecraft.dire_en_jeu(a["titre"], a["contenu"])
            except minecraft.RconErreur as e:
                erreurs.append(f"En jeu : {e}")
        db.maj("annonces", "id", a["id"], {"envoyee_le": db.maintenant(), "erreur": " · ".join(erreurs) or None})

    # --- mascottes ------------------------------------------------------------------

    async def parler_en_clan(self, clan, contenu=None, embed=None, mentions=None):
        """Publie dans le salon du clan sous le nom et l'avatar de sa mascotte (webhook)."""
        if not clan or not clan.get("webhook_url"):
            raise RuntimeError("salon du clan non créé (onglet Structure du panneau)")
        webhook = discord.Webhook.from_url(clan["webhook_url"], session=self.session_http)
        await webhook.send(content=contenu, embed=embed, username=clan.get("mascotte_nom") or clan["nom"],
                           avatar_url=clan.get("mascotte_avatar") or discord.utils.MISSING,
                           allowed_mentions=mentions or discord.AllowedMentions.none())

    async def appliquer_mascotte(self, nom, avatar_url):
        """Surnom du bot sur le serveur + avatar du bot (l'avatar est limité à 2 changements/heure par Discord)."""
        resultats = []
        if nom and self.guild:
            try:
                await self.guild.me.edit(nick=nom[:32])
                resultats.append("surnom mis à jour")
            except discord.HTTPException as e:
                resultats.append(f"surnom : {e}")
        if avatar_url:
            try:
                async with self.session_http.get(avatar_url) as r:
                    r.raise_for_status()
                    image = await r.read()
                await self.user.edit(avatar=image)
                resultats.append("avatar mis à jour")
            except Exception as e:  # noqa: BLE001
                resultats.append(f"avatar : {e}")
        return resultats

    # --- structure du serveur Discord -------------------------------------------------

    def permissions_manquantes(self, perms: discord.Permissions):
        if not self.guild:
            return ["bot absent du serveur"]
        actuelles = self.guild.me.guild_permissions
        if actuelles.administrator:
            return []
        return [nom for nom, voulu in perms if voulu and not getattr(actuelles, nom)]

    async def creer_structure(self):
        """Crée (ou retrouve) rôles, salons et webhooks. Peut être relancé sans rien dupliquer."""
        guild = self.guild
        manque = self.permissions_manquantes(PERMS_STRUCTURE)
        if manque:
            raise PermissionError(manque)
        rapport = []
        raison = "Structure Shogunat (panneau d'admin)"
        moi = guild.me

        async def role(nom, couleur=None):
            r = discord.utils.get(guild.roles, name=nom)
            if r:
                return r
            rapport.append(f"rôle « {nom} » créé")
            return await guild.create_role(name=nom, colour=couleur or discord.Colour.default(), reason=raison)

        async def categorie(nom, overwrites=None):
            c = discord.utils.get(guild.categories, name=nom)
            if c:
                return c
            rapport.append(f"catégorie « {nom} » créée")
            return await guild.create_category(nom, overwrites=overwrites or {}, reason=raison)

        async def salon(cat, nom, sujet, overwrites, cle_existante=None):
            existant = guild.get_channel(cle_existante) if cle_existante else None
            existant = existant or discord.utils.get(cat.text_channels, name=nom)
            if existant:
                return existant
            rapport.append(f"salon #{nom} créé")
            return await guild.create_text_channel(nom, category=cat, topic=sujet, overwrites=overwrites, reason=raison)

        staff = self.role_staff()
        if not staff:
            staff = await role("Staff Shogunat", discord.Colour(OR))
            db.definir("role_staff", staff.id)

        bot_ok = discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True,
                                             manage_threads=True, create_private_threads=True,
                                             send_messages_in_threads=True, manage_webhooks=True)
        lecture = {guild.default_role: discord.PermissionOverwrite(send_messages=False, add_reactions=True,
                                                                   create_public_threads=False,
                                                                   create_private_threads=False),
                   moi: bot_ok, staff: discord.PermissionOverwrite(send_messages=True)}
        salons = db.reglage("salons") or {}
        for nom_cat, liste in STRUCTURE:
            cat = await categorie(nom_cat)
            for cle, nom, sujet, _ in liste:
                ow = dict(lecture)
                if cle == "tickets":  # les membres écrivent seulement dans leur fil privé
                    ow[guild.default_role] = discord.PermissionOverwrite(send_messages=False,
                                                                         send_messages_in_threads=True)
                salons[cle] = (await salon(cat, nom, sujet, ow, salons.get(cle))).id

        prive = {guild.default_role: discord.PermissionOverwrite(view_channel=False),
                 moi: bot_ok, staff: discord.PermissionOverwrite(view_channel=True, send_messages=True)}
        cat_staff = await categorie(CATEGORIE_STAFF[0], prive)
        salons["staff"] = (await salon(cat_staff, CATEGORIE_STAFF[2], CATEGORIE_STAFF[3], prive,
                                       salons.get("staff"))).id
        db.definir("salons", salons)

        cat_clans = await categorie(CATEGORIE_CLANS)
        for clan in db.tous("SELECT * FROM clans ORDER BY slug"):
            r = guild.get_role(clan["role_id"] or 0) or await role(clan["nom"], discord.Colour(int(clan["couleur"][1:], 16)))
            ow = {guild.default_role: discord.PermissionOverwrite(view_channel=False),
                  r: discord.PermissionOverwrite(view_channel=True, send_messages=True),
                  staff: discord.PermissionOverwrite(view_channel=True, send_messages=True), moi: bot_ok}
            nom_salon = f"{clan['kanji'] or ''}・{clan['slug']}".lstrip("・")
            s = await salon(cat_clans, nom_salon, f"Salon du clan {clan['nom']}.", ow, clan["salon_id"])
            champs = {"role_id": r.id, "salon_id": s.id}
            if not clan["webhook_url"]:
                wh = await s.create_webhook(name=f"Mascotte {clan['nom']}", reason=raison)
                champs["webhook_url"] = wh.url
                rapport.append(f"mascotte du clan {clan['nom']} prête")
            db.maj("clans", "slug", clan["slug"], champs)

        from . import interactions
        await interactions.publier_panneau_tickets(self)
        await interactions.publier_faq(self)
        await self.publier_classements()
        rapport.append("messages des salons publiés")
        return rapport

    def lien_invitation(self, structure=False):
        perms = discord.Permissions(PERMS_QUOTIDIEN.value | (PERMS_STRUCTURE.value if structure else 0))
        return discord.utils.oauth_url(config.DISCORD_CLIENT_ID, permissions=perms, guild=discord.Object(config.GUILD_ID),
                                       scopes=("bot", "applications.commands"))


# --- contenus ---------------------------------------------------------------------------

def embed_statut(st):
    if st["en_ligne"]:
        e = discord.Embed(title="🟢 Serveur en ligne", color=0x4ADE80)
        e.add_field(name="Joueurs", value=f"**{st['joueurs']}** / {st['max']}")
        e.add_field(name="Version", value=st["version"] or "?")
        e.add_field(name="Ping", value=f"{st['latence']} ms")
        if st["noms"]:
            e.add_field(name="Connectés", value=discord.utils.escape_markdown(", ".join(st["noms"]))[:1024], inline=False)
    else:
        e = discord.Embed(title="🔴 Serveur hors ligne", color=ROUGE,
                          description="Le serveur est éteint ou redémarre. Réessaie dans quelques minutes.")
    e.add_field(name="Adresse", value=f"`{config.MC_ADRESSE}`", inline=False)
    e.set_footer(text="Mis à jour")
    e.timestamp = discord.utils.utcnow()
    return e


def embeds_classements(categories=None):
    stats = minecraft.stats_en_cache()
    embeds = []
    for cat in [c for c in (categories or minecraft.CATEGORIES) if c in minecraft.CATEGORIES]:
        cle, titre, fmt = minecraft.CATEGORIES[cat]
        lignes = [f"{medaille(i)} **{discord.utils.escape_markdown(j['n'])}** — {fmt(j.get(cle) or 0)}" for i, j in enumerate(minecraft.top(stats, cat))]
        embeds.append(discord.Embed(title=titre, description="\n".join(lignes) or "Aucun joueur pour l'instant.", color=OR))
    if categories is None or "clans" in categories:
        clans = {c["slug"]: c for c in db.tous("SELECT * FROM clans")}
        lignes = []
        for i, c in enumerate(minecraft.classement_clans(stats)):
            info = clans.get(c["slug"], {"nom": c["slug"].capitalize(), "kanji": ""})
            lignes.append(f"{medaille(i)} **{info['nom']}** {info.get('kanji') or ''} — {c['kills']} kills · "
                          f"${minecraft.format_argent(c['argent'])} · {c['membres']} membre(s)")
        e = discord.Embed(title="⛩️ Classement des clans", description="\n".join(lignes) or "Aucun clan pour l'instant.",
                          color=SAKURA)
        if stats.get("maj_le"):
            e.set_footer(text="Données du serveur")
            e.timestamp = datetime.fromtimestamp(stats["maj_le"], tz=timezone.utc)
        embeds.append(e)
    return embeds


def medaille(i):
    return ("🥇", "🥈", "🥉")[i] if i < 3 else f"`{i + 1}.`"


# --- commandes slash ----------------------------------------------------------------------

def enregistrer_commandes(bot: ShogunatBot):
    @bot.tree.command(name="statut", description="État du serveur Minecraft")
    async def cmd_statut(inter: discord.Interaction):
        await inter.response.defer()
        await inter.followup.send(embed=embed_statut(await minecraft.statut()))

    choix = [app_commands.Choice(name=n, value=v) for n, v in
             (("Kills", "kills"), ("Morts", "morts"), ("Argent", "argent"), ("Temps de jeu", "temps"), ("Clans", "clans"))]

    @bot.tree.command(name="classement", description="Classements du Shogunat")
    @app_commands.describe(categorie="Un classement précis (par défaut : tous)")
    @app_commands.choices(categorie=choix)
    async def cmd_classement(inter: discord.Interaction, categorie: Optional[app_commands.Choice[str]] = None):
        await inter.response.send_message(embeds=embeds_classements([categorie.value] if categorie else None))

    @bot.tree.command(name="ip", description="Adresse du serveur Minecraft")
    async def cmd_ip(inter: discord.Interaction):
        await inter.response.send_message(f"⛩️ Adresse du serveur : `{config.MC_ADRESSE}`", ephemeral=True)



