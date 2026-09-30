"""No-network regressions for fragment fan-out and lightweight admin reads."""
import asyncio
import json
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nova.models import Base, Model, Provider, ProviderKey, ProviderSession, RequestLog, StorageFile, utcnow
from routers import admin_misc, admin_models, admin_providers, admin_storage
from ui.tabs import models, overview, providers


def request(path="/"):
    return Request({"type": "http", "method": "GET", "path": path, "query_string": b"", "headers": []})


class FragmentFanoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_overview_optional_reads_overlap_and_fail_independently(self):
        started = set()
        release = asyncio.Event()

        async def get(path, **kwargs):
            if path == "/api/admin/stats":
                return {"total_requests": 0}
            started.add(path)
            await release.wait()
            if path == "/api/admin/meta":
                raise RuntimeError("meta unavailable")
            return []

        with patch.object(overview.api, "get", side_effect=get), patch.object(
            overview, "render", side_effect=lambda _name, **ctx: str(ctx)
        ):
            task = asyncio.create_task(overview.overview_tab())
            try:
                await asyncio.wait_for(self._until(lambda: len(started) == 3), 0.5)
            finally:
                release.set()
            response = await task
        self.assertIn("providers", response.body.decode())
        self.assertIn("'meta': None", response.body.decode())

    async def test_models_secondary_reads_overlap_and_degrade(self):
        started = set()
        release = asyncio.Event()

        async def get(path, **kwargs):
            if path == "/api/admin/models":
                return {"rows": [], "total": 0}
            started.add(path)
            await release.wait()
            if path == "/api/admin/providers":
                raise RuntimeError("unavailable")
            return {"active_models": 2}

        with patch.object(models.api, "get", side_effect=get):
            task = asyncio.create_task(models._models_ctx(request()))
            try:
                await asyncio.wait_for(self._until(lambda: len(started) == 2), 0.5)
            finally:
                release.set()
            ctx = await task
        self.assertEqual(ctx["providers"], [])
        self.assertEqual(ctx["stats"]["active_models"], 2)

    async def test_provider_presets_are_not_serialized_after_other_reads(self):
        started = set()
        release = asyncio.Event()

        async def get(path, **kwargs):
            started.add(path)
            await release.wait()
            if path.endswith("/presets"):
                raise RuntimeError("presets unavailable")
            return [] if path.endswith("/providers") else {}

        with patch.object(providers.api, "get", side_effect=get):
            task = asyncio.create_task(providers._providers_tab_ctx(request()))
            try:
                await asyncio.wait_for(self._until(lambda: "/api/admin/providers/presets" in started
                                                  and "/api/admin/providers" in started), 0.5)
            finally:
                release.set()
            ctx = await task
        self.assertEqual(ctx["presets"], [])
        self.assertEqual(ctx["rows"], [])

    async def _until(self, predicate):
        while not predicate():
            await asyncio.sleep(0.001)


class AdminReadTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool,
                                    connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_provider_list_counts_without_loading_model_or_key_rows(self):
        with self.session() as db:
            p = Provider(key="p", name="Provider", priority=10, baseUrl="https://example.com")
            empty = Provider(key="empty", name="Empty", priority=20, baseUrl="https://example.com")
            db.add_all([p, empty])
            db.flush()
            db.add(ProviderKey(providerId=p.id, apiKey="secret", enabled=False))
            db.add_all([ProviderSession(providerKey="p", username="older", connectedAt=utcnow()),
                        ProviderSession(providerKey="p", username="newer", connectedAt=utcnow())])
            db.add_all(Model(providerId=p.id, modelId=str(i), exposedId=str(i), status=s)
                       for i, s in enumerate(("healthy", "cooling", "dead", "healthy")))
            db.commit()
            statements = []
            def record(_conn, _cursor, statement, _params, _context, _many):
                statements.append(statement)
            event.listen(self.engine, "before_cursor_execute", record)
            try:
                data = json.loads(admin_providers.list_providers(db).body)
            finally:
                event.remove(self.engine, "before_cursor_execute", record)
        self.assertEqual([(r["key_count"], r["model_count"], r["ok_count"], r["cooling_count"])
                          for r in data], [(1, 4, 2, 1), (0, 0, 0, 0)])
        self.assertEqual(data[0]["session"]["username"], "newer")
        self.assertIsNone(data[1]["session"])
        self.assertFalse(any('"apiKey"' in s or '"capabilities"' in s for s in statements))

    def test_stats_recent_aggregation_keeps_counts_and_bounded_window(self):
        with self.session() as db:
            now = utcnow()
            db.add_all([
                RequestLog(ts=now, model="x", status=200, latencyMs=10, via="cache", tokensIn=2, tokensOut=3),
                RequestLog(ts=now, model="x", status=429, latencyMs=30, via="upstream", tokensIn=4, tokensOut=5),
                RequestLog(ts=now - timedelta(days=2), model="x", status=500, latencyMs=100, via="upstream",
                           tokensIn=6, tokensOut=7),
            ])
            db.commit()
            with patch.object(admin_misc, "_rss_kb", return_value=0), patch.object(
                admin_misc, "_vm_peak_kb", return_value=0
            ):
                result = admin_misc.stats(db)
        self.assertEqual(result["total_requests"], 3)
        self.assertEqual((result["total_tokens_in"], result["total_tokens_out"]), (12, 15))
        self.assertEqual((result["avg_latency_ms"], result["cache_hit_rate"], result["error_rate"]),
                         (20, 50, 50))

    def test_storage_listing_does_not_fetch_base64_payload(self):
        with self.session() as db:
            db.add(StorageFile(name="large.txt", size=10_000_000, data="Z" * 10000,
                               providerKey="local_disk"))
            db.commit()
            statements = []

            def record(_conn, _cursor, statement, _params, _context, _many):
                statements.append(statement)

            event.listen(self.engine, "before_cursor_execute", record)
            try:
                info = json.loads(admin_storage.storage_info(db).body)
                files = json.loads(admin_storage.list_files(db).body)
            finally:
                event.remove(self.engine, "before_cursor_execute", record)
        self.assertEqual(info["files"], files)
        self.assertEqual(files[0]["name"], "large.txt")
        self.assertFalse(any('"StorageFile"."data"' in s for s in statements))

    def test_single_model_read_preserves_detail_payload_and_404(self):
        with self.session() as db:
            p = Provider(key="p", name="Provider", color="#abc", baseUrl="https://example.com")
            db.add(p)
            db.flush()
            db.add(Model(providerId=p.id, modelId="upstream", exposedId="p/model",
                         capabilities='{"vision":true}', status="healthy"))
            db.commit()
            model = json.loads(admin_models.get_model("1", db).body)
            missing = admin_models.get_model("999", db)
        self.assertEqual((model["exposed_id"], model["provider_name"], model["capabilities"]["vision"]),
                         ("p/model", "Provider", True))
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
