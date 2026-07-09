"""诚实性审计：对复现代码目录做确定性规则型 smell 检测（S5-03）。

架构裁决（architecture.md §1 Q-S5-5 / §7.3 / §10.1 R-3）：
- 落点 reporting 侧入口，本模块为**纯函数模块**——零 LLM 调用、零 state 依赖，
  输入一个目录路径，输出一个可 JSON 序列化的审计结果 dict；
- 三条规则，``rule`` 为三值字符串字面量（不建 Enum）：
    * ``answer_leakage``   R1 答案泄漏：非评估角色代码以字面量键读取答案字段；
    * ``hardcoded_score``  R2 硬编码分数：评分标识符绑定/返回裸数字字面量、
      baseline/实验名 → 数字字面量映射（dict 字面量或 if/elif 分派两种字面形态）；
    * ``constant_outcome`` R3 常量结局：评估角色函数所有 return 均为常量表达式
      且入参未参与任何 return 值计算（单函数 AST 可判定）。

总纪律：
- 只认字面量证据（AST/文本），不做跨文件数据流推断；
- 每条命中必附 ``(file, line, snippet)`` 证据，无证据不产 hit；
- 命中只供结论降档 + 标注（S5-04 消费），**绝不阻断流程**——本模块只产出结果，
  不做任何流程控制；
- 误报红线与命中同权重（AC-S5-06），三重豁免防线：
    * 评估角色（文件/函数名含 verify/eval/score/judge 语义）读答案字段合法；
    * ``score = 0.0`` 类初始化若同函数内有后续更新（增量赋值/重绑定）不命中；
    * 任一 return 引用入参或非常量中间值即不命中 R3。
- 脱敏出口③：``hits[].snippet`` 一律过 ``mask_value``（生成代码可能硬编码 key），
  先脱敏后截断（防止截断切掉敏感值尾部导致部分明文残留）；
- 语法错误 / 编码错误 / JSON 解析错误文件容忍跳过 + WARNING（失败非静默）。

扫描范围 = ``code_dir`` 下 ``*.py``（排除 venv / outputs / tests 等目录）
+ 数据清单类 JSON（文件名含 manifest / tasks / dataset）。
"""

from __future__ import annotations

import ast
import json
import logging
import re
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple, Union

from core.secrets_store import mask_value

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量：语义词表（字面量匹配，刻意保持最小集——极简纪律，不预设开放式配置）
# ---------------------------------------------------------------------------

#: 扫描时按目录名排除（相对 code_dir 的任一路径分量命中即整棵子树跳过）
_EXCLUDED_DIR_NAMES = frozenset(
    {"venv", ".venv", "outputs", "tests", "__pycache__", ".git", "site-packages"}
)

#: R1 答案字段名集合：expected_* / answer* / ground_truth* / *_keywords / label*
_ANSWER_KEY_PREFIXES: Tuple[str, ...] = ("expected_", "answer", "ground_truth", "label")
_ANSWER_KEY_SUFFIXES: Tuple[str, ...] = ("_keywords",)

#: 评估角色语义（R1 豁免 + R3 靶选择）：verify/eval/score/judge——按名字分段前缀匹配
_EVALUATOR_TOKENS: Tuple[str, ...] = ("verif", "eval", "scor", "judge")

#: R2 评分语义标识符：score/accuracy/pass_rate/f1/reward
_SCORING_PREFIXES: Tuple[str, ...] = ("score", "accuracy", "reward")
_SCORING_EXACT: Tuple[str, ...] = ("f1",)
_SCORING_MULTIWORD: Tuple[str, ...] = ("pass_rate",)

#: R2(b) baseline/实验名语义
_BASELINE_TOKENS: Tuple[str, ...] = ("baseline", "experiment", "variant")

#: R2(a) "执行函数"语义（评分函数语义直接复用评分标识符判定 + 评估角色判定）
_EXEC_FUNC_TOKENS: Tuple[str, ...] = ("execute", "exec", "run", "compute", "measure")

#: 数据清单类 JSON 文件名特征
_MANIFEST_JSON_TOKENS: Tuple[str, ...] = ("manifest", "tasks", "dataset")

#: snippet 截断上限（先 mask 后截断）
_SNIPPET_MAX_LEN = 200

_FUNC_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)


