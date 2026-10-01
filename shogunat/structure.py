"""Structure du serveur Discord, tirée du lore du Shogunat : catégories, salons, rôles, permissions, nettoyage.

Commandes (staff uniquement : admin, modérateur ou rôle Staff Shogunat) :
  /structure apercu      ce qui manque, ce qui serait renommé ou déplacé, les anciens salons
  /structure ranger      applique la structure du Shogunat (avec confirmation)
  /structure nettoyer    archiver ou supprimer les anciens salons (avec confirmation)
  /structure supprimer   supprimer un salon précis (avec confirmation)
  /structure visibilite  rendre un salon public, en lecture seule, réservé à un clan ou au staff
  /structure salon-clan  ajouter un salon privé à un clan
  /banniere donner       donner à un membre le rôle de son clan (retire l'ancien)
  /banniere retirer      lui retirer son rôle de clan
Le bot n'a besoin de « Gérer les salons / les rôles / les webhooks » que pendant ces opérations.
"""
import logging
from typing import Optional

import discord
from discord import app_commands

from . import config, db

log = logging.getLogger("shogunat.structure")
RAISON = "Structure du Shogunat"

PUBLIC, LECTURE, TICKETS, STAFF, CLAN = "public", "lecture", "tickets", "staff", "clan"
TEXTE, VOCAL = "texte", "vocal"

# (clé, nom, accès de la catégorie, [(clé, nom, sujet, accès, type)])
CATEGORIES = [
    ("ile", "🌸・Île des Cerisiers", PUBLIC, [
        ("reglement", "⛩・porte-torii", "Ici commence ton voyage : le Code du Bushido et les quatre bannières de l'archipel.", LECTURE, TEXTE),
        ("annonces", "📯・cor-du-shogun", "Quand le cor du Shogun retentit, tout l'archipel l'entend. Annonces officielles.", LECTURE, TEXTE),
        ("statut", "🏮・lanterne-du-portail", "La lanterne brille quand le portail vers l'archipel est ouvert. État du serveur, chaque minute.", LECTURE, TEXTE),
        ("faq", "📜・parchemins", "Les réponses consignées par les scribes du Temple. Tape /faq pour chercher.", LECTURE, TEXTE),
        ("tickets", "🎐・doleances", "Présente ta doléance au Bakufu : un fil privé s'ouvre entre toi et le staff.", TICKETS, TEXTE),
    ]),
    ("temple", "🏯・Temple du Conseil", PUBLIC, [
        ("general", "🍵・maison-de-the", "Sol neutre et sacré : on y parle de tout, on n'y tire jamais la lame.", PUBLIC, TEXTE),
        ("diplomatie", "🕊・table-des-traites", "Alliances, griefs et traités entre les clans. Ici, la parole remplace l'acier.", PUBLIC, TEXTE),
        ("classements", "🏆・tableau-d-honneur", "Les plus grands guerriers et les clans les plus puissants de l'archipel.", LECTURE, TEXTE),
        ("suggestions", "🪶・requetes-au-shogun", "Propose un mod ou une fonctionnalité avec /suggestion, puis vote avec 👍 / 👎.", LECTURE, TEXTE),
        ("galerie", "🖼・galerie-des-batisseurs", "Forteresses, palais sur pilotis, machines et rizières : montre tes œuvres.", PUBLIC, TEXTE),
        ("vocal", "🍵 Jardin de thé", "", PUBLIC, VOCAL),
    ]),
    ("plaines", "⚔・Plaines de Sang", PUBLIC, [
        ("guerre", "📣・proclamations", "Seul le Shogun proclame la guerre et la paix. Que nul ne prétende l'ignorer.", LECTURE, TEXTE),
        ("vocal_guerre", "⚔ Champ de bataille", "", PUBLIC, VOCAL),
    ]),
    ("bakufu", "🔒・Bakufu", STAFF, [
        ("staff", "📋・registre-du-bakufu", "Journal du bot : tickets, suggestions et changements de la structure.", STAFF, TEXTE),
        ("tribunal", "⚖・tribunal-du-shogun", "Sanctions, Arène de Pénitence et décisions du staff.", STAFF, TEXTE),
        ("vocal_staff", "🔒 Conseil restreint", "", STAFF, VOCAL),
    ]),
]

