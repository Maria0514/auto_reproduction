"""C2 - reporting 节点自测（Sprint 3，S3-05）。

覆盖 dev-plan.md 任务 C2 的 6 个自测检查点（CP-C2-1 ~ CP-C2-6），全部 mock state
单测，无 LLM、无 interrupt。

- CP-C2-1 可导入；reporting 为 callable，inspect.signature 形参为 (state)
- CP-C2-2 full_success（execution_result.success=True）→ 报告含指标对比表
  （baseline vs 复现值）+ artifact 清单 + 成功结论；report_path 非空且文件真写出
- CP-C2-3 code_only（execution_mode==CODE_ONLY）→ 含代码位置 + deliverables，标注
  "仅生成代码"，无指标章节；execution_result is None 时仍产有效报告
- CP-C2-4 degraded（success=False / export_code）→ 标 degraded，含降级原因 +
  node_errors 摘要（解析 [error_category=...]）+ fix_loop_history 修复历程 + 保留代码
- CP-C2-5 reporting 不写任何 list 字段（返回 dict 键集合精确 = {report_path, current_step}）
- CP-C2-6 report_path 经 resolve()+is_relative_to(WORKSPACE_DIR) 校验，落在 workspace 下
  （构造越界场景断言被限定）

补充：三形态判定优先级边界 + 指标对比表"仅对比不硬判定"。
"""
from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.state import ExecutionMode  # noqa: E402

# 用 importlib 拿真实子模块对象——core/nodes/__init__.py 把同名 callable 注册为包属性，
# 会遮蔽子模块引用（sp1 教训，CLAUDE 指令第 6 条）。
reporting_module = importlib.import_module("core.nodes.reporting")

from core.nodes.reporting import (  # noqa: E402
    NODE_NAME,
    _determine_report_form,
    reporting,
)


# ----------------------------- fixtures / helpers -----------------------------


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """把 reporting 模块内的 WORKSPACE_DIR 指向临时目录，避免污染真实 workspace。"""
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(reporting_module, "WORKSPACE_DIR", ws)
    return ws


def _base_state(workspace: Path, arxiv_id: str = "2401.00001") -> Dict[str, Any]:
    """构造一个最小可用 state；code_output_dir 落 workspace/<arxiv_id>/code（与 C1 对齐）。"""
    code_dir = workspace / arxiv_id / "code"
    code_dir.mkdir(parents=True, exist_ok=True)
    return {
        "workspace_dir": str(workspace),
        "code_output_dir": str(code_dir.resolve()),
        "execution_mode": ExecutionMode.FULL,
        "paper_meta": {"arxiv_id": arxiv_id, "title": "A Great Paper"},
        "paper_analysis": {"baseline_results": {}, "metrics": []},
        "reproduction_plan": {"expected_results": {}, "deliverables": []},
        "execution_result": None,
        "node_errors": [],
        "degraded_nodes": [],
        "fix_loop_history": [],
        "fix_loop_count": 0,
        "user_fix_decision": None,
    }


# ----------------------------- CP-C2-1 -----------------------------


def test_cp_c2_1_importable_signature():
    """reporting 可导入；签名为 (state) -> dict。"""
    assert callable(reporting)
    params = list(inspect.signature(reporting).parameters.keys())
    assert params == ["state"], params


# ----------------------------- CP-C2-2 -----------------------------


def test_cp_c2_2_full_success(workspace):
    """full_success：指标对比表 + artifact 清单 + 成功结论；文件真写出。"""
    state = _base_state(workspace)
    state["paper_analysis"] = {
        "baseline_results": {"accuracy": 0.91, "f1": 0.88},
        "metrics": ["accuracy", "f1"],
    }
    state["reproduction_plan"] = {
        "expected_results": {"accuracy": 0.90},
        "deliverables": ["train.py"],
    }
    state["execution_result"] = {
        "success": True,
        "metrics": {"accuracy": 0.893, "f1": 0.86},
        "logs": "...",
        "errors": [],
        "artifacts": [str(workspace / "2401.00001" / "code" / "model.pt")],
        "runtime_seconds": 123.4,
        "environment_info": {"python_version": "3.11"},
    }

    assert _determine_report_form(state) == "full_success"
    out = reporting(state)

    report_path = out["report_path"]
    assert report_path
    md = Path(report_path).read_text(encoding="utf-8")
    # 成功结论
    assert "复现成功" in md
    # 指标对比表：列头 + baseline 值 + 复现值
    assert "指标对比" in md
    assert "论文 baseline" in md and "本次复现值" in md
    assert "accuracy" in md
    assert "0.91" in md   # baseline
    assert "0.893" in md  # 复现值
    # artifact 清单
    assert "model.pt" in md
    assert "产物清单" in md
    # 执行概况
    assert "执行概况" in md
    assert "123.4" in md


