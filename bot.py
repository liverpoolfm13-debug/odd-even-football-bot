
import os
import json
import subprocess
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

API_FOOTBALL_KEY = os.environ["API_FOOTBALL_KEY"]
API_FOOTBALL_URL = "https://v3.football.api-sports.io/fixtures"

OLD_API_KEY = os.environ.get("FOOTBALL_API_KEY")
OLD_API_URL = "https://openfootapi.com/v1/matches"

STATE_FILE = Path("state.json")
TASHKENT = ZoneInfo("Asia/Tashkent")
UTC = timezone.utc

FINAL_STATUSES = {"FT", "AET", "PEN", "AWD", "WO"}
CANCEL_STATUSES = {"PST", "CANC", "ABD"}

# Защита от вечного ожидания старого матча.
STALE_AFTER_HOURS = 6

# Если API сообщил о завершении, но не дал счёт,
# ждём дольше, прежде чем закрыть запись без результата.
SCORE_GRACE_HOURS = 24


def get_json(url, headers):
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


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


def old_api_matches(day):
    if not OLD_API_KEY:
        raise RuntimeError(
            "Missing FOOTBALL_API_KEY for old active match"
        )

    params = urllib.parse.urlencode({"date": day.isoformat()})
    payload = get_json(
        f"{OLD_API_URL}?{params}",
        {
            "Accept": "application/json",
            "Authorization": f"Bearer {OLD_API_KEY}",
        },
    )

    if not isinstance(payload.get("data"), list):
        raise RuntimeError("OpenFoot returned an unexpected response")

    return payload["data"]


def api_football_request(params):
    url = f"{API_FOOTBALL_URL}?{urllib.parse.urlencode(params)}"

    payload = get_json(
        url,
        {
            "x-apisports-key": API_FOOTBALL_KEY,
            "Accept": "application/json",
        },
    )

    if payload.get("errors"):
        raise RuntimeError(
            f"API-Football error: {payload['errors']}"
        )

    matches = payload.get("response")
    if not isinstance(matches, list):
        raise RuntimeError("Unexpected API-Football response")

    return matches


def api_football_match_by_id(fixture_id):
    # Отдельно проверяем активный матч по ID,
    # даже если его уже нет в списке сегодняшних матчей.
    matches = api_football_request({"id": str(fixture_id)})

    for match in matches:
        current_id = (match.get("fixture") or {}).get("id")
        if str(current_id) == str(fixture_id):
            return match

    return None


def api_football_matches():
    today = datetime.now(TASHKENT).date()
    matches_by_id = {}

    for day in (today, today + timedelta(days=1)):
        matches = api_football_request({
            "date": day.isoformat(),
            "timezone": "Asia/Tashkent",
        })

        for match in matches:
            fixture_id = (match.get("fixture") or {}).get("id")
            if fixture_id is not None:
                matches_by_id[str(fixture_id)] = match

    print("API-Football fixtures received:", len(matches_by_id))
    return list(matches_by_id.values())


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
    changed = subprocess.run(["git", "diff", "--cached", "--quiet"])

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


def parse_datetime(value):
    if not value:
        return None

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)

    return parsed.astimezone(UTC)


def old_kickoff(match):
    return parse_datetime(match.get("kickoffAt"))


def new_kickoff(match):
    fixture = match.get("fixture") or {}
    return parse_datetime(fixture.get("date"))


def old_team_names(match):
    home = match.get("homeTeam") or {}
    away = match.get("awayTeam") or {}

    return (
        home.get("name", "Неизвестная команда"),
        away.get("name", "Неизвестная команда"),
    )


def new_team_names(match):
    teams = match.get("teams") or {}
    home = teams.get("home") or {}
    away = teams.get("away") or {}

    return (
        home.get("name", "Неизвестная команда"),
        away.get("name", "Неизвестная команда"),
    )


def old_side_score(value):
    if isinstance(value, dict):
        for key in ("goals", "score", "total"):
            if key in value:
                result = old_side_score(value[key])
                if result is not None:
                    return result
        return None

    if isinstance(value, int) and not isinstance(value, bool):
        return value

    if isinstance(value, str) and value.isdigit():
        return int(value)

    return None


