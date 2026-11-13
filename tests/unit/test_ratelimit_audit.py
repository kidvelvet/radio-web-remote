import json

from radio_web_remote.security.audit import AuditLog
from radio_web_remote.security.ratelimit import RateLimiter


def test_rate_limiter_window():
    now = [0.0]
    rl = RateLimiter(3, 10.0, clock=lambda: now[0])
    assert all(rl.allow("a") for _ in range(3))
    assert not rl.allow("a") and rl.allow("b")
    assert rl.retry_after("a") > 0
    now[0] = 11.0
    assert rl.allow("a")


def test_audit_writes_json_lines_without_secrets(tmp_path):
    log = AuditLog(tmp_path / "audit.log")
    log({"event": "login", "user": "alice", "password": "hunter2hunter2", "csrf": "x"})
    log({"event": "tx_stop", "callsign": "N0CALL", "duration_s": 1.5})
    log.close()
    lines = [json.loads(line) for line in (tmp_path / "audit.log").read_text().splitlines()]
    assert lines[0]["event"] == "login" and "password" not in lines[0] and "csrf" not in lines[0]
    assert lines[1]["duration_s"] == 1.5 and "ts" in lines[1]
