from types import SimpleNamespace
from app.main import public_config


def test_runtime_config_only_exposes_public_client_settings(monkeypatch):
    cfg=SimpleNamespace(session_secret='secret-session',database_url='secret-db',
                        llm_api_key='secret-llm',llm_model='deepseek-v4.1-flash-1017',execution_enabled=False)
    monkeypatch.setattr('app.main.settings',lambda:cfg)
    config=public_config()
    assert config['configured'] is True
    assert config['model']=='deepseek-v4.1-flash-1017'
    assert config['execution_enabled'] is False
    assert not any('secret' in str(v) for v in config.values())