def old_extract_score(match):
    score = match.get("score")

    if isinstance(score, dict):
        for key in ("fullTime", "full_time", "regularTime", "ft"):
            section = score.get(key)

            if isinstance(section, dict):
                home = old_side_score(section.get("home"))
                away = old_side_score(section.get("away"))

                if home is not None and away is not None:
                    return home, away

        home = old_side_score(
            score.get("home", score.get("homeScore"))
        )
        away = old_side_score(
            score.get("away", score.get("awayScore"))
        )

        if home is not None and away is not None:
            return home, away

    for home_key, away_key in (
        ("homeScore", "awayScore"),
        ("homeGoals", "awayGoals"),
    ):
        home = old_side_score(match.get(home_key))
        away = old_side_score(match.get(away_key))

        if home is not None and away is not None:
            return home, away

    return None


def new_extract_score(match):
    goals = match.get("goals") or {}
    home = goals.get("home")
    away = goals.get("away")

    if (
        isinstance(home, int) and not isinstance(home, bool)
        and isinstance(away, int) and not isinstance(away, bool)
    ):
        return home, away

    return None


def remember_processed(state, match_key):
    processed = state["processed"]

    if match_key not in processed:
        processed.append(match_key)

    state["processed"] = processed[-200:]


def send_result(active, score):
    home_goals, away_goals = score
    total = home_goals + away_goals
    actual = "ЧЁТ" if total % 2 == 0 else "НЕЧЁТ"
    prediction = active["prediction"]

    verdict = (
        "✅ УГАДАНО" if prediction == actual
        else "❌ НЕ УГАДАНО"
    )

    send_telegram(
        f"🏁 РЕЗУЛЬТАТ МАТЧА\n\n"
        f"{active['home']} — {active['away']}\n"
        f"Счёт: {home_goals}:{away_goals}\n"
        f"Всего голов: {total} — {actual}\n\n"
        f"Прогноз бота: {prediction}\n"
        f"{verdict}"
    )


def finish_cancelled(state, active, status, provider):
    send_telegram(
        f"⚠️ Матч отменён, перенесён или прерван:\n"
        f"{active['home']} — {active['away']}\n"
        f"Статус: {status}"
    )

    remember_processed(state, f"{provider}:{active['id']}")
    state["active"] = None
    save_state(state)


def finish_stale(state, active, provider, reason):
    # Не объявляем матч отменённым и не придумываем счёт.
    # Сообщаем, что результат не удалось получить.
    send_telegram(
        f"⚠️ Старая запись матча закрыта\n\n"
        f"{active['home']} — {active['away']}\n"
        f"ID матча: {active['id']}\n"
        f"Причина: {reason}\n\n"
        f"Итоговый счёт не подтверждён. "
        f"Это не означает, что матч отменён.\n"
        f"Бот продолжит поиск новых матчей."
    )

    remember_processed(state, f"{provider}:{active['id']}")
    state["active"] = None
    save_state(state)


def is_older_than(kickoff, hours, now):
    return now >= kickoff + timedelta(hours=hours)


def close_if_score_unavailable(state, active, provider, now):
    kickoff = parse_datetime(active.get("kickoffAt"))

    if (
        kickoff
        and is_older_than(kickoff, SCORE_GRACE_HOURS, now)
    ):
        finish_stale(
            state,
            active,
            provider,
            "API сообщил о завершении матча, "
            "но итоговый счёт долго недоступен",
        )
        return True

    return False


