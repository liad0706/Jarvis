from pathlib import Path

from core.dev_reload import AutoReloadWatcher, build_restart_reason, build_resume_message, summarize_changed_files
from skills import restart as restart_mod


def test_auto_reload_watcher_detects_changes_and_ignores_data(tmp_path):
    (tmp_path / "core").mkdir()
    (tmp_path / "skills").mkdir()
    (tmp_path / "data").mkdir()

    watched = tmp_path / "core" / "brain.py"
    watched.write_text("print('v1')\n", encoding="utf-8")
    ignored = tmp_path / "data" / "metrics.json"
    ignored.write_text("{}", encoding="utf-8")

    watcher = AutoReloadWatcher(tmp_path)
    watcher.prime()

    watched.write_text("print('v2')\n", encoding="utf-8")
    ignored.write_text('{"x": 1}', encoding="utf-8")
    created = tmp_path / "skills" / "new_skill.py"
    created.write_text("VALUE = 1\n", encoding="utf-8")

    changed = watcher.scan_changes()
    changed_names = {path.name for path in changed}

    assert "brain.py" in changed_names
    assert "new_skill.py" in changed_names
    assert "metrics.json" not in changed_names


def test_restart_summary_helpers_use_relative_paths(tmp_path):
    changed = [
        tmp_path / "core" / "orchestrator.py",
        tmp_path / "skills" / "restart.py",
        tmp_path / "dashboard" / "index.html",
        tmp_path / "main.py",
    ]
    for path in changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")

    summary = summarize_changed_files(changed, tmp_path, limit=2)
    reason = build_restart_reason(changed, tmp_path)
    resume = build_resume_message(changed, tmp_path)

    assert summary == "core/orchestrator.py, dashboard/index.html, +2 more"
    assert reason.startswith("Auto-reload after code changes:")
    assert "Reloaded with fresh code:" in resume


def test_restart_context_persists_source_and_changed_files(tmp_path, monkeypatch):
    restart_file = tmp_path / "restart_context.json"
    monkeypatch.setattr(restart_mod, "RESTART_FILE", restart_file)

    restart_mod.save_restart_context(
        reason="Auto reload after edit",
        resume_message="Jarvis reloaded",
        source="auto_reload",
        changed_files=["core/brain.py", "skills/restart.py"],
    )

    loaded = restart_mod.has_pending_restart()

    assert loaded is not None
    assert loaded["source"] == "auto_reload"
    assert loaded["changed_files"] == ["core/brain.py", "skills/restart.py"]

    restart_mod.clear_restart_context()
    assert not restart_file.exists()