# Chaque clan reçoit sa catégorie privée : un salon de discussion (où parle sa mascotte),
# une salle de stratégie et un salon vocal, nommés d'après son lore.
CLANS_LORE = {
    "akamatsu": {"emoji": "🔥", "titre": "Clan du Feu", "devise": "Le fer ne ment jamais.",
                 "salon": ("forge-ecarlate", "La forge du bastion ne s'éteint jamais. Discussions du clan Akamatsu."),
                 "strategie": ("salle-de-guerre", "Plans de bataille. Ce qui se dit ici reste entre les murs de pierre sombre."),
                 "vocal": "Bastion de pierre"},
    "mizuki": {"emoji": "🌊", "titre": "Clan de l'Eau", "devise": "L'eau contourne la pierre, puis l'emporte.",
               "salon": ("port-d-argent", "Les lanternes d'argent se reflètent sur les canaux. Discussions du clan Mizuki."),
               "strategie": ("salle-des-cartes", "Traités, routes commerciales et coups préparés dix tours à l'avance."),
               "vocal": "Palais sur pilotis"},
    "kurogane": {"emoji": "🌑", "titre": "Clan de l'Ombre", "devise": "Ce que l'on voit n'est jamais ce qui est.",
                 "salon": ("jardin-de-nuit", "Sous la lanterne violette. Discussions du clan Kurogane."),
                 "strategie": ("chambre-secrete", "Ce qui compte se trouve toujours dessous. Secrets et stratégie du clan."),
                 "vocal": "Gorge cachée"},
    "shinrin": {"emoji": "🌿", "titre": "Clan de la Terre", "devise": "Tout ce qui tombe nourrit ce qui pousse.",
                "salon": ("sanctuaire-des-kami", "Sous la mousse et les torii. Discussions du clan Shinrin."),
                "strategie": ("cercle-des-anciens", "On pense en saisons et en siècles. Projets et stratégie du clan."),
                "vocal": "Clairière moussue"},
}
ARCHIVES = ("archives", "🗃・Archives du Shogunat")


# --- droits ------------------------------------------------------------------------------

async def est_staff(bot, membre) -> bool:
    """Admin, modérateur (peut exclure temporairement / gérer les salons) ou rôle Staff Shogunat."""
    if not isinstance(membre, discord.Member):
        return False
    p = membre.guild_permissions
    if p.administrator or p.manage_guild or p.moderate_members or p.manage_channels:
        return True
    staff = bot.role_staff()
    return bool(staff and staff in membre.roles)


def _filtrer(po: discord.PermissionOverwrite, perms_bot: discord.Permissions):
    """Discord refuse qu'un bot accorde ou retire une permission qu'il n'a pas lui-même : on l'omet."""
    if perms_bot.administrator:
        return po
    return discord.PermissionOverwrite(**{nom: valeur for nom, valeur in po
                                          if valeur is not None and getattr(perms_bot, nom, False)})


def overwrites(bot, acces, clan_role=None):
    guild = bot.guild
    perms_bot = guild.me.guild_permissions
    po = discord.PermissionOverwrite
    ow = {guild.me: po(view_channel=True, send_messages=True, embed_links=True, read_message_history=True,
                       manage_threads=True, create_private_threads=True, send_messages_in_threads=True,
                       manage_webhooks=True, connect=True)}
    staff = bot.role_staff()
    if staff:
        ow[staff] = po(view_channel=True, send_messages=True, connect=True, speak=True)
    tous = guild.default_role
    if acces == LECTURE:
        ow[tous] = po(send_messages=False, add_reactions=True, create_public_threads=False, create_private_threads=False)
    elif acces == TICKETS:
        ow[tous] = po(send_messages=False, send_messages_in_threads=True, create_public_threads=False)
    elif acces == STAFF:
        ow[tous] = po(view_channel=False)
    elif acces == CLAN:
        ow[tous] = po(view_channel=False)
        if clan_role:
            ow[clan_role] = po(view_channel=True, send_messages=True, read_message_history=True, connect=True, speak=True)
    return {cible: _filtrer(p, perms_bot) for cible, p in ow.items()}