def test_cp_c2_2_full_success_compare_not_judge(workspace):
    """指标对比表仅对比、不硬判定：不出现"达标/不达标"硬结论。"""
    state = _base_state(workspace)
    state["paper_analysis"] = {"baseline_results": {"acc": 0.9}, "metrics": ["acc"]}
    state["execution_result"] = {
        "success": True,
        "metrics": {"acc": 0.5},  # 明显低于 baseline，但不应被判"不达标"
        "logs": "",
        "errors": [],
        "artifacts": [],
        "runtime_seconds": 1.0,
        "environment_info": {},
    }
    out = reporting(state)
    md = Path(out["report_path"]).read_text(encoding="utf-8")
    assert "达标" not in md
    assert "不达标" not in md
    assert "未达标" not in md


# ----------------------------- CP-C2-3 -----------------------------


def test_cp_c2_3_code_only(workspace):
    """code_only：代码位置 + deliverables，标注"仅生成代码"，无指标章节。"""
    state = _base_state(workspace)
    state["execution_mode"] = ExecutionMode.CODE_ONLY
    state["reproduction_plan"] = {
        "expected_results": {},
        "deliverables": ["train.py", "README.md"],
    }
    state["execution_result"] = None

    assert _determine_report_form(state) == "code_only"
    out = reporting(state)
    md = Path(out["report_path"]).read_text(encoding="utf-8")
    assert "仅生成代码" in md
    assert "代码位置" in md
    assert state["code_output_dir"] in md
    # deliverables
    assert "train.py" in md and "README.md" in md
    assert "交付物清单" in md or "Deliverables" in md
    # 无指标章节
    assert "指标对比" not in md
    assert "本次复现值" not in md


def test_cp_c2_3_code_only_exec_result_none_still_valid(workspace):
    """code_only 且 execution_result is None 时仍产有效报告（边界）。"""
    state = _base_state(workspace)
    state["execution_mode"] = ExecutionMode.CODE_ONLY
    state["execution_result"] = None
    out = reporting(state)
    md = Path(out["report_path"]).read_text(encoding="utf-8")
    assert md.strip()  # 非空有效
    assert "仅生成代码" in md


# ----------------------------- CP-C2-4 -----------------------------


def test_cp_c2_4_degraded(workspace):
    """degraded：降级原因 + node_errors 摘要(解析 category) + 修复历程 + 保留代码。"""
    state = _base_state(workspace)
    state["execution_result"] = {
        "success": False,
        "metrics": {},
        "logs": "Traceback ...",
        "errors": ["[error_category=runtime] 运行时异常"],
        "artifacts": [str(workspace / "2401.00001" / "code" / "partial.log")],
        "runtime_seconds": 5.0,
        "environment_info": {},
    }
    state["degraded_nodes"] = ["execution"]
    state["node_errors"] = [
        {
            "node_name": "execution",
            "error_type": "transient",
            "error_message": "[error_category=runtime] 运行时异常",
            "error_detail": None,
            "timestamp": "2026-06-25T00:00:00",
            "retry_count": 0,
            "resolved": False,
        }
    ]
    state["fix_loop_history"] = [
        {
            "round_number": 1,
            "error_summary": "import 错误",
            "error_category": "import",
            "fix_strategy": "补依赖",
            "timestamp": "2026-06-25T00:00:01",
        },
        {
            "round_number": 2,
            "error_summary": "运行时异常",
            "error_category": "runtime",
            "fix_strategy": "修正张量维度",
            "timestamp": "2026-06-25T00:00:02",
        },
    ]
    state["fix_loop_count"] = 2
    state["user_fix_decision"] = "export_code"

    assert _determine_report_form(state) == "degraded"
    out = reporting(state)
    md = Path(out["report_path"]).read_text(encoding="utf-8")
    # 降级结论 + 原因
    assert "未成功复现" in md
    assert "降级原因" in md
    assert "execution" in md
    # node_errors 摘要解析出 error_category
    assert "节点错误摘要" in md
    assert "runtime" in md
    # fix_loop_history 修复历程：两轮 + 策略
    assert "修复历程" in md
    assert "import" in md and "补依赖" in md
    assert "修正张量维度" in md
    # user_fix_decision
    assert "export_code" in md
    # 保留代码
    assert state["code_output_dir"] in md


