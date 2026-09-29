"""Private, server-rendered analytics for the Dash Flask server."""

import csv
import hashlib
import hmac
import io
import os
import re
import secrets
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from flask import Blueprint, Response, abort, redirect, render_template, request, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer
from werkzeug.middleware.proxy_fix import ProxyFix


BRASILIA = timezone(timedelta(hours=-3))
AUTH_COOKIE = "estatisticas_auth"
CSRF_COOKIE = "estatisticas_csrf"
BROWSER_COOKIE = "dashboard_browser"
AUTH_TTL = 8 * 60 * 60


def today():
    return datetime.now(BRASILIA).date()


def same_token(left, right):
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def association_key(*parts):
    # Length prefixes avoid ambiguous combinations of association fields.
    value = "".join(f"{len(str(part))}:{part}" for part in parts)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def external_url(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or any(ord(char) < 32 for char in value) or "\\" in value:
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme.lower() in {"http", "https"} and parsed.hostname
                and not parsed.username and not parsed.password):
            parsed.port  # Reject malformed ports as well as malformed hosts.
            return value
    except ValueError:
        pass
    return None


def build_association_registry(entries):
    entries = list(entries)
    variants = {}
    normalized = []
    for base_key, association in entries:
        signature = (str(association["name"]), external_url(association.get("site")) or "",
                     external_url(association.get("instagram")) or "")
        variants.setdefault(base_key, set()).add(signature)
        normalized.append((base_key, signature))
    keys, registry = [], {}
    for base_key, signature in normalized:
        # Keep existing keys unless the group has genuinely different destinations/names.
        key = (base_key if len(variants[base_key]) == 1
               else f"{base_key}-{association_key(*signature)}")
        keys.append(key)
        registry[key] = dict(zip(("name", "site", "instagram"), signature))
    return keys, registry


class EventStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as conn, conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY,
                    day TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('visit', 'site', 'instagram')),
                    association TEXT,
                    browser TEXT,
                    CHECK ((kind = 'visit' AND association IS NULL AND browser IS NOT NULL)
                        OR (kind != 'visit' AND association IS NOT NULL AND browser IS NULL))
                );
                CREATE INDEX IF NOT EXISTS events_day_kind ON events(day, kind);
            """)

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=2)
        conn.row_factory = sqlite3.Row
        return conn

    def record(self, kind, association=None, browser=None):
        with closing(self.connect()) as conn, conn:
            conn.execute(
                "INSERT INTO events(day, kind, association, browser) VALUES (?, ?, ?, ?)",
                (today().isoformat(), kind, association, browser),
            )

    def report(self, start, end):
        with closing(self.connect()) as conn, conn:
            # A single snapshot keeps the cards, daily rows and export consistent.
            conn.execute("BEGIN")
            totals = dict(conn.execute("""
                SELECT COUNT(*) AS visits, COUNT(DISTINCT browser) AS browsers
                FROM events WHERE kind = 'visit' AND day BETWEEN ? AND ?
            """, (start.isoformat(), end.isoformat())).fetchone())
            daily = {row["day"]: row["visits"] for row in conn.execute("""
                SELECT day, COUNT(*) AS visits FROM events
                WHERE kind = 'visit' AND day BETWEEN ? AND ? GROUP BY day ORDER BY day
            """, (start.isoformat(), end.isoformat()))}
            clicks = [dict(row) for row in conn.execute("""
                SELECT association,
                    SUM(kind = 'site') AS site, SUM(kind = 'instagram') AS instagram
                FROM events WHERE kind != 'visit' AND day BETWEEN ? AND ?
                GROUP BY association ORDER BY COUNT(*) DESC, association
            """, (start.isoformat(), end.isoformat()))]
        days = []
        for offset in range((end - start).days + 1):
            current = start + timedelta(days=offset)
            days.append({"day": current.isoformat(), "label": current.strftime("%d/%m/%Y"),
                         "visits": daily.get(current.isoformat(), 0)})
        return totals, days, clicks


def register_statistics(server, associations):
    proxy_hops = int(os.environ.get("ESTATISTICAS_PROXY_HOPS", "0"))
    if proxy_hops < 0:
        raise ValueError("ESTATISTICAS_PROXY_HOPS deve ser um inteiro nao negativo.")
    secure_setting = os.environ.get("ESTATISTICAS_COOKIE_SECURE", "false").strip().lower()
    if secure_setting not in {"true", "false"}:
        raise ValueError("ESTATISTICAS_COOKIE_SECURE deve ser true ou false.")
    force_secure = secure_setting == "true"
    if proxy_hops:
        server.wsgi_app = ProxyFix(server.wsgi_app, x_for=0, x_proto=proxy_hops,
                                   x_host=0, x_port=0, x_prefix=0)
    path = os.environ.get("ESTATISTICAS_DB_PATH") or str(
        Path(__file__).resolve().parent / "data" / "estatisticas.sqlite3"
    )
    # Startup failure is explicit if the configured persistent storage is unusable.
    store = EventStore(path)
    server.extensions["statistics_store"] = store
    bp = Blueprint("statistics", __name__, template_folder="templates")

    def serializer():
        password = os.environ.get("ESTATISTICAS_SENHA", "")
        if not password.strip():
            return None
        key = hashlib.sha256(("statistics-auth-v1:" + password).encode("utf-8")).digest()
        return URLSafeTimedSerializer(key, salt="statistics")

    def valid_token(token, purpose, max_age=AUTH_TTL):
        signer = serializer()
        if signer is None or not token:
            return False
        try:
            payload = signer.loads(token, max_age=max_age)
            return isinstance(payload, dict) and payload.get("purpose") == purpose
        except BadSignature:
            return False

    def token(purpose):
        return serializer().dumps({"purpose": purpose, "nonce": secrets.token_hex(24)})

    def set_cookie(response, name, value, age, path="/estatisticas"):
        response.set_cookie(name, value, max_age=age, httponly=True,
                            secure=force_secure or request.is_secure, samesite="Strict", path=path)

    def delete_cookie(response, name):
        response.delete_cookie(name, path="/estatisticas", httponly=True,
                               secure=force_secure or request.is_secure, samesite="Strict")

    @bp.before_request
    def protect():
        if not request.path.startswith("/estatisticas"):
            return None
        if serializer() is None:
            return render_template("estatisticas.html", mode="unavailable"), 503
        if request.endpoint != "statistics.login" and not valid_token(
            request.cookies.get(AUTH_COOKIE), "auth"
        ):
            return redirect(url_for("statistics.login"))

    @bp.after_request
    def private_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
            "frame-ancestors 'none'; base-uri 'none'"
        )
        return response

    @bp.route("/estatisticas/entrar", methods=["GET", "POST"])
    def login():
        error = None
        status = 200
        if request.method == "POST":
            csrf = request.form.get("csrf", "")
            if (not valid_token(csrf, "csrf", 1800)
                    or not same_token(csrf, request.cookies.get(CSRF_COOKIE, ""))):
                error, status = "Sessao expirada. Tente novamente.", 400
            elif hmac.compare_digest(
                hashlib.sha256(request.form.get("senha", "").encode("utf-8")).digest(),
                hashlib.sha256(os.environ["ESTATISTICAS_SENHA"].encode("utf-8")).digest(),
            ):
                response = redirect(url_for("statistics.index"), code=303)
                set_cookie(response, AUTH_COOKIE, token("auth"), AUTH_TTL)
                delete_cookie(response, CSRF_COOKIE)
                return response
            else:
                error, status = "Senha incorreta.", 401
        csrf = token("csrf")
        response = Response(render_template("estatisticas.html", mode="login", csrf=csrf,
                                            error=error), status=status, mimetype="text/html")
        set_cookie(response, CSRF_COOKIE, csrf, 1800)
        return response

    @bp.post("/estatisticas/sair")
    def logout():
        if not same_token(request.form.get("csrf", ""), request.cookies.get(AUTH_COOKIE, "")):
            abort(400)
        response = redirect(url_for("statistics.login"), code=303)
        delete_cookie(response, AUTH_COOKIE)
        return response

    def date_range():
        end = date.fromisoformat(request.args.get("fim", today().isoformat()))
        start = date.fromisoformat(request.args.get("inicio", (today() - timedelta(days=29)).isoformat()))
        if start > end:
            raise ValueError("A data inicial deve ser anterior ou igual a data final.")
        if (end - start).days > 3660:
            raise ValueError("Selecione um periodo de ate 10 anos.")
        return start, end

    def report(start, end):
        totals, days, clicks = store.report(start, end)
        for row in clicks:
            association = associations.get(row["association"], {})
            row["name"] = association.get("name", "Associacao fora da base atual")
        return totals, days, clicks

    @bp.get("/estatisticas", strict_slashes=False)
    def index():
        try:
            start, end = date_range()
        except ValueError:
            return render_template("estatisticas.html", mode="error",
                                   error="Periodo invalido. Informe datas em ordem, com intervalo de ate 10 anos."), 400
        try:
            totals, days, clicks = report(start, end)
        except sqlite3.Error:
            return render_template("estatisticas.html", mode="error",
                                   error="Consulta indisponivel no momento. Tente novamente."), 503
        return render_template("estatisticas.html", mode="report", start=start, end=end,
                               totals=totals, days=days, clicks=clicks,
                               max_visits=max(1, max(row["visits"] for row in days)),
                               csrf=request.cookies.get(AUTH_COOKIE))

    @bp.get("/estatisticas/exportar.csv")
    def export():
        try:
            start, end = date_range()
        except ValueError:
            abort(400)
        try:
            totals, days, clicks = report(start, end)
        except sqlite3.Error:
            abort(503)
        output = io.StringIO(newline="")
        writer = csv.writer(output, delimiter=";")
        writer.writerow(["Tipo", "Inicio", "Fim", "Data", "Associacao", "ID da associacao",
                         "Visitas", "Navegadores estimados", "Cliques Site", "Cliques Instagram"])
        writer.writerow(["Resumo", start, end, "", "", "", totals["visits"], totals["browsers"],
                         sum(row["site"] for row in clicks), sum(row["instagram"] for row in clicks)])
        for row in days:
            writer.writerow(["Visitas por dia", start, end, row["day"], "", "", row["visits"], "", "", ""])
        for row in clicks:
            name = row["name"]
            if name.lstrip().startswith(("=", "+", "-", "@")) or name.startswith(("\t", "\r", "\n")):
                name = "'" + name
            writer.writerow(["Cliques por associacao", start, end, "", name, row["association"],
                             "", "", row["site"], row["instagram"]])
        return Response("\ufeff" + output.getvalue(), mimetype="text/csv", headers={
            "Content-Disposition": f'attachment; filename="estatisticas_{start}_{end}.csv"',
        })

    def record_safely(kind, association=None, browser=None):
        try:
            store.record(kind, association, browser)
        except sqlite3.Error:
            # Logging contains no request metadata or cookie values.
            server.logger.warning("Nao foi possivel registrar evento de estatisticas.")

    @bp.get("/saida/<association>/<kind>")
    def outgoing(association, kind):
        if kind not in {"site", "instagram"} or request.args:
            abort(400)
        destination = external_url(associations.get(association, {}).get(kind))
        if not destination:
            abort(404)
        if request.method == "GET":
            record_safely(kind, association=association)
        return redirect(destination, code=302)

    @server.after_request
    def visit(response):
        if (request.method == "GET" and request.path == "/" and response.status_code == 200
                and response.mimetype == "text/html"):
            browser = request.cookies.get(BROWSER_COOKIE, "")
            if not re.fullmatch(r"[0-9a-f]{64}", browser):
                browser = secrets.token_hex(32)
                set_cookie(response, BROWSER_COOKIE, browser, 365 * 24 * 60 * 60, path="/")
            record_safely("visit", browser=hashlib.sha256(browser.encode("ascii")).hexdigest())
            response.headers["Cache-Control"] = "no-store"
        return response

    server.register_blueprint(bp)
    return store