def _meme_ow(actuels, voulus):
    a = {c.id: p.pair() for c, p in actuels.items()}
    b = {c.id: p.pair() for c, p in voulus.items()}
    return a == b


def _norm(nom, type_):
    return nom.lower().replace(" ", "-") if type_ == TEXTE else nom


# --- application de la structure -----------------------------------------------------------

async def ranger(bot, appliquer=True):
    """Crée ou retrouve chaque catégorie, salon et rôle du lore. Avec appliquer=False : simple liste des actions."""
    guild = bot.guild
    actions = []
    salons = db.reglage("salons") or {}
    categories = db.reglage("categories") or {}

    async def role(nom, couleur, id_connu=None):
        r = guild.get_role(id_connu or 0) or discord.utils.get(guild.roles, name=nom)
        if r is None:
            actions.append(f"Créer le rôle « {nom} »")
            if appliquer:
                r = await guild.create_role(name=nom, colour=couleur, hoist=True, mentionable=True, reason=RAISON)
        return r

    staff = bot.role_staff() or await role("Staff Shogunat", discord.Colour(0xFFB3DC))
    if staff and appliquer:
        db.definir("role_staff", staff.id)

    async def categorie(cle, nom, acces, clan_role=None):
        c = guild.get_channel(categories.get(cle) or 0) or discord.utils.get(guild.categories, name=nom)
        ow = overwrites(bot, acces, clan_role)
        if c is None:
            actions.append(f"Créer la catégorie « {nom} »")
            if appliquer:
                c = await guild.create_category(nom, overwrites=ow, reason=RAISON)
        else:
            modifs = {}
            if c.name != nom:
                modifs["name"] = nom
                actions.append(f"Renommer la catégorie « {c.name} » en « {nom} »")
            if not _meme_ow(c.overwrites, ow):
                modifs["overwrites"] = ow
                actions.append(f"Régler les permissions de « {nom} »")
            if modifs and appliquer:
                await c.edit(**modifs, reason=RAISON)
        if c is not None:
            categories[cle] = c.id
        return c

    async def salon(cle, nom, sujet, acces, type_, cat, nom_cat, clan_role=None, id_connu=None):
        existant = guild.get_channel(id_connu or salons.get(cle) or 0)
        if existant is None:
            liste = guild.text_channels if type_ == TEXTE else guild.voice_channels
            existant = discord.utils.find(lambda ch: _norm(ch.name, type_) == _norm(nom, type_), liste)
        ow = overwrites(bot, acces, clan_role)
        if existant is None:
            actions.append(f"Créer {'#' if type_ == TEXTE else '🔊 '}{nom} dans « {nom_cat} »")
            if appliquer:
                if type_ == TEXTE:
                    existant = await guild.create_text_channel(nom, category=cat, topic=sujet or None, overwrites=ow, reason=RAISON)
                else:
                    existant = await guild.create_voice_channel(nom, category=cat, overwrites=ow, reason=RAISON)
        else:
            modifs = {}
            if _norm(existant.name, type_) != _norm(nom, type_):
                modifs["name"] = nom
                actions.append(f"Renommer « {existant.name} » en « {nom} »")
            if cat is None or existant.category_id != cat.id:  # catégorie pas encore créée (aperçu) ou différente
                modifs["category"] = cat
                actions.append(f"Déplacer « {nom} » vers « {nom_cat} »")
            if type_ == TEXTE and (existant.topic or "") != (sujet or ""):
                modifs["topic"] = sujet or None
            if not _meme_ow(existant.overwrites, ow):
                modifs["overwrites"] = ow
                actions.append(f"Régler les permissions de « {nom} »")
            if modifs and appliquer:
                if modifs.get("category", 0) is None:
                    modifs.pop("category")
                await existant.edit(**modifs, reason=RAISON)
        if existant is not None:
            salons[cle] = existant.id
        return existant

    for cle_cat, nom_cat, acces_cat, liste in CATEGORIES:
        cat = await categorie(cle_cat, nom_cat, acces_cat)
        for cle, nom, sujet, acces, type_ in liste:
            await salon(cle, nom, sujet, acces, type_, cat, nom_cat)

    for clan in db.tous("SELECT * FROM clans ORDER BY slug"):
        slug, lore = clan["slug"], CLANS_LORE.get(clan["slug"])
        if not lore:
            continue
        r = await role(clan["nom"], discord.Colour(int(clan["couleur"][1:], 16)), clan["role_id"])
        nom_cat = f"{lore['emoji']}・Clan {clan['nom']} {clan['kanji'] or ''}".strip()
        cat = await categorie(f"clan:{slug}", nom_cat, CLAN, r)
        discussion = await salon(f"clan:{slug}", f"{lore['emoji']}・{lore['salon'][0]}",
                                 f"« {lore['devise']} » {lore['salon'][1]}", CLAN, TEXTE, cat, nom_cat, r, clan["salon_id"])
        await salon(f"clan:{slug}:strategie", f"🗺・{lore['strategie'][0]}", lore["strategie"][1], CLAN, TEXTE, cat, nom_cat, r)
        await salon(f"clan:{slug}:vocal", f"{lore['emoji']} {lore['vocal']}", "", CLAN, VOCAL, cat, nom_cat, r)
        if appliquer:
            champs = {"role_id": r.id if r else None, "salon_id": discussion.id if discussion else None}
            if discussion and not clan["webhook_url"]:
                wh = await discussion.create_webhook(name=f"Mascotte {clan['nom']}", reason=RAISON)
                champs["webhook_url"] = wh.url
                actions.append(f"Mascotte du clan {clan['nom']} prête")
            db.maj("clans", "slug", slug, champs)
        elif not clan["webhook_url"]:
            actions.append(f"Préparer la mascotte du clan {clan['nom']}")

    if appliquer:
        db.definir("salons", salons)
        db.definir("categories", categories)
        from . import interactions
        await bot.message_permanent("reglement", "msg_reglement", embeds=embeds_reglement())
        await interactions.publier_panneau_tickets(bot)
        await interactions.publier_faq(bot)
        await bot.publier_classements()
    return actions