def test_cp_c2_4_degraded_export_code_with_metrics_none(workspace):
    """export_code 场景（success=False，仍走 degraded）。"""
    state = _base_state(workspace)
    state["execution_result"] = {
        "success": False,
        "metrics": {},
        "logs": "",
        "errors": ["[error_category=data_missing] 数据集缺失需人工下载"],
        "artifacts": [],
        "runtime_seconds": 0.0,
        "environment_info": {},
    }
    state["user_fix_decision"] = "export_code"
    assert _determine_report_form(state) == "degraded"
    out = reporting(state)
    md = Path(out["report_path"]).read_text(encoding="utf-8")
    assert "data_missing" in md


# ----------------------------- CP-C2-5 (红线) -----------------------------


def test_cp_c2_5_pure_read_only_keys(workspace):
    """reporting 纯读：返回 dict 键集合精确 = {report_path, current_step}。"""
    for mode_setup in (
        # full_success
        lambda s: s.update(execution_result={
            "success": True, "metrics": {"a": 1}, "logs": "", "errors": [],
            "artifacts": [], "runtime_seconds": 1.0, "environment_info": {},
        }),
        # code_only
        lambda s: s.update(execution_mode=ExecutionMode.CODE_ONLY),
        # degraded
        lambda s: s.update(execution_result={
            "success": False, "metrics": {}, "logs": "", "errors": ["x"],
            "artifacts": [], "runtime_seconds": 0.0, "environment_info": {},
        }),
    ):
        state = _base_state(workspace)
        mode_setup(state)
        out = reporting(state)
        assert set(out.keys()) == {"report_path", "current_step"}, out.keys()
        assert out["current_step"] == NODE_NAME
        # 绝不返回任何 list 字段
        for forbidden in ("node_errors", "degraded_nodes", "fix_loop_history"):
            assert forbidden not in out


def test_cp_c2_5_does_not_mutate_state_lists(workspace):
    """reporting 不修改 state 里的 list 字段（纯读，原对象不被改）。"""
    state = _base_state(workspace)
    state["node_errors"] = [{"node_name": "execution", "error_type": "transient",
                             "error_message": "[error_category=runtime] x",
                             "error_detail": None, "timestamp": "t",
                             "retry_count": 0, "resolved": False}]
    state["degraded_nodes"] = ["execution"]
    state["fix_loop_history"] = [{"round_number": 1, "error_summary": "s",
                                  "error_category": "runtime", "fix_strategy": "f",
                                  "timestamp": "t"}]
    state["execution_result"] = {
        "success": False, "metrics": {}, "logs": "", "errors": ["x"],
        "artifacts": [], "runtime_seconds": 0.0, "environment_info": {},
    }
    ne_before = list(state["node_errors"])
    dn_before = list(state["degraded_nodes"])
    fh_before = list(state["fix_loop_history"])

    reporting(state)

    assert state["node_errors"] == ne_before
    assert state["degraded_nodes"] == dn_before
    assert state["fix_loop_history"] == fh_before


# ----------------------------- CP-C2-6 (路径校验) -----------------------------


def test_cp_c2_6_report_path_within_workspace(workspace):
    """report_path 落在 workspace 下，与 code_output_dir 同父目录。"""
    state = _base_state(workspace)
    state["execution_result"] = {
        "success": True, "metrics": {"a": 1}, "logs": "", "errors": [],
        "artifacts": [], "runtime_seconds": 1.0, "environment_info": {},
    }
    out = reporting(state)
    report_path = Path(out["report_path"]).resolve()
    ws_resolved = workspace.resolve()
    assert report_path == ws_resolved or report_path.is_relative_to(ws_resolved)
    # 与 code_output_dir 同父目录（report.md 与 code/ 同级）
    assert report_path == (Path(state["code_output_dir"]).parent / "report.md").resolve()
    assert report_path.exists()


