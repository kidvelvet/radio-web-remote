"""Browser tests against the real server and simulated IC-7300, in Chromium, Firefox, WebKit.

Run: .tools/bin/uv run pytest -m e2e --browser chromium --browser firefox --browser webkit
"""

import json
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
    page.wait_for_timeout(200)  # hold still before lifting: a drag, not a flick
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


def test_toggle_tap_after_a_denial_keys(page: Page, server):
    """Review D3-1: only a tap after a server stop is "release only"; after a
    refused press the next tap transmits."""
    login(page, server, "twenty")
    page.check("#toggle")
    page.fill("#entry", "7.074")
    page.press("#entry", "Enter")
    expect(page.locator("#freqtext")).to_have_text("7.074.000")
    page.click("#ptt")  # outside this user's band: refused
    expect(page.locator("#msg")).to_contain_text("outside", timeout=5_000)
    expect(page.locator("#msg")).not_to_contain_text("out_of_limits")  # review D3-5
    page.click("#bands button:has-text('20m')")
    expect(page.locator("#freqtext")).to_have_text("14.200.000")
    page.click("#ptt")
    expect(page.locator("#txtext")).to_have_text(re.compile(r"ON AIR\s+N0TW"), timeout=5_000)
    page.click("#ptt")
    expect(page.locator("#txtext")).to_have_text("Receive", timeout=5_000)


def test_mouse_click_elsewhere_keeps_space_ptt(page: Page, server):
    """Review D3-3: a click on another control must not drop a Space press."""
    login(page, server)
    page.locator("#freq").focus()
    page.keyboard.down("Space")
    expect(page.locator("#txtext")).to_have_text(re.compile("ON AIR"), timeout=5_000)
    page.click("#up")
    page.wait_for_timeout(500)
    expect(page.locator("#txtext")).to_have_text(re.compile("ON AIR"))
    page.keyboard.up("Space")
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


def _options(page: Page, sel: str) -> list[str]:
    return page.eval_on_selector_all(f"{sel} option", "els => els.map(e => e.value)")


def test_ic705_page_shows_its_own_modes_and_scope_options(page: Page, server):
    login(page, server)
    assert _options(page, "#sc-mode") == ["center", "fixed"]  # first radio: IC-7300
    assert _options(page, "#sc-edge") == ["1", "2", "3"]
    expect(page.locator("#modes button:has-text('DV')")).to_have_count(0)
    page.goto("/?radio=sim-ic705")
    expect(page.locator("#link")).to_have_text("Connected", timeout=10_000)
    expect(page.locator("#modes button:has-text('WFM')")).to_be_visible(timeout=10_000)
    expect(page.locator("#modes button:has-text('DV')")).to_be_visible()
    _scope_on(page)
    assert _options(page, "#sc-mode") == ["center", "fixed", "scroll-c", "scroll-f"]
    assert _options(page, "#sc-edge") == ["1", "2", "3", "4"]
    page.select_option("#sc-mode", "scroll-f")
    expect(page.locator(".sc-fixed")).to_be_visible()
    expect(page.locator(".sc-center")).to_be_hidden()
    page.select_option("#sc-edge", "4")
    page.select_option("#sc-mode", "scroll-c")
    expect(page.locator(".sc-center")).to_be_visible(timeout=5_000)
    expect(page.locator(".sc-fixed")).to_be_hidden()
    expect(page.locator("#sc-note")).to_have_text("")
    page.select_option("#sc-mode", "center")


def test_radio_menu_switches_in_place_and_is_remembered(page: Page, server):
    login(page, server)
    expect(page.locator("#title")).to_be_hidden()
    pick = page.locator("#radio-pick")
    expect(pick).to_be_visible()
    assert _options(page, "#radio-pick") == ["sim-ic7300", "sim-ic705", "sim-ic9700"]
    expect(pick.locator("option").first).to_have_text("sim-ic7300 (IC-7300)")
    expect(pick).to_have_value("sim-ic7300")
    _scope_on(page)
    page.evaluate("window.rwrMark = 1")  # gone if the page reloads
    pick.select_option("sim-ic705")
    expect(page).to_have_url(re.compile(r"/\?radio=sim-ic705$"))
    expect(page.locator("#link")).to_have_text("Connected", timeout=10_000)
    expect(page.locator("#modes button:has-text('DV')")).to_be_visible(timeout=10_000)
    expect(page.locator("#scope-edges")).to_have_text(re.compile(r"MHz"), timeout=10_000)
    assert page.evaluate("window.rwrMark") == 1  # switched in place
    # The home-screen app opens "/": it comes back to the radio used last.
    page.goto("/")
    expect(page.locator("#link")).to_have_text("Connected", timeout=10_000)
    expect(pick).to_have_value("sim-ic705")
    expect(page.locator("#modes button:has-text('DV')")).to_be_visible(timeout=10_000)


