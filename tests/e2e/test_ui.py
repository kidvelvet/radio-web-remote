"""Browser tests against the real server and simulated IC-7300, in Chromium, Firefox, WebKit.

Run: .tools/bin/uv run pytest -m e2e --browser chromium --browser firefox --browser webkit
"""

import re

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e


def login(page: Page, server, user="op"):
    page.goto("/login")
    page.fill("#username", user)
    page.fill("#password", server["password"])
    page.click("#go")
    expect(page.locator("#link")).to_have_text("Connected", timeout=10_000)
    expect(page.locator("#freqtext")).not_to_have_text("--.---.---", timeout=10_000)
    # Tests share one simulated radio: start only once no one holds TX.
    expect(page.locator("#txtext")).to_have_text("Receive", timeout=10_000)


def collect_csp_errors(page: Page) -> list[str]:
    errors: list[str] = []
    page.on(
        "console", lambda m: errors.append(m.text) if "Content Security Policy" in m.text else None
    )
    return errors


def test_unauthenticated_goes_to_login(page: Page, server):
    page.goto("/")
    expect(page).to_have_url(re.compile(r"/login$"))


def test_wrong_password_message(page: Page, server):
    page.goto("/login")
    page.fill("#username", "op")
    page.fill("#password", "definitely wrong")
    page.click("#go")
    expect(page.locator("#msg")).to_have_text("Wrong username or password.")


def test_login_and_state(page: Page, server):
    csp = collect_csp_errors(page)
    login(page, server)
    expect(page.locator("#title")).to_have_text("IC-7300")
    expect(page.locator("#freqtext")).to_have_text(re.compile(r"^\d{1,2}\.\d{3}\.\d{3}$"))
    expect(page.locator("#who")).to_contain_text("N0OP")
    expect(page.locator("#modes button[aria-pressed=true]")).to_have_count(1)
    expect(page.locator("#m-s-t")).not_to_have_text("-", timeout=5_000)
    assert csp == [], csp


def test_tune_controls(page: Page, server):
    login(page, server)
    page.click("#bands button:has-text('20m')")
    expect(page.locator("#freqtext")).to_have_text("14.200.000")
    page.click("#up")
    expect(page.locator("#freqtext")).to_have_text("14.200.100")
    page.fill("#entry", "7.074")
    page.press("#entry", "Enter")
    expect(page.locator("#freqtext")).to_have_text("7.074.000")
    page.click("#bands button:has-text('20m')")
    expect(page.locator("#freqtext")).to_have_text("14.200.000")


def test_drag_tuning(page: Page, server):
    login(page, server)
    page.click("#bands button:has-text('20m')")
    expect(page.locator("#freqtext")).to_have_text("14.200.000")
    box = page.locator("#freq").bounding_box()
    y = box["y"] + box["height"] / 2
    page.mouse.move(box["x"] + 40, y)
    page.mouse.down()
    page.mouse.move(box["x"] + 40 + 80, y, steps=10)  # 80 px = 10 steps of 100 Hz
    page.mouse.up()
    expect(page.locator("#freqtext")).to_have_text("14.201.000", timeout=5_000)


def test_mode_and_filter(page: Page, server):
    login(page, server)
    page.click("#modes button[data-mode=LSB]")
    expect(page.locator("#modes button[data-mode=LSB]")).to_have_attribute("aria-pressed", "true")
    page.click("[data-filter='2']")
    expect(page.locator("[data-filter='2']")).to_have_attribute(
        "aria-pressed", "true", timeout=5_000
    )
    page.click("#modes button[data-mode=USB]")


def test_ptt_hold_and_release(page: Page, server):
    login(page, server)
    ptt = page.locator("#ptt")
    expect(ptt).to_be_enabled()
    ptt.hover()  # scrolls it into view and puts the pointer on it
    page.mouse.down()
    expect(page.locator("#txtext")).to_have_text(re.compile(r"ON AIR\s+N0OP"), timeout=5_000)
    expect(page.locator("#m-po-t")).not_to_have_text("-", timeout=5_000)
    page.mouse.up()
    expect(page.locator("#txtext")).to_have_text("Receive", timeout=5_000)


def test_spacebar_ptt(page: Page, server):
    login(page, server)
    page.locator("#freq").focus()
    page.keyboard.down("Space")
    expect(page.locator("#txtext")).to_have_text(re.compile("ON AIR"), timeout=5_000)
    page.keyboard.up("Space")
    expect(page.locator("#txtext")).to_have_text("Receive", timeout=5_000)


