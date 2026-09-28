#!/usr/bin/env python3
"""OTT-play remote CLI. Python 3 standard library; secrets stay in private files."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

HELP = """ott [--config FILE] [--json] PLAYER [COMMAND ...]
  ott devices                         устройства и время последнего подключения
  ott alias NAME UUID                 короткое имя существующего устройства
  ott add NAME UUID                   создать отдельный код и очередь устройства
  ott pair NAME                      показать адрес и код для ввода в плеере
  ott NAME                           состояние плеера
  ott NAME 12                        включить канал №12 из списка s
  ott NAME название                  найти и включить канал (без учёта регистра)
  ott NAME play s                    включить канал с зарезервированным именем
  ott NAME s [текст]                  каналы, фильтр по названию
  ott NAME p [текст]                  канал — текущая программа, фильтр по программе
  ott NAME v                         громкость
  ott NAME v 35                      громкость 35%
  ott NAME v +5 / v -5               прибавить / убавить
  ott NAME providers                 список провайдеров (индексы с нуля)
  ott NAME provider m3u               выбрать провайдера по ID, индексу или имени
  ott NAME provider-config FILE      настройки активного провайдера из JSON
  ott NAME playlist URL              изменить M3U-плейлист
  ott NAME random [FROM TO]           случайный канал
  ott NAME msg текст                 сообщение на экране
  ott NAME exit                      закрыть плеер / standby