def test_radio_menu_switches_with_an_older_cached_scope_script(page: Page, server):
    # An iPhone kept scope.js from before Scope.clear() existed: the switch must still happen.
    def old_scope(route):
        resp = route.fetch()
        route.fulfill(response=resp, body=resp.text().replace("  clear() {", "  gone() {"))

    page.route("**/static/js/scope.js", old_scope)
    login(page, server)
    _scope_on(page)
    page.select_option("#radio-pick", "sim-ic705")
    expect(page.locator("#link")).to_have_text("Connected", timeout=10_000)
    expect(page.locator("#modes button:has-text('DV')")).to_be_visible(timeout=10_000)


def test_changing_radio_releases_ptt_first(page: Page, server, browser):
    watcher_ctx = browser.new_context(base_url=server["url"], ignore_https_errors=True)
    watcher = watcher_ctx.new_page()
    try:
        login(watcher, server, "lee")
        login(page, server)
        page.check("#toggle")
        page.click("#ptt")
        expect(watcher.locator("#txtext")).to_have_text("TX held by N0OP", timeout=5_000)
        page.select_option("#radio-pick", "sim-ic705")
        expect(watcher.locator("#txtext")).to_have_text("Receive", timeout=5_000)
        expect(page.locator("#link")).to_have_text("Connected", timeout=10_000)
        expect(page.locator("#txtext")).to_have_text("Receive", timeout=5_000)
        expect(page.locator("#ptt")).to_have_attribute("aria-pressed", "false")
        # The page released PTT before it closed the socket (not "connection_closed").
        audit = (server["dir"] / "logs" / "audit.log").read_text().splitlines()
        stops = [e for e in map(json.loads, audit) if e.get("event") == "tx_stop"]
        assert stops[-1]["radio"] == "sim-ic7300"
        assert stops[-1]["reason"] == "released"
    finally:
        watcher_ctx.close()


def _band_names(page: Page) -> list[str]:
    return page.eval_on_selector_all("#bands button", "els => els.map(e => e.textContent)")


def test_ic9700_main_sub_and_band_shortcuts(page: Page, server):
    login(page, server)
    expect(page.locator("#bandsel")).to_be_hidden()  # IC-7300: one band
    assert "20m" in _band_names(page) and "2m" not in _band_names(page)
    page.goto("/?radio=sim-ic9700")
    expect(page.locator("#link")).to_have_text("Connected", timeout=10_000)
    expect(page.locator("#bandsel")).to_be_visible(timeout=10_000)
    assert _band_names(page) == ["2m", "70cm", "23cm"]  # coverage, Basic Manual 11-1
    expect(page.locator("#satnote")).to_be_hidden()
    # The simulated radio is shared by every browser in the run: work from its state.
    freq = page.locator("#freqtext")
    page.click("[data-band=main]")
    expect(page.locator("[data-band=main]")).to_have_attribute("aria-pressed", "true")
    expect(freq).not_to_have_text("--.---.---")
    main_text = freq.inner_text()
    page.click("[data-band=sub]")
    expect(page.locator("[data-band=sub]")).to_have_attribute("aria-pressed", "true")
    expect(freq).not_to_have_text(main_text, timeout=5_000)
    sub_text = freq.inner_text()
    page.click("#band-swap")  # contents swap; SUB stays selected
    expect(freq).to_have_text(main_text, timeout=5_000)
    expect(page.locator("[data-band=sub]")).to_have_attribute("aria-pressed", "true")
    page.click("#band-swap")
    expect(freq).to_have_text(sub_text, timeout=5_000)
    page.click("#bands button:has-text('23cm')")
    expect(freq).to_have_text("1296.100.000", timeout=5_000)
    page.click("#bands button:has-text('70cm')")
    expect(freq).to_have_text("432.100.000", timeout=5_000)
    page.click("[data-band=main]")
    expect(freq).to_have_text(main_text, timeout=5_000)
    # Dualwatch off: the radio refuses SUB, so the page disables it (H9700-2).
    expect(page.locator("#band-dw")).to_have_attribute("aria-pressed", "true")
    page.click("#band-dw")
    expect(page.locator("#band-dw")).to_have_attribute("aria-pressed", "false", timeout=5_000)
    expect(page.locator("[data-band=sub]")).to_be_disabled()
    page.click("#band-dw")
    expect(page.locator("[data-band=sub]")).to_be_enabled(timeout=5_000)


