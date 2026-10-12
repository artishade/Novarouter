"""No-network regression tests for dashboard and scheduled health sweeps.

Run: python -m unittest discover -s tests -p test_health_sweeps.py
"""
import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cron_health_check as cron
from nova.models import Model, Provider
from ui.tabs import models as ui_models


class HealthCandidatesTests(unittest.IsolatedAsyncioTestCase):
    async def test_click_targets_all_enabled_models_across_pages_and_filters(self):
        rows = [{"id": n, "enabled": n != 275, "status": "dead" if n == 450 else "unknown"}
                for n in range(1, 451)]
        offsets = []

        async def get(path, *, params):
            self.assertEqual(path, "/api/admin/models")
            self.assertEqual(set(params), {"limit", "offset"})  # not current UI filters
            offsets.append(params["offset"])
            return {"rows": rows[params["offset"]:params["offset"] + params["limit"]],
                    "total": len(rows)}

        with patch.object(ui_models.api, "get", side_effect=get):
            response = await ui_models.health_candidates()
        data = json.loads(response.body)
        self.assertEqual(offsets, [0, 200, 400])
        self.assertEqual(data["total"], 449)
        self.assertEqual(len(data["ids"]), 449)
        self.assertNotIn(275, data["ids"])
        self.assertIn(450, data["ids"])  # enabled dead rows may recover

    async def test_failed_page_is_not_silently_treated_as_complete(self):
        async def get(path, *, params):
            return {"rows": [{"id": n, "enabled": True} for n in range(200)]
                    if params["offset"] == 0 else [], "total": 201}

        with patch.object(ui_models.api, "get", side_effect=get):
            response = await ui_models.health_candidates()
        self.assertEqual(response.status_code, 502)
        self.assertIn("retry", json.loads(response.body)["error"])


class ScheduledHealthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool,
                                    connect_args={"check_same_thread": False})
        Provider.__table__.create(self.engine)
        Model.__table__.create(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.session() as db:
            provider = Provider(key="example", name="Example", kind="openai", baseUrl="https://example.test")
            db.add(provider)
            db.flush()
            db.add_all(Model(providerId=provider.id, modelId=str(n), exposedId=str(n),
                             enabled=n != 33, status="dead" if n in (1, 33) else "unknown")
                       for n in range(1, 34))
            db.commit()

    def tearDown(self):
        self.engine.dispose()

    async def test_checks_every_enabled_model_with_bounded_concurrency_and_fresh_disable(self):
        checked = []
        active = peak = 0

        async def probe(model):
            nonlocal active, peak
            checked.append(model.id)
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.001)
            active -= 1
            if model.id == 3:
                raise RuntimeError("transient failure")
            status = "dead" if model.id == 2 else "healthy"
            with self.session() as db:
                row = db.get(Model, model.id)
                row.status = status
                db.commit()
            return {"ok": status == "healthy", "status": status, "http_status": 200}

        with patch.object(cron, "SessionLocal", self.session), patch.object(cron, "ping_model", probe):
            summary = await cron.run_auto_health_check()
        self.assertEqual(len(checked), 32)
        self.assertEqual(len(set(checked)), 32)
        self.assertEqual(peak, cron.MAX_CONCURRENT_PROBES)
        self.assertEqual(summary["total"], 32)
        self.assertEqual(summary["healthy"], 30)
        self.assertEqual(summary["dead"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["disabled"], 1)
        with self.session() as db:
            states = {row.id: row.enabled for row in db.scalars(select(Model)).all()}
        self.assertTrue(states[1])  # stale dead row recovered on this run
        self.assertFalse(states[2])
        self.assertTrue(states[3])  # error must not disable a model
        self.assertFalse(states[33])  # disabled rows remain untouched

    async def test_scheduled_probe_uses_admin_probe_response(self):
        async def admin_probe(model_id):
            self.assertEqual(model_id, "1")
            return JSONResponse({"ok": True, "status": "healthy", "latency_ms": 42})

        with patch.object(cron, "probe_model", admin_probe):
            result = await cron.ping_model(Model(id=1))
        self.assertEqual(result["latency_ms"], 42)


if __name__ == "__main__":
    unittest.main()
