"""Веб-сервер мини-приложения: страница, API для неё, фон и аватарки."""

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.utils.web_app import WebAppUser, safe_parse_webapp_init_data
from aiohttp import web

from .. import media, texts
from ..checker import find_missing
from ..config import Config
from ..database import Database, Sponsor
from ..prizes import total_prize

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
INIT_DATA_TTL = timedelta(days=1)
CHECK_CACHE_SECONDS = 2.0

BOT = web.AppKey("bot", Bot)
DB = web.AppKey("db", Database)
CONFIG = web.AppKey("config", Config)
CHECK_CACHE = web.AppKey("check_cache", dict)
ASSET_VERSION = web.AppKey("asset_version", str)


def _auth_user(request: web.Request) -> WebAppUser:
    """Пользователь из initData, подписанной Telegram. Иначе — 401."""
    header = request.headers.get("Authorization", "")
    if not header.startswith("tma "):
        raise web.HTTPUnauthorized(text="Откройте приложение через бота в Telegram")
    try:
        data = safe_parse_webapp_init_data(request.app[CONFIG].bot_token, header[4:])
    except ValueError:
        raise web.HTTPUnauthorized(text="Неверная подпись Telegram") from None
    auth_date = data.auth_date if data.auth_date.tzinfo else data.auth_date.replace(tzinfo=timezone.utc)
    if data.user is None or datetime.now(timezone.utc) - auth_date > INIT_DATA_TTL:
        raise web.HTTPUnauthorized(text="Сессия устарела, откройте приложение заново")
    return data.user


async def _missing_ids(app: web.Application, user_id: int, sponsors: list[Sponsor], *, fresh: bool) -> set[int]:
    """ID спонсоров, на которых пользователь не подписан (с коротким кэшем от частых опросов)."""
    cache: dict[int, tuple[float, set[int]]] = app[CHECK_CACHE]
    now = time.monotonic()
    hit = cache.get(user_id)
    if hit and not fresh and now - hit[0] < CHECK_CACHE_SECONDS:
        return hit[1]
    missing = {s.id for s in await find_missing(app[BOT], sponsors, user_id)}
    if len(cache) > 50_000:
        cache.clear()
    cache[user_id] = (now, missing)
    return missing


async def _state(app: web.Application, user: WebAppUser, *, fresh: bool = False) -> dict:
    db, config = app[DB], app[CONFIG]
    sponsors = await db.get_sponsors()
    participant = await db.get_participant(user.id)
    missing = await _missing_ids(app, user.id, sponsors, fresh=fresh) if sponsors else set()
    background = await media.get_background(db, config.media_dir)
    return {
        "giveaway": config.giveaway_name,
        "user": {"first_name": user.first_name},
        "total_prize": total_prize(s.amount for s in sponsors),
        "participants": await db.count_participants(),
        "participant": {"number": participant.number} if participant else None,
        "background": (
            {"kind": background.kind, "url": f"media/background?v={background.version}"} if background else None
        ),
        "sponsors": [
            {
                "id": s.id,
                "kind": s.kind,
                "title": s.title,
                "url": s.url,
                "amount": s.amount,
                "verifiable": s.checkable,
                "done": s.id not in missing if s.checkable else None,
                "avatar": f"media/avatar/{s.id}" if s.checkable else None,
            }
            for s in sponsors
        ],
        "can_join": bool(sponsors) and not missing,
    }


async def index(request: web.Request) -> web.Response:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return web.Response(
        text=html.replace("__V__", request.app[ASSET_VERSION]),
        content_type="text/html",
        headers={"Cache-Control": "no-cache"},
    )


async def api_state(request: web.Request) -> web.Response:
    user = _auth_user(request)
    return web.json_response(await _state(request.app, user))


async def api_join(request: web.Request) -> web.Response:
    """Выдаёт номер, если все каналы и чаты выполнены. 409 — если ещё нет."""
    user = _auth_user(request)
    app = request.app
    db, bot, config = app[DB], app[BOT], app[CONFIG]

    if await db.get_participant(user.id) is None:
        sponsors = await db.get_sponsors()
        if not sponsors or await _missing_ids(app, user.id, sponsors, fresh=True):
            return web.json_response(await _state(app, user), status=409)
        full_name = " ".join(filter(None, (user.first_name, user.last_name)))
        participant, created = await db.register_participant(user.id, user.username, full_name)
        if created:
            try:
                await bot.send_message(user.id, texts.success(participant.number, config.giveaway_name))
            except TelegramAPIError:
                pass  # пользователь не запускал бота — номер всё равно сохранён

    return web.json_response(await _state(app, user))


async def media_background(request: web.Request) -> web.StreamResponse:
    background = await media.get_background(request.app[DB], request.app[CONFIG].media_dir)
    if background is None:
        raise web.HTTPNotFound()
    return web.FileResponse(background.path, headers={"Cache-Control": "public, max-age=31536000, immutable"})


async def media_avatar(request: web.Request) -> web.StreamResponse:
    try:
        sponsor = await request.app[DB].get_sponsor(int(request.match_info["sponsor_id"]))
    except ValueError:
        sponsor = None
    if sponsor is None:
        raise web.HTTPNotFound()
    path = await media.sponsor_avatar(request.app[BOT], request.app[CONFIG].media_dir, sponsor)
    if path is None:
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Cache-Control": "public, max-age=3600"})


async def health(_: web.Request) -> web.Response:
    return web.Response(text="ok")


def create_app(bot: Bot, db: Database, config: Config) -> web.Application:
    app = web.Application()
    app[BOT], app[DB], app[CONFIG] = bot, db, config
    app[CHECK_CACHE] = {}
    app[ASSET_VERSION] = str(int(max(p.stat().st_mtime for p in STATIC_DIR.iterdir())))

    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/api/state", api_state)
    app.router.add_post("/api/join", api_join)
    app.router.add_get("/media/background", media_background)
    app.router.add_get("/media/avatar/{sponsor_id}", media_avatar)
    app.router.add_static("/static/", STATIC_DIR)
    return app


async def start_web_server(bot: Bot, db: Database, config: Config) -> web.AppRunner | None:
    runner = web.AppRunner(create_app(bot, db, config), access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner, config.web_host, config.web_port).start()
    except OSError as e:
        log.error("Не удалось запустить сервер мини-приложения на порту %s: %s", config.web_port, e)
        await runner.cleanup()
        return None
    log.info(
        "Мини-приложение слушает %s:%s, публичный адрес: %s",
        config.web_host,
        config.web_port,
        config.webapp_url or "не задан (WEBAPP_URL)",
    )
    return runner
