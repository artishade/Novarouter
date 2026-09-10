"""Extensions E2E test: skill add -> catalogue -> direct call -> auto-activation check."""
import json
import os
import sys
import tempfile

os.environ["NOVA_DATA_DIR"] = tempfile.mkdtemp(prefix="nova-ext-")
os.environ["NOVA_ADMIN_TOKEN"] = "t"
sys.path.insert(0, "/root/novarouter")

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

c = TestClient(app)
with c:
    H = {"X-Admin-Token": "t"}

    # 1. create a skill extension
    r = c.post("/admin/api/extensions", headers=H, json={
        "kind": "skill", "name": "trading-analyst",
        "description": "Analyze trading questions",
        "config": {
            "instructions": "You are a senior trading analyst. Always mention risk management.",
            "description": "Analyze a trading/market question with senior-analyst rigor",
            "keywords": "market, trade, crypto",
        },
    })
    print("create skill:", r.status_code, r.json())
    assert r.status_code == 200, r.text
    eid = r.json()["id"]

    # 2. list extensions shows the auto-registered tool
    r = c.get("/admin/api/extensions", headers=H)
    assert r.status_code == 200
    rows = r.json()
    assert rows[0]["tools"] and rows[0]["tools"][0]["tool_name"] == "trading-analyst"
    print("list: tool registered ->", rows[0]["tools"][0]["tool_name"])

    # 3. create gateway key
    r = c.post("/admin/api/client-keys", headers=H, json={"name": "ext-test"})
    token = r.json()["token"]

    # 4. GET /v1/tools discovery
    r = c.get("/v1/tools", headers={"Authorization": "Bearer " + token})
    assert r.status_code == 200
    body = r.json()
    assert body["auto_active"] is True
    assert body["tools"][0]["name"] == "trading-analyst"
    print("tools catalogue:", body["tools"][0]["name"], "| auto_active:", body["auto_active"])

    # 5. direct tool call
    r = c.post("/v1/tools/trading-analyst", headers={"Authorization": "Bearer " + token},
               json={"input": "should I buy BTC now?"})
    assert r.status_code == 200
    out = r.json()
    assert out["ok"] is True
    assert "risk management" in out["output"]
    print("direct call ok ->", out["output"][:60].replace("\n", " "))

    # 6. inject_tools merge check (what the model would receive)
    from app import extensions  # noqa: E402
    payload = {"model": "x", "messages": [{"role": "user", "content": "hi"}]}
    payload, added = extensions.inject_tools(payload)
    assert added == {"trading-analyst"}, added
    assert payload["tools"][0]["function"]["name"] == "trading-analyst"
    print("inject_tools ->", added)

    # 7. auth still enforced on /v1/tools
    r = c.get("/v1/tools")
    assert r.status_code == 401
    print("unauth /v1/tools -> 401 OK")

    # 8. cleanup
    c.delete(f"/admin/api/extensions/{eid}", headers=H)
    r = c.get("/admin/api/extensions", headers=H)
    assert r.json() == []
    print("cleanup OK")

print("\nALL EXTENSION E2E TESTS PASSED")