def test_cp_c2_6_out_of_workspace_code_dir_clamped(workspace, tmp_path):
    """code_output_dir 被构造到 workspace 外时，report_path 被限定回 workspace 下。"""
    outside = tmp_path / "outside" / "evil" / "code"
    outside.mkdir(parents=True, exist_ok=True)
    state = _base_state(workspace)
    state["code_output_dir"] = str(outside.resolve())  # 越界
    out = reporting(state)
    report_path = Path(out["report_path"]).resolve()
    ws_resolved = workspace.resolve()
    # 必须被限定回 workspace 下（不在 outside 下写）
    assert report_path.is_relative_to(ws_resolved)
    assert not report_path.is_relative_to((tmp_path / "outside").resolve())


def test_cp_c2_6_no_code_dir_fallback_arxiv(workspace):
    """code_output_dir 缺失时回退用 arxiv_id 拼 workspace/<arxiv_id>/report.md。"""
    state = _base_state(workspace)
    state["code_output_dir"] = None
    out = reporting(state)
    report_path = Path(out["report_path"]).resolve()
    expected = (workspace / "2401.00001" / "report.md").resolve()
    assert report_path == expected
    assert report_path.is_relative_to(workspace.resolve())


# ----------------------------- 三形态判定优先级边界 -----------------------------


def test_form_priority_code_only_over_success(workspace):
    """code_only 优先级最高：即便 execution_result.success=True 仍走 code_only。"""
    state = _base_state(workspace)
    state["execution_mode"] = ExecutionMode.CODE_ONLY
    state["execution_result"] = {
        "success": True, "metrics": {"a": 1}, "logs": "", "errors": [],
        "artifacts": [], "runtime_seconds": 1.0, "environment_info": {},
    }
    assert _determine_report_form(state) == "code_only"


def test_form_exec_result_none_non_code_only_is_degraded(workspace):
    """execution_result is None 且非 code_only → degraded。"""
    state = _base_state(workspace)
    state["execution_mode"] = ExecutionMode.FULL
    state["execution_result"] = None
    assert _determine_report_form(state) == "degraded"


def test_form_code_only_str_value(workspace):
    """execution_mode 为字符串 "code_only" 也能识别（兼容 Enum/str）。"""
    state = _base_state(workspace)
    state["execution_mode"] = "code_only"
    assert _determine_report_form(state) == "code_only"


# ============================================================================
# 以下为测试工程师独立验收补强用例（@测试工程师代理，2026-06-25）
# 覆盖：三形态优先级矩阵深化 / 仅对比不判定加严 / node_errors 解析正确性 /
#       fix_loop_history 逐轮渲染 / 缺字段健壮性 / 越界绝不写外部 / 路径对齐推导
# ============================================================================


# ----------------------------- 三形态优先级矩阵深化 -----------------------------


def test_form_code_only_enum_with_success_true(workspace):
    """优先级矩阵①：Enum CODE_ONLY + success=True → 仍 code_only（不被 success 抢走）。"""
    state = _base_state(workspace)
    state["execution_mode"] = ExecutionMode.CODE_ONLY
    state["execution_result"] = {"success": True, "metrics": {"a": 1}}
    assert _determine_report_form(state) == "code_only"


def test_form_code_only_str_with_success_true(workspace):
    """优先级矩阵①bis：str "code_only" + success=True → 仍 code_only。"""
    state = _base_state(workspace)
    state["execution_mode"] = "code_only"
    state["execution_result"] = {"success": True, "metrics": {"a": 1}}
    assert _determine_report_form(state) == "code_only"


def test_form_export_code_exec_none_non_code_only_is_degraded(workspace):
    """优先级矩阵②：execution_result is None + export_code + 非 code_only → degraded。"""
    state = _base_state(workspace)
    state["execution_mode"] = ExecutionMode.FULL
    state["execution_result"] = None
    state["user_fix_decision"] = "export_code"
    assert _determine_report_form(state) == "degraded"


def test_form_success_false_is_degraded(workspace):
    """优先级矩阵③：success 显式 False → degraded（不被误判 full_success）。"""
    state = _base_state(workspace)
    state["execution_mode"] = ExecutionMode.FULL
    state["execution_result"] = {"success": False, "metrics": {}}
    assert _determine_report_form(state) == "degraded"


def test_form_success_missing_key_is_degraded(workspace):
    """边界：execution_result 缺 success 键（非 True）→ degraded（success is True 严格判定）。"""
    state = _base_state(workspace)
    state["execution_mode"] = ExecutionMode.FULL
    state["execution_result"] = {"metrics": {"a": 1}}  # 无 success 键
    assert _determine_report_form(state) == "degraded"


