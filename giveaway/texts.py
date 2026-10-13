from collections.abc import Iterable
from html import escape

from .database import KIND_BOT, KIND_CHANNEL, KIND_CHAT, Participant, Sponsor
from .media import Background
from .prizes import total_prize

KIND_ICONS = {KIND_CHANNEL: "📢", KIND_CHAT: "💬", KIND_BOT: "🤖"}
KIND_NAMES = {KIND_CHANNEL: "Канал", KIND_CHAT: "Чат", KIND_BOT: "Бот"}

# ---------- Участники розыгрыша ----------


def sponsor_line(s: Sponsor) -> str:
    line = f'{KIND_ICONS[s.kind]} <a href="{escape(s.url)}">{escape(s.title)}</a>'
    if s.amount:
        line += f" — 💰 <b>{escape(s.amount)}</b>"
    return line


def welcome(first_name: str, giveaway: str, sponsors: list[Sponsor]) -> str:
    kinds = {s.kind for s in sponsors}
    steps = []
    if KIND_CHANNEL in kinds:
        steps.append("подпишись на все каналы спонсоров")
    if KIND_CHAT in kinds:
        steps.append("вступи во все чаты")
    if KIND_BOT in kinds:
        steps.append("запусти всех ботов спонсоров")
    steps_text = "\n".join(f"{i}. {step.capitalize()}" for i, step in enumerate(steps, 1))

    return (
        f"🇪🇺 <b>{escape(giveaway)}</b>\n\n"
        f"Привет, {escape(first_name)}! 👋\n\n"
        f"Чтобы участвовать в розыгрыше, выполни условия:\n{steps_text}\n\n"
        f"<b>Спонсоры розыгрыша:</b>\n"
        + "\n".join(sponsor_line(s) for s in sponsors)
        + "\n\nКогда всё будет готово — нажми «✅ Проверить подписку», "
        "и бот выдаст тебе уникальный номер участника."
    )


def no_sponsors(giveaway: str) -> str:
    return (
        f"🇪🇺 <b>{escape(giveaway)}</b>\n\nРозыгрыш ещё готовится — список спонсоров пока пуст. Загляни чуть позже! ⏳"
    )


def missing(sponsors: list[Sponsor]) -> str:
    return (
        "❌ <b>Ещё не все условия выполнены</b>\n\n"
        "Ты не подписан(а) на:\n"
        + "\n".join(f"• {sponsor_line(s)}" for s in sponsors)
        + "\n\nПодпишись и нажми «✅ Проверить подписку» ещё раз."
    )


def success(number: int, giveaway: str) -> str:
    return (
        "🎉 <b>Все условия выполнены!</b>\n\n"
        f"Ты участник розыгрыша <b>{escape(giveaway)}</b>.\n"
        f"Твой номер участника: <b>№{number}</b>\n\n"
        "Сохрани его — победители будут определяться по номерам. Удачи! 🍀"
    )


def already_participant(number: int, giveaway: str) -> str:
    return (
        f"✅ Ты уже участвуешь в розыгрыше <b>{escape(giveaway)}</b>.\n\n"
        f"Твой номер участника: <b>№{number}</b>\n\n"
        "Не отписывайся от спонсоров до подведения итогов — победителей перепроверяют. 🍀"
    )


# ---------- Админка ----------


def admin_menu(giveaway: str, sponsors: list[Sponsor], participants: int, webapp_url: str | None) -> str:
    counts = {kind: sum(1 for s in sponsors if s.kind == kind) for kind in KIND_ICONS}
    by_kind = " · ".join(f"{KIND_ICONS[k]} {n}" for k, n in counts.items())
    prize = total_prize(s.amount for s in sponsors)
    webapp = "✅ подключено" if webapp_url else "⚠️ не подключено (нужен WEBAPP_URL)"
    return (
        f"⚙️ <b>Админ-панель — {escape(giveaway)}</b>\n\n"
        f"Спонсоров: <b>{len(sponsors)}</b> ({by_kind})\n"
        f"Призовой фонд: <b>{escape(prize) if prize else 'суммы не указаны'}</b>\n"
        f"Участников: <b>{participants}</b>\n"
        f"Мини-приложение: {webapp}"
    )