def _audio_started(page: Page):
    try:
        expect(page.locator("#au-start")).to_have_text("Stop audio", timeout=10_000)
    except AssertionError as e:
        raise AssertionError(
            f"audio did not start: {page.locator('#au-status').inner_text()}"
        ) from e


def test_listen_only_audio_plays(page: Page, server):
    login(page, server)
    page.select_option("#au-mode", "monitor")
    page.click("#au-start")
    _audio_started(page)
    # The player worklet reports its buffer once a second: receive audio is flowing.
    expect(page.locator("#au-status")).to_have_text(
        re.compile(r"Listening: buffer \d+ ms"), timeout=10_000
    )
    page.click("#au-start")
    expect(page.locator("#au-start")).to_have_text("Start audio")


def test_audio_keeps_playing_after_changing_radio(page: Page, server):
    # Count receive-audio frames (kind byte 2) on every control socket.
    page.add_init_script(
        """window.rwrAudio = 0;
        const Native = window.WebSocket;
        window.WebSocket = class extends Native {
          constructor(...a) {
            super(...a);
            this.addEventListener("message", (ev) => {
              const d = ev.data;
              if (d instanceof ArrayBuffer && new Uint8Array(d)[0] === 2) window.rwrAudio++;
            });
          }
        };"""
    )
    login(page, server)
    page.select_option("#au-mode", "monitor")
    page.click("#au-start")
    _audio_started(page)
    page.wait_for_function("() => window.rwrAudio > 10", timeout=10_000)
    page.select_option("#radio-pick", "sim-ic9700")
    expect(page.locator("#link")).to_have_text("Connected", timeout=10_000)
    expect(page.locator("#title")).to_have_text("IC-9700", timeout=10_000)
    before = page.evaluate("window.rwrAudio")
    page.wait_for_function("n => window.rwrAudio > n", arg=before + 10, timeout=10_000)
    expect(page.locator("#au-start")).not_to_have_text("Start audio")
    page.click("#au-start")


def test_resume_audio_after_reopening(page: Page, server):
    login(page, server)
    expect(page.locator("#au-resume-bar")).to_be_hidden()
    page.select_option("#au-mode", "monitor")
    page.click("#au-start")
    _audio_started(page)
    page.reload()  # like reopening the home-screen app: audio needs a tap again
    expect(page.locator("#link")).to_have_text("Connected", timeout=10_000)
    expect(page.locator("#au-resume")).to_have_text("Resume audio: Listen only")
    expect(page.locator("#au-mode")).to_have_value("monitor")
    page.click("#au-resume")
    _audio_started(page)
    expect(page.locator("#au-resume-bar")).to_be_hidden()
    page.click("#au-start")  # stopped on purpose: no offer next time
    page.reload()
    expect(page.locator("#link")).to_have_text("Connected", timeout=10_000)
    expect(page.locator("#au-resume-bar")).to_be_hidden()
    # "Not now" also clears the offer.
    page.click("#au-start")
    _audio_started(page)
    page.reload()
    page.click("#au-resume-no")
    expect(page.locator("#au-resume-bar")).to_be_hidden()
    page.reload()
    expect(page.locator("#au-resume-bar")).to_be_hidden()


def test_microphone_to_radio_while_keyed(page: Page, server, browser_name):
    if browser_name == "webkit":
        pytest.skip("Playwright WebKit has no fake microphone; covered by the iPhone/iPad pass")
    login(page, server)
    page.select_option("#au-mode", "remote")
    page.click("#au-start")
    _audio_started(page)
    expect(page.locator("#au-status")).to_have_text(
        re.compile(r"Mic and speaker on: buffer"), timeout=10_000
    )
    page.locator("#ptt").hover()
    page.mouse.down()
    # The server reports the level of microphone audio it actually received.
    expect(page.locator("#txtext")).to_have_text(re.compile(r"ON AIR\s+N0OP"), timeout=8_000)
    # The level is beside the status, outside the announced live region.
    expect(page.locator("#txlevel")).to_have_text(re.compile(r"audio -?\d+ dBFS"), timeout=8_000)
    page.mouse.up()
    expect(page.locator("#txtext")).to_have_text("Receive", timeout=5_000)
    page.click("#au-start")


def test_keying_without_mic_route_warns(page: Page, server):
    login(page, server)
    page.check("#toggle")
    page.click("#ptt")
    expect(page.locator("#msg")).to_contain_text("No microphone path")
    page.click("#ptt")
    expect(page.locator("#txtext")).to_have_text("Receive", timeout=5_000)
