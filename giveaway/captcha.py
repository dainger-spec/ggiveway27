"""Капча перед выдачей номера: слово на картинке, которое нужно написать.

Каждая буква — своим шрифтом, цветом, размером и под своим углом; слово изогнуто волной, поверх — линии
цвета букв, на фоне — бледные случайные буквы. Человек читает слово сразу, а распознать его программой
заметно сложнее, чем ровные цифры. Ответ знает только сервер, капча одноразовая.

Слова можно заменить своими через переменную окружения CAPTCHA_WORDS (через запятую) — тогда их не будет
в открытом репозитории.
"""

import asyncio
import colorsys
import io
import math
import os
import random
import secrets
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SETTING = "captcha"  # «1» — капча включена (по умолчанию)

TTL = 300  # секунд на ответ в мини-приложении
CHAT_TTL = 600  # в чате — больше: нужно ещё набрать сообщение
PASS_TTL = 600  # прошедшему капчу не нужно проходить её снова, если сразу отписался и вернулся
WINDOW, MAX_PER_WINDOW = 600, 8  # не больше 8 картинок за 10 минут на человека — подбирать слова долго

WIDTH, HEIGHT = 520, 200

WORDS = (
    "кардинг", "прокси", "антик", "ролка", "дроп", "вбив", "логи", "чекер", "дедик", "сокс", "куки",
    "холдер", "биллинг", "профит", "залив", "гарант", "ворк", "пробив", "мерч", "обнал", "баланс",
    "транзит", "крипта", "кошелек", "профиль", "сервак", "аккаунт", "биржа", "токен", "майнинг",
    "блокчейн", "трейдинг", "браузер", "хостинг", "домен", "пароль", "логин", "сессия", "ключ", "шифр",
    "сделка", "перевод", "карта", "банк", "чек", "пруф", "схема", "тема", "отстук", "кэшаут",
)  # fmt: skip

FONTS_DIR = Path(__file__).parent / "fonts"
NOISE_LETTERS = "абвгдежзийклмнопрстуфхцчшщыэюя"

# Ответ, набранный в английской раскладке, переводим в русскую: «hjkrf» → «ролка».
_LAYOUT = str.maketrans("qwertyuiop[]asdfghjkl;'zxcvbnm,.`", "йцукенгшщзхъфывапролджэячсмитьбюё")


class CaptchaRateLimited(Exception):
    pass


def normalize(text: str) -> str:
    """Ответ без регистра, пробелов и знаков; «ё» = «е»; английская раскладка — как русская."""
    text = text.strip().lower()
    if not any("а" <= ch <= "я" or ch == "ё" for ch in text):
        text = text.translate(_LAYOUT)
    return "".join(ch for ch in text if ch.isalpha()).replace("ё", "е")


def words() -> list[str]:
    """Слова для капчи: свои из CAPTCHA_WORDS или встроенные."""
    custom = [normalize(w) for w in os.environ.get("CAPTCHA_WORDS", "").split(",")]
    custom = [w for w in custom if 3 <= len(w) <= 12]
    return custom or list(WORDS)


# ---------- Картинка ----------


@lru_cache(maxsize=64)
def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def _fonts() -> list[str]:
    return sorted(str(p) for p in FONTS_DIR.glob("*.ttf"))


def _bright(rng: random.Random, alpha: int = 255) -> tuple[int, int, int, int]:
    r, g, b = colorsys.hsv_to_rgb(rng.random(), rng.uniform(0.55, 0.9), rng.uniform(0.9, 1.0))
    return int(r * 255), int(g * 255), int(b * 255), alpha


