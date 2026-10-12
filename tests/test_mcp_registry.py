"""Custom MCP servers (console plugins) — registry + agent wiring.

Run: python -m unittest discover -s tests -p test_mcp_registry.py

The registry is where user data meets the transport, so these tests pin the
parts that would be expensive to get wrong: validation and slugs on save, the
"masked header keeps the stored secret" rule, the cached tool catalogue the
planner reads, how an ambiguous `tool::name` is reported, and one real agent
tool call into the stdio fixture.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nova import agent, mcp_registry
from nova.mcp_client import McpError
from nova.models import McpServer

FIXTURE = Path(__file__).resolve().parent / "mcp_stdio_server.py"
STDOIO_COMMAND = f"{sys.executable} {FIXTURE}"


class RegistryTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool,
                                    connect_args={"check_same_thread": False})
        McpServer.__table__.create(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)

    def tearDown(self):
        self.engine.dispose()

    def add(self, **overrides) -> McpServer:
        payload = {
            "name": "Fixture",
            "transport": "stdio",
            "command": STDOIO_COMMAND,
            "enabled": True,
        }
        payload.update(overrides)
        with self.session() as db:
            return mcp_registry.upsert(db, payload)


class ValidationTests(RegistryTestCase):
    def test_name_becomes_the_slug(self):
        row = self.add(name="Deep Wiki Server")
        self.assertEqual(row.id, "deep-wiki-server")
        self.assertEqual(row.name, "Deep Wiki Server")

    def test_an_explicit_id_wins(self):
        row = self.add(name="Wiki", id="deepwiki")
        self.assertEqual(row.id, "deepwiki")

    def test_http_needs_a_url(self):
        with self.assertRaises(ValueError):
            self.add(name="Wiki", transport="http", url="")

    def test_http_url_must_be_absolute(self):
        with self.assertRaises(ValueError):
            self.add(name="Wiki", transport="http", url="mcp.example.com")

    def test_stdio_needs_a_command(self):
        with self.assertRaises(ValueError):
            self.add(name="Wiki", transport="stdio", command="")

    def test_unknown_transport_is_rejected(self):
        with self.assertRaises(ValueError):
            self.add(name="Wiki", transport="carrier-pigeon")


class SecretHandlingTests(RegistryTestCase):
    def test_a_masked_resave_keeps_the_stored_secret(self):
        self.add(name="Wiki", transport="http", url="https://mcp.test/mcp",
                 headers={"Authorization": "Bearer real-token"})
        # What the browser got back is masked; saving it unchanged must not
        # overwrite the credential with bullets.
        with self.session() as db:
            row = mcp_registry.upsert(db, {
                "name": "Wiki", "transport": "http", "url": "https://mcp.test/mcp",
                "headers": {"Authorization": "Bearer ••••"},
            })
            self.assertIn("real-token", row.headers)
            self.assertEqual(mcp_registry.serialize(row)["headers"]["Authorization"], "Bearer ••••")

    def test_a_new_header_value_is_stored(self):
        self.add(name="Wiki", transport="http", url="https://mcp.test/mcp",
                 headers={"Authorization": "Bearer old"})
        with self.session() as db:
            row = mcp_registry.upsert(db, {
                "name": "Wiki", "transport": "http", "url": "https://mcp.test/mcp",
                "headers": {"Authorization": "Bearer fresh"},
            })
            self.assertIn("fresh", row.headers)

    def test_serialize_never_leaks(self):
        self.add(name="Wiki", transport="http", url="https://mcp.test/mcp",
                 headers={"Authorization": "Bearer top-secret", "X-Trace": "t-1"})
        with self.session() as db:
            payload = mcp_registry.serialize(mcp_registry.get_row(db, "wiki"))
        self.assertNotIn("top-secret", str(payload))
        self.assertEqual(payload["headers"]["X-Trace"], "t-1")
        self.assertTrue(payload["has_credentials"])


class CatalogueTests(RegistryTestCase, unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        from nova import mcp_client

        await mcp_client.close_all()

    async def test_discovered_tools_reach_the_planner_prompt(self):
        self.add(name="Fixture", id="fixture")
        real_local = agent.SessionLocal
        agent.SessionLocal = self.session
        try:
            with self.session() as db:
                row = mcp_registry.get_row(db, "fixture")
                result = await mcp_registry.refresh(db, row)
                self.assertTrue(result["ok"], result["error"])
                self.assertEqual(row.status, "connected")
                catalogue = mcp_registry.tool_catalogue(db)
                self.assertEqual([t["qualified"] for t in catalogue],
                                 ["fixture::echo", "fixture::add"])
                # The argument schema has to survive the snapshot — the
                # Playground's "run this tool" form is generated from it.
                self.assertIn("message", catalogue[0]["schema"].get("properties", {}))
            prompt = agent._mcp_catalogue_prompt()
        finally:
            agent.SessionLocal = real_local
        self.assertIn("fixture::echo", prompt)
        self.assertIn("Add two numbers", prompt)

    async def test_a_broken_server_reports_its_error_and_offers_nothing(self):
        self.add(name="Broken", id="broken", command="definitely-not-a-real-binary-xyz")
        with self.session() as db:
            row = mcp_registry.get_row(db, "broken")
            result = await mcp_registry.refresh(db, row)
            self.assertFalse(result["ok"])
            self.assertEqual(row.status, "error")
            self.assertIn("definitely-not-a-real-binary-xyz", row.lastError)
            self.assertEqual(mcp_registry.tool_catalogue(db), [])

    def test_disabled_servers_are_not_offered(self):
        self.add(name="Fixture", id="fixture")
        with self.session() as db:
            mcp_registry.set_enabled(db, "fixture", False)
            self.assertEqual(mcp_registry.tool_catalogue(db), [])
            self.assertEqual(mcp_registry.catalogue_summary(db)["enabled"], 0)


class DispatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool,
                                    connect_args={"check_same_thread": False})
        McpServer.__table__.create(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.session() as db:
            mcp_registry.upsert(db, {"id": "fixture", "name": "Fixture",
                                     "transport": "stdio", "command": STDOIO_COMMAND})

    def tearDown(self):
        self.engine.dispose()

    async def test_an_ambiguous_tool_name_asks_for_qualification(self):
        with self.session() as db:
            mcp_registry.upsert(db, {"id": "second", "name": "Second",
                                     "transport": "stdio", "command": STDOIO_COMMAND})
            for row in mcp_registry.rows(db):
                row.tools = '[{"name": "echo", "description": ""}]'
            db.commit()
            with self.assertRaises(McpError) as err:
                await mcp_registry.invoke(db, "", "echo", {"message": "x"})
        self.assertIn("fixture::echo", str(err.exception))

    async def test_an_unknown_server_lists_what_is_registered(self):
        with self.session() as db:
            with self.assertRaises(McpError) as err:
                await mcp_registry.invoke(db, "nope", "echo", {})
        self.assertIn("fixture", str(err.exception))


class AgentToolTests(unittest.IsolatedAsyncioTestCase):
    """The agent's own `mcp_call` action, end to end against a real plugin."""

    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool,
                                    connect_args={"check_same_thread": False})
        McpServer.__table__.create(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.session() as db:
            mcp_registry.upsert(db, {"id": "fixture", "name": "Fixture",
                                     "transport": "stdio", "command": STDOIO_COMMAND})
        # The tool resolves its server through the registry's own session.
        self._real_local = agent.SessionLocal
        agent.SessionLocal = self.session

    def tearDown(self):
        agent.SessionLocal = self._real_local
        self.engine.dispose()

    async def asyncTearDown(self):
        from nova import mcp_client

        await mcp_client.close_all()

    async def test_the_agent_can_call_a_plugin(self):
        out = await agent.tool_mcp_call('fixture::echo {"message":"nova"}')
        self.assertIn("mcp fixture::echo", out)
        self.assertIn("nova", out)

    async def test_a_bad_call_is_reported_not_raised(self):
        out = await agent.tool_mcp_call("fixture::missing")
        self.assertIn("failed", out)
        out = await agent.tool_mcp_call("not-a-call")
        self.assertIn("server::tool", out)

    async def test_execute_tool_dispatches_the_new_action(self):
        from nova.agent import AgentPlan

        detail = await agent.execute_tool(
            AgentPlan(action="mcp_call", title="echo", query='fixture::echo {"message":"hi"}', reason="")
        )
        self.assertIn("hi", detail)


if __name__ == "__main__":
    unittest.main()