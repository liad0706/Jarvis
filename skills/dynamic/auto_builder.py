from core.skill_base import BaseSkill

class AutoBuilderSkill(BaseSkill):
    name = "auto_builder"
    description = "Automatically builds new skills for Jarvis based on user requests."

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        try:
            result = await method(**(params or {}))
            return {"status": "ok", **result}
        except Exception as e:
            return {"error": str(e)}

    async def do_check(self, request: dict) -> dict:
        """Checks if a new skill is needed and builds it if necessary."""
        from config import get_settings
        settings = get_settings()
        skill_name = request.get("skill_name")
        description = request.get("description")

        if not skill_name or not description:
            return {"error": "Missing required fields"}

        if self.name != "auto_builder":
            return {"status": "not_applicable", "message": f"{self.name} cannot build new skills."}

        existing_skill = next((skill for skill in settings.skills if skill["name"] == skill_name), None)
        if not existing_skill:
            await self._build_skill(skill_name, description)
            return {"status": "created", "new_skill_name": skill_name, "description": description}
        else:
            return {"status": "exists", "message": f"Skill {skill_name} already exists."}

    async def _build_skill(self, name: str, description: str) -> None:
        from core.skill_base import BaseSkill
        new_skill = type(name, (BaseSkill,), {
            "name": name,
            "description": description,
        })
        setattr(self.__class__, name, new_skill)