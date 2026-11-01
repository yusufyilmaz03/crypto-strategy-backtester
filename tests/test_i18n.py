import glob
import os
import re

import pytest

from kripto_bot import instances, settings
from kripto_bot.app import dashboard, i18n
from kripto_bot.core import strategy

APP_DIR = os.path.dirname(dashboard.__file__)
LITERAL = r'"((?:[^"\\]|\\.)*)"'
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _first_arg(src, start):
    """Source of the first argument of a call whose "(" ends at start."""
    depth, j = 0, start
    while j < len(src):
        c = src[j]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif c == "," and depth == 0:
            break
        elif c in "\"'`":
            j += 1
            while src[j] != c:
                j += 2 if src[j] == "\\" else 1
        j += 1
    return src[start:j]


def panel_keys():
    """Texts the templates and app.js translate: _("..."), KB.t("..." + "...") and
    KB.t(cond ? "a" : "b"). Mixed forms ((a ? "x" : "y") + "z") are listed in the catalog by hand."""
    keys = set()
    files = glob.glob(os.path.join(APP_DIR, "templates", "*.html")) + [os.path.join(APP_DIR, "static", "app.js")]
    for path in files:
        src = open(path, encoding="utf-8").read()
        keys.update(m.group(1) for m in re.finditer(r'\b_\(\s*' + LITERAL, src))
        keys.update(m.group(1) for m in re.finditer(r"\b_\(\s*'([^']*)'", src))
        for m in re.finditer(r"KB\.t\(", src):
            arg = _first_arg(src, m.end())
            literals = re.findall(LITERAL, arg)
            if not literals or ("?" in arg and "+" in arg):
                continue
            if "?" in arg:              # the branches, not the condition's literals
                keys.update(re.findall(LITERAL, arg.split("?", 1)[1]))
            else:
                keys.add("".join(literals))
    return {k.replace('\\"', '"') for k in keys if k}


def server_texts():
    """English texts the server sends that the browser shows (translated by KB.t)."""
    texts = set(strategy.STRATEGY_INFO.values()) | set(instances.SIZINGS.values())
    texts |= {d for p in strategy.PARAM_INFO.values() for d in p.values()}
    for s in settings.SCHEMA:
        texts |= {s.group, s.label} | ({s.help} if s.help else set())
    texts |= {"Binance API key", "Testnet API key", "Telegram bot"}     # dashboard._settings_view
    return texts


@pytest.mark.parametrize("source", ["panel", "server"])
def test_every_text_has_a_turkish_translation(source):
    texts = panel_keys() if source == "panel" else server_texts()
    missing = sorted(t for t in texts if t not in i18n.TR)
    assert not missing, missing


def test_translations_keep_their_placeholders():
    for key, tr in i18n.TR.items():
        assert set(PLACEHOLDER.findall(key)) == set(PLACEHOLDER.findall(tr)), key


def test_translate_escapes_text_and_values():
    assert i18n.translate("Overview", "tr") == "Genel bakış"
    assert i18n.translate("Overview", "en") == "Overview"
    assert i18n.translate("[button] Close", "en") == "Close"
    out = i18n.translate("e.g. {example}", "tr", example="<b>")
    assert out == "örn. &lt;b&gt;"


@pytest.fixture
def client():
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


def test_language_from_cookie_then_browser(client):
    page = client.get("/settings").get_data(as_text=True)
    assert '<html lang="en"' in page and "window.KB_I18N = {}" in page
    page = client.get("/settings", headers={"Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8"}).get_data(as_text=True)
    assert '<html lang="tr"' in page and "Ayarlar" in page
    client.set_cookie(i18n.COOKIE, "en")
    page = client.get("/settings", headers={"Accept-Language": "tr"}).get_data(as_text=True)
    assert '<html lang="en"' in page and ">Settings</h1>" in page
    client.set_cookie(i18n.COOKIE, "tr")
    page = client.get("/settings").get_data(as_text=True)
    assert '<html lang="tr"' in page and "<code>runtime/settings.json</code>" in page