def test_form_execution_mode_full_enum_explicit(workspace):
    """边界：execution_mode=ExecutionMode.FULL 不被 _is_code_only 误判。"""
    state = _base_state(workspace)
    state["execution_mode"] = ExecutionMode.FULL
    assert _determine_report_form(state) == "degraded"  # exec_result=None


# ----------------------------- 指标对比表"仅对比不判定"加严 -----------------------------


def test_full_success_no_hard_verdict_wording(workspace):
    """加严：复现值远高/远低于 baseline 时都不得出现硬判定措辞。"""
    state = _base_state(workspace)
    state["paper_analysis"] = {"baseline_results": {"acc": 0.9}, "metrics": ["acc"]}
    state["reproduction_plan"] = {"expected_results": {"acc": 0.92}, "deliverables": []}
    state["execution_result"] = {
        "success": True, "metrics": {"acc": 0.99},  # 远高于 baseline
        "logs": "", "errors": [], "artifacts": [],
        "runtime_seconds": 1.0, "environment_info": {},
    }
    md = Path(reporting(state)["report_path"]).read_text(encoding="utf-8")
    for forbidden in ("达标", "未达标", "不达标", "成功复现达到", "复现达到 baseline",
                      "超过 baseline", "通过验证", "PASS", "FAIL"):
        assert forbidden not in md, f"出现硬判定措辞: {forbidden}"
    # 仍应并列三列数值
    assert "0.9" in md and "0.92" in md and "0.99" in md


def test_full_success_metrics_three_columns_present(workspace):
    """对比表必须并列 baseline / expected / 复现值 三个来源。"""
    state = _base_state(workspace)
    state["paper_analysis"] = {"baseline_results": {"f1": 0.8}}
    state["reproduction_plan"] = {"expected_results": {"f1": 0.82}, "deliverables": []}
    state["execution_result"] = {
        "success": True, "metrics": {"f1": 0.79}, "logs": "", "errors": [],
        "artifacts": [], "runtime_seconds": 1.0, "environment_info": {},
    }
    md = Path(reporting(state)["report_path"]).read_text(encoding="utf-8")
    assert "论文 baseline" in md and "计划 expected" in md and "本次复现值" in md
    # 三个不同来源的值都渲染出来（baseline 独有键也并列）
    assert "0.8" in md and "0.82" in md and "0.79" in md


def test_full_success_empty_metrics_no_crash(workspace):
    """边界：success=True 但 baseline/expected/metrics 全空 → 报告产出且提示无可对比。"""
    state = _base_state(workspace)
    state["paper_analysis"] = {"baseline_results": {}}
    state["reproduction_plan"] = {"expected_results": {}, "deliverables": []}
    state["execution_result"] = {
        "success": True, "metrics": {}, "logs": "", "errors": [],
        "artifacts": [], "runtime_seconds": 1.0, "environment_info": {},
    }
    md = Path(reporting(state)["report_path"]).read_text(encoding="utf-8")
    assert "复现成功" in md
    assert "无可对比指标" in md


# ----------------------------- node_errors error_category 解析正确性 -----------------------------


def test_degraded_node_errors_category_parsed_into_column(workspace):
    """node_errors 的 [error_category=import] 前缀被解析进独立的 category 列。"""
    state = _base_state(workspace)
    state["execution_result"] = {
        "success": False, "metrics": {}, "logs": "", "errors": [],
        "artifacts": [], "runtime_seconds": 0.0, "environment_info": {},
    }
    state["node_errors"] = [
        {"node_name": "coding", "error_type": "transient",
         "error_message": "[error_category=import] ModuleNotFoundError: no module named torch",
         "error_detail": None, "timestamp": "t", "retry_count": 1, "resolved": False},
        {"node_name": "execution", "error_type": "permanent",
         "error_message": "[error_category=data_missing] dataset not found",
         "error_detail": None, "timestamp": "t", "retry_count": 0, "resolved": False},
    ]
    md = Path(reporting(state)["report_path"]).read_text(encoding="utf-8")
    # 解析出 category 关键词
    assert "import" in md and "data_missing" in md
    # 节点名与 error_type 同时渲染
    assert "coding" in md and "execution" in md
    assert "transient" in md and "permanent" in md
    # 原始 message 也保留（摘要列）
    assert "ModuleNotFoundError" in md


