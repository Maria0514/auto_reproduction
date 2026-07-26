"""BUG-E2E2-01 防回潮守门 —— 用户可见 message 不得泄漏内部标识符。

背景：resource_scout 找不到仓库降级时，message 硬编码内部枚举 `from_scratch`，
经 `make_node_error(...)` 写进 `node_errors`，UI / Markdown 报告全程无翻译层，
原样渲染给用户看（见 docs/bugfix-e2e2/architecture.md §9 传播链）。

本用例用 AST 扫描 `make_node_error(...)` 第 3 个位置参数（或关键字 `error_message`）
的字面量片段，断言不含内部枚举 / 节点名 / 技术术语。

扫描范围（Maria 2026-07-26 拍板的最小范围，见 architecture.md §8.1）：
仅 `core/nodes/resource_scout.py`。architecture.md §11 留档的其余 16 处同族泄漏
本次不动，另开 TODO；日后清理时只需往 `_GUARDED_MODULES` 里加模块名即可，
不必重写本用例。

离线维（零 LLM、零网络、零 deepxiv 配额）。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 扩围预留：日后清理 architecture.md §11 的 P1 同族泄漏时，往这里加模块名即可。
_GUARDED_MODULES: Tuple[str, ...] = ("resource_scout",)

# 用户可见文案黑名单：内部枚举 / 节点名 / 技术术语（大小写不敏感 + 词边界匹配）。
_BLACKLIST: Tuple[str, ...] = (
    # 内部枚举
    "from_scratch",
    "use_repo",
    "hybrid",
    # 节点名
    "resource_scout",
    # 术语
    "ReAct",
)

# 唯一豁免：`[error_category=` 开头的机器契约前缀（execution 节点写、reporting 解析），
# 属机器可读契约而非人类文案，整串跳过（architecture.md §10.2 红线 3-2）。
_CONTRACT_PREFIX = "[error_category="


def _module_path(name: str) -> Path:
    return PROJECT_ROOT / "core" / "nodes" / f"{name}.py"


_SCOPE_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def _iter_scopes(tree: ast.AST):
    """产出模块本身 + 每个函数/lambda 作用域。"""
    yield tree
    for node in ast.walk(tree):
        if isinstance(node, _SCOPE_TYPES):
            yield node


def _iter_scope_nodes(scope: ast.AST):
    """遍历 scope 内节点，不下钻嵌套函数/类（它们各自作为独立 scope 处理）。"""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, _SCOPE_TYPES + (ast.ClassDef,)):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _literals_of(node: ast.AST) -> List[Tuple[int, str]]:
    """取表达式内全部字符串字面量片段。

    Constant 取整串；JoinedStr（f-string）取其中全部 Constant 片段；
    `_coerce_str(x) or "兜底"` 这类 BoolOp 逐分支收集。
    """
    out: List[Tuple[int, str]] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            out.append((getattr(sub, "lineno", getattr(node, "lineno", 0)), sub.value))
    return out


def _extract_message_literals(tree: ast.AST) -> List[Tuple[int, str]]:
    """抽取所有 make_node_error(...) 的 message 实参里的字符串字面量片段。

    - 位置调用取第 3 个位置参数（node_name, error_type, error_message, ...）；
    - 关键字调用取 `error_message=`；
    - 实参若是变量名（生产代码普遍写法 `message = "..."` 后再传入），
      在同一作用域内解析该变量的全部赋值字面量。

    返回去重后的 [(行号, 字面量), ...]。
    """
    found = set()
    for scope in _iter_scopes(tree):
        # 该作用域内 name -> 赋值字面量片段
        name_literals = {}
        calls = []
        for node in _iter_scope_nodes(scope):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if node.value is None:
                    continue
                lits = _literals_of(node.value)
                if not lits:
                    continue
                for tgt in targets:
                    if isinstance(tgt, ast.Name):
                        name_literals.setdefault(tgt.id, []).extend(lits)
            elif isinstance(node, ast.Call):
                func = node.func
                fname = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                if fname == "make_node_error":
                    calls.append(node)

        for call in calls:
            arg = call.args[2] if len(call.args) >= 3 else None
            for kw in call.keywords:
                if kw.arg == "error_message":
                    arg = kw.value
                    break
            if arg is None:
                continue
            if isinstance(arg, ast.Name):
                found.update(name_literals.get(arg.id, []))
            else:
                found.update(_literals_of(arg))
    return sorted(found)


def _hits(literal: str) -> List[str]:
    """返回该字面量命中的黑名单词（大小写不敏感、词边界匹配）。"""
    if literal.startswith(_CONTRACT_PREFIX):
        return []
    hits: List[str] = []
    for word in _BLACKLIST:
        if re.search(rf"(?<![0-9A-Za-z_]){re.escape(word)}(?![0-9A-Za-z_])",
                     literal, flags=re.IGNORECASE):
            hits.append(word)
    return hits


def test_node_error_messages_have_no_internal_jargon():
    """用户可见的 node_errors message 不得泄漏内部枚举 / 节点名 / 技术术语。"""
    violations: List[str] = []
    scanned = 0

    for name in _GUARDED_MODULES:
        path = _module_path(name)
        assert path.is_file(), f"守门目标模块不存在：{path}"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        literals = _extract_message_literals(tree)
        assert literals, (
            f"{path} 内未扫到任何 make_node_error message 字面量——"
            f"守门可能已失效（函数改名 / 调用形态变化），请检查扫描逻辑。"
        )
        scanned += len(literals)
        for lineno, literal in literals:
            hit = _hits(literal)
            if hit:
                violations.append(
                    f"  {path}:{lineno} 命中 {hit} -> {literal!r}"
                )

    assert not violations, (
        "用户可见文案禁用内部标识符（内部枚举 / 节点名 / 技术术语），"
        "请改为通俗中文；若为机器契约请加入豁免并说明理由。\n"
        + "\n".join(violations)
        + f"\n（本次共扫描 {scanned} 条 message 字面量，"
        f"范围 _GUARDED_MODULES={_GUARDED_MODULES}）"
    )
