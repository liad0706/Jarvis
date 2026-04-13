"""Tests for the code writer skill (Cursor stub only — no LLM file fill)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from skills.code_writer import CodeWriterSkill


@pytest.fixture
def code_writer(tmp_path):
    with patch("skills.code_writer.get_settings") as mock_settings:
        settings = MagicMock()
        settings.code_output_dir = str(tmp_path / "code")
        mock_settings.return_value = settings
        Path(tmp_path / "code").mkdir(parents=True, exist_ok=True)
        with patch("skills.code_writer._find_cursor_cli", return_value="/fake/cursor"):
            skill = CodeWriterSkill()
            skill._open_in_cursor = MagicMock(return_value=True)
            yield skill


class TestCodeWriter:
    @pytest.mark.asyncio
    async def test_execute_unknown_action(self, code_writer):
        result = await code_writer.execute("nonexistent")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_write_stub(self, code_writer, tmp_path):
        result = await code_writer.do_write(prompt="make a fibonacci function")

        assert result["status"] == "written"
        assert result["mode"] == "cursor_stub"
        assert result["cursor_opened"] is True
        p = Path(result["file"])
        text = p.read_text(encoding="utf-8")
        assert "fibonacci" in text
        assert "pass" in text or "TODO" in text
        code_writer._open_in_cursor.assert_called_once()

    @pytest.mark.asyncio
    async def test_write_with_custom_filename(self, code_writer):
        result = await code_writer.do_write(prompt="Log hi", filename="test.js")

        assert result["filename"] == "test.js"
        assert result["file"].endswith("test.js")
        assert Path(result["file"]).read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_write_cursor_not_found(self, code_writer):
        code_writer._open_in_cursor = MagicMock(return_value=False)

        result = await code_writer.do_write(prompt="test")
        assert result["cursor_opened"] is False
        assert result["status"] == "written"

    @pytest.mark.asyncio
    async def test_edit_file_not_found(self, code_writer):
        result = await code_writer.do_edit(
            file_path="/nonexistent/file.py",
            instructions="fix it",
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_edit_stub_does_not_rewrite_file(self, code_writer, tmp_path):
        code_dir = Path(code_writer.output_dir)
        tf = code_dir / "keep.py"
        tf.write_text("x = 1\n", encoding="utf-8")

        result = await code_writer.do_edit(
            file_path=str(tf),
            instructions="change x to 2",
        )

        assert result["mode"] == "cursor_stub"
        assert tf.read_text(encoding="utf-8") == "x = 1\n"
        sc = Path(result["sidecar"])
        assert sc.exists()
        assert "change x to 2" in sc.read_text(encoding="utf-8")
        assert code_writer._open_in_cursor.call_count == 1
        args = code_writer._open_in_cursor.call_args[0]
        assert tf in args
        assert sc in args

    @pytest.mark.asyncio
    async def test_run_python_file(self, code_writer):
        code_dir = Path(code_writer.output_dir)
        test_file = code_dir / "run_test.py"
        test_file.write_text('print("hello from test")', encoding="utf-8")

        result = await code_writer.do_run(file_path=str(test_file))

        assert result["status"] == "ok"
        assert "hello from test" in result["stdout"]

    @pytest.mark.asyncio
    async def test_run_file_not_found(self, code_writer):
        result = await code_writer.do_run(file_path="/nonexistent.py")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_run_disallowed_extension(self, code_writer):
        code_dir = Path(code_writer.output_dir)
        test_file = code_dir / "script.sh"
        test_file.write_text("echo hi", encoding="utf-8")

        result = await code_writer.do_run(file_path=str(test_file))
        assert "error" in result
        assert "Only Python" in result["error"]

    @pytest.mark.asyncio
    async def test_run_failing_script(self, code_writer):
        code_dir = Path(code_writer.output_dir)
        test_file = code_dir / "fail_test.py"
        test_file.write_text('raise ValueError("boom")', encoding="utf-8")

        result = await code_writer.do_run(file_path=str(test_file))
        assert result["status"] == "error"
        assert result["return_code"] != 0

    @pytest.mark.asyncio
    async def test_list_empty(self, code_writer):
        result = await code_writer.do_list()
        assert result["status"] == "ok"
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_list_with_files(self, code_writer):
        code_dir = Path(code_writer.output_dir)
        (code_dir / "a.py").write_text("pass")
        (code_dir / "b.js").write_text("//")

        result = await code_writer.do_list()
        assert result["count"] == 2

    def test_get_actions(self, code_writer):
        actions = code_writer.get_actions()
        assert "write" in actions
        assert "edit" in actions
        assert "run" in actions
        assert "list" in actions

    def test_skill_name(self, code_writer):
        assert code_writer.name == "code"

    def test_as_tools_prompt_description(self, code_writer):
        tools = {t["function"]["name"]: t["function"] for t in code_writer.as_tools()}
        write = tools["code_write"]
        props = write["parameters"]["properties"]
        assert "requirements" in props["prompt"]["description"].lower() or "Do NOT" in props["prompt"]["description"]
        assert "Cursor" in props["prompt"]["description"]

    def test_as_tools_edit_instructions_description(self, code_writer):
        tools = {t["function"]["name"]: t["function"] for t in code_writer.as_tools()}
        edit = tools["code_edit"]
        props = edit["parameters"]["properties"]
        assert "Cursor" in props["instructions"]["description"]
