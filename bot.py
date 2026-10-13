import os
import json
import random
import subprocess
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
API_KEY = os.environ["FOOTBALL_API_KEY"]

API_URL = "https://openfootapi.com/v1/matches"
STATE_FILE = Path("state.json")
TASHKENT = ZoneInfo("Asia/Tashkent")
UTC = timezone.utc


def api_matches(day):
    params = urllib.parse.urlencode({"date": day.isoformat()})
    request = urllib.request.Request(
        f"{API_URL}?{params}",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {API_KEY}",
        },
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if not isinstance(payload.get("data"), list):
        raise RuntimeError("OpenFoot API did not return a match list")

    return payload["data"]


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    body = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": message,
    }).encode("utf-8")

    request = urllib.request.Request(url, data=body)
    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))

    if not result.get("ok"):
        raise RuntimeError("Telegram did not accept the message")


def load_state():
    if not STATE_FILE.exists():
        return {"active": None, "processed": []}

    with STATE_FILE.open("r", encoding="utf-8") as file:
        state = json.load(file)

    state.setdefault("active", None)
    state.setdefault("processed", [])
    return state


def save_state(state):
    with STATE_FILE.open("w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)

    subprocess.run(["git", "add", "state.json"], check=True)

    changed = subprocess.run(
        ["git", "diff", "--cached", "--quiet"]
    )

    if changed.returncode == 1:
        subprocess.run(
            ["git", "config", "user.name", "github-actions[bot]"],
            check=True,
        )
        subprocess.run(
            [
                "git", "config", "user.email",
                "41898282+github-actions[bot]@users.noreply.github.com",
            ],
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Save football bot state"],
            check=True,
        )
        subprocess.run(["git", "push"], check=True)
    elif changed.returncode != 0:
        raise RuntimeError("Could not check state changes")


def kickoff_datetime(match):
    value = match.get("kickoffAt")
    if not value:
        return None

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def team_names(match):
    home = match.get("homeTeam") or {}
    away = match.get("awayTeam") or {}
    return (
        home.get("name", "Неизвестная команда"),
        away.get("name", "Неизвестная команда"),
    )


def side_score(value):
    if isinstance(value, dict):
        for key in ("goals", "score", "total"):
            if key in value:
                result = side_score(value[key])
                if result is not None:
                    return result
        return None

    if isinstance(value, int) and not isinstance(value, bool):
        return value

    if isinstance(value, str) and value.isdigit():
        return int(value)

    return None


def extract_score(match):
    # Check common score formats returned by football APIs.
    score = match.get("score")

    if isinstance(score, dict):
        for key in ("fullTime", "full_time", "regularTime", "ft"):
            section = score.get(key)
            if isinstance(section, dict):
                home = side_score(section.get("home"))
                away = side_score(section.get("away"))
                if home is not None and away is not None:
                    return home, away

        home = side_score(score.get("home", score.get("homeScore")))
        away = side_score(score.get("away", score.get("awayScore")))
        if home is not None and away is not None:
            return home, away

    for home_key, away_key in (
        ("homeScore", "awayScore"),
        ("homeGoals", "awayGoals"),
    ):
        home = side_score(match.get(home_key))
        away = side_score(match.get(away_key))
        if home is not None and away is not None:
            return home, away

    return None


def remember_processed(state, match_id):
    processed = state["processed"]
    if match_id not in processed:
        processed.append(match_id)
    state["processed"] = processed[-100:]


def main():
    now = datetime.now(UTC)
    today = now.date()
    state = load_state()

    # Fetch today's and tomorrow's matches.
    matches_by_date = {
        today: api_matches(today),
        today + timedelta(days=1): api_matches(
            today + timedelta(days=1)
        ),
    }

    active = state.get("active")

    if active:
        active_date = datetime.fromisoformat(
            active["kickoffAt"].replace("Z", "+00:00")
        ).date()

        if active_date not in matches_by_date:
            matches_by_date[active_date] = api_matches(active_date)

        current = next(
            (
                match
                for match in matches_by_date[active_date]
                if str(match.get("id")) == str(active["id"])
            ),
            None,
        )

        if current is None:
            print("Active match not found yet; waiting.")
            return

        status = str(current.get("status", "")).lower()

        if status in ("postponed", "cancelled"):
            send_telegram(
                f"⚠️ Матч отменён или перенесён:\n"
                f"{active['home']} — {active['away']}\n"
                f"Статус: {status}"
            )
            remember_processed(state, active["id"])
            state["active"] = None
            save_state(state)

        elif status == "finished":
            score = extract_score(current)

            if score is None:
                print("Match finished, but final score is not available yet.")
                return

            home_goals, away_goals = score
            total = home_goals + away_goals
            actual = "ЧЁТ" if total % 2 == 0 else "НЕЧЁТ"
            prediction = active["prediction"]
            verdict = "✅ УГАДАНО" if prediction == actual else "❌ НЕ УГАДАНО"

            send_telegram(
                f"🏁 РЕЗУЛЬТАТ МАТЧА\n\n"
                f"{active['home']} — {active['away']}\n"
                f"Счёт: {home_goals}:{away_goals}\n"
                f"Всего голов: {total} — {actual}\n\n"
                f"Прогноз бота: {prediction}\n"
                f"{verdict}"
            )

            remember_processed(state, active["id"])
            state["active"] = None
            save_state(state)

        else:
            print(f"Waiting for match to finish. Status: {status}")
            return

    processed = set(state["processed"])
    candidates = []

    for day_matches in matches_by_date.values():
        for match in day_matches:
            match_id = match.get("id")
            kickoff = kickoff_datetime(match)

            if not match_id or kickoff is None:
                continue
            if str(match_id) in processed:
                continue
            if str(match.get("status", "")).lower() != "scheduled":
                continue
            if kickoff <= now:
                continue

            candidates.append((kickoff, match))

    candidates.sort(key=lambda item: item[0])

    if not candidates:
        print("No suitable upcoming match found.")
        return

    kickoff, match = candidates[0]
    home, away = team_names(match)
    prediction = random.choice(["ЧЁТ", "НЕЧЁТ"])
    local_time = kickoff.astimezone(TASHKENT).strftime(
        "%d.%m.%Y %H:%M"
    )

    state["active"] = {
        "id": str(match["id"]),
        "kickoffAt": kickoff.isoformat(),
        "home": home,
        "away": away,
        "prediction": prediction,
    }

    send_telegram(
        f"⚽ ПРОГНОЗ НА МАТЧ\n\n"
        f"{home} — {away}\n"
        f"🕒 Начало: {local_time} (Ташкент)\n\n"
        f"🎯 Тотал голов: {prediction}\n\n"
        f"ℹ️ Экспериментальный прогноз, не гарантия."
    )

    save_state(state)
    print(f"Prediction sent for {home} — {away}")


if __name__ == "__main__":
    main()
