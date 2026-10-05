#!/usr/bin/env python3
from __future__ import annotations

import hmac
import html
import ipaddress
import json
import os
import re
import secrets
import socket
import sys
from html.parser import HTMLParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import Request, urlopen


# ---------------------------------------------------------------------------
# Configuração
# Precedência: variável de ambiente > arquivo .env > default abaixo.
ROOT = Path(__file__).resolve().parent


def load_env_file(path: Path) -> dict[str, str]:
    """Parser .env mínimo (stdlib): KEY=VALUE, ignora comentários e vazias."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def env_config(file_values: dict[str, str], key: str, default: str) -> str:
    return os.environ.get(key, file_values.get(key, default))


_ENV = load_env_file(ROOT / ".env")

SERVER_PORT = int(env_config(_ENV, "SERVER_PORT", "8787"))
PASSWORD_PROTECTED = int(env_config(_ENV, "PASSWORD_PROTECTED", "0"))
ACCESS_PASSWORD = env_config(_ENV, "ACCESS_PASSWORD", "")
SESSION_COOKIE_NAME = "link_preview_session"
APP_NAME = "LinkSee"

MAX_BYTES = 2_000_000
TIMEOUT_SECONDS = 12
SESSION_TOKEN = secrets.token_urlsafe(32)


class MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.in_title = False
        self.metas: list[dict[str, str]] = []
        self.links: list[dict[str, str]] = []
        self.base_href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): value or "" for name, value in attrs}

        if tag == "title":
            self.in_title = True
            return

        if tag == "meta":
            key = values.get("property") or values.get("name") or values.get("itemprop")
            content = values.get("content")
            if key and content:
                self.metas.append({"key": key.strip().lower(), "content": content.strip()})
            return

        if tag == "link":
            rel = values.get("rel")
            href = values.get("href")
            if rel and href:
                self.links.append({"rel": rel.strip().lower(), "href": href.strip()})
            return

        if tag == "base" and values.get("href"):
            self.base_href = values["href"].strip()

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)

    @property
    def title(self) -> str:
        return normalize_text(" ".join(self.title_parts))


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def first_meta(metas: dict[str, list[str]], keys: list[str]) -> str:
    for key in keys:
        for value in metas.get(key, []):
            clean = normalize_text(value)
            if clean:
                return clean
    return ""


def first_link(links: list[dict[str, str]], wanted: list[str]) -> str:
    for link in links:
        rels = set(link["rel"].split())
        if any(rel in rels for rel in wanted):
            return link["href"]
    return ""


def public_http_url(raw_url: str) -> str:
    parsed = urlparse(raw_url.strip())
    if not parsed.scheme:
        raw_url = f"https://{raw_url.strip()}"
        parsed = urlparse(raw_url)

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Use uma URL http ou https valida.")

    host = parsed.hostname or ""
    if host.lower() in {"localhost", "0.0.0.0"} or host.startswith("127."):
        raise ValueError("URLs locais nao sao permitidas nesta ferramenta.")

    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError("URLs com IP privado/local nao sao permitidas.")
    except ValueError as error:
        if "URLs" in str(error):
            raise

    return raw_url


def decode_body(data: bytes, content_type: str) -> str:
    match = re.search(r"charset=([^;\s]+)", content_type, re.IGNORECASE)
    encoding = match.group(1).strip("\"'") if match else "utf-8"
    try:
        return data.decode(encoding, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


def fetch_html(raw_url: str, refresh: bool) -> dict[str, Any]:
    url = public_http_url(raw_url)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/124.0 Safari/537.36 {APP_NAME}/1.0"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.7",
    }

    if refresh:
        headers["Cache-Control"] = "no-cache"
        headers["Pragma"] = "no-cache"

    request = Request(url, headers=headers, method="GET")
    with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        content_type = response.headers.get("Content-Type", "")
        data = response.read(MAX_BYTES + 1)

        if len(data) > MAX_BYTES:
            raise ValueError("A resposta passou de 2 MB; parei para manter a ferramenta leve.")

        return {
            "body": decode_body(data, content_type),
            "content_type": content_type,
            "status": response.status,
            "final_url": response.url,
        }


def build_preview(raw_url: str, refresh: bool) -> dict[str, Any]:
    fetched = fetch_html(raw_url, refresh)
    parser = MetadataParser()
    parser.feed(fetched["body"])

    metas: dict[str, list[str]] = {}
    for item in parser.metas:
        metas.setdefault(item["key"], []).append(item["content"])

    final_url = fetched["final_url"]
    base_url = urljoin(final_url, parser.base_href) if parser.base_href else final_url
    canonical = first_link(parser.links, ["canonical"])
    image_src = first_link(parser.links, ["image_src"])
    icon = first_link(parser.links, ["apple-touch-icon", "icon", "shortcut"])

    title = first_meta(metas, ["og:title", "twitter:title"]) or parser.title
    description = first_meta(metas, ["og:description", "twitter:description", "description"])
    image = first_meta(
        metas,
        ["og:image:secure_url", "og:image:url", "og:image", "twitter:image", "twitter:image:src"],
    ) or image_src
    preview_url = first_meta(metas, ["og:url"]) or canonical or final_url
    site_name = first_meta(metas, ["og:site_name", "application-name"])

    parsed_final_url = urlparse(final_url)
    host = parsed_final_url.netloc.replace("www.", "", 1)

    return {
        "inputUrl": raw_url,
        "finalUrl": final_url,
        "status": fetched["status"],
        "contentType": fetched["content_type"],
        "title": title,
        "description": description,
        "image": urljoin(base_url, image) if image else "",
        "url": urljoin(base_url, preview_url) if preview_url else final_url,
        "siteName": site_name or host,
        "host": host,
        "type": first_meta(metas, ["og:type"]),
        "icon": urljoin(base_url, icon) if icon else "",
        "raw": {
            "titleTag": parser.title,
            "metas": parser.metas[:120],
            "links": parser.links[:80],
        },
    }


class Handler(BaseHTTPRequestHandler):
    server_version = f"{APP_NAME}/1.0"

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        file_path = self.resolve_static_path(parsed.path)
        if file_path is None:
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == "/api/preview":
            if not self.has_access():
                self.write_json(HTTPStatus.UNAUTHORIZED, {"error": "Sessao expirada. Faça login novamente."})
                return
            self.handle_preview(parsed.query)
            return

        if parsed.path == "/logout":
            self.redirect_to_login(clear_cookie=True)
            return

        file_path = self.resolve_static_path(parsed.path)
        if file_path is None:
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(file_path.read_bytes())

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/login":
            self.send_error(HTTPStatus.NOT_FOUND, "Rota nao encontrada")
            return

        if not PASSWORD_PROTECTED:
            self.write_json(HTTPStatus.OK, {"ok": True})
            return

        try:
            length = min(int(self.headers.get("Content-Length", "0")), 2048)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": "JSON invalido."})
            return

        password = str(payload.get("password", ""))
        if hmac.compare_digest(password, ACCESS_PASSWORD):
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Set-Cookie",
                f"{SESSION_COOKIE_NAME}={SESSION_TOKEN}; Path=/; HttpOnly; SameSite=Lax; Max-Age=604800",
            )
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            return

        self.write_json(HTTPStatus.UNAUTHORIZED, {"error": "Senha incorreta."})

    def resolve_static_path(self, request_path: str) -> Path | None:
        if not self.has_access():
            path = "login.html"
        else:
            path = "index.html" if request_path in {"/", "", "/login.html"} else request_path.lstrip("/")

        file_path = (ROOT / path).resolve()
        if not str(file_path).startswith(str(ROOT)) or not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Arquivo nao encontrado")
            return None
        return file_path

    def has_access(self) -> bool:
        return not PASSWORD_PROTECTED or self.is_authenticated()

    def is_authenticated(self) -> bool:
        cookie = self.headers.get("Cookie", "")
        for chunk in cookie.split(";"):
            name, _, value = chunk.strip().partition("=")
            if name == SESSION_COOKIE_NAME:
                return hmac.compare_digest(value, SESSION_TOKEN)
        return False

    def redirect_to_login(self, clear_cookie: bool = False) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", "/")
        self.send_header("Cache-Control", "no-store")
        if clear_cookie:
            self.send_header(
                "Set-Cookie",
                f"{SESSION_COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0",
            )
        self.end_headers()

    def handle_preview(self, query: str) -> None:
        params = parse_qs(query)
        url = params.get("url", [""])[0]
        refresh = params.get("refresh", ["0"])[0] == "1"

        try:
            if not url.strip():
                raise ValueError("Informe uma URL.")
            payload = build_preview(url, refresh)
            self.write_json(HTTPStatus.OK, payload)
        except HTTPError as error:
            self.write_json(
                HTTPStatus.BAD_GATEWAY,
                {"error": f"O site respondeu HTTP {error.code}.", "detail": str(error.reason)},
            )
        except (TimeoutError, URLError, socket.timeout) as error:
            self.write_json(HTTPStatus.BAD_GATEWAY, {"error": "Nao consegui buscar essa URL.", "detail": str(error)})
        except Exception as error:
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})

    def write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.log_date_time_string(), format % args))


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", SERVER_PORT), Handler)
    print(f"{APP_NAME}: http://127.0.0.1:{SERVER_PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
