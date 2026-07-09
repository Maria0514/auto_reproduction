class SkillPackage(object):
    def __init__(self, version, summary, files, history=None):
        self.version = version
        self.summary = summary
        self.files = files
        self.history = history or []

    def to_dict(self):
        return {
            "version": self.version,
            "summary": self.summary,
            "files": self.files,
            "history": self.history,
        }


class SkillGenerator(object):
    def __init__(self, meta_prompt, background_context=""):
        self.meta_prompt = meta_prompt
        self.background_context = background_context

    def initial_skill(self, task):
        keywords = task.get("verifier", {}).get("expected_skill_keywords", [])
        files = {
            "SKILL.md": "# Skill\n" + task.get("instruction", "") + "\nKeywords: " + ", ".join(keywords),
            "runbook.txt": "Use deterministic steps. Background: " + self.background_context,
        }
        return SkillPackage(0, "Initial skill for %s" % task["task_id"], files, ["initial_generation"])

    def refine_skill(self, skill, diagnostics):
        new_files = dict(skill.files)
        missing = diagnostics.get("missing_keywords", [])
        if missing:
            new_files["patch_notes.txt"] = "Added missing keywords: " + ", ".join(missing)
            new_files["SKILL.md"] = new_files.get("SKILL.md", "") + "\nRefinement: " + ", ".join(missing)
        new_history = list(skill.history) + [diagnostics.get("message", "refine")]
        return SkillPackage(skill.version + 1, "Refined v%d for %s" % (skill.version + 1, diagnostics.get("task_id", "task")), new_files, new_history)
