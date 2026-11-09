"""Разрешённые признаки внешних систем из конфигурации, без секретов и URL-путей."""

from bisect import bisect_right
import ipaddress
import json
import re
from urllib.parse import urlsplit


def logical_name(value, fallback: str = "configuration") -> str:
    """Допускает короткое логическое имя, исключая URI, пары ключ=значение и пути."""
    text = str(value or "")
    return text if re.fullmatch(r"[\w.\-]{1,160}", text) else fallback


def safe_host(value, port=None) -> str | None:
    """Нормализует DNS/IP и порт без userinfo, query, пути и разрешения DNS."""
    if not isinstance(value, str) or not value or len(value) > 500:
        return None
    try:
        parsed = urlsplit(value if "://" in value else "//" + value)
        host = parsed.hostname
        parsed_port = parsed.port
        if not host:
            return None
        host = host.lower().rstrip(".")
        try:
            address = ipaddress.ip_address(host)
            host = f"[{address.compressed}]" if address.version == 6 else address.compressed
        except ValueError:
            host = host.encode("idna").decode("ascii")
            if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.\-]{0,251}[a-z0-9])?", host):
                return None
        port = parsed_port if parsed_port is not None else port
        if port is not None:
            port = int(port)
            if not 1 <= port <= 65535:
                return None
            host += f":{port}"
        return host
    except (ValueError, UnicodeError, TypeError):
        return None


def _key_lines(text: str) -> dict:
    """Сохраняет позиции JSON-ключей по полному пути, не выдавая значения наружу."""
    tokens = list(re.finditer(r'"(?:\\.|[^"\\])*"|[{}\[\]:,]|[^\s{}\[\]:,]+', text))
    newlines = [match.start() for match in re.finditer("\n", text)]
    positions = {}

    def value(index: int, path: tuple, depth: int) -> int:
        """Проходит уже проверенный JSON; предел глубины защищает служебный обход."""
        if depth > 128 or index >= len(tokens):
            raise ValueError("JSON depth")
        token = tokens[index].group()
        if token == "{":
            index += 1
            while tokens[index].group() != "}":
                key = json.loads(tokens[index].group())
                child = path + (str(key),)
                positions[child] = bisect_right(newlines, tokens[index].start()) + 1
                index = value(index + 2, child, depth + 1)
                if tokens[index].group() == ",":
                    index += 1
            return index + 1
        if token == "[":
            index += 1
            item = 0
            while tokens[index].group() != "]":
                index = value(index, path + (str(item),), depth + 1)
                item += 1
                if tokens[index].group() == ",":
                    index += 1
            return index + 1
        return index + 1

    try:
        value(0, (), 0)
    except (ValueError, IndexError, RecursionError):
        return {}
    return positions


def _hosts(value, port=None) -> list[str]:
    """Ограничивает список брокеров и сохраняет только нормализованные адреса."""
    values = value if isinstance(value, list) else str(value or "").split(",")
    result = set()
    for item in values[:20]:
        host = safe_host(item.strip() if isinstance(item, str) else None, port)
        if host:
            result.add(host)
    return sorted(result)


def _database(value: str) -> tuple[str, str | None]:
    """Из connection string извлекает только тип URI и Host/Server/Data Source."""
    if not isinstance(value, str) or len(value) > 10000:
        return "unknown", None
    try:
        scheme = urlsplit(value).scheme.lower()
    except ValueError:
        scheme = ""
    engines = {"postgres": "PostgreSQL", "postgresql": "PostgreSQL", "mongodb": "MongoDB",
               "mongodb+srv": "MongoDB", "mysql": "MySQL", "redis": "Redis"}
    if scheme in engines:
        return engines[scheme], safe_host(value)
    pairs = {}
    for part in value.split(";")[:100]:
        key, separator, entry = part.partition("=")
        if separator:
            pairs[key.strip().lower()] = entry.strip().strip('"\'')
    host = None
    for key in ("host", "server", "data source", "datasource"):
        if key in pairs:
            host = safe_host(pairs[key], pairs.get("port"))
            break
    return "unknown", host


