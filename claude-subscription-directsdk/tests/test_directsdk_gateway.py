"""Opt-in gateway mode (fork addition): native reaches an Anthropic-compatible HTTPS gateway with a gateway key.

With ``upstream_base_url`` and ``upstream_auth_env`` set, the relay forwards to the gateway instead of
api.anthropic.com, native authenticates with the key read from the named Hermes environment variable,
no local Claude login is needed, and an inherited subscription token never reaches the child. Unset,
behaviour is unchanged (covered by the existing suite, e.g. test_directsdk_inherited_api_key).
"""
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from conftest import PLUGIN_NAME
from test_directsdk import FAKE
from test_directsdk_telemetry import CAPTURE, _captured

GATEWAY = "https://hub.example.test"
KEY_ENV = "TEST_GATEWAY_KEY"
KEY = "gateway-key-test"
TOOLS = [{"type": "function", "function": {"name": "probe", "description": "TAIL",
                                         "parameters": {"type": "object", "properties": {}}}}]


def _set_gateway(url=GATEWAY, key_env=KEY_ENV):
    from hermes_cli.config import get_config_path
    lines = ["plugins:", "  enabled: []", "  entries:", f"    {PLUGIN_NAME}:", "      settings:"]
    if url is not None:
        lines.append(f"        upstream_base_url: {url!r}")
    if key_env is not None:
        lines.append(f"        upstream_auth_env: {key_env!r}")
    get_config_path().write_text("\n".join(lines) + "\n", encoding="utf-8")


def _record_upstreams(monkeypatch):
    """Wrap the loaded plugin's Admission so each turn's relay upstream is observable."""
    seen = []
    for name, module in list(sys.modules.items()):
        if name.startswith("_hermes_user_provider_") and name.endswith(".directsdk"):
            original = module.Admission

            class Recording(original):
                def __init__(self, upstream, *args, **kwargs):
                    seen.append(upstream)
                    super().__init__(upstream, *args, **kwargs)

            monkeypatch.setattr(module, "Admission", Recording)
    return seen


def _turn(profile, tmp_path, monkeypatch):
    monkeypatch.setenv("ENV_CAPTURE", str(tmp_path / "env.jsonl"))
    script = tmp_path / "native.py"
    script.write_text(FAKE.replace("rows=[]", CAPTURE + "rows=[]"))
    client = profile.create_client(command=[sys.executable, str(script)])
    upstreams = _record_upstreams(monkeypatch)
    try:
        client.create(model="sonnet", messages=[{"role": "user", "content": "go"}], tools=TOOLS)
    finally:
        client.close()
    return upstreams


def test_gateway_turn_uses_the_gateway_key_and_upstream(profile, tmp_path, monkeypatch):
    _set_gateway()
    monkeypatch.setenv(KEY_ENV, KEY)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "subscription-token-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-test")
    upstreams = _turn(profile, tmp_path, monkeypatch)
    (env,) = _captured(tmp_path)
    assert env["ANTHROPIC_AUTH_TOKEN"] == KEY
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env and "ANTHROPIC_API_KEY" not in env
    # Native talks to the loopback relay; the relay's upstream is the gateway.
    assert urlsplit(env["ANTHROPIC_BASE_URL"]).hostname == "127.0.0.1"
    assert upstreams == [GATEWAY]
    assert env["CLAUDE_CODE_ENTRYPOINT"] == "hermes-agent"


@pytest.mark.parametrize("url, key_env, key, message", [
    (GATEWAY, KEY_ENV, None, "unset or empty"),
    (GATEWAY, None, KEY, "names no environment variable"),
    ("http://hub.example.test", KEY_ENV, KEY, "https"),
])
def test_misconfigured_gateway_fails_closed(profile, tmp_path, monkeypatch, url, key_env, key, message):
    _set_gateway(url, key_env)
    monkeypatch.delenv(KEY_ENV, raising=False)
    if key is not None:
        monkeypatch.setenv(KEY_ENV, key)
    monkeypatch.setenv("ENV_CAPTURE", str(tmp_path / "env.jsonl"))
    script = tmp_path / "native.py"
    script.write_text(FAKE.replace("rows=[]", CAPTURE + "rows=[]"))
    client = profile.create_client(command=[sys.executable, str(script)])
    try:
        with pytest.raises(ValueError, match=message):
            client.create(model="sonnet", messages=[{"role": "user", "content": "go"}])
    finally:
        client.close()
    assert not (tmp_path / "env.jsonl").exists(), "native must not start on a misconfigured gateway"


def test_inherited_base_url_is_still_refused_in_gateway_mode(profile, tmp_path, monkeypatch):
    _set_gateway()
    monkeypatch.setenv(KEY_ENV, KEY)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.invalid")
    with pytest.raises(ValueError, match="ANTHROPIC_BASE_URL"):
        _turn(profile, tmp_path, monkeypatch)


def test_gateway_setup_needs_no_local_login(profile, tmp_path, monkeypatch):
    _set_gateway()
    monkeypatch.setenv(KEY_ENV, KEY)
    marker = tmp_path / "ran"
    script = tmp_path / "claude.py"
    script.write_text(f"open({str(marker)!r}, 'w').write('x')\n")
    command = [sys.executable, str(script)]
    status = profile.setup_status(command=command)
    assert status["logged_in"] is True and status["login_command"] is None
    assert status["plan"] == "via hub.example.test"
    assert profile.discover_models(command=command) is None
    assert not marker.exists(), "gateway mode must not probe the local login or picker"


def test_gateway_usage_points_at_the_gateway(profile, monkeypatch):
    _set_gateway()
    monkeypatch.setenv(KEY_ENV, KEY)
    snapshot = profile.fetch_account_usage()
    assert snapshot.windows == () and "gateway" in snapshot.unavailable_reason


def test_manifest_declares_the_gateway_settings(profile):
    from hermes_cli.plugins_settings import plugin_settings_fields
    import directsdk_setup
    root = Path(directsdk_setup.__file__).resolve().parent
    fields = {field["key"]: field for field in plugin_settings_fields(PLUGIN_NAME, root)}
    for key in (directsdk_setup.GATEWAY_URL_SETTING, directsdk_setup.GATEWAY_AUTH_SETTING):
        assert fields[key]["type"] == "string" and fields[key]["default"] == ""
        assert fields[key]["label"] and fields[key]["description"]
