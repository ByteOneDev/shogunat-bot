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
    runner = await web.demarrer(bot)
    try:
        if bot:
            async with bot:
                await bot.start(config.DISCORD_TOKEN)
        else:
            log.warning("Pas de DISCORD_TOKEN : seul le panneau tourne (aperçu).")
            await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
