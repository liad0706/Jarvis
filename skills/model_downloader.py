"""3D model search and download skill - Thingiverse & Printables."""

import asyncio
import logging
import re
from pathlib import Path
from urllib.parse import quote_plus

from core.skill_base import BaseSkill
from config import get_settings

logger = logging.getLogger(__name__)


class ModelDownloaderSkill(BaseSkill):
    name = "models"
    description = "Search and download free 3D printable models from Thingiverse and Printables"

    def __init__(self):
        self.settings = get_settings()
        self.download_dir = Path(self.settings.stl_download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        return await method(**(params or {}))

    async def do_search(self, query: str) -> dict:
        """Search for 3D models on Thingiverse and Printables."""
        import requests
        from bs4 import BeautifulSoup

        results = []

        # Search Thingiverse
        try:
            url = f"https://www.thingiverse.com/search?q={quote_plus(query)}&type=things"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            resp = await asyncio.to_thread(requests.get, url, headers=headers, timeout=15)
            if resp.ok:
                soup = BeautifulSoup(resp.text, "html.parser")
                cards = soup.select(".ThingCardBody, [class*='ThingCard']")
                for card in cards[:5]:
                    link = card.find("a", href=True)
                    title_el = card.find(class_=re.compile("ThingCard.*Name|CardBody.*title", re.I))
                    if link:
                        title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)
                        href = link["href"]
                        if not href.startswith("http"):
                            href = f"https://www.thingiverse.com{href}"
                        results.append({
                            "title": title or "Untitled",
                            "url": href,
                            "source": "Thingiverse",
                        })
        except Exception as e:
            logger.warning("Thingiverse search failed: %s", e)

        # Search Printables
        try:
            url = f"https://www.printables.com/search/models?q={quote_plus(query)}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            resp = await asyncio.to_thread(requests.get, url, headers=headers, timeout=15)
            if resp.ok:
                soup = BeautifulSoup(resp.text, "html.parser")
                cards = soup.select("[class*='print-card'], [class*='PrintCard'], .search-result")
                for card in cards[:5]:
                    link = card.find("a", href=True)
                    title_el = card.find("h3") or card.find("h2") or card.find(class_=re.compile("title", re.I))
                    if link:
                        title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)
                        href = link["href"]
                        if not href.startswith("http"):
                            href = f"https://www.printables.com{href}"
                        results.append({
                            "title": title or "Untitled",
                            "url": href,
                            "source": "Printables",
                        })
        except Exception as e:
            logger.warning("Printables search failed: %s", e)

        return {
            "status": "ok",
            "query": query,
            "results": results,
            "count": len(results),
            "message": f"Found {len(results)} models for '{query}'" if results else f"No results for '{query}'",
        }

    async def do_download(self, url: str, filename: str = "") -> dict:
        """Download a 3D model from a given URL."""
        import requests

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        def _download():
            from bs4 import BeautifulSoup

            # Get the page to find download links
            resp = requests.get(url, headers=headers, timeout=15)
            if not resp.ok:
                return {"error": f"Failed to access {url}: {resp.status_code}"}

            soup = BeautifulSoup(resp.text, "html.parser")

            # Find STL/3MF download links
            download_links = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if any(ext in href.lower() for ext in [".stl", ".3mf", ".obj", "download"]):
                    if not href.startswith("http"):
                        if "thingiverse" in url:
                            href = f"https://www.thingiverse.com{href}"
                        elif "printables" in url:
                            href = f"https://www.printables.com{href}"
                    download_links.append(href)

            if not download_links:
                return {"error": "No downloadable files found on the page", "url": url}

            # Download the first STL file
            dl_url = download_links[0]
            resp = requests.get(dl_url, headers=headers, timeout=60, stream=True)
            if not resp.ok:
                return {"error": f"Download failed: {resp.status_code}"}

            # Determine filename
            if not filename:
                # Try to get from content-disposition
                cd = resp.headers.get("content-disposition", "")
                if "filename=" in cd:
                    fname = cd.split("filename=")[-1].strip('"\'')
                else:
                    fname = dl_url.split("/")[-1].split("?")[0]
                    if not any(fname.endswith(ext) for ext in [".stl", ".3mf", ".obj"]):
                        fname = f"model_{hash(url) % 10000}.stl"
            else:
                fname = filename

            # Save file
            save_path = self.download_dir / fname
            with open(save_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)

            return {
                "status": "downloaded",
                "file": str(save_path),
                "filename": fname,
                "size_kb": round(save_path.stat().st_size / 1024, 1),
                "message": f"Downloaded {fname} ({save_path.stat().st_size // 1024} KB)",
            }

        return await asyncio.to_thread(_download)

    async def do_list_downloads(self) -> dict:
        """List all downloaded 3D model files."""
        files = []
        for f in self.download_dir.rglob("*"):
            if f.is_file() and f.suffix.lower() in [".stl", ".3mf", ".obj", ".gcode"]:
                files.append({
                    "name": f.name,
                    "path": str(f),
                    "size_kb": round(f.stat().st_size / 1024, 1),
                })
        return {
            "status": "ok",
            "files": files,
            "count": len(files),
            "message": f"{len(files)} model files in downloads" if files else "No models downloaded yet",
        }
