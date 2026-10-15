"""Капча перед выдачей номера — простая, в одно действие.

В мини-приложении — «вставь кусочек пазла»: передвинуть ползунок, пока кусочек не встанет в выемку.
В чате с ботом — «какое число на картинке»: нажать на кнопку с этим числом.
Картинки каждый раз рисуются заново, правильный ответ знает только сервер.
"""

import asyncio
import io
import random
import secrets
import time
from dataclasses import dataclass, field

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

SETTING = "captcha"  # «1» — капча включена (по умолчанию)

TTL = 300  # секунд на решение одной капчи
WINDOW, MAX_PER_WINDOW = 600, 12  # не больше 12 новых капч за 10 минут на человека

SLIDER_W, SLIDER_H = 300, 180
PIECE, KNOB = 46, 9  # сторона кусочка пазла и радиус «ушка»
TOLERANCE = 6  # на сколько пикселей можно промахнуться

CHAT_TTL = 600  # секунд на ответ в чате
CHAT_OPTIONS = 6  # кнопок с числами под картинкой
CHAT_W, CHAT_H = 480, 240
CHAT_PASS_TTL = 600  # прошедшему капчу в чате не нужно проходить её снова, если сразу отписался и вернулся

PALETTES = [
    ((255, 94, 98), (255, 195, 113)),
    ((67, 206, 162), (24, 90, 157)),
    ((86, 204, 242), (47, 128, 237)),
    ((247, 151, 30), (255, 210, 0)),
    ((142, 45, 226), (74, 0, 224)),
    ((238, 9, 121), (255, 106, 0)),
    ((17, 153, 142), (56, 239, 125)),
    ((252, 70, 107), (63, 94, 251)),
    ((255, 175, 189), (100, 65, 165)),
    ((0, 198, 255), (0, 114, 255)),
]


class CaptchaRateLimited(Exception):
    pass


class _RateLimit:
    """Не больше MAX_PER_WINDOW новых капч за WINDOW секунд на человека."""

    def __init__(self) -> None:
        self._issued: dict[int, list[float]] = {}

    def hit(self, user_id: int) -> None:
        now = time.monotonic()
        for uid in list(self._issued):
            self._issued[uid] = [t for t in self._issued[uid] if now - t < WINDOW]
            if not self._issued[uid]:
                del self._issued[uid]
        recent = self._issued.setdefault(user_id, [])
        if len(recent) >= MAX_PER_WINDOW:
            raise CaptchaRateLimited
        recent.append(now)


