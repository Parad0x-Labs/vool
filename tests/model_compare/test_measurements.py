from __future__ import annotations

import sqlite3


def test_upgraded_home_adds_timing_without_reclassifying_old_usage(tmp_path):
    from core import usage_meter
    from storage.db import configure_default_db_path
    path = tmp_path/'old-home.db'
    conn = sqlite3.connect(path)
    conn.execute('''CREATE TABLE token_usage(entry_id TEXT PRIMARY KEY,created_at TEXT,created_ts REAL,
        provider_id TEXT,model_id TEXT,cost_class TEXT,prompt_tokens INTEGER,output_tokens INTEGER)''')
    conn.execute("INSERT INTO token_usage VALUES ('historic','before',1,'local','model','free_local',7,9)")
    conn.commit()
    conn.close()
    configure_default_db_path(path)
    try:
        usage_meter.record_call_timing(model_call_id='new-call', usage_entry_id=None, provider_id='local', model_id='model',
                                       first_token_ms=None,total_ms=10,state='failed')
        row = usage_meter.measured_call('new-call')
        assert row == {'first_token_ms': None,'total_ms':10,'state':'failed','prompt_tokens':None,'output_tokens':None,'billed_usd':None}
        conn = sqlite3.connect(path)
        assert conn.execute("SELECT prompt_tokens,output_tokens,usd_actual FROM token_usage WHERE entry_id='historic'").fetchone() == (7,9,None)
        conn.close()
    finally:
        configure_default_db_path(None)
