"""Live endpoint smoke test: every new v2 route responds correctly."""
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ["NOVA_DATA_DIR"] = tmp
os.environ["NOVA_ADMIN_TOKEN"] = "t"
sys.path.insert(0, "/root/novarouter")

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

with TestClient(app) as c:
    H = {"X-Admin-Token": "t"}
    c.post("/admin/api/providers", headers=H,
           json={"name": "p", "base_url": "https://example.com/v1"})
    r = c.post("/admin/api/client-keys", headers=H, json={"name": "t"})
    tok = r.json()["token"]
    A = {"Authorization": "Bearer " + tok}

    from app import db  # noqa: E402
    db.execute("INSERT INTO models (provider_id, model_id, exposed_id) VALUES (1,'m1','m1')")

    checks = [
        ("GET", "/v1/models", None, 200),
        ("POST", "/v1/chat/completions", {"model": "m1", "messages": []}, 502),
        ("POST", "/v1/responses", {"model": "m1", "input": "hi"}, 502),
        ("POST", "/v1/messages", {"model": "m1", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 10}, 502),
        ("POST", "/v1/images/generations", {"model": "m1", "prompt": "cat"}, 502),
        ("POST", "/v1/videos", {"model": "m1", "prompt": "cat"}, 502),
        ("POST", "/v1/audio/speech", {"model": "m1", "input": "hi"}, 502),
        ("POST", "/v1/moderations", {"model": "m1", "input": "hi"}, 502),
        ("GET", "/v1/videos/xxx", None, 404),
    ]
    ok = 0
    for method, path, body, expect in checks:
        if method == "GET":
            resp = c.get(path, headers=A)
        else:
            resp = c.post(path, headers=A, json=body)
        status = "PASS" if resp.status_code == expect else "FAIL"
        if resp.status_code == expect:
            ok += 1
        print(f"{status} {method} {path} -> {resp.status_code} (expected {expect})")
        if resp.status_code != expect:
            print("   body:", resp.text[:200])
    print(f"\n{ok}/{len(checks)} endpoint checks passed")