def embeds_reglement():
    intro = discord.Embed(
        title="⛩️ Bienvenue sur l'Île des Cerisiers",
        description=(
            "À la mort du dernier Empereur-Dragon, le Trône de Jade resta vide et quatre grandes maisons se levèrent "
            "pour réclamer son héritage. Après les **Années de Cendre**, elles acceptèrent l'autorité d'un **Shogun**, "
            "gardien de l'équilibre, et d'un lieu où nul sang ne coule : **le Temple du Conseil**.\n\n"
            "Toi qui arrives sur ces terres, tu devras choisir ta bannière. Et avec elle, tes alliés, tes ennemis, "
            "et le prix que tu es prêt à payer pour ton honneur."),
        color=0xFF5FAE)
    clans = discord.Embed(title="Les Quatre Bannières", color=0xB84DFF)
    for c in db.tous("SELECT * FROM clans ORDER BY slug"):
        lore = CLANS_LORE.get(c["slug"])
        if lore:
            clans.add_field(name=f"{lore['emoji']} {c['nom']} {c['kanji'] or ''} · {lore['titre']}",
                            value=f"*« {lore['devise']} »*", inline=False)
    code = discord.Embed(title="📜 Le Code du Bushido", color=0xFF5FAE, description=(
        "**I. La guerre.** Nulle guerre ne commence dans l'ombre : seul le Shogun la proclame, au Temple du Conseil. "
        "Attaquer avant la proclamation est une traîtrise.\n"
        "**II. Le combat.** On ne combat jamais au Temple du Conseil ni sur l'Île des Cerisiers. On ne frappe pas "
        "un voyageur sans bannière, ni un clan qui n'est pas en guerre avec le tien. On ne rase pas un sanctuaire "
        "ou une ferme par pure cruauté.\n"
        "**III. La paix.** La paix et les alliances se scellent au Temple, sous le regard du Shogun. Les rompre engage "
        "l'honneur de tout le clan.\n"
        "**IV. L'honneur.** Celui qui trahit ce code sera envoyé dans l'**Arène de Pénitence**. "
        "Il n'en sortira que par la victoire."))
    code.set_footer(text="« Un clan se mesure moins aux guerres qu'il gagne qu'à la façon dont il les mène. »")
    return [intro, clans, code]


# --- anciens salons ----------------------------------------------------------------------------