# ---------------------------------------------------------------------------
# 命名语义判定 helpers（纯字符串，无 IO）
# ---------------------------------------------------------------------------

def _segments(name: str) -> List[str]:
    """把标识符/文件名切成小写语义分段（snake_case + camelCase）。"""
    parts = re.split(r"[^0-9A-Za-z]+", name)
    segs: List[str] = []
    for part in parts:
        for m in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z0-9]+", part):
            if m:
                segs.append(m.lower())
    return segs


def _is_answer_key(key: str) -> bool:
    lowered = key.lower()
    return lowered.startswith(_ANSWER_KEY_PREFIXES) or lowered.endswith(
        _ANSWER_KEY_SUFFIXES
    )


def _is_evaluator_name(name: str) -> bool:
    """评估角色语义：verify/eval/score/judge 出现在名字任一分段的前缀。"""
    return any(seg.startswith(_EVALUATOR_TOKENS) for seg in _segments(name))


def _is_scoring_identifier(name: str) -> bool:
    """评分语义标识符：score/accuracy/pass_rate/f1/reward。"""
    segs = _segments(name)
    if any(seg.startswith(_SCORING_PREFIXES) or seg in _SCORING_EXACT for seg in segs):
        return True
    lowered = name.lower()
    return any(tok in lowered for tok in _SCORING_MULTIWORD)


def _is_baseline_identifier(name: str) -> bool:
    return any(seg.startswith(_BASELINE_TOKENS) for seg in _segments(name))


def _is_exec_func_name(name: str) -> bool:
    return any(seg.startswith(_EXEC_FUNC_TOKENS) for seg in _segments(name))


# ---------------------------------------------------------------------------
# AST 字面量判定 helpers
# ---------------------------------------------------------------------------

def _is_number(node: ast.AST) -> bool:
    """裸数字字面量（bool 不算——True/False 不是"分数"）。"""
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    )


def _is_numeric_literal_expr(node: ast.AST) -> bool:
    """裸数字字面量，或两分支均为数字字面量的条件表达式（``0.3 if x else 0.1``）。"""
    if _is_number(node):
        return True
    if isinstance(node, ast.IfExp):
        return _is_number(node.body) and _is_number(node.orelse)
    return False


def _is_constant_expr(node: ast.AST) -> bool:
    """R3 常量表达式：不含任何 Name/Call/Attribute 引用（引用即豁免）。"""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_is_constant_expr(elt) for elt in node.elts)
    if isinstance(node, ast.Dict):
        return all(k is not None and _is_constant_expr(k) for k in node.keys) and all(
            _is_constant_expr(v) for v in node.values
        )
    if isinstance(node, ast.UnaryOp):
        return _is_constant_expr(node.operand)
    if isinstance(node, ast.BinOp):
        return _is_constant_expr(node.left) and _is_constant_expr(node.right)
    if isinstance(node, ast.IfExp):
        return (
            _is_constant_expr(node.test)
            and _is_constant_expr(node.body)
            and _is_constant_expr(node.orelse)
        )
    return False


