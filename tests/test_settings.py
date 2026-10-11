import importlib
import json

import pytest

import settings
from settings import BY_KEY, SettingsError, coerce, load_overrides, save


def test_coerce_types_and_limits():
    assert coerce(BY_KEY["FEE_RATE_TAKER"], "0.00075") == 0.00075
    assert coerce(BY_KEY["WFO_FOLDS"], "6") == 6
    with pytest.raises(SettingsError):
        coerce(BY_KEY["WFO_FOLDS"], "2.5")
    with pytest.raises(SettingsError):
        coerce(BY_KEY["FEE_RATE_TAKER"], "0.5")          # above max
    with pytest.raises(SettingsError):
        coerce(BY_KEY["SLIPPAGE_BPS"], "abc")


def test_symbols_are_normalized_and_validated():
    s = BY_KEY["SYMBOLS"]
    assert coerce(s, "btc/usdt, ETH/USDT\nbtc/usdt") == ["BTC/USDT", "ETH/USDT"]
    with pytest.raises(SettingsError):
        coerce(s, "BTCUSDT")
    with pytest.raises(SettingsError):
        coerce(s, "")


def test_save_reports_restarts_and_rejects_read_only(tmp_path):
    path = str(tmp_path / "settings.json")
    saved, restart = save({"MAX_ORDER_USDT": "50", "BACKTEST_DAYS": 30}, path)
    assert saved == {"MAX_ORDER_USDT": 50.0, "BACKTEST_DAYS": 30}
    assert restart == ["testnet"]
    _, restart = save({"MAX_ORDER_USDT": 50}, path)         # unchanged value: no restart
    assert restart == []
    with pytest.raises(SettingsError):
        save({"LIVE_TRADING_ENABLED": True}, path)
    with pytest.raises(SettingsError):
        save({"NOT_A_SETTING": 1}, path)


def test_load_ignores_tampered_entries(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"LIVE_TRADING_ENABLED": True, "FEE_RATE_TAKER": 5, "SLIPPAGE_BPS": 7}))
    assert load_overrides(str(path)) == {"SLIPPAGE_BPS": 7.0}


def test_config_applies_overrides(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"SLIPPAGE_BPS": 12}))
    monkeypatch.setattr(settings, "SETTINGS_FILE", str(path))
    import config
    try:
        importlib.reload(config)
        assert config.SLIPPAGE_BPS == 12.0
        assert config.engine_config().slippage_bps == 12.0
        assert config.LIVE_TRADING_ENABLED is False
    finally:
        monkeypatch.undo()
        importlib.reload(config)
