"""The agent must still plan when the NovaFree engine sidecar is off.

Run: python -m unittest discover -s tests -p test_agent_llm_fallback.py

Handing a task to the console used to fail before a single tool ran: the agent's
planner and its final summary had exactly one brain — the builtin engine
sidecar — and that sidecar is legitimately unavailable in many deployments
(`NOVA_ENGINE_DISABLED`, no bun/node runtime, no reachable free catalogue). The
task then died with "Agent planner returned unparseable output twice" while the
chat box beside it answered through the configured providers.

`agent_llm` keeps the sidecar first (free, keyless) and falls back to the
server's own /v1/chat/completions, and because the gateway routes by *model id*
the fallback has to resolve `auto` into a concrete model. These tests pin both
halves: which model is chosen, and that the fallback chain is actually used.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nova import agent
from nova.models import Model, Provider


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def completion(content="hi"):
    return {"choices": [{"message": {"content": content}}]}


class DefaultModelTests(unittest.TestCase):
    """`_default_model_id` is what makes the `auto` model routable at all."""

    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool,
                                    connect_args={"check_same_thread": False})
        Provider.__table__.create(self.engine)
        Model.__table__.create(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.session() as db:
            live = Provider(key="live", name="Live", kind="openai", baseUrl="https://live.test")
            off = Provider(key="off", name="Off", kind="openai", baseUrl="https://off.test",
                           enabled=False)
            db.add_all([live, off])
            db.flush()
            db.add_all([
                # A dead free model and a disabled provider's faster free model
                # must both lose to a healthy, reachable, free one.
                Model(providerId=live.id, modelId="m1", exposedId="free-dead", isFree=True,
                      status="dead", enabled=True, latencyMs=1),
                Model(providerId=live.id, modelId="m2", exposedId="free-healthy", isFree=True,
                      status="healthy", enabled=True, latencyMs=900),
                Model(providerId=live.id, modelId="m3", exposedId="paid-healthy", isFree=False,
                      status="healthy", enabled=True, latencyMs=10),
                Model(providerId=live.id, modelId="m4", exposedId="disabled-model", isFree=True,
                      status="healthy", enabled=False, latencyMs=1),
                Model(providerId=off.id, modelId="m5", exposedId="free-off-provider", isFree=True,
                      status="healthy", enabled=True, latencyMs=1),
            ])
            db.commit()

    def tearDown(self):
        self.engine.dispose()

    def test_picks_a_healthy_free_model_on_an_enabled_provider(self):
        with patch.object(agent, "SessionLocal", self.session):
            self.assertEqual(agent._default_model_id(), "free-healthy")

    def test_falls_back_to_a_paid_model_when_nothing_free_is_healthy(self):
        with self.session() as db:
            db.query(Model).filter(Model.exposedId == "free-healthy").delete()
            db.commit()
        with patch.object(agent, "SessionLocal", self.session):
            self.assertEqual(agent._default_model_id(), "paid-healthy")

    def test_no_usable_model_is_none_not_a_crash(self):
        with self.session() as db:
            db.query(Model).delete()
            db.commit()
        with patch.object(agent, "SessionLocal", self.session):
            self.assertIsNone(agent._default_model_id())


class FakeAsyncClient:
    """Records every gateway POST and replays a scripted answer per model id."""

    calls: list = []
    answers: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None):
        model = (json or {}).get("model")
        self.calls.append((url, json))
        return self.answers.get(model, FakeResponse(502, {"error": "no route"}))


class GatewayFallbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeAsyncClient.calls = []
        FakeAsyncClient.answers = {}

    async def test_engine_first_when_it_answers(self):
        async def engine_chat(messages, **kwargs):
            return completion("from the sidecar")

        with patch.object(agent.nova_engine, "chat", engine_chat), \
             patch.object(agent.httpx, "AsyncClient", FakeAsyncClient):
            result = await agent.agent_llm([{"role": "user", "content": "hi"}])
        self.assertEqual(agent._completion_content(result), "from the sidecar")
        self.assertEqual(FakeAsyncClient.calls, [], "a working sidecar costs no gateway call")

    async def test_engine_out_falls_back_to_the_gateway(self):
        async def engine_chat(messages, **kwargs):
            raise agent.nova_engine.EngineUnavailable("engine sidecar unreachable")

        FakeAsyncClient.answers = {"free-healthy": FakeResponse(200, completion("planned"))}

        def fake_default():
            return "free-healthy"

        with patch.object(agent.nova_engine, "chat", engine_chat), \
             patch.object(agent.httpx, "AsyncClient", FakeAsyncClient), \
             patch.object(agent, "_default_model_id", fake_default):
            result = await agent.agent_llm([{"role": "user", "content": "hi"}], "auto")
        self.assertEqual(agent._completion_content(result), "planned")
        url, payload = FakeAsyncClient.calls[0]
        self.assertTrue(url.endswith("/v1/chat/completions"))
        self.assertEqual(payload["model"], "free-healthy",
                         "`auto` is not routable, so a real model must be chosen")

    async def test_the_requested_model_is_tried_before_the_default(self):
        def fake_default():
            return "free-healthy"

        FakeAsyncClient.answers = {"free-healthy": FakeResponse(200, completion("default"))}
        with patch.object(agent.httpx, "AsyncClient", FakeAsyncClient), \
             patch.object(agent, "_default_model_id", fake_default):
            result = await agent._gateway_chat([{"role": "user", "content": "hi"}], "chosen-model")
        self.assertEqual(agent._completion_content(result), "default")
        self.assertEqual([payload["model"] for _url, payload in FakeAsyncClient.calls],
                         ["chosen-model", "free-healthy"])

    async def test_a_dead_gateway_reports_the_real_failure(self):
        async def engine_chat(messages, **kwargs):
            raise agent.nova_engine.EngineUnavailable("engine sidecar unreachable")

        def fake_default():
            return None

        with patch.object(agent.nova_engine, "chat", engine_chat), \
             patch.object(agent.httpx, "AsyncClient", FakeAsyncClient), \
             patch.object(agent, "_default_model_id", fake_default):
            with self.assertRaises(RuntimeError) as err:
                await agent.agent_llm([{"role": "user", "content": "hi"}])
        self.assertIn("no enabled model", str(err.exception))


if __name__ == "__main__":
    unittest.main()