def ids_geres():
    ids = set((db.reglage("salons") or {}).values()) | set((db.reglage("categories") or {}).values())
    ids |= {c["salon_id"] for c in db.tous("SELECT salon_id FROM clans") if c["salon_id"]}
    return ids


def anciens_salons(bot):
    """Salons et catégories qui ne font pas partie de la structure du Shogunat (hors archives)."""
    guild = bot.guild
    geres = ids_geres()
    archives = (db.reglage("categories") or {}).get(ARCHIVES[0])
    requis = {getattr(guild.rules_channel, "id", None), getattr(guild.public_updates_channel, "id", None)}
    resultat = []
    for ch in guild.channels:
        if ch.id in geres or ch.id == archives or ch.category_id == archives:
            continue
        if isinstance(ch, discord.CategoryChannel) and any(e.id in geres for e in ch.channels):
            continue
        resultat.append((ch, ch.id in requis))
    resultat.sort(key=lambda x: (isinstance(x[0], discord.CategoryChannel), x[0].position))
    return resultat


def libelle(ch):
    if isinstance(ch, discord.CategoryChannel):
        return f"📁 {ch.name}"
    prefixe = "🔊 " if isinstance(ch, (discord.VoiceChannel, discord.StageChannel)) else "#"
    return f"{prefixe}{ch.name}" + (f"  ({ch.category.name})" if ch.category else "")


async def categorie_archives(bot):
    categories = db.reglage("categories") or {}
    c = bot.guild.get_channel(categories.get(ARCHIVES[0]) or 0) or discord.utils.get(bot.guild.categories, name=ARCHIVES[1])
    if c is None:
        c = await bot.guild.create_category(ARCHIVES[1], overwrites=overwrites(bot, STAFF), reason=RAISON)
    categories[ARCHIVES[0]] = c.id
    db.definir("categories", categories)
    return c


async def archiver(bot, salons_):
    cat = await categorie_archives(bot)
    faits = []
    for ch in salons_:
        try:
            if isinstance(ch, discord.CategoryChannel):
                for enfant in ch.channels:
                    await enfant.edit(category=cat, sync_permissions=True, reason=RAISON)
                await ch.delete(reason=RAISON)  # la catégorie vide n'a plus d'utilité
            else:
                await ch.edit(category=cat, sync_permissions=True, reason=RAISON)
            faits.append(f"🗃 {libelle(ch)}")
        except discord.HTTPException as e:
            faits.append(f"⚠️ {libelle(ch)} : {e.text or e}")
    return faits


async def supprimer(bot, salons_):
    faits = []
    salons_cfg = db.reglage("salons") or {}
    for ch in salons_:
        try:
            nom = libelle(ch)
            await ch.delete(reason=RAISON)
            faits.append(f"🗑 {nom}")
            salons_cfg = {k: v for k, v in salons_cfg.items() if v != ch.id}
        except discord.HTTPException as e:
            faits.append(f"⚠️ {libelle(ch)} : {e.text or e}")
    db.definir("salons", salons_cfg)
    return faits


# --- interface Discord ----------------------------------------------------------------------------

def _decouper(lignes, limite=3800):
    texte = "\n".join(lignes)
    return texte if len(texte) <= limite else texte[:limite].rsplit("\n", 1)[0] + "\n…"


async def _verifier(inter: discord.Interaction, perms=True) -> bool:
    """Staff obligatoire ; et le bot doit avoir, à cet instant, les permissions de structure."""
    from .bot import PERMS_STRUCTURE
    bot = inter.client
    if not await est_staff(bot, inter.user):
        await inter.response.send_message("⛔ Réservé au staff du Shogunat (admin ou modérateur).", ephemeral=True)
        return False
    manque = bot.permissions_manquantes(PERMS_STRUCTURE) if perms else []
    if manque:
        await inter.response.send_message(
            "Il me manque pour cela : **" + ", ".join(manque) + "**.\nDonne-les-moi le temps de l'opération avec "
            f"[ce lien]({bot.lien_invitation(structure=True)}) (ou sur mon rôle), puis retire-les ensuite.",
            ephemeral=True)
        return False
    return True