def test_degraded_node_errors_no_category_prefix_renders_dash(workspace):
    """node_errors 无 [error_category=] 前缀时 category 列渲染占位符，不报错。"""
    state = _base_state(workspace)
    state["execution_result"] = {
        "success": False, "metrics": {}, "logs": "", "errors": [],
        "artifacts": [], "runtime_seconds": 0.0, "environment_info": {},
    }
    state["node_errors"] = [
        {"node_name": "coding", "error_type": "degraded",
         "error_message": "纯文本错误无前缀", "error_detail": None,
         "timestamp": "t", "retry_count": 0, "resolved": False},
    ]
    md = Path(reporting(state)["report_path"]).read_text(encoding="utf-8")
    assert "节点错误摘要" in md
    assert "纯文本错误无前缀" in md


def test_degraded_empty_node_errors_renders_no_record(workspace):
    """degraded 但 node_errors 为空 → 摘要章节提示无记录，不报错。"""
    state = _base_state(workspace)
    state["execution_result"] = {"success": False, "metrics": {}, "logs": "",
                                 "errors": [], "artifacts": [], "runtime_seconds": 0.0,
                                 "environment_info": {}}
    state["node_errors"] = []
    md = Path(reporting(state)["report_path"]).read_text(encoding="utf-8")
    assert "节点错误摘要" in md


# ----------------------------- fix_loop_history 逐轮渲染 -----------------------------


def test_degraded_fix_loop_history_renders_each_round_in_order(workspace):
    """fix_loop_history 三轮逐轮渲染：轮次/分类/摘要/策略均出现且行数正确。"""
    state = _base_state(workspace)
    state["execution_result"] = {"success": False, "metrics": {}, "logs": "",
                                 "errors": [], "artifacts": [], "runtime_seconds": 0.0,
                                 "environment_info": {}}
    state["fix_loop_history"] = [
        {"round_number": 1, "error_summary": "缺依赖A", "error_category": "dependency",
         "fix_strategy": "pip install A", "timestamp": "t1"},
        {"round_number": 2, "error_summary": "语法错B", "error_category": "syntax",
         "fix_strategy": "修正缩进", "timestamp": "t2"},
        {"round_number": 3, "error_summary": "路径错C", "error_category": "path",
         "fix_strategy": "改相对路径", "timestamp": "t3"},
    ]
    state["fix_loop_count"] = 3
    md = Path(reporting(state)["report_path"]).read_text(encoding="utf-8")
    assert "修复历程" in md
    # 三轮每一项的关键字段都渲染
    for kw in ("缺依赖A", "dependency", "pip install A",
               "语法错B", "syntax", "修正缩进",
               "路径错C", "path", "改相对路径"):
        assert kw in md, f"缺失修复历程字段: {kw}"
    # 渲染了 3 轮（统计表格数据行 = 表头+分隔+3 数据行）
    history_section = md.split("修复历程")[1]
    data_rows = [ln for ln in history_section.splitlines()
                 if ln.startswith("|") and "---" not in ln]
    # 表头 1 行 + 3 数据行
    assert len(data_rows) == 4, data_rows
    # fix_loop_count 数值呈现
    assert "3" in md


def test_degraded_empty_fix_history_shows_count_only(workspace):
    """fix_loop_history 为空但 fix_loop_count>0 → 显示计数 + 无逐轮记录提示。"""
    state = _base_state(workspace)
    state["execution_result"] = {"success": False, "metrics": {}, "logs": "",
                                 "errors": [], "artifacts": [], "runtime_seconds": 0.0,
                                 "environment_info": {}}
    state["fix_loop_history"] = []
    state["fix_loop_count"] = 2
    md = Path(reporting(state)["report_path"]).read_text(encoding="utf-8")
    assert "修复历程" in md
    assert "2" in md  # fix_loop_count


# ----------------------------- 缺字段健壮性（reporting 是终点消费者，必须容错）-----------------------------


def test_robust_all_optional_fields_missing_degraded(workspace):
    """极简 state（仅 workspace_dir + execution_mode）→ degraded 不抛异常、产有效报告。"""
    state = {
        "workspace_dir": str(workspace),
        "execution_mode": ExecutionMode.FULL,
    }
    out = reporting(state)
    md = Path(out["report_path"]).read_text(encoding="utf-8")
    assert md.strip()
    assert "未成功复现" in md  # 走 degraded（exec_result 缺失）
    assert set(out.keys()) == {"report_path", "current_step"}


