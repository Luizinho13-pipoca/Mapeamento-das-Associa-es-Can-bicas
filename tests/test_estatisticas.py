import csv
import importlib
import io
import os
import re
import secrets
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask, request

from estatisticas import (
    AUTH_COOKIE, BROWSER_COOKIE, EventStore, association_key, build_association_registry, external_url,
    register_statistics, today,
)


class StatisticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.password = secrets.token_urlsafe(32)
        self.path = str(Path(self.temp.name) / "analytics.sqlite3")
        self.env = patch.dict(os.environ, {
            "ESTATISTICAS_SENHA": self.password, "ESTATISTICAS_DB_PATH": self.path,
            "ESTATISTICAS_PROXY_HOPS": "0", "ESTATISTICAS_COOKIE_SECURE": "false",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.associations = {
            "a": {"name": "Associacao A", "site": "https://example.org/path?q=1",
                  "instagram": "https://www.instagram.com/association/"},
            "b": {"name": "=UNTRUSTED()", "site": "https://example.com/"},
            "unsafe": {"name": "Unsafe", "site": "javascript:alert(1)"},
        }
        self.app = Flask(__name__)
        self.app.testing = True
        self.app.add_url_rule("/", endpoint="dashboard", view_func=lambda: "<html>Dashboard</html>")
        self.app.add_url_rule("/asset", endpoint="asset", view_func=lambda: "asset")
        self.app.add_url_rule("/_dash-update-component", endpoint="callback", methods=["POST"], view_func=lambda: "{}")
        self.store = register_statistics(self.app, self.associations)
        self.client = self.app.test_client()

    def login(self, client=None, password=None):
        client = client or self.client
        response = client.get("/estatisticas/entrar")
        csrf = re.search(r'name="csrf" value="([^"]+)"', response.text).group(1)
        return client.post("/estatisticas/entrar", data={
            "senha": password or self.password, "csrf": csrf,
        })

    def rows(self):
        with closing(self.store.connect()) as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM events")]

    def test_authentication_export_logout_and_password_rotation(self):
        for path in ("/estatisticas", "/estatisticas/", "/estatisticas/exportar.csv"):
            self.assertEqual(self.client.get(path).status_code, 302)
        self.assertEqual(self.login(password=secrets.token_urlsafe()).status_code, 401)
        self.assertEqual(self.login().status_code, 303)
        page = self.client.get("/estatisticas")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.headers["Cache-Control"], "no-store")
        self.assertEqual(self.client.get("/estatisticas/exportar.csv").status_code, 200)
        self.assertEqual(self.client.post("/estatisticas/sair").status_code, 400)
        csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
        self.assertEqual(self.client.post("/estatisticas/sair", data={"csrf": csrf}).status_code, 303)
        self.assertEqual(self.client.get("/estatisticas").status_code, 302)
        self.login()
        with patch.dict(os.environ, {"ESTATISTICAS_SENHA": secrets.token_urlsafe()}):
            self.assertEqual(self.client.get("/estatisticas/exportar.csv").status_code, 302)
        self.assertEqual(self.rows(), [])

    def test_missing_password_fails_closed_and_dashboard_stays_available(self):
        with patch.dict(os.environ, {"ESTATISTICAS_SENHA": ""}):
            for path in ("/estatisticas", "/estatisticas/entrar", "/estatisticas/exportar.csv"):
                self.assertEqual(self.client.get(path).status_code, 503)
            self.assertEqual(self.client.get("/").status_code, 200)
            self.assertEqual(self.client.get("/saida/a/site").status_code, 302)

    def test_csrf_tampered_and_expired_sessions(self):
        self.assertEqual(self.client.post("/estatisticas/entrar", data={"senha": self.password}).status_code, 400)
        self.login()
        self.assertEqual(self.client.post("/estatisticas/sair", data={"csrf": "\u00e7"}).status_code, 400)
        with patch("time.time", return_value=__import__("time").time() + 9 * 3600):
            self.assertEqual(self.client.get("/estatisticas").status_code, 302)
        self.client.set_cookie(AUTH_COOKIE, "forged", path="/estatisticas")
        self.assertEqual(self.client.get("/estatisticas/exportar.csv").status_code, 302)

    def test_visits_only_root_get_and_estimated_distinct_browsers(self):
        self.client.get("/", environ_base={"REMOTE_ADDR": "192.0.2.1"}, headers={"User-Agent": "private-agent"})
        self.client.get("/")
        self.app.test_client().get("/")
        self.client.head("/")
        self.client.get("/asset")
        self.client.post("/_dash-update-component")
        self.login()
        self.client.get("/estatisticas")
        self.client.get("/estatisticas/exportar.csv")
        totals, _, _ = self.store.report(today(), today())
        self.assertEqual(totals, {"visits": 3, "browsers": 2})
        rows = self.rows()
        self.assertEqual(set(rows[0]), {"id", "day", "kind", "association", "browser"})
        self.assertNotIn("192.0.2.1", str(rows))
        self.assertNotIn("private-agent", str(rows))
        self.assertNotIn(self.client.get_cookie(BROWSER_COOKIE).value, str(rows))

    def test_redirects_use_only_association_data(self):
        for kind in ("site", "instagram"):
            response = self.client.get(f"/saida/a/{kind}")
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.location, self.associations["a"][kind])
            self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.client.head("/saida/a/site")
        for path, status in (("/saida/a/site?url=https://evil.example", 400),
                             ("/saida/a/other", 400), ("/saida/missing/site", 404),
                             ("/saida/unsafe/site", 404), ("/saida/b/instagram", 404)):
            self.assertEqual(self.client.get(path).status_code, status)
        self.assertEqual([row["kind"] for row in self.rows()], ["site", "instagram"])
        self.assertTrue(all(row["browser"] is None for row in self.rows()))

    def test_persistence_date_boundaries_zero_days_and_filtered_csv(self):
        start = date(2026, 1, 2)
        for day, kind, assoc, browser in (
            (date(2026, 1, 1), "visit", None, "excluded"),
            (start, "visit", None, "same-browser"),
            (date(2026, 1, 4), "visit", None, "same-browser"),
            (date(2026, 1, 4), "site", "b", None),
            (date(2026, 1, 5), "instagram", "a", None),
        ):
            with patch("estatisticas.today", return_value=day):
                self.store.record(kind, assoc, browser)
        reopened = EventStore(self.path)
        totals, days, clicks = reopened.report(start, date(2026, 1, 4))
        self.assertEqual(totals, {"visits": 2, "browsers": 1})
        self.assertEqual([row["visits"] for row in days], [1, 0, 1])
        self.assertEqual(clicks, [{"association": "b", "site": 1, "instagram": 0}])
        self.login()
        response = self.client.get("/estatisticas/exportar.csv?inicio=2026-01-02&fim=2026-01-04")
        rows = list(csv.reader(io.StringIO(response.data.decode("utf-8-sig")), delimiter=";"))
        self.assertEqual(rows[1][6:10], ["2", "1", "1", "0"])
        self.assertEqual(rows[-1][4], "'=UNTRUSTED()")
        self.assertNotIn("same-browser", response.text)
        self.assertEqual(self.client.get("/estatisticas?inicio=9999-12-31&fim=9999-12-31").status_code, 200)
        for args in ("inicio=invalid", "inicio=2026-02-01&fim=2026-01-01", "inicio=1900-01-01"):
            self.assertEqual(self.client.get("/estatisticas?" + args).status_code, 400)
            self.assertEqual(self.client.get("/estatisticas/exportar.csv?" + args).status_code, 400)

    def test_database_failure_does_not_break_dashboard_or_redirects(self):
        with patch.object(self.store, "record", side_effect=sqlite3.OperationalError("locked")):
            self.assertEqual(self.client.get("/").status_code, 200)
            self.assertEqual(self.client.get("/saida/a/site").location, self.associations["a"]["site"])
        self.login()
        with patch.object(self.store, "report", side_effect=sqlite3.OperationalError("locked")):
            self.assertEqual(self.client.get("/estatisticas").status_code, 503)
            self.assertEqual(self.client.get("/estatisticas/exportar.csv").status_code, 503)

    def test_url_validation(self):
        for value in (None, "javascript:alert(1)", "//evil.example", "https://", "https://u:p@example.org",
                      "https://example.org\r\nInjected: yes", "https://example.org:bad", "https://bad\\host"):
            self.assertIsNone(external_url(value))
        self.assertEqual(external_url(" https://example.org/a?b=c "), "https://example.org/a?b=c")
        self.assertNotEqual(association_key("ab", "c"), association_key("a", "bc"))

    def test_https_cookies_and_concurrent_writes(self):
        from concurrent.futures import ThreadPoolExecutor

        response = self.client.get("/estatisticas/entrar", base_url="https://localhost")
        cookie = response.headers["Set-Cookie"]
        for attribute in ("Secure", "HttpOnly", "SameSite=Strict", "Path=/estatisticas"):
            self.assertIn(attribute, cookie)
        with ThreadPoolExecutor(max_workers=4) as workers:
            list(workers.map(lambda _: self.store.record("site", association="a"), range(24)))
        self.assertEqual(len(self.rows()), 24)

    def test_colliding_destinations_preserve_identical_groups_and_redirects(self):
        base_key = association_key("local", "Associacao A", "SP", "Sao Paulo")
        original = self.associations["a"]
        site_variant = {**original, "site": "https://other.example/site"}
        instagram_variant = {**original, "instagram": "https://instagram.com/other"}
        entries = [(base_key, row) for row in (original, original.copy(), site_variant, instagram_variant)]
        keys, registry = build_association_registry(entries)
        self.assertEqual(keys[0], keys[1])
        self.assertEqual(len(set(keys)), 3)
        reversed_keys, reversed_registry = build_association_registry(reversed(entries))
        self.assertEqual(keys, list(reversed(reversed_keys)))
        self.assertEqual(registry, reversed_registry)
        identical_keys, _ = build_association_registry(entries[:2])
        self.assertEqual(identical_keys, [base_key, base_key])
        self.associations.update(registry)
        for key, (_, association) in zip(keys, entries):
            for kind in ("site", "instagram"):
                response = self.client.get(f"/saida/{key}/{kind}")
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.location, association[kind])
        _, _, clicks = self.store.report(today(), today())
        by_key = {row["association"]: row for row in clicks}
        self.assertEqual(by_key[keys[0]]["site"], 2)
        self.assertEqual(by_key[keys[0]]["instagram"], 2)
        self.assertEqual(by_key[keys[2]]["site"], 1)
        self.assertEqual(by_key[keys[3]]["instagram"], 1)

    def test_nginx_https_and_production_auth_cookies(self):
        for hops, force_secure, headers, expected_scheme, expected_secure in (
            ("0", "false", {"X-Forwarded-Proto": "https"}, "http", False),
            ("1", "false", {"X-Forwarded-Proto": "https"}, "https", True),
            ("1", "true", {"X-Forwarded-Proto": "https"}, "https", True),
            ("1", "true", {}, "http", True),
            ("1", "false", {"X-Forwarded-Proto": "https, http"}, "http", False),
        ):
            with self.subTest(hops=hops, force_secure=force_secure, headers=headers), patch.dict(os.environ, {
                "ESTATISTICAS_PROXY_HOPS": hops, "ESTATISTICAS_COOKIE_SECURE": force_secure,
            }):
                app = Flask(__name__)
                app.testing = True
                app.add_url_rule("/scheme", view_func=lambda: {
                    "scheme": request.scheme, "host": request.host, "ip": request.remote_addr,
                })
                register_statistics(app, self.associations)
                client = app.test_client()
                probe = client.get("/scheme", headers={**headers, "X-Forwarded-Host": "evil.example",
                                                       "X-Forwarded-For": "192.0.2.99"})
                self.assertEqual(probe.json, {"scheme": expected_scheme, "host": "localhost", "ip": "127.0.0.1"})
                page = client.get("/estatisticas/entrar", headers=headers)
                csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
                response = client.post("/estatisticas/entrar", headers=headers,
                                       data={"senha": self.password, "csrf": csrf})
                self.assertEqual(response.status_code, 303)
                cookie = client.get_cookie(AUTH_COOKIE, path="/estatisticas")
                self.assertEqual(cookie.secure, expected_secure)
                auth_header = next(value for value in response.headers.getlist("Set-Cookie")
                                   if value.startswith(AUTH_COOKIE + "="))
                self.assertEqual("; Secure" in auth_header, expected_secure)
                self.assertTrue(cookie.http_only)
                response = client.post("/estatisticas/sair", headers=headers, data={"csrf": cookie.value})
                self.assertEqual(response.status_code, 303)
                self.assertEqual("; Secure" in response.headers["Set-Cookie"], expected_secure)