class Confirmation(discord.ui.View):
    """Deux boutons ; seul un membre du staff peut confirmer."""

    def __init__(self, action, libelle_ok="Confirmer", style=discord.ButtonStyle.danger):
        super().__init__(timeout=300)
        self.action = action
        self.ok.label = libelle_ok
        self.ok.style = style

    async def interaction_check(self, inter: discord.Interaction) -> bool:
        if await est_staff(inter.client, inter.user):
            return True
        await inter.response.send_message("⛔ Seul le staff peut confirmer.", ephemeral=True)
        return False

    @discord.ui.button(label="Confirmer")
    async def ok(self, inter: discord.Interaction, _):
        self.stop()
        await inter.response.edit_message(content="⏳ En cours…", embed=None, view=None)
        lignes = await self.action(inter)
        texte = _decouper(lignes or ["Rien à faire."])
        await inter.edit_original_response(content=None, embed=discord.Embed(title="✅ Terminé", description=texte, color=0x4ADE80))
        await inter.client.journal(f"🏯 Structure modifiée par **{inter.user.display_name}** :\n{_decouper(lignes or [], 1800)}")

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
    async def annuler(self, inter: discord.Interaction, _):
        self.stop()
        await inter.response.edit_message(content="Annulé. Rien n'a été modifié.", embed=None, view=None)


class Nettoyage(discord.ui.View):
    def __init__(self, liste):
        super().__init__(timeout=300)
        self.par_id = {str(ch.id): ch for ch, requis in liste if not requis}
        options = [discord.SelectOption(label=libelle(ch)[:100], value=str(ch.id)) for ch, requis in liste if not requis][:25]
        self.choix.options = options
        self.choix.max_values = len(options)

    async def interaction_check(self, inter: discord.Interaction) -> bool:
        return await est_staff(inter.client, inter.user)

    @discord.ui.select(placeholder="Choisis les anciens salons…", min_values=1)
    async def choix(self, inter: discord.Interaction, _):
        await inter.response.defer()

    def selection(self):
        return [self.par_id[v] for v in self.choix.values if v in self.par_id]

    @discord.ui.button(label="Archiver (réversible)", emoji="🗃", style=discord.ButtonStyle.primary)
    async def archiver_btn(self, inter: discord.Interaction, _):
        sel = self.selection()
        if not sel:
            await inter.response.send_message("Choisis d'abord des salons dans le menu.", ephemeral=True)
            return
        await inter.response.edit_message(
            content=f"Archiver **{len(sel)}** salon(s) dans « {ARCHIVES[1]} » (visible du staff seulement) ?\n"
                    + "\n".join(libelle(c) for c in sel),
            view=Confirmation(lambda i: archiver(i.client, sel), "Archiver", discord.ButtonStyle.primary))

    @discord.ui.button(label="Supprimer définitivement", emoji="🗑", style=discord.ButtonStyle.danger)
    async def supprimer_btn(self, inter: discord.Interaction, _):
        sel = self.selection()
        if not sel:
            await inter.response.send_message("Choisis d'abord des salons dans le menu.", ephemeral=True)
            return
        await inter.response.edit_message(
            content=f"⚠️ **Supprimer définitivement {len(sel)} salon(s) ?** Les messages seront perdus, "
                    "c'est irréversible.\n" + "\n".join(libelle(c) for c in sel),
            view=Confirmation(lambda i: supprimer(i.client, sel), "Oui, supprimer"))


CHOIX_CLANS = [app_commands.Choice(name=f"{l['emoji']} {slug.capitalize()}", value=slug) for slug, l in CLANS_LORE.items()]
CHOIX_ACCES = [app_commands.Choice(name="Public (tout le monde lit et écrit)", value=PUBLIC),
               app_commands.Choice(name="Lecture seule (seul le staff écrit)", value=LECTURE),
               app_commands.Choice(name="Réservé à un clan", value=CLAN),
               app_commands.Choice(name="Réservé au staff", value=STAFF)]


