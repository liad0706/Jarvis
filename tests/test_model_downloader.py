"""Tests for the 3D model downloader skill."""

from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from skills.model_downloader import ModelDownloaderSkill


@pytest.fixture
def downloader(tmp_path):
    with patch("skills.model_downloader.get_settings") as mock_settings:
        settings = MagicMock()
        settings.stl_download_dir = str(tmp_path / "downloads")
        mock_settings.return_value = settings
        skill = ModelDownloaderSkill()
        yield skill


class TestModelDownloader:
    @pytest.mark.asyncio
    async def test_execute_unknown_action(self, downloader):
        result = await downloader.execute("nonexistent")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_search_success(self, downloader):
        mock_html = """
        <html>
        <div class="ThingCardBody">
            <a href="/thing:12345">
                <span class="ThingCardBody-title">Test Benchy</span>
            </a>
        </div>
        </html>
        """
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.text = mock_html

        with patch("requests.get", return_value=mock_response):
            result = await downloader.do_search("benchy")

        assert result["status"] == "ok"
        assert result["query"] == "benchy"

    @pytest.mark.asyncio
    async def test_search_network_error(self, downloader):
        with patch("requests.get", side_effect=Exception("Network error")):
            result = await downloader.do_search("test")

        # Should not crash, returns empty results
        assert result["status"] == "ok"
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_download_page_not_found(self, downloader):
        mock_response = MagicMock()
        mock_response.ok = False
        mock_response.status_code = 404

        with patch("requests.get", return_value=mock_response):
            result = await downloader.do_download("https://example.com/model")

        assert "error" in result

    @pytest.mark.asyncio
    async def test_download_no_stl_links(self, downloader):
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.text = "<html><body>No download links here</body></html>"

        with patch("requests.get", return_value=mock_response):
            result = await downloader.do_download("https://example.com/model")

        assert "error" in result
        assert "No downloadable" in result["error"]

    @pytest.mark.asyncio
    async def test_download_success(self, downloader, tmp_path):
        # Mock the page with an STL link
        page_html = '<html><a href="/download/model.stl">Download STL</a></html>'
        page_response = MagicMock()
        page_response.ok = True
        page_response.text = page_html

        # Mock the file download
        file_response = MagicMock()
        file_response.ok = True
        file_response.headers = {"content-disposition": 'attachment; filename="cool_model.stl"'}
        file_response.iter_content = MagicMock(return_value=[b"solid test\nendsolid test"])

        call_count = 0

        def mock_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return page_response
            return file_response

        with patch("requests.get", side_effect=mock_get):
            result = await downloader.do_download(
                "https://www.thingiverse.com/thing:12345"
            )

        assert result["status"] == "downloaded"
        assert "cool_model.stl" in result["filename"]

    @pytest.mark.asyncio
    async def test_list_downloads_empty(self, downloader):
        result = await downloader.do_list_downloads()
        assert result["status"] == "ok"
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_list_downloads_with_files(self, downloader):
        # Create some fake STL files
        dl_dir = Path(downloader.download_dir)
        (dl_dir / "model1.stl").write_text("solid")
        (dl_dir / "model2.3mf").write_bytes(b"fake3mf")
        (dl_dir / "readme.txt").write_text("not a model")

        result = await downloader.do_list_downloads()
        assert result["status"] == "ok"
        assert result["count"] == 2  # Only STL and 3MF
        names = [f["name"] for f in result["files"]]
        assert "model1.stl" in names
        assert "model2.3mf" in names
        assert "readme.txt" not in names

    def test_get_actions(self, downloader):
        actions = downloader.get_actions()
        assert "search" in actions
        assert "download" in actions
        assert "list_downloads" in actions

    def test_skill_name(self, downloader):
        assert downloader.name == "models"