def background_status(background: Background | None, webapp_url: str | None) -> str:
    kinds = {"image": "картинка", "video": "видео / GIF"}
    current = f"загружен ({kinds.get(background.kind, background.kind)})" if background else "не задан"
    lines = [
        "🖼 <b>Фон мини-приложения</b>",
        "",
        f"Сейчас: <b>{current}</b>",
        "",
        "Фон растягивается на весь экран и слегка затемняется, чтобы текст хорошо читался. "
        "Подойдёт картинка, GIF или видео до 20 МБ.",
    ]
    if not webapp_url:
        lines += ["", "⚠️ Мини-приложение не подключено: укажите WEBAPP_URL в настройках хостинга."]
    return "\n".join(lines)


def sponsors_list(sponsors: list[Sponsor]) -> str:
    if not sponsors:
        return "📋 <b>Спонсоры</b>\n\nСписок пуст. Нажмите «➕ Добавить спонсора»."
    return (
        "📋 <b>Спонсоры</b>\n\n"
        + "\n".join(sponsor_line(s) for s in sponsors)
        + "\n\nВыберите спонсора, чтобы изменить сумму или удалить его."
    )


def sponsor_card(s: Sponsor, bot_admin: bool | None) -> str:
    lines = [
        f"{KIND_ICONS[s.kind]} <b>{escape(s.title)}</b>",
        "",
        f"Тип: {KIND_NAMES[s.kind]}",
        f"Ссылка: {escape(s.url)}",
    ]
    if s.chat_id is not None:
        lines.append(f"ID: <code>{s.chat_id}</code>")
    lines.append(f"Сумма: <b>{escape(s.amount)}</b>" if s.amount else "Сумма: не указана")
    lines.append("")
    if s.kind == KIND_BOT:
        lines.append(
            "ℹ️ Telegram не позволяет проверить, запустил ли пользователь чужого бота, "
            "поэтому это условие показывается участникам, но не проверяется."
        )
    elif bot_admin:
        lines.append("✅ Бот — администратор, подписка проверяется.")
    else:
        lines.append(
            "⚠️ Бот не является администратором — проверка подписки не работает, "
            "и участники не смогут получить номер! Назначьте бота администратором."
        )
    return "\n".join(lines)


def participant_line(p: Participant) -> str:
    username = f" @{escape(p.username)}" if p.username else ""
    return (
        f'<b>№{p.number}</b> — <a href="tg://user?id={p.user_id}">{escape(p.full_name or "—")}</a>'
        f"{username} · <code>{p.user_id}</code>"
    )


def participants_page(participants: Iterable[Participant], total: int, page: int, pages: int) -> str:
    body = "\n".join(participant_line(p) for p in participants) or "Пока никто не выполнил условия."
    return f"👥 <b>Участники</b> (всего: {total}) · стр. {page + 1}/{pages}\n\n{body}"


def participant_card(p: Participant) -> str:
    return (
        f"🎟 <b>Участник №{p.number}</b>\n\n"
        f'Имя: <a href="tg://user?id={p.user_id}">{escape(p.full_name or "—")}</a>\n'
        f"Username: {'@' + escape(p.username) if p.username else '—'}\n"
        f"ID: <code>{p.user_id}</code>\n"
        f"Получил номер: {escape(p.joined_at)} (UTC)"
    )


def recheck_result(missing_sponsors: list[Sponsor]) -> str:
    if not missing_sponsors:
        return "✅ Сейчас подписан(а) на все каналы и чаты спонсоров."
    return "❌ Сейчас НЕ подписан(а) на:\n" + "\n".join(f"• {sponsor_line(s)}" for s in missing_sponsors)
