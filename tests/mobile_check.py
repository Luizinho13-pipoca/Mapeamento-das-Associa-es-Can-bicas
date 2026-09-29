"""Run with .venv Python: tests/mobile_check.py (Playwright required)."""

import csv
import io
import json
import logging
import os
import secrets
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from test_estatisticas import fixture_get
from estatisticas import today


def mobile_fixture(session, url, **kwargs):
    response = fixture_get(session, url, **kwargs)
    if "geojson" in url:
        return response
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8"))))
    extra = ["CNPJ", "dt_fundacao_osc", "Oferece Flores?",
             "Distribui\u00e7\u00e3o de \u00f3leo \u00e0 base de cannabis (sim, n\u00e3o ou NI)",
             "Distribui\u00e7\u00e3o de pomada/gel/creme \u00e0 base de cannabis (sim, n\u00e3o ou NI)",
             "Distribui\u00e7\u00e3o de produtos espec\u00edficos para pets \u00e0 base de cannabis (sim, n\u00e3o ou NI)",
             "Possui algum outro produto para distribui\u00e7\u00e3o \u00e0 base de cannabis? (sim, n\u00e3o ou NI)",
             "Oferece atendimento m\u00e9dico? (sim, n\u00e3o, NI ou MP)",
             "Oferece assist\u00eancia jur\u00eddica? (sim, n\u00e3o ou NI)",
             "Oferece acolhimento? (sim, n\u00e3o ou NI)",
             "Oferece algum outro tipo de servi\u00e7o? (sim, n\u00e3o ou NI)"]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(rows[0] + extra)
    for index in range(32):
        writer.writerow([index + 1, f"Associacao {index + 1:02d}", "SP" if index < 20 else "RJ",
                         "Sao Paulo" if index < 20 else "Rio de Janeiro", "https://instagram.com/teste",
                         "https://example.org", "123" if index % 2 else "", "2015-01-01"]
                        + ["sim" if index % 2 else "nao"] * (len(extra) - 2))
    return SimpleNamespace(content=output.getvalue().encode("utf-8"), raise_for_status=lambda: None)


def assert_width(page):
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), page.evaluate(
        "({viewport: innerWidth, document: document.documentElement.scrollWidth})"
    )