@dataclass
class Challenge:
    id: str
    user_id: int
    answer: int  # x выемки
    files: dict[str, bytes]
    public: dict
    created: float = field(default_factory=time.monotonic)

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.created > TTL


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _art(width: int, height: int, rng: random.Random) -> Image.Image:
    """Случайная яркая картинка: градиент и крупные фигуры, которые переходят через границы кусочков."""
    s = 2  # рисуем крупнее и уменьшаем — так края гладкие
    w, h = width * s, height * s
    c1, c2 = rng.choice(PALETTES)
    if rng.random() < 0.5:
        c1, c2 = c2, c1
    # Градиент рисуем на квадрате больше диагонали и вырезаем середину, чтобы после поворота не было пустых углов.
    d = int((w * w + h * h) ** 0.5) + 4
    mask = Image.linear_gradient("L").resize((d, d), Image.BICUBIC).rotate(rng.uniform(0, 360), resample=Image.BICUBIC)
    mask = mask.crop(((d - w) // 2, (d - h) // 2, (d - w) // 2 + w, (d - h) // 2 + h))
    img = Image.composite(Image.new("RGB", (w, h), c2), Image.new("RGB", (w, h), c1), mask)

    draw = ImageDraw.Draw(img, "RGBA")
    light, dark = (255, 255, 255), (20, 16, 40)
    for _ in range(rng.randint(3, 4)):  # круги
        r = rng.randint(int(min(w, h) * 0.12), int(min(w, h) * 0.32))
        cx, cy = rng.randint(0, w), rng.randint(0, h)
        color = rng.choice((light, dark)) + (rng.randint(70, 140),)
        if rng.random() < 0.5:
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
        else:
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=color, width=rng.randint(10, 22) * s)
    for _ in range(rng.randint(1, 2)):  # треугольники
        pts = [(rng.randint(-w // 4, w + w // 4), rng.randint(-h // 4, h + h // 4)) for _ in range(3)]
        draw.polygon(pts, fill=rng.choice((light, dark)) + (rng.randint(60, 120),))
    step = rng.randint(14, 22) * s  # полосы
    x0 = rng.randint(0, w // 2)
    for i in range(rng.randint(3, 5)):
        x = x0 + i * step * 2
        draw.line([(x, 0), (x - h // 2, h)], fill=light + (90,), width=step // 2)
    return img.resize((width, height), Image.LANCZOS)


def _piece_mask(scale: int) -> Image.Image:
    """Форма кусочка: скруглённый квадрат с круглым «ушком» справа."""
    p, k = PIECE * scale, KNOB * scale
    mask = Image.new("L", (p + k, p), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, p - 1, p - 1), radius=6 * scale, fill=255)
    draw.ellipse((p - k, p // 2 - k, p + k - 1, p // 2 + k), fill=255)
    return mask


def _make_slider(cid: str, user_id: int, rng: random.Random) -> Challenge:
    img = _art(SLIDER_W, SLIDER_H, rng).convert("RGBA")
    shape = _piece_mask(4).resize((PIECE + KNOB, PIECE), Image.LANCZOS)  # сглаженные края
    pw, ph = shape.size
    tx = rng.randint(pw + 24, SLIDER_W - pw - 8)
    ty = rng.randint(10, SLIDER_H - ph - 10)

    outline = ImageChops.subtract(shape.filter(ImageFilter.MaxFilter(5)), shape)  # тонкая белая обводка

    piece = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    piece.paste(img.crop((tx, ty, tx + pw, ty + ph)), (0, 0), shape)
    piece.paste(Image.new("RGBA", (pw, ph), (255, 255, 255, 255)), (0, 0), outline)

    shade = Image.new("RGBA", img.size, (0, 0, 0, 0))
    shade.paste(Image.new("RGBA", (pw, ph), (8, 10, 24, 170)), (tx, ty), shape)
    rim = Image.new("RGBA", img.size, (0, 0, 0, 0))
    rim.paste(Image.new("RGBA", (pw, ph), (255, 255, 255, 150)), (tx, ty), outline)
    background = Image.alpha_composite(Image.alpha_composite(img, shade), rim).convert("RGB")

    public = {
        "id": cid,
        "width": SLIDER_W,
        "height": SLIDER_H,
        "background": f"captcha/{cid}/bg.png",
        "piece": {"url": f"captcha/{cid}/piece.png", "width": pw, "height": ph, "y": ty},
        "max": SLIDER_W - pw,
    }
    return Challenge(cid, user_id, tx, {"bg": _png(background), "piece": _png(piece)}, public)


class CaptchaStore:
    """Активные капчи в памяти процесса: одна на человека, одноразовые, живут TTL секунд."""

    def __init__(self) -> None:
        self._items: dict[str, Challenge] = {}
        self._by_user: dict[int, str] = {}
        self._limit = _RateLimit()

    def _cleanup(self) -> None:
        for cid in [cid for cid, ch in self._items.items() if ch.expired]:
            ch = self._items.pop(cid)
            if self._by_user.get(ch.user_id) == cid:
                del self._by_user[ch.user_id]

    async def issue(self, user_id: int) -> dict:
        self._cleanup()
        self._limit.hit(user_id)

        cid = secrets.token_urlsafe(16)
        rng = random.Random(secrets.randbits(64))
        challenge = await asyncio.to_thread(_make_slider, cid, user_id, rng)

        if old := self._by_user.pop(user_id, None):
            self._items.pop(old, None)
        self._items[cid] = challenge
        self._by_user[user_id] = cid
        return challenge.public

    def file(self, cid: str, name: str) -> bytes | None:
        challenge = self._items.get(cid)
        if challenge is None or challenge.expired:
            return None
        return challenge.files.get(name)

    def verify(self, user_id: int, cid: str, answer: object) -> bool:
        """Проверяет ответ. Капча одноразовая: после любой попытки её нужно получить заново."""
        challenge = self._items.get(cid)
        if challenge is None or challenge.user_id != user_id:
            return False
        del self._items[cid]
        if self._by_user.get(user_id) == cid:
            del self._by_user[user_id]
        if challenge.expired:
            return False
        try:
            return abs(float(answer) - challenge.answer) <= TOLERANCE
        except (TypeError, ValueError):
            return False


# ---------- «Какое число на картинке» в чате с ботом ----------


def _jpeg(img: Image.Image, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _render_number(number: str, rng: random.Random) -> bytes:
    """Крупное число на цветной картинке: цифры слегка повёрнуты, поверх пара тонких линий."""
    s = 2  # рисуем в двойном размере, чтобы в Telegram было чётко
    w, h = CHAT_W * s, CHAT_H * s
    img = Image.blend(_art(w, h, rng), Image.new("RGB", (w, h), (0, 0, 0)), 0.35).convert("RGBA")

    font = ImageFont.load_default(size=int(h * 0.5))
    step = w * 0.62 / len(number)
    x0 = (w - step * len(number)) / 2
    for i, digit in enumerate(number):
        size = int(h * 0.62)
        layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text(
            (size / 2, size / 2), digit, font=font, anchor="mm",
            fill=(255, 255, 255, 255), stroke_width=5 * s, stroke_fill=(10, 10, 20, 255),
        )  # fmt: skip
        layer = layer.rotate(rng.uniform(-14, 14), resample=Image.BICUBIC)
        x = int(x0 + i * step + step / 2 - size / 2 + rng.uniform(-6, 6) * s)
        y = int((h - size) / 2 + rng.uniform(-12, 12) * s)
        img.alpha_composite(layer, (x, y))

    img = img.convert("RGB")
    draw = ImageDraw.Draw(img, "RGBA")  # полупрозрачные линии поверх
    for _ in range(2):
        y1, y2 = rng.randint(int(h * 0.3), int(h * 0.7)), rng.randint(int(h * 0.3), int(h * 0.7))
        draw.line([(0, y1), (w, y2)], fill=(255, 255, 255, 110), width=3 * s)
    return _jpeg(img)


@dataclass
class ChatChallenge:
    id: str
    user_id: int
    options: list[str]  # числа на кнопках
    answer: int  # номер правильной кнопки
    created: float = field(default_factory=time.monotonic)

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.created > CHAT_TTL


def _make_number_challenge(cid: str, user_id: int, rng: random.Random) -> tuple[ChatChallenge, bytes]:
    options = [str(n) for n in rng.sample(range(100, 1000), CHAT_OPTIONS)]
    answer = rng.randrange(CHAT_OPTIONS)
    return ChatChallenge(cid, user_id, options, answer), _render_number(options[answer], rng)


class ChatCaptchaStore:
    """Капчи в чате с ботом: одна на человека, одноразовые, в памяти процесса."""

    def __init__(self) -> None:
        self._items: dict[int, ChatChallenge] = {}
        self._passed: dict[int, float] = {}
        self._limit = _RateLimit()

    async def issue(self, user_id: int) -> tuple[ChatChallenge, bytes]:
        """Новая капча и её картинка (JPEG)."""
        now = time.monotonic()
        for uid in [uid for uid, ch in self._items.items() if ch.expired]:
            del self._items[uid]
        for uid in [uid for uid, t in self._passed.items() if now - t > CHAT_PASS_TTL]:
            del self._passed[uid]
        self._limit.hit(user_id)
        rng = random.Random(secrets.randbits(64))
        challenge, image = await asyncio.to_thread(_make_number_challenge, secrets.token_urlsafe(6), user_id, rng)
        self._items[user_id] = challenge
        return challenge, image

    def get(self, user_id: int, cid: str) -> ChatChallenge | None:
        """Капча этого человека с этим кодом (кнопки старых картинок не подходят)."""
        challenge = self._items.get(user_id)
        return challenge if challenge is not None and challenge.id == cid else None

    def check(self, user_id: int, cid: str, option: int) -> bool:
        """Проверяет ответ. Капча одноразовая: после ошибки нужна новая."""
        challenge = self.get(user_id, cid)
        if challenge is None:
            return False
        del self._items[user_id]
        if challenge.expired or option != challenge.answer:
            return False
        self._passed[user_id] = time.monotonic()
        return True

    def recently_passed(self, user_id: int) -> bool:
        passed = self._passed.get(user_id)
        return passed is not None and time.monotonic() - passed <= CHAT_PASS_TTL
