import os
import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
FOOTBALL_API_KEY = os.environ["FOOTBALL_API_KEY"]

API_URL = "https://openfootapi.com/v1/matches"
TASHKENT = ZoneInfo("Asia/Tashkent")


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": text
    }).encode("utf-8")

    request = urllib.request.Request(url, data=data)

    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))

    if not result.get("ok"):
        raise RuntimeError("Telegram did not accept the message")


def get_matches(day):
    params = urllib.parse.urlencode({
        "date": day.isoformat(),
        "status": "scheduled"
    })

    request = urllib.request.Request(
        f"{API_URL}?{params}",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {FOOTBALL_API_KEY}"
        }
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8"))

    if "data" not in result:
        raise RuntimeError("API response did not contain match data")

    return result["data"]


def main():
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=4)

    matches_by_id = {}

    for day in (now.date(), (now + timedelta(days=1)).date()):
        for match in get_matches(day):
            kickoff = match.get("kickoffAt")
            if not kickoff:
                continue

            kickoff_time = datetime.fromisoformat(
                kickoff.replace("Z", "+00:00")
            )

            if now <= kickoff_time <= cutoff:
                matches_by_id[match.get("id", kickoff)] = (
                    kickoff_time, match
                )

    matches = sorted(
        matches_by_id.values(),
        key=lambda item: item[0]
    )[:10]

    if not matches:
        send_telegram(
            "⚽ Футбольный бот\n\n"
            "В ближайшие 4 часа API не вернул запланированных матчей."
        )
        return

    lines = [
        "⚽ ПРЕДСТОЯЩИЕ ФУТБОЛЬНЫЕ МАТЧИ",
        "Расписание на ближайшие 4 часа",
        ""
    ]

    for number, (kickoff_time, match) in enumerate(matches, start=1):
        home = match.get("homeTeam", {}).get("name", "Неизвестная команда")
        away = match.get("awayTeam", {}).get("name", "Неизвестная команда")
        local_time = kickoff_time.astimezone(TASHKENT).strftime("%H:%M")

        lines.append(f"{number}. {home} — {away}")
        lines.append(f"🕒 Начало: {local_time} (Ташкент)")
        lines.append("")

    lines.append("ℹ️ Это проверка расписания. Прогнозы будут добавлены следующим этапом.")

    send_telegram("\n".join(lines))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Bot error: {type(error).__name__}: {error}")
        send_telegram(
            "⚠️ Не удалось получить расписание матчей.\n"
            f"Ошибка: {type(error).__name__}: {error}"
        )
        raise
