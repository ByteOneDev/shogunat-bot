"""Lance le bot Discord et le panneau d'admin dans le même programme : python run.py"""
import asyncio
import logging

from shogunat import config, db, web
from shogunat.bot import ShogunatBot


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s : %(message)s")
    log = logging.getLogger("shogunat")
    db.conn()
    for probleme in config.problemes():
        log.warning("Réglage manquant dans .env : %s", probleme)
    bot = ShogunatBot() if config.DISCORD_TOKEN and config.GUILD_ID else None

    runner = None
    if config.PANEL_ACTIF or config.BOUTIQUE_ACTIVE:
        try:
            runner = await web.demarrer(bot)
        except OSError as e:  # port refusé ou déjà pris : le bot doit quand même démarrer
            log.warning("Le panneau n'a pas pu démarrer (%s). Le bot continue sans panneau ; "
                        "vérifie le port de l'hébergeur ou mets PANEL=off.", e)
    else:
        log.info("Panneau et boutique désactivés : seul le bot tourne.")

    try:
        if bot:
            async with bot:
                await bot.start(config.DISCORD_TOKEN)
        elif runner:
            log.warning("Pas de DISCORD_TOKEN ou de GUILD_ID : seul le web tourne (panneau et/ou boutique).")
            await asyncio.Event().wait()
        else:
            log.error("Rien à lancer : renseigne DISCORD_TOKEN et GUILD_ID dans le fichier .env.")
    finally:
        if runner:
            await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