def configured_systems(document: dict, text: str) -> list[dict]:
    """Возвращает только конфигурационные свидетельства, не runtime-зависимости."""
    result = []
    lines = _key_lines(text)
    pending = [((), document)]
    while pending:
        trail, current = pending.pop()
        if len(trail) > 12 or not isinstance(current, dict):
            continue
        lower_trail = ":".join(trail).lower()
        for key, value in sorted(current.items()):
            lowered = key.lower()
            path = trail + (key,)
            logical = logical_name(trail[-1] if trail else key)
            line = lines.get(path, 1)
            if (lowered in {"baseurl", "url", "baseaddress", "endpoint", "otel_exporter_otlp_endpoint"} or lowered.endswith("baseurl")) and isinstance(value, str):
                try:
                    scheme = urlsplit(value).scheme.lower()
                except ValueError:
                    scheme = ""
                if scheme in {"http", "https"}:
                    host = safe_host(value)
                    if host:
                        result.append({"kind": "http", "host": host, "logicalName": logical,
                                       "label": f"{logical} · {host}", "scheme": scheme, "line": line})
            if lowered == "connectionstrings" and isinstance(value, dict):
                for name, connection in sorted(value.items()):
                    if not isinstance(connection, str):
                        continue
                    engine, host = _database(connection)
                    alias = logical_name(name, "database")
                    result.append({"kind": "database", "host": host, "logicalName": alias,
                                   "label": f"{engine if engine != 'unknown' else 'Database'} · {alias}",
                                   "databaseType": engine, "line": lines.get(path + (name,), line)})
            if lowered in {"dbsettings", "databasesettings", "postgresql", "postgres", "mongodb", "redis"} and isinstance(value, dict):
                host = safe_host(value.get("Host") or value.get("Server"), value.get("Port"))
                engine = {"postgresql": "PostgreSQL", "postgres": "PostgreSQL", "mongodb": "MongoDB", "redis": "Redis"}.get(lowered, "unknown")
                result.append({"kind": "database", "host": host, "logicalName": logical_name(key),
                               "label": f"{engine if engine != 'unknown' else 'Database'} · {logical_name(key)}",
                               "databaseType": engine, "line": line})
            if lowered == "brokerconnectionsettings" and "kafka" in lower_trail and isinstance(value, dict):
                for name, settings in sorted(value.items()):
                    if not isinstance(settings, dict):
                        continue
                    hosts = _hosts(settings.get("Servers") or settings.get("BootstrapServers"))
                    alias = logical_name(name, "Kafka")
                    result.append({"kind": "kafka", "host": ", ".join(hosts) or None, "hosts": hosts,
                                   "logicalName": alias, "label": f"Kafka · {alias}",
                                   "line": lines.get(path + (name,), line)})
            if lowered in {"bootstrapservers", "brokers"} and "kafka" in lower_trail and "brokerconnectionsettings" not in lower_trail and isinstance(value, (str, list)):
                hosts = _hosts(value)
                if hosts:
                    result.append({"kind": "kafka", "host": ", ".join(hosts), "hosts": hosts,
                                   "logicalName": logical, "label": f"Kafka · {logical}", "line": line})
            if "rabbit" in lowered and isinstance(value, dict):
                connections = value.get("Connections")
                if not isinstance(connections, list):
                    connections = [value]
                for settings in connections[:40]:
                    if not isinstance(settings, dict):
                        continue
                    hosts = _hosts(settings.get("HostNames") or settings.get("HostName") or settings.get("Host"), settings.get("Port"))
                    if not hosts and not settings.get("ConnectionName"):
                        continue
                    alias = logical_name(settings.get("ConnectionName") or key, "RabbitMQ")
                    result.append({"kind": "rabbit", "host": ", ".join(hosts) or None, "hosts": hosts,
                                   "logicalName": alias, "label": f"RabbitMQ · {alias}", "line": line})
            if isinstance(value, dict):
                pending.append((path, value))
    return result