def check():
    expect.set_options(timeout=120000)
    output = Path("test-results/mobile")
    output.mkdir(parents=True, exist_ok=True)
    logging.getLogger("werkzeug").disabled = True
    password = secrets.token_urlsafe(32)
    results = []
    with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
        "ESTATISTICAS_DB_PATH": str(Path(temp) / "mobile.sqlite3"),
        "ESTATISTICAS_SENHA": password, "ESTATISTICAS_PROXY_HOPS": "0",
        "ESTATISTICAS_COOKIE_SECURE": "false",
    }), patch("requests.Session.get", mobile_fixture):
        import dashboard_associacoes as dashboard

        destinations = Flask("mobile_destinations")
        destinations.add_url_rule("/<kind>", view_func=lambda kind: "Destino: " + kind)
        target_server = make_server("127.0.0.1", 0, destinations, threaded=True)
        target_origin = f"http://127.0.0.1:{target_server.server_port}"
        for association in dashboard._statistics_associations.values():
            association.update(site=target_origin + "/site", instagram=target_origin + "/instagram")
        server = make_server("127.0.0.1", 0, dashboard.server, threaded=True)
        origin = f"http://127.0.0.1:{server.server_port}"
        threads = [threading.Thread(target=item.serve_forever, daemon=True) for item in (server, target_server)]
        for thread in threads:
            thread.start()
        try:
            with sync_playwright() as playwright:
                cached = list((Path(os.environ["LOCALAPPDATA"]) / "ms-playwright").glob("chromium-*/chrome-win64/chrome.exe"))
                browser = playwright.chromium.launch(executable_path=str(cached[-1]) if cached else None)
                for width in map(int, sys.argv[1:] or (360, 390, 768, 1440)):
                    context = browser.new_context(viewport={"width": width, "height": 900}, has_touch=width <= 900)
                    topology = Path(os.environ.get("MOBILE_TOPOJSON", "test-results/world_110m.json"))
                    if topology.is_file():
                        context.route("https://cdn.plot.ly/un/world_110m.json", lambda route: route.fulfill(path=str(topology), content_type="application/json"))
                    context.route("https://fonts.googleapis.com/**", lambda route: route.abort())
                    context.route("https://fonts.gstatic.com/**", lambda route: route.abort())
                    context.route("https://docs.google.com/forms/**", lambda route: route.fulfill(
                        content_type="text/html", body="<h1>Formulario de teste</h1>"))
                    context.set_default_timeout(120000)
                    page = context.new_page()
                    page.on("pageerror", lambda error: print("PAGE ERROR:", error, flush=True))
                    try:
                        print(f"START {width}px", flush=True)
                        page.goto(origin, wait_until="domcontentloaded")
                        expect(page.locator("#kpis h3").first).to_have_text("32")
                        page.wait_for_function("document.querySelector('#mapa .js-plotly-plot')?._fullLayout?.width > 0")
                        assert_width(page)
                        assert all(page.locator(".brand-logo").evaluate_all("imgs => imgs.map(i => i.complete && i.naturalWidth > 0)"))
                        toggle = page.locator("#mobile-filters-toggle")
                        filters = page.locator("#dashboard-filters")
                        if width <= 900:
                            expect(toggle).to_be_visible()
                            expect(toggle).to_have_attribute("aria-expanded", "false")
                            expect(filters).to_be_hidden()
                            toggle.tap()
                            expect(filters).to_be_visible()
                            expect(toggle).to_have_attribute("aria-expanded", "true")
                            page.screenshot(path=str(output / f"{width}-painel.png"))
                        else:
                            expect(toggle).to_be_hidden()
                            expect(filters).to_be_visible()
                        uf = page.locator("#f-uf")
                        uf.click()
                        page.get_by_role("option", name="SP", exact=True).click()
                        expect(page.locator("#kpis h3").first).to_have_text("20")
                        page.get_by_label("Tem site", exact=True).check()
                        if width <= 900:
                            toggle.focus()
                            toggle.press("Space")
                            expect(filters).to_be_hidden()
                            expect(toggle).to_have_attribute("aria-expanded", "false")
                            toggle.press("Enter")
                            expect(filters).to_be_visible()
                            expect(page.get_by_label("Tem site", exact=True)).to_be_checked()
                            expect(page.locator("#f-uf")).to_contain_text("SP")
                        for label, url in (("Indicar associa\u00e7\u00e3o", dashboard.FORM_URL),
                                           ("Validar/atualizar dados", dashboard.FORM_VALIDACAO_URL),
                                           ("Enviar sugest\u00e3o", dashboard.FORM_SUGESTOES_URL)):
                            link = filters.get_by_role("link", name=label, exact=True)
                            expect(link).to_have_attribute("href", url)
                            with page.expect_popup() as opened:
                                link.click(no_wait_after=True)
                            popup = opened.value
                            popup.wait_for_url(url)
                            popup.close()
                        if width <= 900:
                            toggle.tap()
                            expect(filters).to_be_hidden()
                        expect(page.locator("#kpis h3").first).to_have_text("20")
                        assert_width(page)
                        map_box = page.locator("#mapa").bounding_box()
                        assert map_box["width"] <= width
                        page.wait_for_function("document.querySelector('#mapa .choroplethlayer path') !== null")
                        page.locator("#mapa").scroll_into_view_if_needed()
                        page.screenshot(path=str(output / f"{width}-mapa.png"))
                        table = page.locator("#lista-mapa")
                        expect(table).to_contain_text("Associacao 01")
                        table.locator("button.next-page").click()
                        expect(table).to_contain_text("Associacao 13")
                        table.locator("button.previous-page").click()
                        for label in ("Site", "Instagram"):
                            with page.expect_popup() as opened:
                                table.get_by_role("link", name=label, exact=True).first.click(no_wait_after=True)
                            popup = opened.value
                            popup.wait_for_url(target_origin + "/" + label.lower())
                            popup.close()
                        page.locator(".tab").filter(has_text="Estat\u00edsticas").click()
                        expect(page.locator("#rosca-serv-8")).to_be_attached()
                        page.wait_for_function("""() => {
                            const plots = [...document.querySelectorAll('.statistics-grid .js-plotly-plot')];
                            return plots.length === 13 && plots.every(p => p._fullData?.length > 0 && p._fullLayout?.title?.text);
                        }""")
                        boxes = page.locator(".statistics-grid > div").evaluate_all(
                            "nodes => nodes.map(n => {const r=n.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height};})")
                        assert len(boxes) == 13
                        if width <= 900:
                            for before, after in zip(boxes, boxes[1:]):
                                assert after["y"] >= before["y"] + before["h"] - 1, (width, before, after)
                        else:
                            assert abs(boxes[0]["y"] - boxes[1]["y"]) < 1
                            assert abs(boxes[4]["y"] - boxes[5]["y"]) < 1
                        assert_width(page)
                        page.locator("#rank-mun").scroll_into_view_if_needed()
                        page.screenshot(path=str(output / f"{width}-estatisticas.png"))
                        page.screenshot(path=str(output / f"{width}-graficos.png"), full_page=True)
                        page.locator("#rosca-serv-0").scroll_into_view_if_needed()
                        page.screenshot(path=str(output / f"{width}-servicos.png"))
                        page.locator(".tab").filter(has_text="Tabela").click()
                        expect(page.locator("#tabela")).to_contain_text("Associacao 01")
                        scroll = page.locator("#tabela .dash-spreadsheet-container")
                        assert scroll.evaluate("e => {e.scrollLeft = e.scrollWidth; return e.scrollLeft > 0;}")
                        assert_width(page)
                        page.screenshot(path=str(output / f"{width}-tabela.png"))
                        # Removing the UF filter restores enough rows to exercise table pagination.
                        if width <= 900:
                            toggle.tap()
                        uf.click()
                        page.get_by_role("option", name="RJ", exact=True).click()
                        if width <= 900:
                            toggle.tap()
                        expect(page.locator("#kpis h3").first).to_have_text("32")
                        page.locator("#tabela button.next-page").click()
                        expect(page.locator("#tabela")).to_contain_text("Associacao 26")
                        before_visits = dashboard.server.extensions["statistics_store"].report(today(), today())[0]["visits"]
                        page.goto(origin + "/estatisticas")
                        page.get_by_label("Senha", exact=True).fill(password)
                        page.get_by_role("button", name="Entrar", exact=True).click()
                        page.wait_for_url(origin + "/estatisticas")
                        expect(page.locator(".metric strong").first).to_have_text(str(before_visits))
                        assert dashboard.server.extensions["statistics_store"].report(today(), today())[0]["visits"] == before_visits
                        assert_width(page)
                        page.screenshot(path=str(output / f"{width}-privada.png"), full_page=True)
                        results.append({"width": width, "status": "PASS", "charts": len(boxes),
                                        "map_width": map_box["width"], "visits": before_visits})
                        (output / f"results-{width}.json").write_text(json.dumps(results[-1], indent=2), encoding="utf-8")
                        print(f"PASS {width}px: panel, keyboard/touch, filters, forms, tabs, charts, tables, tracking, private page", flush=True)
                    except Exception:
                        import traceback
                        traceback.print_exc()
                        try:
                            page.screenshot(path=str(output / f"{width}-failure.png"), full_page=True, timeout=5000)
                        except Exception:
                            pass
                        raise
                    finally:
                        context.close()
                browser.close()
            (output / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        finally:
            for item in (server, target_server):
                item.shutdown()
                item.server_close()
            for thread in threads:
                thread.join(timeout=5)


if __name__ == "__main__":
    check()
