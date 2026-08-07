# 测试执行报告 - p-s8-20-isolation-fix（测试隔离缺陷修复 + 全量回归）

- **日期**：2026-08-07 02:33（PDT）
- **执行人**：@测试工程师代理
- **Sprint**：sprint8（缺陷 `P-S8-20` 登记在 `docs/sprint8/dev-plan.md:2468`；**本报告 2026-08-07 03:07 由 sprint7 迁入，迁移始末见文末「归档位置」一节**）
- **触发原因**：`P-S8-20` —— 两处测试断言依赖**全局文件系统状态**（`/tmp/evil.py`），非 `tmp_path` 隔离；开发代理做 `CP-1a.4-6②` 验红时该用例真把文件写了出来，还原生产代码后**仍然红**，白排查一轮
- **commit**：`0e250fb`（改动未提交，留工作区）

---

## 1. 缺陷与修法

### 1.1 改前 / 改后

| 位置 | 改前 | 改后 |
|---|---|---|
| `tests/test_sprint3_b2.py::test_cp_b2_1_write_absolute_outside_rejected` | `tool.invoke({"path": "/tmp/evil.py", ...})` + `assert not Path("/tmp/evil.py").exists()` | 落点改为 `tmp_path / "outside" / "evil.py"`；断言 `success is False` + `"越界" in error` + `not outside.exists()` + `not outside.parent.exists()` |
| `tests/test_sprint3_b2_strengthen.py::test_bug_s1_02_error_json_parseable_by_extract` | 同款 `/tmp/evil.py`（无存在性断言） | 同上落点；并**补**一条 `assert not outside.exists()` |

两处均加了**前置自证**，把"这个落点确实在工作区之外"变成用例自己检查的事实，而不是靠读者相信路径字面量：

```python
ws, _code_dir = workspace          # ws 即被 patch 的 WORKSPACE_DIR
outside = tmp_path / "outside" / "evil.py"
assert not outside.is_relative_to(ws)
assert outside.is_absolute()
assert not outside.exists()
```

### 1.2 为什么这不算弱化

守的契约是「**工作区之外的绝对路径必须被拒**」。逐条对齐：

| 契约要素 | 旧写法 | 新写法 |
|---|---|---|
| 是绝对路径 | `/tmp/evil.py` 字面量 | `tmp_path/...` 天然绝对，且 `assert outside.is_absolute()` 显式钉住 |
| 在 WORKSPACE_DIR 之外 | 靠读者心算（fixture 把 `WORKSPACE_DIR` patch 到 `tmp_path/workspace`，`/tmp` 在其外） | `assert not outside.is_relative_to(ws)` **机器验证** |
| 必须被拒 | `success is False` + `"越界" in error` | 一字未动 |
| 一个字节都不许落盘 | `not Path("/tmp/evil.py").exists()` | `not outside.exists()` **外加** `not outside.parent.exists()`（连 `mkdir` 出父目录都不许） |

⇒ 断言项**只增不减**，判定强度不降；变的只是落点从"进程间共享的全局固定路径"换成"随用例隔离的绝对路径"。

⚠ 一处口径差异如实说明：旧写法的落点 `/tmp` 是**系统目录**，新落点在 `tmp_path`（实际也位于 `/tmp/pytest-of-*/` 下）。被测谓词 `_is_within_workspace` 走的是 `resolve() + is_relative_to(WORKSPACE_DIR.resolve())`，**对"是不是系统目录"无感知**——它只判包含关系。故这条差异不构成覆盖损失。

### 1.3 验红（证明断言仍然受力）

不改 `core/`。用一次性 pytest 插件（跑完即删，未落仓库）把守卫打成恒 True：

```python
def pytest_configure(config):
    from core.tools import code_fs_tools
    code_fs_tools._is_within_workspace = lambda target: True
    code_fs_tools._is_within_base = lambda target, base: True
```