def enregistrer(bot):
    groupe = app_commands.Group(name="structure", description="Salons, catégories et permissions du serveur (staff)",
                                default_permissions=discord.Permissions(manage_channels=True), guild_only=True)

    @groupe.command(name="apercu", description="Ce qui manque à la structure du Shogunat, et les anciens salons")
    async def apercu(inter: discord.Interaction):
        if not await _verifier(inter, perms=False):
            return
        await inter.response.defer(ephemeral=True)
        actions = await ranger(inter.client, appliquer=False)
        anciens = anciens_salons(inter.client)
        e = discord.Embed(title="🏯 Structure du Shogunat", color=0xFF5FAE,
                          description=_decouper(["**À faire avec `/structure ranger` :**"] + ([f"• {a}" for a in actions] or ["• Rien, tout est en place ✅"])
                                                + ["", f"**Anciens salons ({len(anciens)}) — `/structure nettoyer` :**"]
                                                + ([f"• {libelle(c)}" + (" *(requis par Discord)*" if r else "") for c, r in anciens] or ["• Aucun"])))
        await inter.followup.send(embed=e, ephemeral=True)

    @groupe.command(name="ranger", description="Appliquer la structure du Shogunat (créer, renommer, déplacer, régler les accès)")
    async def ranger_cmd(inter: discord.Interaction):
        if not await _verifier(inter):
            return
        await inter.response.defer(ephemeral=True)
        actions = await ranger(inter.client, appliquer=False)
        e = discord.Embed(title="🏯 Ranger le serveur ?", color=0xFF5FAE,
                          description=_decouper([f"• {a}" for a in actions] or ["Tout est déjà en place. Les messages seront republiés."])
                          + "\n\nLes anciens salons ne sont pas touchés (voir `/structure nettoyer`).")
        await inter.followup.send(embed=e, view=Confirmation(lambda i: ranger(i.client), "Appliquer", discord.ButtonStyle.success),
                                  ephemeral=True)

    @groupe.command(name="nettoyer", description="Archiver ou supprimer les anciens salons")
    async def nettoyer(inter: discord.Interaction):
        if not await _verifier(inter):
            return
        liste = anciens_salons(inter.client)
        if not [c for c, requis in liste if not requis]:
            await inter.response.send_message("Aucun ancien salon à nettoyer ✅", ephemeral=True)
            return
        texte = "Choisis les anciens salons (25 au maximum à la fois), puis archive-les ou supprime-les."
        if len(liste) > 25:
            texte += f"\n{len(liste)} au total : relance la commande pour la suite."
        await inter.response.send_message(texte, view=Nettoyage(liste), ephemeral=True)

    @groupe.command(name="supprimer", description="Supprimer un salon précis (avec confirmation)")
    @app_commands.describe(salon="Le salon ou la catégorie à supprimer")
    async def supprimer_cmd(inter: discord.Interaction, salon: discord.abc.GuildChannel):
        if not await _verifier(inter):
            return
        avertissement = ""
        if salon.id in ids_geres():
            avertissement = "\nC'est un salon de la structure du Shogunat : `/structure ranger` le recréera vide."
        await inter.response.send_message(
            f"⚠️ **Supprimer définitivement {libelle(salon)} ?** Les messages seront perdus.{avertissement}",
            view=Confirmation(lambda i: supprimer(i.client, [salon]), "Oui, supprimer"), ephemeral=True)

    @groupe.command(name="visibilite", description="Qui peut voir et écrire dans un salon")
    @app_commands.describe(salon="Le salon ou la catégorie", acces="Qui y a accès", clan="Le clan (si « Réservé à un clan »)")
    @app_commands.choices(acces=CHOIX_ACCES, clan=CHOIX_CLANS)
    async def visibilite(inter: discord.Interaction, salon: discord.abc.GuildChannel, acces: app_commands.Choice[str],
                         clan: Optional[app_commands.Choice[str]] = None):
        if not await _verifier(inter):
            return
        role_clan = None
        if acces.value == CLAN:
            if not clan:
                await inter.response.send_message("Précise le clan.", ephemeral=True)
                return
            info = db.un("SELECT role_id FROM clans WHERE slug = ?", clan.value)
            role_clan = inter.guild.get_role((info or {}).get("role_id") or 0)
            if not role_clan:
                await inter.response.send_message("Le rôle de ce clan n'existe pas encore : lance `/structure ranger`.", ephemeral=True)
                return
        await salon.edit(overwrites=overwrites(inter.client, acces.value, role_clan), reason=RAISON)
        message = f"✅ {libelle(salon)} : {acces.name}" + (f" ({clan.name})" if role_clan else "")
        await inter.response.send_message(message, ephemeral=True)
        await inter.client.journal(f"🔐 {message} — par **{inter.user.display_name}**")

    @groupe.command(name="salon-clan", description="Ajouter un salon privé à un clan")
    @app_commands.describe(clan="Le clan", nom="Nom du salon", vocal="Salon vocal plutôt que textuel")
    @app_commands.choices(clan=CHOIX_CLANS)
    async def salon_clan(inter: discord.Interaction, clan: app_commands.Choice[str], nom: str, vocal: bool = False):
        if not await _verifier(inter):
            return
        info = db.un("SELECT * FROM clans WHERE slug = ?", clan.value)
        cat = inter.guild.get_channel((db.reglage("categories") or {}).get(f"clan:{clan.value}") or 0)
        role_clan = inter.guild.get_role(info["role_id"] or 0)
        if not cat or not role_clan:
            await inter.response.send_message("La catégorie du clan n'existe pas encore : lance `/structure ranger`.", ephemeral=True)
            return
        ow = overwrites(inter.client, CLAN, role_clan)
        if vocal:
            ch = await inter.guild.create_voice_channel(nom[:100], category=cat, overwrites=ow, reason=RAISON)
        else:
            ch = await inter.guild.create_text_channel(f"{CLANS_LORE[clan.value]['emoji']}・{nom}"[:100], category=cat,
                                                       overwrites=ow, reason=RAISON)
        await inter.response.send_message(f"✅ {ch.mention} créé, visible seulement du clan {clan.name} et du staff.", ephemeral=True)
        await inter.client.journal(f"➕ {ch.mention} créé pour le clan {clan.name} par **{inter.user.display_name}**")

    banniere = app_commands.Group(name="banniere", description="Rôle de clan d'un membre (staff)",
                                  default_permissions=discord.Permissions(manage_roles=True), guild_only=True)

    @banniere.command(name="donner", description="Placer un membre sous la bannière d'un clan")
    @app_commands.describe(membre="Le joueur", clan="Son clan")
    @app_commands.choices(clan=CHOIX_CLANS)
    async def donner(inter: discord.Interaction, membre: discord.Member, clan: app_commands.Choice[str]):
        if not await _verifier(inter, perms=False):
            return
        roles = {c["slug"]: inter.guild.get_role(c["role_id"] or 0) for c in db.tous("SELECT slug, role_id FROM clans")}
        nouveau = roles.get(clan.value)
        if not nouveau:
            await inter.response.send_message("Le rôle de ce clan n'existe pas encore : lance `/structure ranger`.", ephemeral=True)
            return
        try:
            anciens = [r for s, r in roles.items() if r and s != clan.value and r in membre.roles]
            if anciens:
                await membre.remove_roles(*anciens, reason=RAISON)
            await membre.add_roles(nouveau, reason=RAISON)
        except discord.Forbidden:
            await inter.response.send_message("Je n'ai pas le droit de gérer ce rôle : il me faut « Gérer les rôles », "
                                              "et mon rôle doit être placé au-dessus des rôles de clan.", ephemeral=True)
            return
        lore = CLANS_LORE[clan.value]
        await inter.response.send_message(f"{lore['emoji']} {membre.mention} rejoint la bannière **{clan.value.capitalize()}**. "
                                          f"*« {lore['devise']} »*", allowed_mentions=discord.AllowedMentions(users=[membre]))

    @banniere.command(name="retirer", description="Retirer à un membre son rôle de clan")
    @app_commands.describe(membre="Le joueur")
    async def retirer(inter: discord.Interaction, membre: discord.Member):
        if not await _verifier(inter, perms=False):
            return
        ids = {c["role_id"] for c in db.tous("SELECT role_id FROM clans") if c["role_id"]}
        a_retirer = [r for r in membre.roles if r.id in ids]
        try:
            if a_retirer:
                await membre.remove_roles(*a_retirer, reason=RAISON)
        except discord.Forbidden:
            await inter.response.send_message("Je n'ai pas le droit de gérer ces rôles (« Gérer les rôles » manquant).", ephemeral=True)
            return
        await inter.response.send_message(f"{membre.mention} redevient un rōnin, sans bannière.", ephemeral=True)

    bot.tree.add_command(groupe)
    bot.tree.add_command(banniere)
