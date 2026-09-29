"""Optional end-to-end check: python tests/browser_check.py (requires Playwright)."""

import csv
import io
import logging
import os
import secrets
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright
from flask import Flask
from werkzeug.serving import make_server

from test_estatisticas import fixture_get
from estatisticas import today


def check():
    artifacts = Path("test-results")
    artifacts.mkdir(exist_ok=True)
    logging.getLogger("werkzeug").disabled = True
    password = secrets.token_urlsafe(32)
    with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
        "ESTATISTICAS_DB_PATH": str(Path(temp) / "browser.sqlite3"),
        "ESTATISTICAS_SENHA": password,
    }), patch("requests.Session.get", fixture_get):
        import dashboard_associacoes as dashboard

        server = make_server("127.0.0.1", 0, dashboard.server, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        destination_app = Flask("test_destinations")
        destination_app.add_url_rule("/<kind>", view_func=lambda kind: "Destination: " + kind)
        destination_server = make_server("127.0.0.1", 0, destination_app, threaded=True)
        destination_thread = threading.Thread(target=destination_server.serve_forever, daemon=True)
        destination_thread.start()
        destination_origin = f"http://127.0.0.1:{destination_server.server_port}"
        for association in dashboard._statistics_associations.values():
            association["site"] = destination_origin + "/site"
            association["instagram"] = destination_origin + "/instagram"
        try:
            with sync_playwright() as playwright:
                cached = list((Path(os.environ["LOCALAPPDATA"]) / "ms-playwright").glob(
                    "chromium-*/chrome-win64/chrome.exe"
                ))
                browser = playwright.chromium.launch(executable_path=str(cached[-1]) if cached else None)
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                page = context.new_page()
                page.goto(origin + "/estatisticas/exportar.csv")
                assert page.url.endswith("/estatisticas/entrar")
                page.get_by_label("Senha", exact=True).fill(secrets.token_urlsafe())
                page.get_by_role("button", name="Entrar", exact=True).click()
                assert page.get_by_role("alert").inner_text() == "Senha incorreta."
                page.get_by_label("Senha", exact=True).fill(password)
                page.get_by_role("button", name="Entrar", exact=True).click()
                page.wait_for_url(origin + "/estatisticas")
                print("PASS: login and protected export", flush=True)
                assert "Nenhuma visita" in page.inner_text("body")
                assert dashboard.server.extensions["statistics_store"].report(today(), today())[0]["visits"] == 0

                page.goto(origin + "/")
                for label in ("Site", "Instagram"):
                    link = page.locator("#lista-mapa").get_by_role("link", name=label, exact=True)
                    assert link.get_attribute("target") == "_blank"
                    with page.expect_popup() as opened:
                        link.click(no_wait_after=True)
                    popup = opened.value
                    popup.wait_for_url(destination_origin + "/" + label.lower())
                    popup.close()
                print("PASS: external links in new tabs", flush=True)
                page.goto(origin + "/estatisticas")
                page.locator('input[name="inicio"]').fill(today().isoformat())
                page.locator('input[name="fim"]').fill(today().isoformat())
                page.get_by_role("button", name="Aplicar").click()
                assert page.locator(".metric strong").all_text_contents() == ["1", "1", "1", "1"]
                for name, width, height in (("desktop", 1440, 1000), ("mobile", 390, 844)):
                    page.set_viewport_size({"width": width, "height": height})
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                    page.screenshot(path=str(artifacts / f"estatisticas-{name}.png"), full_page=True)
                with page.expect_download() as exported:
                    page.get_by_role("link", name="Exportar CSV").click()
                exported.value.save_as(str(artifacts / "estatisticas.csv"))
                rows = list(csv.reader(io.StringIO((artifacts / "estatisticas.csv").read_text(encoding="utf-8-sig")), delimiter=";"))
                assert rows[1][6:10] == ["1", "1", "1", "1"]
                page.get_by_role("button", name="Sair", exact=True).click()
                page.wait_for_url(origin + "/estatisticas/entrar")
                page.goto(origin + "/estatisticas/exportar.csv")
                assert page.url.endswith("/estatisticas/entrar")
                browser.close()
                print("PASS: login, protected export, dashboard links/new tabs, counters, date filter, CSV, logout, desktop/mobile.")
                print("Screenshots: test-results/estatisticas-desktop.png and estatisticas-mobile.png")
        finally:
            destination_server.shutdown()
            destination_thread.join(timeout=5)
            destination_server.server_close()
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()


if __name__ == "__main__":
    check()