def test_robust_paper_analysis_and_plan_none_full_success(workspace):
    """full_success 下 paper_analysis / reproduction_plan 为 None → 不抛异常。"""
    state = _base_state(workspace)
    state["paper_analysis"] = None
    state["reproduction_plan"] = None
    state["execution_result"] = {
        "success": True, "metrics": {"acc": 0.5}, "logs": "", "errors": [],
        "artifacts": [], "runtime_seconds": 1.0, "environment_info": {},
    }
    md = Path(reporting(state)["report_path"]).read_text(encoding="utf-8")
    assert "复现成功" in md
    assert "0.5" in md  # 复现值仍渲染（baseline/expected 缺失列为占位）


def test_robust_fix_history_none_degraded(workspace):
    """degraded 下 fix_loop_history / node_errors / degraded_nodes 为 None → 不抛异常。"""
    state = _base_state(workspace)
    state["execution_result"] = {"success": False, "metrics": {}, "logs": "",
                                 "errors": [], "artifacts": [], "runtime_seconds": 0.0,
                                 "environment_info": {}}
    state["fix_loop_history"] = None
    state["node_errors"] = None
    state["degraded_nodes"] = None
    md = Path(reporting(state)["report_path"]).read_text(encoding="utf-8")
    assert "未成功复现" in md


def test_robust_code_only_plan_none(workspace):
    """code_only 下 reproduction_plan 为 None → 交付物清单提示无、不抛异常。"""
    state = _base_state(workspace)
    state["execution_mode"] = ExecutionMode.CODE_ONLY
    state["reproduction_plan"] = None
    state["execution_result"] = None
    md = Path(reporting(state)["report_path"]).read_text(encoding="utf-8")
    assert "仅生成代码" in md
    assert "交付物清单" in md or "Deliverables" in md


def test_robust_paper_meta_none_header_fallback(workspace):
    """paper_meta 为 None → 报告头部回退默认标题，不抛异常。"""
    state = _base_state(workspace)
    state["paper_meta"] = None
    state["execution_result"] = None  # degraded
    md = Path(reporting(state)["report_path"]).read_text(encoding="utf-8")
    assert "论文复现报告" in md


# ----------------------------- 越界绝不写外部（红线加严）-----------------------------


def test_out_of_workspace_never_writes_outside(workspace, tmp_path):
    """越界 code_output_dir：不仅退回 workspace，且 outside 目录下绝不出现 report.md。"""
    outside = tmp_path / "outside" / "evil" / "code"
    outside.mkdir(parents=True, exist_ok=True)
    state = _base_state(workspace)
    state["code_output_dir"] = str(outside.resolve())
    out = reporting(state)
    report_path = Path(out["report_path"]).resolve()
    # 退回 workspace 下
    assert report_path.is_relative_to(workspace.resolve())
    # outside 父目录下不得有任何 report.md
    evil_report = outside.parent / "report.md"
    assert not evil_report.exists()
    assert not (outside / "report.md").exists()
    # 实际写出的文件确实在 workspace 内
    assert report_path.exists()
    assert report_path.read_text(encoding="utf-8").strip()


def test_path_dotdot_traversal_clamped(workspace):
    """code_output_dir 含 ../ 逃逸到 workspace 外时被限定回 workspace。"""
    escape = workspace / ".." / "escaped" / "code"
    state = _base_state(workspace)
    state["code_output_dir"] = str(escape)
    out = reporting(state)
    report_path = Path(out["report_path"]).resolve()
    assert report_path.is_relative_to(workspace.resolve())


def test_report_path_parent_alignment_with_coding(workspace):
    """report_path 推导 = Path(code_output_dir).parent / report.md（与 C1 同父目录对齐）。"""
    state = _base_state(workspace, arxiv_id="2405.14831")
    state["execution_result"] = None  # degraded 也走同一路径推导
    out = reporting(state)
    report_path = Path(out["report_path"]).resolve()
    expected = (Path(state["code_output_dir"]).parent / "report.md").resolve()
    assert report_path == expected
    # 与 coding 落点 workspace/<arxiv_id>/code 的父级一致
    assert report_path == (workspace / "2405.14831" / "report.md").resolve()
