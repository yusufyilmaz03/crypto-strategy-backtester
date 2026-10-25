import pytest

from kripto_bot import config
from kripto_bot import instances
from kripto_bot.instances import InstanceError


@pytest.fixture
def path(tmp_path):
    return str(tmp_path / "instances.json")


def test_first_load_migrates_the_forward_test(path):
    items = instances.load_all(path)
    fwd = items["forward-v9"]
    assert fwd.run_key == config.strategy_label()          # same paper.db key: no interruption
    assert fwd.enabled and fwd.mode == "paper" and fwd.params == config.STRATEGY_PARAMS
    assert items["testnet-v9"].run_key == "testnet.db" and not items["testnet-v9"].enabled


def test_create_validates_and_assigns_storage(path):
    inst = instances.create({"name": "trend-4h", "strategy": "v10", "params": {"entry": "55", "exit_len": 20},
                             "timeframe": "4h", "stop": "none", "symbols": ["btc/usdt"]}, path)
    assert inst.params == {"entry": 55, "exit_len": 20}
    assert inst.symbols == ["BTC/USDT"] and inst.run_key == "instance:trend-4h"
    assert instances.get("trend-4h", path).timeframe == "4h"


@pytest.mark.parametrize("data, message", [
    ({"name": "Bad Name"}, "name"),
    ({"name": "x1", "strategy": "v99"}, "unknown strategy"),
    ({"name": "x1", "params": {"nope": 1}}, "no parameter"),
    ({"name": "x1", "params": {"slow": 2.5}}, "whole number"),
    ({"name": "x1", "timeframe": "2d"}, "timeframe"),
    ({"name": "x1", "symbols": ["BTCUSDT"]}, "invalid symbols"),
    ({"name": "x1", "mode": "testnet", "stop": "loose"}, "can't use a stop"),
    ({"name": "forward-v9"}, "already exists"),
])
def test_create_rejects_invalid(path, data, message):
    instances.load_all(path)
    with pytest.raises(InstanceError, match=message):
        instances.create(data, path)


def test_setup_is_locked_after_creation(path):
    instances.load_all(path)
    with pytest.raises(InstanceError, match="can't be changed"):
        instances.update("forward-v9", path, params={"slow": 100})
    instances.update("forward-v9", path, description="note", enabled=False)
    assert instances.get("forward-v9", path).description == "note"


def test_only_one_testnet_instance_runs(path):
    instances.load_all(path)
    instances.update("testnet-v9", path, enabled=True)
    instances.create({"name": "tn2", "mode": "testnet"}, path)
    with pytest.raises(InstanceError, match="only one testnet"):
        instances.update("tn2", path, enabled=True)


def test_delete_requires_stop(path):
    instances.load_all(path)
    with pytest.raises(InstanceError, match="stop the instance"):
        instances.delete("forward-v9", path)
    instances.update("forward-v9", path, enabled=False)
    instances.delete("forward-v9", path)
    assert "forward-v9" not in instances.load_all(path)


def test_param_spec():
    spec = {p["name"]: p for p in instances.param_spec("v9")}
    assert spec["slow"] == {"name": "slow", "default": 200, "type": "int"}
    assert {p["name"]: p["type"] for p in instances.param_spec("v4")}["bb_mult"] == "float"


def test_engine_config_follows_instance(path):
    inst = instances.create({"name": "loose-v5", "strategy": "v5", "stop": "loose", "equity": 250}, path)
    cfg = inst.engine_config()
    assert cfg.atr_multiplier == 2.0 and cfg.initial_equity == 250.0


def test_unknown_fields_from_a_newer_version_are_ignored(tmp_path):
    import json
    path = tmp_path / "instances.json"
    path.write_text(json.dumps([{"name": "abc", "mode": "paper", "future_field": 1}]))
    assert instances.load_all(str(path))["abc"].sizing == "equal"


def test_trailing_exit_is_a_locked_paper_setting(path):
    inst = instances.create({"name": "v9-trail", "strategy": "v9", "params": {"slow": 50}, "trail": "3"}, path)
    assert inst.trail == 3.0 and inst.engine_config().trail_atr == 3.0
    assert instances.create({"name": "v9-plain", "strategy": "v9", "trail": ""}, path).engine_config().trail_atr is None
    with pytest.raises(instances.InstanceError, match="can't be changed"):
        instances.update("v9-trail", path, trail=None)
    for data, message in [({"trail": "x"}, "must be a number"), ({"trail": 0.1}, "between"),
                          ({"trail": 3, "mode": "testnet"}, "only paper")]:
        with pytest.raises(instances.InstanceError, match=message):
            instances.create({"name": "bad-trail", "strategy": "v9", **data}, path)