def _background(w: int, h: int, rng: random.Random) -> Image.Image:
    """Тёмный двухцветный градиент с полупрозрачными кругами и полосами."""
    c1 = tuple(int(v * 255) for v in colorsys.hsv_to_rgb(rng.random(), 0.6, 0.32))
    c2 = tuple(int(v * 255) for v in colorsys.hsv_to_rgb(rng.random(), 0.6, 0.22))
    d = int(math.hypot(w, h)) + 4  # градиент под углом без пустых углов
    mask = Image.linear_gradient("L").resize((d, d)).rotate(rng.uniform(0, 360), resample=Image.BICUBIC)
    mask = mask.crop(((d - w) // 2, (d - h) // 2, (d - w) // 2 + w, (d - h) // 2 + h))
    img = Image.composite(Image.new("RGB", (w, h), c1), Image.new("RGB", (w, h), c2), mask)

    draw = ImageDraw.Draw(img, "RGBA")  # на RGB-картинке полупрозрачные фигуры смешиваются с фоном
    for _ in range(rng.randint(4, 6)):
        r = rng.randint(h // 8, h // 2)
        cx, cy = rng.randint(0, w), rng.randint(0, h)
        color = _bright(rng, rng.randint(25, 55))
        if rng.random() < 0.5:
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
        else:
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=color, width=rng.randint(6, 16))
    for _ in range(rng.randint(3, 6)):
        x = rng.randint(-w // 4, w)
        draw.line([(x, 0), (x + rng.randint(-h, h), h)], fill=_bright(rng, 40), width=rng.randint(6, 18))
    return img.convert("RGBA")


def _noise_letters(img: Image.Image, rng: random.Random, fonts: list[str]) -> None:
    """Бледные мелкие буквы по всему фону — мешают программе найти, где слово."""
    w, h = img.size
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for _ in range(rng.randint(22, 32)):
        font = _font(rng.choice(fonts), int(h * rng.uniform(0.1, 0.2)))
        draw.text((rng.randint(0, w), rng.randint(0, h)), rng.choice(NOISE_LETTERS), font=font,
                  fill=_bright(rng, rng.randint(45, 80)), anchor="mm")  # fmt: skip
    img.alpha_composite(layer)


def _glyph(ch: str, font: ImageFont.FreeTypeFont, color: tuple, angle: float, stroke: int) -> Image.Image:
    size = int(font.size * 1.8)
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((size / 2, size / 2), ch, font=font, fill=color, anchor="mm",
                               stroke_width=stroke, stroke_fill=(12, 12, 22, 230))  # fmt: skip
    layer = layer.rotate(angle, resample=Image.BICUBIC, expand=False)
    return layer.crop(layer.getbbox() or (0, 0, 1, 1))


def _wave(layer: Image.Image, rng: random.Random) -> Image.Image:
    """Изгибает слово волной: столбики пикселей сдвигаются вверх-вниз по синусоиде."""
    w, h = layer.size
    amp = rng.uniform(0.04, 0.07) * h
    period = rng.uniform(0.35, 0.6) * w
    phase = rng.uniform(0, 2 * math.pi)
    out = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    for x in range(0, w, 2):
        dy = int(amp * math.sin(2 * math.pi * x / period + phase))
        out.paste(layer.crop((x, 0, x + 2, h)), (x, dy))
    return out


def render_word(word: str, rng: random.Random) -> bytes:
    """Картинка со словом (JPEG)."""
    s = 2  # рисуем в двойном размере — в Telegram и на телефонах так чётче
    w, h = WIDTH * s, HEIGHT * s
    fonts = _fonts()
    img = _background(w, h, rng)
    _noise_letters(img, rng, fonts)

    text = word.upper() if rng.random() < 0.5 else word
    base = min(h * 0.4, w * 0.84 / (len(text) * 0.66))
    glyphs = []
    for ch in text:
        font = _font(rng.choice(fonts), int(base * rng.uniform(0.85, 1.12)))
        glyphs.append(_glyph(ch, font, _bright(rng), rng.uniform(-28, 28), 2 * s))
    overlap = 0.86  # буквы чуть наезжают друг на друга — так их сложнее разрезать по отдельности
    total = sum(g.width for g in glyphs[:-1]) * overlap + glyphs[-1].width
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    x = (w - total) / 2
    for g in glyphs:
        y = (h - g.height) / 2 + rng.uniform(-0.07, 0.07) * h
        y = min(max(y, 0.1 * h), 0.9 * h - g.height)  # с запасом на волну — буквы не уходят за край
        layer.alpha_composite(g, (int(x), int(y)))
        x += g.width * overlap
    layer = _wave(layer, rng)

    draw = ImageDraw.Draw(layer)
    for _ in range(2):  # линии цвета букв, которые проходят прямо через слово
        y0, amp, period = rng.uniform(0.38, 0.62) * h, rng.uniform(0.05, 0.12) * h, rng.uniform(0.5, 1.0) * w
        phase = rng.uniform(0, 2 * math.pi)
        points = [(x, y0 + amp * math.sin(2 * math.pi * x / period + phase)) for x in range(0, w + 1, 8)]
        draw.line(points, fill=_bright(rng, 220), width=3 * s)

    img.alpha_composite(layer)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=88)
    return buf.getvalue()


# ---------- Хранилище ----------


@dataclass
class Challenge:
    id: str
    user_id: int
    answer: str
    ttl: int
    message_id: int | None = None  # сообщение с картинкой в чате — чтобы убрать под ним кнопку
    created: float = field(default_factory=time.monotonic)

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.created > self.ttl


class CaptchaStore:
    """Активные капчи в памяти процесса: одна на человека, одноразовые.

    Отдельные хранилища — для мини-приложения (картинка отдаётся по ссылке) и для чата с ботом.
    """

    def __init__(self, ttl: int = TTL, *, keep_images: bool = False) -> None:
        self._ttl = ttl
        self._keep_images = keep_images
        self._items: dict[int, Challenge] = {}
        self._images: dict[str, bytes] = {}
        self._passed: dict[int, float] = {}
        self._issued: dict[int, list[float]] = {}

    def _cleanup(self) -> None:
        now = time.monotonic()
        for user_id in [uid for uid, ch in self._items.items() if ch.expired]:
            self._drop(user_id)
        for user_id in [uid for uid, t in self._passed.items() if now - t > PASS_TTL]:
            del self._passed[user_id]
        for user_id in list(self._issued):
            self._issued[user_id] = [t for t in self._issued[user_id] if now - t < WINDOW]
            if not self._issued[user_id]:
                del self._issued[user_id]

    def _drop(self, user_id: int) -> None:
        challenge = self._items.pop(user_id, None)
        if challenge is not None:
            self._images.pop(challenge.id, None)

    async def issue(self, user_id: int) -> tuple[Challenge, bytes]:
        """Новая капча вместо прежней и её картинка (JPEG)."""
        self._cleanup()
        recent = self._issued.setdefault(user_id, [])
        if len(recent) >= MAX_PER_WINDOW:
            raise CaptchaRateLimited
        recent.append(time.monotonic())

        rng = random.Random(secrets.randbits(64))
        word = rng.choice(words())
        image = await asyncio.to_thread(render_word, word, rng)
        challenge = Challenge(secrets.token_urlsafe(9), user_id, word, self._ttl)
        self._drop(user_id)
        self._items[user_id] = challenge
        if self._keep_images:
            self._images[challenge.id] = image
        return challenge, image

    def discard(self, user_id: int) -> None:
        self._drop(user_id)

    def image(self, cid: str) -> bytes | None:
        return self._images.get(cid)

    def current(self, user_id: int) -> Challenge | None:
        return self._items.get(user_id)

    def get(self, user_id: int, cid: str) -> Challenge | None:
        """Капча этого человека с этим кодом (кнопки и ссылки старых картинок не подходят)."""
        challenge = self._items.get(user_id)
        return challenge if challenge is not None and challenge.id == cid else None

    def verify(self, user_id: int, answer: object, cid: str | None = None) -> bool:
        """Проверяет ответ. Капча одноразовая: после любой попытки нужна новая."""
        challenge = self._items.get(user_id) if cid is None else self.get(user_id, cid)
        if challenge is None:
            return False
        self._drop(user_id)
        if challenge.expired or not isinstance(answer, str) or normalize(answer) != challenge.answer:
            return False
        self._passed[user_id] = time.monotonic()
        return True

    def recently_passed(self, user_id: int) -> bool:
        passed = self._passed.get(user_id)
        return passed is not None and time.monotonic() - passed <= PASS_TTL
