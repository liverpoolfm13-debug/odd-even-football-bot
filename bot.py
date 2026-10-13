import os
import json
import urllib.request
import urllib.parse
import urllib.error

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()


def send_message(text):
    if not TOKEN or not CHAT_ID:
        raise RuntimeError(
            "Не заданы TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID в GitHub Secrets."
        )

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": text
    }).encode("utf-8")

    request = urllib.request.Request(url, data=data)

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))

        if not result.get("ok"):
            raise RuntimeError(f"Telegram API сообщил об ошибке: {result}")

        print("Успех: Telegram принял сообщение.")
        print("Message ID:", result["result"]["message_id"])

    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Ошибка Telegram API: HTTP {error.code}: {details}"
        ) from error


if __name__ == "__main__":
    send_message(
        "⚽️ Odd/Even Football Bot\n\n"
        "Тестовое сообщение успешно отправлено!\n"
        "Следующий этап — подключение расписания матчей."
    )
