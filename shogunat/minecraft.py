"""Lien avec le serveur Minecraft : statut (ping), RCON, statistiques des joueurs."""
import asyncio
import json
import logging
import struct
import time

from mcstatus import JavaServer

from . import config, db

log = logging.getLogger("shogunat.minecraft")


# --- statut ---------------------------------------------------------------------------

async def statut():
    """Ping du serveur : {en_ligne, joueurs, max, noms, version, latence}."""
    try:
        serveur = await JavaServer.async_lookup(config.MC_ADRESSE, timeout=5)
        st = await serveur.async_status()
        return {
            "en_ligne": True,
            "joueurs": st.players.online,
            "max": st.players.max,
            "noms": sorted(p.name for p in (st.players.sample or []) if p.name and not p.name.startswith("§")),
            "version": st.version.name,
            "latence": round(st.latency),
        }
    except Exception as e:  # serveur éteint, en hibernation ou injoignable
        log.debug("ping impossible : %s", e)
        return {"en_ligne": False, "joueurs": 0, "max": 0, "noms": [], "version": None, "latence": None}


# --- RCON (protocole Source, utilisé par Minecraft) ----------------------------------

class RconErreur(Exception):
    pass


async def _lire_paquet(reader):
    taille = struct.unpack("<i", await reader.readexactly(4))[0]
    donnees = await reader.readexactly(taille)
    req_id, type_ = struct.unpack("<ii", donnees[:8])
    return req_id, type_, donnees[8:-2].decode("utf-8", "replace")


def _paquet(req_id, type_, corps):
    charge = struct.pack("<ii", req_id, type_) + corps.encode("utf-8") + b"\x00\x00"
    return struct.pack("<i", len(charge)) + charge


_verrou = asyncio.Lock()


async def rcon(commande, timeout=8):
    """Envoie une commande à la console du serveur et renvoie la réponse."""
    if not (config.RCON_PORT and config.RCON_PASSWORD):
        raise RconErreur("RCON n'est pas configuré (RCON_PORT / RCON_PASSWORD dans .env).")
    async with _verrou:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(config.RCON_HOST, config.RCON_PORT), timeout)
        except (OSError, asyncio.TimeoutError) as e:
            raise RconErreur(f"serveur injoignable en RCON ({e.__class__.__name__}).")
        try:
            writer.write(_paquet(1, 3, config.RCON_PASSWORD))
            await writer.drain()
            req_id, _, _ = await asyncio.wait_for(_lire_paquet(reader), timeout)
            if req_id == -1:
                raise RconErreur("mot de passe RCON refusé.")
            writer.write(_paquet(2, 2, commande))
            await writer.drain()
            _, _, morceau = await asyncio.wait_for(_lire_paquet(reader), timeout)
            reponse = [morceau]
            # Minecraft découpe les longues réponses en paquets de 4096 octets
            while len(morceau.encode("utf-8")) >= 4000:
                try:
                    _, _, morceau = await asyncio.wait_for(_lire_paquet(reader), 0.5)
                except asyncio.TimeoutError:
                    break
                reponse.append(morceau)
            return "".join(reponse)
        except (OSError, asyncio.IncompleteReadError, asyncio.TimeoutError) as e:
            raise RconErreur(f"connexion RCON interrompue ({e.__class__.__name__}).")
        finally:
            writer.close()


async def dire_en_jeu(titre, texte):
    """Affiche une annonce dans le chat de tous les joueurs connectés."""
    message = [
        "",
        {"text": "⛩ ", "color": "#F07AB2"},
        {"text": titre, "color": "gold", "bold": True},
        {"text": "\n" + texte, "color": "white"},
    ]
    await rcon("tellraw @a " + json.dumps(message, ensure_ascii=False))


# --- statistiques (commande /shogunat_discord du script KubeJS fourni) ------------------

async def rafraichir_stats():
    """Récupère kills, morts, temps de jeu, argent et clans. Garde la dernière copie en base."""
    brut = await rcon("shogunat_discord export")
    debut = brut.find("{")
    if debut < 0:
        raise RconErreur("réponse inattendue du serveur (le script KubeJS shogunat_discord.js est-il installé ?) : "
                         + brut[:200])
    donnees = json.loads(brut[debut:])
    donnees["maj_le"] = int(time.time())
    db.definir("stats", donnees)
    return donnees


def stats_en_cache():
    return db.reglage("stats", {"joueurs": {}, "clans": {}, "maj_le": None})


CATEGORIES = {
    "kills": ("k", "⚔️ Top kills", lambda v: f"{v} kills"),
    "morts": ("d", "☠️ Top morts", lambda v: f"{v} morts"),
    "argent": ("m", "💰 Top argent", lambda v: "$" + format_argent(v)),
    "temps": ("t", "⌛ Top temps de jeu", lambda v: format_temps(v)),
}


def format_argent(v):
    for seuil, unite in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if v >= seuil:
            x = v / seuil
            return f"{int(x) if x >= 100 else int(x * 10) / 10}{unite}"
    return str(int(v))


def format_temps(ticks):
    minutes = int(ticks) // 1200
    return f"{minutes // 60}h {minutes % 60}min" if minutes >= 60 else f"{minutes}min"


def top(stats, categorie, n=10):
    cle = CATEGORIES[categorie][0]
    joueurs = list(stats.get("joueurs", {}).values())
    joueurs.sort(key=lambda j: j.get(cle) or 0, reverse=True)
    return joueurs[:n]


def classement_clans(stats):
    """Clans triés par kills cumulés (même règle que le menu /classement en jeu)."""
    clans = {}
    for j in stats.get("joueurs", {}).values():
        c = j.get("c")
        if not c:
            continue
        cl = clans.setdefault(c, {"slug": c, "kills": 0, "argent": 0, "membres": 0})
        cl["kills"] += j.get("k") or 0
        cl["argent"] += j.get("m") or 0
        cl["membres"] += 1
    return sorted(clans.values(), key=lambda c: c["kills"], reverse=True)