Настройки: ~/.config/ottplay-control/cli.json или OTT_CONFIG.
Поиск имён плееров, каналов, программ и провайдеров регистронезависимый.
"""


class Error(Exception):
    pass


class HTTPError(Error):
    def __init__(self, code):
        self.code = code
        super().__init__(f"Сервер вернул HTTP {code}. " + {
            401: "Проверьте admin_token в приватной конфигурации.",
            403: "Недостаточно прав или запрещён Origin.",
            404: "Проверьте адрес и обновите сервер для поддержки запросов CLI.",
            429: "Очередь или лимит запросов исчерпан; повторите позже.",
        }.get(code, "Проверьте параметры запроса."))


def read_json(path):
    try:
        with Path(path).expanduser().open(encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError) as exc:
        raise Error(f"Не удалось прочитать JSON-файл {path}") from exc


def write_private(path, data):
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".ott-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Client:
    def __init__(self, config, timeout=45):
        self.config = config
        self.timeout = timeout
        self.server = config["server"].rstrip("/")
        parsed = urllib.parse.urlsplit(self.server)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise Error("server должен быть HTTP(S)-адресом без пароля, query или fragment")
        self.credentials = read_json(config["server_config"])
        self.token = self.credentials["admin_token"]
        # Never forward an administrator credential through an HTTP redirect.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        self.opener = urllib.request.build_opener(NoRedirect())

    def api(self, path, payload=None):
        headers = {"Authorization": "Bearer " + self.token, "User-Agent": "ottplay-cli/1.0"}
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.server + path, data=body, headers=headers)
        try:
            with self.opener.open(request, timeout=min(10, self.timeout)) as response:
                body = response.read(2 * 1024 * 1024 + 1)
                if len(body) > 2 * 1024 * 1024:
                    raise Error("Ответ сервера превышает лимит")
                return response.status, json.loads(body)
        except urllib.error.HTTPError as exc:
            raise HTTPError(exc.code) from None
        except (OSError, ValueError) as exc:
            raise Error("Сервер недоступен или вернул неверный JSON; проверьте адрес и подключение") from exc

    def device(self, name):
        aliases = self.config.get("players", {})
        matches = [value for key, value in aliases.items() if key.casefold() == name.casefold()]
        if len(matches) == 1:
            return matches[0]
        ids = [row["id"] for row in self.credentials.get("devices", []) if row["id"].casefold() == name.casefold()]
        if len(ids) == 1:
            return ids[0]
        raise Error(f"Неизвестный плеер {name!r}. Выполните ott devices или ott add ИМЯ UUID")

    def call(self, device, action, params):
        query = "?" + urllib.parse.urlencode({"device_id": device})
        _, queued = self.api("/api/requests" + query, {"action": action, "params": params})
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            time.sleep(0.8)
            status, result = self.api("/api/requests" + query + "&id=" + queued["id"])
            if status == 202:
                continue
            if result.get("status") != "ok":
                data = result.get("data") or {}
                details = "\n".join(f"{row.get('number', row.get('index', ''))}: {clean(row.get('name', ''))}" for row in data.get("matches", []))
                raise Error(clean(data.get("error", "Плеер отклонил запрос")) + ("\n" + details if details else ""))
            return result["data"]
        raise Error("Плеер не ответил. Откройте его, проверьте адрес/код и версию с поддержкой CLI. Запрос ещё может выполниться до истечения TTL; не повторяйте изменение вслепую.")


def clean(value):
    # Provider-controlled names must not send terminal control/escape sequences.
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", str(value))


def parse_command(words):
    if not words:
        return "status", {}
    verb, tail = words[0].casefold(), words[1:]
    text = " ".join(tail)
    if verb in ("s", "p"):
        return ("channels" if verb == "s" else "programs"), {"search": text}
    if verb == "v":
        if not tail:
            return "status", {}
        if len(tail) != 1 or not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", text):
            raise Error("Используйте v, v 35, v +5 или v -5")
        value = float(text)
        relative = text.startswith(("+", "-"))
        if not math.isfinite(value) or (not relative and not 0 <= value <= 100):
            raise Error("Абсолютная громкость должна быть 0–100")
        return "command", {"command": "set_volume", "volume_step" if relative else "volume": value}
    if verb in ("status", "providers") and not tail:
        return verb, {}
    if verb == "provider" and tail:
        return "provider", {"query": text}
    if verb == "provider-config":
        if len(tail) != 1:
            raise Error("Используйте provider-config FILE.json")
        data = read_json(tail[0])
        if not isinstance(data, dict) or set(data) != {"provider", "settings"} or not isinstance(data["settings"], dict):
            raise Error('Ожидается {"provider":"xtream","settings":{...}}')
        return "provider_settings", data
    if verb == "playlist" and len(tail) == 1:
        return "provider_settings", {"provider": "m3u", "settings": {"playlist": text}}
    if verb == "msg" and tail:
        return "command", {"command": "popup_message", "message": text}
    if verb == "exit" and not tail:
        return "command", {"command": "exit_player"}
    if verb == "random":
        params = {"command": "random_channel"}
        if tail:
            if len(tail) != 2 or not all(re.fullmatch(r"[1-9]\d*", x) for x in tail) or int(tail[0]) > int(tail[1]):
                raise Error("Используйте random или random FROM TO (номера с 1)")
            params["random_range"] = [int(x) for x in tail]
        return "command", params
    if verb == "play":
        if not tail:
            raise Error("После play нужен номер или название канала")
        return "play", {"query": text}
    return "play", {"query": " ".join(words)}


def management(client, config_path, words):
    verb = words[0].casefold()
    if verb == "devices" and len(words) == 1:
        _, data = client.api("/api/devices")
        for row in data["devices"]:
            aliases = [key for key, value in client.config.get("players", {}).items() if value == row["id"]]
            print(f"{','.join(aliases) or '—'}  {row['id']}  last_seen={row.get('last_seen') or 'никогда'}  pending={row['pending']}")
        return True
    if verb == "pair" and len(words) == 2:
        device = client.device(words[1])
        row = next((x for x in client.credentials["devices"] if x["id"] == device), None)
        if not row:
            raise Error("Для устройства нет локального кода")
        print("Настройки → Удалённое управление → Сервер команд")
        print("Адрес:", client.config.get("player_server", client.server))
        print("Код доступа:", row["token"])
        print("Device UUID / очередь:", device)
        print("Нажмите «Подключить». Не используйте этот код в другом плеере.")
        return True
    if verb in ("alias", "add") and len(words) == 3:
        name, device = words[1:]
        if not re.fullmatch(r"[a-zA-Z0-9а-яА-ЯёЁ_-]{1,32}", name) or name.casefold() in {"devices", "alias", "add", "pair", "help"}:
            raise Error("Выберите короткое имя до 32 букв/цифр без пробелов")
        if not re.fullmatch(r"[a-zA-Z0-9._:-]{1,128}", device):
            raise Error("Недопустимый UUID устройства")
        aliases = client.config.setdefault("players", {})
        existing = next((key for key in aliases if key.casefold() == name.casefold()), name)
        if existing in aliases and aliases[existing] != device:
            raise Error("Имя уже привязано к другому UUID; отредактируйте cli.json для переназначения")
        if not any(x["id"] == device for x in client.credentials["devices"]):
            if verb == "alias":
                raise Error("UUID ещё не зарегистрирован. Используйте ott add ИМЯ UUID")
            if len(client.credentials["devices"]) >= 64:
                raise Error("Достигнут лимит 64 устройств")
            updated = json.loads(json.dumps(client.credentials))
            updated["devices"].append({"id": device, "token": secrets.token_urlsafe(32)})
            kube = client.config.get("kubernetes")
            if kube:
                base = ["kubectl", "--context", kube["context"], "-n", kube["namespace"]]
                # Read resourceVersion and live config: never overwrite concurrent credentials.
                resource = json.loads(subprocess.check_output(base + ["get", "secret", kube["secret"], "-o", "json"]))
                import base64
                live = json.loads(base64.b64decode(resource["data"]["config.json"]))
                if live != client.credentials:
                    raise Error("Конфигурация кластера изменилась; синхронизируйте server_config перед добавлением")
                resource["data"]["config.json"] = base64.b64encode(json.dumps(updated).encode()).decode()
                subprocess.run(base + ["replace", "-f", "-"], input=json.dumps(resource), text=True, check=True, stdout=subprocess.DEVNULL)
                write_private(client.config["server_config"], updated)
                subprocess.run(base + ["rollout", "restart", "deployment/" + kube["deployment"]], check=True, stdout=subprocess.DEVNULL)
                subprocess.run(base + ["rollout", "status", "deployment/" + kube["deployment"], "--timeout=90s"], check=True, stdout=sys.stderr)
            else:
                write_private(client.config["server_config"], updated)
                print("Конфигурация сервера обновлена. Перезапустите сервер команд.", file=sys.stderr)
        aliases[existing] = device
        write_private(config_path, client.config)
        print(f"{existing} → {device}. Данные подключения: ott pair {existing}")
        return True
    return False


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", default=os.environ.get("OTT_CONFIG", str(Path.home() / ".config/ottplay-control/cli.json")))
    parser.add_argument("--timeout", type=float, default=45)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--help", "-h", action="store_true")
    parser.add_argument("words", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.help or not args.words or args.words == ["help"]:
        print(HELP)
        return 0
    try:
        if not math.isfinite(args.timeout) or not 1 <= args.timeout <= 300:
            raise Error("--timeout должен быть от 1 до 300 секунд")
        client = Client(read_json(args.config), args.timeout)
        if management(client, args.config, args.words):
            return 0
        device = client.device(args.words[0])
        words = args.words[1:]
        action, params = parse_command(words)
        data = client.call(device, action, params)
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        elif action == "channels":
            for row in data["channels"]:
                print(f"{row['number']}: {clean(row['name'])}")
        elif action == "programs":
            for row in data["programs"]:
                print(f"{clean(row['channel'])} — {clean(row['title'])}")
            if data.get("partial"):
                print(f"EPG загружен частично: проверено {data['checked']} из {data['total']} каналов. Повторите запрос позже.", file=sys.stderr)
                return 3
        elif action == "status" and words and words[0].casefold() == "v":
            if data.get("volume") is None:
                raise Error("Платформа не сообщает громкость")
            print(f"{data['volume']:g}%")
        elif action == "providers":
            for row in data["providers"]:
                print(f"{'*' if row['active'] else ' '} {row['index']}: {clean(row['id'])} — {clean(row['name'])}")
        elif action == "play":
            print(f"Команда переключения: {data['channel']['number']}: {clean(data['channel']['name'])}")
        elif action == "command" and params.get("command") == "set_volume":
            if data.get("volume") is None:
                raise Error("Команда отправлена, но платформа не сообщает громкость")
            print(f"{data['volume']:g}%")
        else:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    except (Error, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        print("Ошибка: " + (str(exc) if isinstance(exc, Error) else "Проверьте конфигурацию; операция не завершена"), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
