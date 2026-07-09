import json
import shutil
from pathlib import Path


class TaskExecutionResult(object):
    def __init__(self, task_id, success, score, output_path, metadata):
        self.task_id = task_id
        self.success = success
        self.score = score
        self.output_path = output_path
        self.metadata = metadata


class TaskExecutor(object):
    def __init__(self, workspace_root="outputs/task_runs"):
        self.workspace_root = Path(workspace_root)
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    def execute(self, task, skill_package=None, baseline_type="no_skill", fresh_env=False):
        task_id = task["task_id"]
        verifier = task.get("verifier", {})
        score = 0.0
        if verifier.get("expected_skill_keywords") and skill_package:
            content = json.dumps(skill_package, ensure_ascii=False)
            matched = sum([1 for kw in verifier["expected_skill_keywords"] if kw.lower() in content.lower()])
            score = float(matched) / float(max(1, len(verifier["expected_skill_keywords"])))
        elif baseline_type == "no_skill":
            score = 0.0
        else:
            score = 0.3 if skill_package else 0.1
        if task.get("difficulty") == "easy":
            score = min(1.0, score + 0.2)
        elif task.get("difficulty") == "hard":
            score = max(0.0, score - 0.1)
        success = score >= 0.999
        run_dir = self.workspace_root / (task_id + ("_oracle" if fresh_env else "_run"))
        if run_dir.exists():
            shutil.rmtree(str(run_dir))
        run_dir.mkdir(parents=True, exist_ok=True)
        artifact = {
            "task_id": task_id,
            "baseline_type": baseline_type,
            "fresh_env": fresh_env,
            "score": score,
            "success": success,
            "skill_summary": skill_package.get("summary") if skill_package else None,
        }
        artifact_path = run_dir / "result.json"
        artifact_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")
        return TaskExecutionResult(task_id, success, score, artifact_path, artifact)


class TaskManifest(object):
    @staticmethod
    def load(path, limit=None):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        tasks = data.get("tasks", data)
        if limit is not None:
            tasks = tasks[:limit]
        return tasks
