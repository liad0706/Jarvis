from types import SimpleNamespace

from fastapi.testclient import TestClient

from dashboard import server


class FakeSmartHomeSkill:
    async def execute(self, action, params=None):
        if action == "list_devices":
            return {
                "devices": [
                    {
                        "entity_id": "light.office",
                        "name": "Office Light",
                        "state": "on",
                        "type": "light",
                        "brightness": 128,
                    }
                ]
            }
        if action == "toggle":
            return {"status": "ok"}
        if action == "set_brightness":
            return {"status": "ok"}
        raise AssertionError(f"Unexpected action: {action}")


class FakeDocumentRagSkill:
    async def execute(self, action, params=None):
        params = params or {}
        if action == "list_documents":
            return {
                "status": "ok",
                "count": 1,
                "documents": [
                    {
                        "id": 3,
                        "file_path": "C:/docs/math.pdf",
                        "file_name": "math.pdf",
                        "ingested_at": "2026-03-30T12:00:00",
                        "chunk_count": 6,
                    }
                ],
            }
        if action == "ingest_file":
            return {"status": "ok", "message": f"Ingested {params['file_path']}"}
        if action == "ask":
            return {
                "status": "ok",
                "result_count": 1,
                "results": [
                    {
                        "file_name": "math.pdf",
                        "file_path": "C:/docs/math.pdf",
                        "chunk_index": 2,
                        "score": 0.9,
                        "matched_keywords": 3,
                        "content": "Derivative rules summary",
                    }
                ],
            }
        if action == "remove_document":
            return {"status": "ok", "message": f"Removed {params['file_path']}"}
        raise AssertionError(f"Unexpected action: {action}")


class FakeRestartSkill:
    def __init__(self):
        self.calls = []

    async def execute(self, action, params=None):
        assert action == "restart"
        params = params or {}
        self.calls.append(params)
        return {"status": "restarting", "message": params.get("reason", "")}


class FakeRegistry:
    def __init__(self):
        self.restart = FakeRestartSkill()
        self._skills = {
            "smart_home": FakeSmartHomeSkill(),
            "document_rag": FakeDocumentRagSkill(),
            "restart": self.restart,
        }

    def get(self, name):
        return self._skills.get(name)


class FakeMemoryManager:
    def __init__(self):
        self.memory = SimpleNamespace(
            remove_fact=self._remove_fact,
            delete_episodic_memory=self._delete_episode,
        )

    async def get_all_facts(self):
        return {"favorite_color": "blue"}

    async def get_episodes(self, limit=50):
        return [
            {
                "id": 7,
                "memory_type": "manual",
                "content": "Jarvis shipped streaming",
                "metadata": {"topic": "dashboard"},
                "created_at": 1710000000,
                "recalled_count": 2,
            }
        ][:limit]

    async def get_relevant_history(self, q, top_k=10):
        return f"Relevant: {q}"

    async def _remove_fact(self, key):
        return key == "favorite_color"

    async def _delete_episode(self, memory_id):
        return memory_id == 7


class FakePatternLearner:
    def get_patterns(self, min_confidence=0.0):
        return [{"name": "Morning focus", "confidence": 0.8, "occurrences": 4}]


class FakeFeedbackLoop:
    def get_all(self, limit=100):
        return [{"action_type": "proactive_suggestion", "action_detail": "Turn off lights", "reaction": "positive", "timestamp": "2026-03-30T10:00:00"}]


class FakeCalendar:
    def get_all_events(self):
        return [{"id": "evt_1", "title": "Math", "date": "2026-03-30", "time": "09:00", "category": "school"}]

    def get_week(self, start_date=None):
        return self.get_all_events()

    def add_event(self, **kwargs):
        return {"id": "evt_2", **kwargs}

    def remove_event(self, event_id):
        return event_id == "evt_1"


class FakeAwareness:
    def __init__(self):
        self.pattern_learner = FakePatternLearner()
        self.feedback_loop = FakeFeedbackLoop()
        self.calendar = FakeCalendar()


class FakeMetrics:
    async def get_summary(self):
        return {"counters": {"llm_calls_total": 3}, "histograms": {}}


class FakeNotifications:
    def get_history(self, unread_only=False, limit=50):
        return [{"id": "n1", "title": "Alert", "message": "Test", "timestamp": 1710000000, "level": "info", "read": False}]

    def unread_count(self):
        return 1

    def mark_read(self, notification_id):
        return True

    def mark_all_read(self):
        return None