def _node_identifier(node: ast.AST) -> Optional[str]:
    """Name → id；Attribute → attr（``self.baseline_type`` 取尾段）；其余 None。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _literal_read_key(node: ast.AST) -> Optional[str]:
    """字面量键读取：``obj["key"]``（Load）或 ``obj.get("key", ...)`` → 键名。"""
    if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
        sl = node.slice
        if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
            return sl.value
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and node.args
    ):
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
    return None


def _walk_function_scope(func: ast.AST) -> Iterator[ast.AST]:
    """迭代函数体内 AST 节点，不进入嵌套函数/类作用域。"""
    stack: List[ast.AST] = list(getattr(func, "body", []))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, _FUNC_DEFS + (ast.ClassDef,)):
            continue
        stack.extend(ast.iter_child_nodes(node))


# ---------------------------------------------------------------------------
# hit 构造（证据三元组 + 脱敏出口③）
# ---------------------------------------------------------------------------

def _make_hit(rule: str, rel_file: str, line: int, snippet: str) -> Dict[str, object]:
    masked = mask_value(snippet.strip()) or ""
    return {
        "rule": rule,
        "file": rel_file,
        "line": int(line),
        "snippet": masked[:_SNIPPET_MAX_LEN],
    }


def _line_snippet(lines: List[str], lineno: int) -> str:
    if 1 <= lineno <= len(lines):
        return lines[lineno - 1]
    return ""


# ---------------------------------------------------------------------------
# R1 答案泄漏
# ---------------------------------------------------------------------------

def _rule_answer_leakage(
    tree: ast.AST, rel_file: str, lines: List[str], file_is_evaluator: bool
) -> List[Dict[str, object]]:
    """非评估角色代码以 subscript/.get() 字面量键读取答案字段。

    豁免（第一道防线）：评估角色文件整体豁免；非评估文件内，任一层
    评估角色函数名下的读取豁免（干净 verifier 必然读答案）。
    """
    hits: List[Dict[str, object]] = []
    if file_is_evaluator:
        return hits

    def visit(node: ast.AST, func_stack: Tuple[str, ...]) -> None:
        if isinstance(node, _FUNC_DEFS):
            func_stack = func_stack + (node.name,)
        key = _literal_read_key(node)
        if (
            key is not None
            and _is_answer_key(key)
            and not any(_is_evaluator_name(fn) for fn in func_stack)
        ):
            hits.append(
                _make_hit(
                    "answer_leakage",
                    rel_file,
                    node.lineno,
                    _line_snippet(lines, node.lineno),
                )
            )
        for child in ast.iter_child_nodes(node):
            visit(child, func_stack)

    visit(tree, ())
    return hits


# ---------------------------------------------------------------------------
# R2 硬编码分数
# ---------------------------------------------------------------------------

def _rule_hardcoded_score(
    tree: ast.AST, rel_file: str, lines: List[str]
) -> List[Dict[str, object]]:
    """四种字面形态（同属 hardcoded_score，一并去重）：

    (a1) 评分/执行语义函数直接 ``return <数字字面量>``（执行语义函数只认 float——
         ``return 0`` 退出码合法）；
    (a2) 函数 ``return <评分标识符>`` 且该标识符在函数内的**全部**绑定均为裸数字
         字面量赋值（豁免：初始化后有任何重绑定/增量赋值即不命中）；
    (b1) 赋值目标为 baseline/评分语义名，值为非空 ``{str: 数字}`` dict 字面量；
    (b2) ``if/elif`` 测试为 baseline/实验名 == 字符串字面量时，分支内对评分标识符
         的数字字面量赋值（含两分支均字面量的 IfExp）或数字字面量 return。
    """
    hits: List[Dict[str, object]] = []

    def add(line: int) -> None:
        hits.append(
            _make_hit("hardcoded_score", rel_file, line, _line_snippet(lines, line))
        )

    # --- (a1) + (a2)：按函数作用域 ---
    for func in (n for n in ast.walk(tree) if isinstance(n, _FUNC_DEFS)):
        scope_nodes = list(_walk_function_scope(func))
        name_is_scoring = _is_scoring_identifier(func.name) or _is_evaluator_name(
            func.name
        )
        name_is_exec = _is_exec_func_name(func.name)

        returned_names: List[Tuple[str, int]] = []
        for node in scope_nodes:
            if not isinstance(node, ast.Return) or node.value is None:
                continue
            if _is_number(node.value):
                is_float = isinstance(node.value.value, float)
                if name_is_scoring or (name_is_exec and is_float):
                    add(node.lineno)
            elif isinstance(node.value, ast.Name):
                returned_names.append((node.value.id, node.lineno))

        for ret_name, _ret_line in returned_names:
            if not _is_scoring_identifier(ret_name):
                continue
            literal_lines: List[int] = []
            all_literal = True
            saw_binding = False
            for node in scope_nodes:
                if isinstance(node, ast.Assign):
                    bound = any(
                        isinstance(t, ast.Name) and t.id == ret_name
                        for t in node.targets
                    )
                    if not bound:
                        continue
                    saw_binding = True
                    single_name = len(node.targets) == 1 and isinstance(
                        node.targets[0], ast.Name
                    )
                    if single_name and _is_number(node.value):
                        literal_lines.append(node.lineno)
                    else:
                        all_literal = False  # 重绑定为计算值 → 初始化豁免
                elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                    target = getattr(node, "target", None)
                    if isinstance(target, ast.Name) and target.id == ret_name:
                        saw_binding = True
                        all_literal = False  # 增量赋值/注解赋值 → 豁免
            if saw_binding and all_literal and literal_lines:
                add(max(literal_lines))

    # --- (b1)：baseline/评分名 = {str: 数字} dict 字面量（含模块级） ---
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            tname = _node_identifier(node.targets[0])
            if tname and (
                _is_baseline_identifier(tname) or _is_scoring_identifier(tname)
            ):
                if _is_str_to_num_dict_literal(node.value):
                    add(node.lineno)

    # --- (b2)：baseline 名分派分支下的评分字面量 ---
    for ifnode in (n for n in ast.walk(tree) if isinstance(n, ast.If)):
        if not _is_baseline_dispatch_test(ifnode.test):
            continue
        for stmt in ifnode.body + ifnode.orelse:
            for sub in ast.walk(stmt):
                if isinstance(sub, ast.Assign) and len(sub.targets) == 1:
                    tname = _node_identifier(sub.targets[0])
                    if (
                        tname
                        and _is_scoring_identifier(tname)
                        and _is_numeric_literal_expr(sub.value)
                    ):
                        add(sub.lineno)
                elif (
                    isinstance(sub, ast.Return)
                    and sub.value is not None
                    and _is_numeric_literal_expr(sub.value)
                ):
                    add(sub.lineno)

    return hits


def _is_str_to_num_dict_literal(node: ast.AST) -> bool:
    """非空 dict 字面量，键全为字符串字面量、值全为数字字面量。"""
    if not isinstance(node, ast.Dict) or not node.keys:
        return False
    keys_ok = all(
        k is not None and isinstance(k, ast.Constant) and isinstance(k.value, str)
        for k in node.keys
    )
    values_ok = all(_is_number(v) for v in node.values)
    return keys_ok and values_ok


def _is_baseline_dispatch_test(test: ast.AST) -> bool:
    """``baseline_ish == "字符串字面量"``（左右任一侧为 baseline/实验语义名）。"""
    if not isinstance(test, ast.Compare) or not any(
        isinstance(op, ast.Eq) for op in test.ops
    ):
        return False
    operands = [test.left] + list(test.comparators)
    has_baseline_name = any(
        (ident := _node_identifier(node)) is not None and _is_baseline_identifier(ident)
        for node in operands
    )
    has_str_literal = any(
        isinstance(node, ast.Constant) and isinstance(node.value, str)
        for node in operands
    )
    return has_baseline_name and has_str_literal


# ---------------------------------------------------------------------------
# R3 常量结局
# ---------------------------------------------------------------------------

def _rule_constant_outcome(
    tree: ast.AST, rel_file: str, lines: List[str], file_is_evaluator: bool
) -> List[Dict[str, object]]:
    """评估角色函数所有带值 return 均为常量表达式 ∧ 入参未参与 return 值计算。

    豁免（第三道防线）：任一 return 引用入参或非常量中间值（任何 Name/Call/
    Attribute）即不命中；无入参（或仅 self/cls）不命中——常量 getter 合法；
    全部 return 均为 None 不命中——过程式函数合法。
    """
    hits: List[Dict[str, object]] = []
    for func in (n for n in ast.walk(tree) if isinstance(n, _FUNC_DEFS)):
        if not (file_is_evaluator or _is_evaluator_name(func.name)):
            continue
        args = func.args
        param_names = [
            a.arg
            for a in (
                list(args.posonlyargs)
                + list(args.args)
                + list(args.kwonlyargs)
                + ([args.vararg] if args.vararg else [])
                + ([args.kwarg] if args.kwarg else [])
            )
            if a.arg not in ("self", "cls")
        ]
        if not param_names:
            continue
        returns = [
            n
            for n in _walk_function_scope(func)
            if isinstance(n, ast.Return) and n.value is not None
        ]
        if not returns:
            continue
        if not all(_is_constant_expr(r.value) for r in returns):
            continue
        non_none = [
            r
            for r in returns
            if not (isinstance(r.value, ast.Constant) and r.value.value is None)
        ]
        if not non_none:
            continue
        first = min(non_none, key=lambda r: r.lineno)
        hits.append(
            _make_hit(
                "constant_outcome",
                rel_file,
                first.lineno,
                _line_snippet(lines, first.lineno),
            )
        )
    return hits


# ---------------------------------------------------------------------------
# 数据清单类 JSON：R2(b) 形态——baseline 键 → {名: 数字} 映射（预写结局清单）
# ---------------------------------------------------------------------------

def _audit_manifest_json(path: Path, rel_file: str) -> List[Dict[str, object]]:
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("honesty_audit: JSON 解析失败，跳过 %s: %s", rel_file, exc)
        return []

    hits: List[Dict[str, object]] = []

    def walk(obj: object) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                if (
                    isinstance(key, str)
                    and _is_baseline_identifier(key)
                    and _is_score_number_map(value)
                ):
                    snippet = json.dumps(
                        {key: value}, ensure_ascii=False, sort_keys=True
                    )
                    hits.append(
                        _make_hit(
                            "hardcoded_score",
                            rel_file,
                            _find_json_key_line(text, key),
                            snippet,
                        )
                    )
                else:
                    walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(data)
    return hits


def _is_score_number_map(value: object) -> bool:
    """非空 ``{名: 数字}`` 映射，且至少一个 float（int-only 视作配置，不算分数）。"""
    if not isinstance(value, dict) or not value:
        return False
    vals = list(value.values())
    if not all(
        isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals
    ):
        return False
    return any(isinstance(v, float) for v in vals)


def _find_json_key_line(text: str, key: str) -> int:
    idx = text.find('"%s"' % key)
    if idx < 0:
        return 1
    return text.count("\n", 0, idx) + 1


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def audit_code_dir(code_dir: Union[str, Path]) -> Dict[str, object]:
    """对 ``code_dir`` 做三规则诚实性审计。

    返回（architecture §7.3 数据契约，可直接作为 state 字段 ``honesty_audit`` 的值）::

        {"clean": bool,
         "hits": [{"rule": "answer_leakage"|"hardcoded_score"|"constant_outcome",
                   "file": str, "line": int, "snippet": str}]}

    - ``file`` 为相对 ``code_dir`` 的 POSIX 路径；``snippet`` 已过 ``mask_value``；
    - 目录不存在 → WARNING + 空结果（容忍，不炸——是否记 None 由调用方决定）；
    - 单文件解析失败 → WARNING + 跳过该文件，其余文件照常审计；
    - 输出按 (file, line, rule) 排序、按三元组去重 → 确定性（同输入同输出）。
    """
    root = Path(code_dir)
    if not root.is_dir():
        logger.warning("honesty_audit: code_dir 不存在或不是目录，跳过审计: %s", root)
        return {"clean": True, "hits": []}

    raw_hits: List[Dict[str, object]] = []
    for path in _iter_python_files(root):
        rel_file = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (OSError, UnicodeDecodeError, SyntaxError) as exc:
            logger.warning("honesty_audit: 源码解析失败，跳过 %s: %s", rel_file, exc)
            continue
        lines = text.splitlines()
        file_is_evaluator = _is_evaluator_name(path.stem)
        raw_hits.extend(_rule_answer_leakage(tree, rel_file, lines, file_is_evaluator))
        raw_hits.extend(_rule_hardcoded_score(tree, rel_file, lines))
        raw_hits.extend(
            _rule_constant_outcome(tree, rel_file, lines, file_is_evaluator)
        )

    for path in _iter_manifest_json_files(root):
        raw_hits.extend(_audit_manifest_json(path, path.relative_to(root).as_posix()))

    seen = set()
    hits: List[Dict[str, object]] = []
    for hit in sorted(raw_hits, key=lambda h: (h["file"], h["line"], h["rule"])):
        dedup_key = (hit["rule"], hit["file"], hit["line"])
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        hits.append(hit)

    return {"clean": not hits, "hits": hits}


def _is_excluded(path: Path, root: Path) -> bool:
    return any(
        part.lower() in _EXCLUDED_DIR_NAMES
        for part in path.relative_to(root).parts[:-1]
    )


def _iter_python_files(root: Path) -> List[Path]:
    return sorted(
        p for p in root.rglob("*.py") if p.is_file() and not _is_excluded(p, root)
    )


def _iter_manifest_json_files(root: Path) -> List[Path]:
    return sorted(
        p
        for p in root.rglob("*.json")
        if p.is_file()
        and not _is_excluded(p, root)
        and any(tok in p.name.lower() for tok in _MANIFEST_JSON_TOKENS)
    )