def fixture_get(_session, url, **kwargs):
    if "geojson" in url:
        return SimpleNamespace(json=lambda: {"type": "FeatureCollection", "features": [{
            "type": "Feature", "properties": {"sigla": "SP", "name": "Sao Paulo"},
            "geometry": {"type": "Polygon", "coordinates": [[[-47, -24], [-46, -24], [-46, -23], [-47, -24]]]},
        }]})
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Nome da associa\u00e7\u00e3o", "UF", "Munic\u00edpio",
                     'P\u00e1gina do Instagram (link ou "n\u00e3o encontrado")',
                     'Site ou p\u00e1gina web (link ou "n\u00e3o encontrado")'])
    writer.writerow([1, "Associacao de teste", "SP", "Sao Paulo", "https://instagram.com/teste", "https://example.org"])
    return SimpleNamespace(content=output.getvalue().encode("utf-8"), raise_for_status=lambda: None)


class DashboardIntegrationTests(unittest.TestCase):
    def test_real_dashboard_callbacks_and_tracked_links(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
            "ESTATISTICAS_DB_PATH": str(Path(temp) / "integration.sqlite3"),
            "ESTATISTICAS_SENHA": secrets.token_urlsafe(32),
        }), patch("requests.Session.get", fixture_get):
            dashboard = importlib.import_module("dashboard_associacoes")
            client = dashboard.server.test_client()
            self.assertEqual(client.get("/").status_code, 200)
            self.assertEqual(client.get("/_dash-layout").status_code, 200)
            self.assertEqual(client.get("/_dash-dependencies").status_code, 200)
            result = dashboard.update_map(dashboard.df.index.tolist(), None, None)
            row = result[2][0]
            for label, kind in (("Site", "site"), ("Instagram", "instagram")):
                path = re.search(r"\]\(([^)]+)\)", row[label]).group(1)
                self.assertTrue(path.startswith("/saida/"))
                response = client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.location, dashboard._statistics_associations[dashboard.df.iloc[0]["_analytics_id"]][kind])
            totals, _, clicks = dashboard.server.extensions["statistics_store"].report(today(), today())
            self.assertEqual(totals["visits"], 1)
            self.assertEqual(clicks[0]["site"], 1)
            self.assertEqual(clicks[0]["instagram"], 1)


if __name__ == "__main__":
    unittest.main()