class FakeAutomationEngine:
    def list_automations(self):
        return [{"name": "Lights Home", "trigger_event": "presence.changed", "actions": []}]

    def add_automation(self, automation):
        self.last_added = automation

    def remove_automation(self, name):
        return name == "Lights Home"


class FakeSkillStore:
    def __init__(self):
        self.skills = [{
            "name": "dynamic_weather",
            "version": "1.0.0",
            "author": "Jarvis",
            "description": "Weather helper",
            "enabled": True,
            "is_dynamic": True,
            "dependencies": [],
        }]

    def get_all(self):
        return list(self.skills)

    def enable_skill(self, name):
        return True

    def disable_skill(self, name):
        return True

    def dependents_of(self, name):
        return []

    def export_skill_archive(self, name, output_dir):
        return f"{output_dir}/{name}.zip"

    def import_skill_archive(self, archive_path):
        return archive_path.endswith(".zip")


class FakeModelRouter:
    def __init__(self):
        self.routes = {
            "general": {"preferred_provider": "ollama", "preferred_model": "qwen2.5:1.5b", "description": "General conversation"},
        }

    def get_all_routes(self):
        return dict(self.routes)

    def set_route(self, task_type, provider, model=None):
        self.routes[task_type] = {"preferred_provider": provider, "preferred_model": model, "description": task_type}


class FakeProvider:
    name = "ollama"
    model = "qwen2.5:7b"


class FakeOrchestrator:
    def __init__(self):
        self.provider = FakeProvider()
        self.conversation = [{"role": "user", "content": "Hi"}]
        self._circuit = SimpleNamespace(_state="closed", _failure_count=0)


def setup_module():
    server._session_store = None
    server._chat_sessions.clear()
    server._pending_progress_by_session.clear()
    awareness = FakeAwareness()
    server.bridge_components(
        orchestrator=FakeOrchestrator(),
        memory_manager=FakeMemoryManager(),
        awareness=awareness,
        metrics=FakeMetrics(),
        notifications=FakeNotifications(),
        automation_engine=FakeAutomationEngine(),
        registry=FakeRegistry(),
        skill_store=FakeSkillStore(),
        model_router=FakeModelRouter(),
    )


def test_dashboard_pages_and_health_routes():
    client = TestClient(server.app)

    for path in (
        "/pages/calendar.html",
        "/pages/documents.html",
        "/pages/health.html",
        "/pages/notifications.html",
        "/pages/automations.html",
        "/pages/skills.html",
        "/pages/branches.html",
    ):
        response = client.get(path)
        assert response.status_code == 200

    health = client.get("/api/health")
    assert health.status_code == 200
    payload = health.json()
    assert payload["llm"]["provider"] == "ollama"
    assert payload["devices"]["count"] == 1
    assert payload["memory"]["facts_count"] == 1
    assert payload["notifications"]["unread_count"] == 1


def test_dashboard_api_routes_cover_devices_memory_skills_and_models():
    client = TestClient(server.app)

    devices = client.get("/api/devices")
    assert devices.status_code == 200
    assert devices.json()["devices"][0]["attributes"]["brightness"] == 50

    episodes = client.get("/api/memory/episodes")
    assert episodes.status_code == 200
    assert episodes.json()["episodes"][0]["id"] == 7

    documents = client.get("/api/documents")
    assert documents.status_code == 200
    assert documents.json()["documents"][0]["chunk_count"] == 6

    doc_query = client.post("/api/documents/query", json={"question": "derivative rules"})
    assert doc_query.status_code == 200
    assert doc_query.json()["results"][0]["file_name"] == "math.pdf"

    delete_episode = client.delete("/api/memory/episodes/7")
    assert delete_episode.status_code == 200

    skills = client.get("/api/skills")
    assert skills.status_code == 200
    assert skills.json()["skills"][0]["name"] == "dynamic_weather"

    export_result = client.post("/api/skills/dynamic_weather/export", json={"output_dir": "data/exports"})
    assert export_result.status_code == 200
    assert export_result.json()["archive_path"].endswith("dynamic_weather.zip")

    routes = client.get("/api/model-routes")
    assert routes.status_code == 200
    assert routes.json()["routes"]["general"]["preferred_provider"] == "ollama"

    updated = client.post("/api/model-routes/general", json={"provider": "codex", "model": "gpt-5.4"})
    assert updated.status_code == 200
    assert updated.json()["routes"]["general"]["preferred_provider"] == "codex"


def test_dashboard_can_request_restart():
    client = TestClient(server.app)

    response = client.post("/api/system/restart", params={"reason": "Apply local code edits"})

    assert response.status_code == 200
    assert response.json()["status"] == "restarting"