def main():
    now = datetime.now(UTC)
    state = load_state()
    active = state.get("active")

    # 1. Обрабатываем оставшийся матч старого OpenFoot API.
    if active and active.get("provider", "openfoot") == "openfoot":
        if not OLD_API_KEY:
            raise RuntimeError(
                "An old OpenFoot match is still active. "
                "Keep FOOTBALL_API_KEY in GitHub Secrets."
            )

        kickoff = old_kickoff(active)

        if kickoff is None:
            raise RuntimeError(
                "Old active match has no valid kickoffAt"
            )

        old_matches = old_api_matches(
            kickoff.astimezone(TASHKENT).date()
        )

        current = next(
            (
                match for match in old_matches
                if str(match.get("id")) == str(active["id"])
            ),
            None,
        )

        if current is None:
            if is_older_than(kickoff, STALE_AFTER_HOURS, now):
                finish_stale(
                    state,
                    active,
                    "openfoot",
                    "Матч отсутствует в ответе старого API "
                    "более 6 часов после начала",
                )
            else:
                print("Old active match not found; waiting.")
                return

        else:
            status = str(current.get("status", "")).lower()

            if status in (
                "postponed", "cancelled", "canceled", "abandoned"
            ):
                finish_cancelled(
                    state, active, status, "openfoot"
                )

            elif status == "finished":
                score = old_extract_score(current)

                if score is None:
                    if close_if_score_unavailable(
                        state, active, "openfoot", now
                    ):
                        pass
                    else:
                        print(
                            "Old match finished, "
                            "but score is not available yet."
                        )
                        return
                else:
                    send_result(active, score)
                    remember_processed(
                        state, f"openfoot:{active['id']}"
                    )
                    state["active"] = None
                    save_state(state)

            elif is_older_than(kickoff, STALE_AFTER_HOURS, now):
                finish_stale(
                    state,
                    active,
                    "openfoot",
                    f"Статус матча не обновлялся: {status}",
                )

            else:
                print(f"Waiting for old match to finish: {status}")
                return

    # 2. Получаем сегодняшние и завтрашние матчи.
    matches = api_football_matches()
    active = state.get("active")

    # 3. Проверяем активный матч API-Football.
    if active and active.get("provider") == "api_football":
        fixture_id = active["id"]

        current = next(
            (
                match for match in matches
                if str((match.get("fixture") or {}).get("id"))
                == str(fixture_id)
            ),
            None,
        )

        # Если в списке нет, запрашиваем отдельно по ID.
        if current is None:
            current = api_football_match_by_id(fixture_id)

        kickoff = parse_datetime(active.get("kickoffAt"))

        if current is None:
            if (
                kickoff
                and is_older_than(
                    kickoff, STALE_AFTER_HOURS, now
                )
            ):
                finish_stale(
                    state,
                    active,
                    "api_football",
                    "Матч не найден даже при отдельном "
                    "запросе по ID более 6 часов после начала",
                )
            else:
                print(
                    "Active match not found by date or ID; "
                    "waiting for the next run."
                )
                return

        else:
            fixture = current.get("fixture") or {}
            status_obj = fixture.get("status") or {}
            status = str(status_obj.get("short", "")).upper()

            if status in FINAL_STATUSES:
                score = new_extract_score(current)

                if score is None:
                    if close_if_score_unavailable(
                        state, active, "api_football", now
                    ):
                        pass
                    else:
                        print(
                            "Match finished, but final score "
                            "is not available yet."
                        )
                        return
                else:
                    send_result(active, score)
                    remember_processed(
                        state, f"api_football:{fixture_id}"
                    )
                    state["active"] = None
                    save_state(state)

            elif status in CANCEL_STATUSES:
                finish_cancelled(
                    state, active, status, "api_football"
                )

            elif (
                kickoff
                and is_older_than(
                    kickoff, STALE_AFTER_HOURS, now
                )
            ):
                finish_stale(
                    state,
                    active,
                    "api_football",
                    f"Матч слишком долго не получил "
                    f"финальный статус: {status or 'неизвестен'}",
                )

            else:
                print(f"Waiting for active match to finish: {status}")
                return

    # 4. Если старая запись закрыта, выбираем новый матч.
    if state.get("active") is not None:
        print("An active match remains; not selecting another.")
        return

    processed = set(state["processed"])
    window_end = now + timedelta(hours=2)
    candidates = []

    for match in matches:
        fixture = match.get("fixture") or {}
        fixture_id = fixture.get("id")
        kickoff = new_kickoff(match)

        status_obj = fixture.get("status") or {}
        status = str(status_obj.get("short", "")).upper()

        if not fixture_id or kickoff is None:
            continue

        if f"api_football:{fixture_id}" in processed:
            continue

        if status != "NS":
            continue

        if not (now < kickoff <= window_end):
            continue

        candidates.append((kickoff, match))

    candidates.sort(key=lambda item: item[0])

    if not candidates:
        print("No suitable match starting within the next 2 hours.")
        return

    kickoff, match = candidates[0]
    fixture = match.get("fixture") or {}
    fixture_id = fixture["id"]

    home, away = new_team_names(match)
    prediction = "НЕЧЁТ"

    local_time = kickoff.astimezone(TASHKENT).strftime(
        "%d.%m.%Y %H:%M"
    )

    state["active"] = {
        "provider": "api_football",
        "id": str(fixture_id),
        "kickoffAt": kickoff.isoformat(),
        "home": home,
        "away": away,
        "prediction": prediction,
    }

    # Сначала сохраняем состояние, затем отправляем прогноз.
    save_state(state)

    send_telegram(
        f"⚽ ПРОГНОЗ НА МАТЧ\n\n"
        f"{home} — {away}\n"
        f"🕒 Начало: {local_time} (Ташкент)\n\n"
        f"🎯 Тотал голов: {prediction}\n\n"
        f"ℹ️ Экспериментальный прогноз, не гарантия."
    )

    print(f"Prediction sent for {home} — {away}")


if __name__ == "__main__":
    main()