def test_toggle_ptt(page: Page, server):
    login(page, server)
    page.check("#toggle")
    page.click("#ptt")
    expect(page.locator("#txtext")).to_have_text(re.compile("ON AIR"), timeout=5_000)
    page.click("#ptt")
    expect(page.locator("#txtext")).to_have_text("Receive", timeout=5_000)


def test_other_user_sees_tx_held(page: Page, server, browser):
    login(page, server)
    other_ctx = browser.new_context(base_url=server["url"], ignore_https_errors=True)
    other = other_ctx.new_page()
    try:
        login(other, server, "lee")
        expect(other.locator("#ptt")).to_be_disabled()
        page.check("#toggle")
        page.click("#ptt")
        expect(other.locator("#txtext")).to_have_text("TX held by N0OP", timeout=5_000)
        page.click("#ptt")
        expect(other.locator("#txtext")).to_have_text("Receive", timeout=5_000)
    finally:
        other_ctx.close()


def test_closing_the_tab_unkeys(page: Page, server, browser):
    watcher_ctx = browser.new_context(base_url=server["url"], ignore_https_errors=True)
    watcher = watcher_ctx.new_page()
    try:
        login(watcher, server, "lee")
        login(page, server)
        page.check("#toggle")
        page.click("#ptt")
        expect(watcher.locator("#txtext")).to_have_text("TX held by N0OP", timeout=5_000)
        page.close()  # no PTT release from the page: the server must unkey
        expect(watcher.locator("#txtext")).to_have_text("Receive", timeout=5_000)
    finally:
        watcher_ctx.close()


def test_mobile_layout_screenshot(page: Page, server, browser_name, tmp_path_factory):
    page.set_viewport_size({"width": 390, "height": 844})
    login(page, server)
    # No horizontal scroll at phone width.
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    out = server["dir"] / f"mobile-{browser_name}.png"
    page.screenshot(path=str(out), full_page=True)


def _scope_on(page: Page):
    page.locator("#scope-toggle").click()
    expect(page.locator("#scope-toggle")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#scope-edges")).to_have_text(
        re.compile(r"\d+\.\d{3} - \d+\.\d{3} MHz"), timeout=10_000
    )


def test_scope_draws_and_click_tunes(page: Page, server):
    csp = collect_csp_errors(page)
    login(page, server)
    page.click("#bands button:has-text('20m')")
    expect(page.locator("#freqtext")).to_have_text("14.200.000")
    _scope_on(page)
    page.select_option("#sc-mode", "center")
    page.select_option("#sc-span", "50000")
    expect(page.locator("#scope-edges")).to_have_text(
        re.compile(r"14\.150 - 14\.250 MHz"), timeout=10_000
    )
    # The waterfall has real pixels (not just the background colour).
    page.wait_for_timeout(1500)
    lit = page.evaluate("""() => {
      const c = document.getElementById('waterfall');
      const d = c.getContext('2d').getImageData(0, 0, c.width, 4).data;
      let n = 0; for (let i = 0; i < d.length; i += 4) if (d[i] + d[i+1] + d[i+2] > 60) n++;
      return n;
    }""")
    assert lit > 0
    # Tap three quarters of the way across: 14.150 + 0.75 * 0.100 MHz = 14.225 MHz.
    page.select_option("#step", "1000")
    box = page.locator("#spectrum").bounding_box()
    page.mouse.click(box["x"] + box["width"] * 0.75, box["y"] + box["height"] / 2)
    expect(page.locator("#freqtext")).to_have_text("14.225.000", timeout=5_000)
    assert csp == [], csp


def test_scope_settings(page: Page, server):
    login(page, server)
    _scope_on(page)
    page.select_option("#sc-mode", "center")
    page.select_option("#sc-span", "10000")
    expect(page.locator("#scope-edges")).to_have_text(re.compile(r"(\d+\.\d{3}) - "), timeout=5_000)
    page.wait_for_timeout(800)
    low, high = re.findall(r"(\d+\.\d{3})", page.locator("#scope-edges").inner_text())[:2]
    assert round(float(high) - float(low), 3) == 0.02  # +/-10 kHz
    page.select_option("#sc-mode", "fixed")
    expect(page.locator(".sc-fixed")).to_be_visible()
    expect(page.locator(".sc-center")).to_be_hidden()
    page.select_option("#sc-mode", "center")
    page.select_option("#sc-span", "50000")