| 阶段 | 命令 | 结果 |
|---|---|---|
| 绿 | `.venv/bin/pytest -q tests/test_sprint3_b2.py tests/test_sprint3_b2_strengthen.py -p no:randomly` | **41 passed** |
| 红 | 同上两条用例 + `-p redproof_plugin` | **2 failed**（两条均 `assert True is False`，卡在 `payload["success"] is False`） |
| 复绿 | 撤掉插件重跑同两条 | **2 passed** |
| **污染检查** | `ls /tmp/evil.py` | **No such file**（红态全程未污染全局路径） |

⇒ 这正是旧写法做不到的第三行：**验红之后不用手工 `rm` 就能复绿**。P-S8-20 记的「还原后必须一并 `rm -f /tmp/evil.py`」这条对后续批次的连带约束，随本次修复**作废**。

---

## 2. 执行范围

- 命令：
  1. `.venv/bin/pytest -q tests/test_sprint3_b2.py tests/test_sprint3_b2_strengthen.py -p no:randomly`
  2. `env PYTHONPATH=/tmp .venv/bin/pytest -q -p no:randomly -p redproof_plugin <两条用例>`（验红）
  3. `.venv/bin/pytest -q -m "not e2e and not browser"`（全量回归）
- 是否包含 e2e：**否**。本次全程零真跑、零外部调用、零配额消耗。

## 3. 结果摘要

| 口径 | 结果 |
|---|---|
| 定向（两文件） | **41 passed**（0.54s） |
| 验红态 | 2 failed（预期）→ 复绿 2 passed |
| **全量 `-m "not e2e and not browser"`** | **2642 passed / 0 failed / 25 skipped / 58 deselected / 7 xfailed**（63.88s，2026-08-07 02:33 PDT） |

⇒ 与交付基线 **2642 / 25 / 58 / 7 / 0 failed** **逐项精确对平**。本次只改既有用例内部写法、**未增删用例**，故总数不变即正确对平（若总数变动反而说明改错了）。

- 警告：3 条，全部为**库级预存**、与本次改动无关：① langgraph `LangChainPendingDeprecationWarning`（`allowed_objects` 默认值将变）；② / ③ `PydanticDeprecatedSince20`（`.schema()` 已弃用，命中 `tests/test_sprint6_b2.py:163` 等）。**长期存在，建议登记为独立清理项**（见 §5）。

## 4. 失败排查

无失败。

## 5. 后续动作

- P-S8-20 的 TODO 条目（`docs/TODO.md:1128`）可勾；连带约束「验红后须 `rm -f /tmp/evil.py`」随修复作废，`docs/sprint8/dev-plan.md:2468` 的 P-S8-20 登记行末建议由主控补一句"已由测试工程师 2026-08-07 修复"（该文件本次不在我方可写边界内，未动）。
- 建议新登记：**3 条库级 Deprecation 警告长期存在**（langgraph ×1 + Pydantic `.schema()` ×2），既不阻塞也无人认领，属"warning 麻木"风险。

## 6. 归档位置说明

**原文（2026-08-07 02:36 落盘时，一字未改）**：

> P-S8-20 登记在 `docs/sprint8/dev-plan.md:2468`，本报告理应归 `docs/sprint8/test-reports/`。但**该目录尚不存在**，且本次任务的文件边界只授权了 `docs/sprint7/test-reports/`（新报告）。⇒ 暂归档于此，**建议 sp8 建目录后由主控迁移或在 sp8 侧留指针**。不自作主张建 sp8 目录，是为避免与正在并行改 sp8 文档的两个代理撞车。

**处置（2026-08-07 03:07 @测试工程师代理，Maria 授权建目录并迁移）**：

- 新建 `docs/sprint8/test-reports/`，本报告**整体迁入**（内容未改，只在页首 Sprint 行补了迁移标注 + 本节补处置）。
- 原路径 `docs/sprint7/test-reports/2026-08-07_p-s8-20-isolation-fix.md` **留一份指针文件**指向此处——`docs/TODO.md` 曾引用旧路径，不让引用断链。
- 顺带说明 sp8 目录的新用途：本次同批新增 `docs/sprint8/test-reports/` 只放报告；真跑证据 bundle 走各 sprint 的 `test-reports/realrun-bundles/` 子目录（见 `docs/sprint7/test-reports/2026-08-07_realrun-evidence-fix.md`）。
