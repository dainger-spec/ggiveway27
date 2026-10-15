"""Веб-сервер мини-приложения: страница, API для неё, фон и аватарки."""

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.utils.web_app import WebAppUser, safe_parse_webapp_init_data
from aiohttp import web

from .. import captcha, media, texts
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
SEEN_USERS = web.AppKey("seen_users", set)
CAPTCHA = web.AppKey("captcha", captcha.CaptchaStore)


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
    missing = {s.id for s in await find_missing(app[BOT], app[DB], sponsors, user_id)}
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
                "avatar": (f"media/avatar/{s.id}?v={s.avatar_file or 'tg'}" if s.checkable or s.avatar_file else None),
                "background": (
                    {"kind": s.bg_kind, "url": f"media/sponsor/{s.id}?v={s.bg_file}"} if s.bg_file else None
                ),
            }
            for s in sponsors
        ],
        "can_join": bool(sponsors) and not missing,
        "captcha": await db.get_flag(captcha.SETTING),
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
    seen: set[int] = request.app[SEEN_USERS]
    if user.id not in seen:  # открывшие приложение тоже получают рассылки; пишем в базу один раз
        await request.app[DB].touch_user(user.id)
        seen.add(user.id)
    return web.json_response(await _state(request.app, user))


async def _conditions_met(app: web.Application, user_id: int) -> bool:
    sponsors = await app[DB].get_sponsors()
    return bool(sponsors) and not await _missing_ids(app, user_id, sponsors, fresh=True)


async def api_captcha(request: web.Request) -> web.Response:
    """Новая капча — только когда все условия уже выполнены."""
    user = _auth_user(request)
    app = request.app
    if await app[DB].get_participant(user.id) is not None or not await _conditions_met(app, user.id):
        return web.json_response(await _state(app, user), status=409)
    try:
        challenge = await app[CAPTCHA].issue(user.id)
    except captcha.CaptchaRateLimited:
        return web.json_response({"error": "Слишком много попыток. Подожди пару минут и попробуй снова."}, status=429)
    return web.json_response(challenge)


async def captcha_file(request: web.Request) -> web.Response:
    data = request.app[CAPTCHA].file(request.match_info["cid"], request.match_info["name"])
    if data is None:
        raise web.HTTPNotFound()
    return web.Response(body=data, content_type="image/png", headers={"Cache-Control": "private, max-age=300"})


async def api_join(request: web.Request) -> web.Response:
    """Выдаёт номер, если все каналы и чаты выполнены и пройдена капча.

    409 — условия не выполнены, 422 — капча не пройдена (нужно получить новую).
    """
    user = _auth_user(request)
    app = request.app
    db, bot, config = app[DB], app[BOT], app[CONFIG]

    if await db.get_participant(user.id) is None:
        if not await _conditions_met(app, user.id):
            return web.json_response(await _state(app, user), status=409)
        if await db.get_flag(captcha.SETTING):
            try:
                body = await request.json()
            except ValueError:
                body = {}
            body = body if isinstance(body, dict) else {}
            if not app[CAPTCHA].verify(user.id, str(body.get("captcha_id", "")), body.get("answer")):
                return web.json_response({"error": "captcha"}, status=422)
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


async def media_sponsor_background(request: web.Request) -> web.StreamResponse:
    try:
        sponsor = await request.app[DB].get_sponsor(int(request.match_info["sponsor_id"]))
    except ValueError:
        sponsor = None
    path = media.sponsor_background_path(request.app[CONFIG].media_dir, sponsor) if sponsor else None
    if path is None:
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable"})


async def health(_: web.Request) -> web.Response:
    return web.Response(text="ok")


def create_app(bot: Bot, db: Database, config: Config) -> web.Application:
    app = web.Application()
    app[BOT], app[DB], app[CONFIG] = bot, db, config
    app[CHECK_CACHE] = {}
    app[SEEN_USERS] = set()
    app[CAPTCHA] = captcha.CaptchaStore()
    app[ASSET_VERSION] = str(int(max(p.stat().st_mtime for p in STATIC_DIR.iterdir())))

    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/api/state", api_state)
    app.router.add_post("/api/join", api_join)
    app.router.add_post("/api/captcha", api_captcha)
    app.router.add_get("/captcha/{cid}/{name}.png", captcha_file)
    app.router.add_get("/media/background", media_background)
    app.router.add_get("/media/avatar/{sponsor_id}", media_avatar)
    app.router.add_get("/media/sponsor/{sponsor_id}", media_sponsor_background)
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
