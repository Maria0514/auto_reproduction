# 测试执行报告 - f2-real-e2e-wiring（CP-F2-2 真实链路 e2e 代码落地 + mock smoke 自验装配）

- **日期**：2026-06-29
- **执行人**：@测试工程师代理
- **Sprint**：sprint3
- **触发原因**：落地 Sprint 3 任务 F2 的 CP-F2-2 —— 把 `tests/test_sprint3_e2e.py` 末尾占位骨架
  `TestRealChainSkeleton` 实现为 5 条正式真实链路 e2e（real-1~5，靶 HippoRAG arXiv:2405.14831）。
  **本次只写代码、绝不跑真实 e2e**（省 deepxiv 日配额 + LLM 凭证），真实补跑由主控统一 smoke + 补跑。
- **commit**：`119abbd`（落地前 HEAD；本次改动未 commit，待 Maria 统一）

## 本次落地内容

把 `TestRealChainSkeleton`（单个 placeholder skip）替换为 `TestRealChainE2E` 类，含 **5 条真实
链路 e2e（real-1~5，参数化展开共 7 个 test item）**，对应 dev-plan §667-672 五场景。

### 真实 / mock 边界（dev-plan §667 权威约定）

| 维度 | 真实 / mock | 说明 |
|---|---|---|
| LLM（intake/analysis/scout/planning/coding 各 ReAct + metrics 抽取） | **真实** | 真实 ChatOpenAI，凭证驱动 |
| deepxiv（read_section / get_paper_structure 读 HippoRAG） | **真实** | 走 deepxiv 日配额；HippoRAG 大概率已被 sp1/sp2 e2e 缓存 |
| `build_graph()` 主图 + SqliteSaver(WAL) 持久化 | **真实** | 沿用 sp2 c1_e2e 的 `_make_wal_saver` 范式 |
| execution 复合节点（错误分类 / B 档判定 / 修复循环边界 / interrupt#2） | **真实** | 节点逻辑全真跑 |
| reporting 三形态渲染 | **真实** | 报告真落盘 tmp（`workspace_dir` 指向 tmp_path，不污染真实 workspace） |
| interrupt#1（planning）/ interrupt#2（execution）resume | **真实 + Command 注入** | `Command(resume={"decision": ...})` |
| **sandbox 三入口**（`prepare_venv` / `run_in_venv` / `collect_artifacts`） | **mock** | §667「happy path B 档 = mock sandbox 返回 exit 0 + 可解析指标 → success=True」，**不真跑 30min venv 训练**，只模拟执行结果（exit code + stdout `<METRICS>`） |

### 5 条真实 e2e 各自边界

- **real-1 happy path B 档成功（AC-S3-01，首次真实端到端复现，smoke 首选）**：真实 LLM 跑
  intake→analysis→scout→planning(interrupt#1, `approve` resume)→coding(真实 ReAct 写代码)→
  execution(真实)→reporting(真实)→END；mock sandbox 注入 `exit 0 + <METRICS>{accuracy:0.893,f1:0.88}`
  → `execution_result.success=True` + metrics.accuracy==0.893 + sandbox prepare/run 各 1 次 + reporting
  full_success 真落盘 + 无 `__interrupt__` 残留。断言聚焦契约（不 hard-code deepxiv 返回的论文标题文本）。
- **real-2 修复循环上限 3（AC-S3-03）**：mock sandbox 连续注入可修复失败（`ModuleNotFoundError`）+
  真实 LLM coding 反复修复 → fix_loop_count 自增至 `MAX_FIX_LOOP_COUNT(3)` 拦截 → interrupt#2
  暂停（kind=`dev_loop_failure` + options 三态）+ fix_loop_history 满 3 条。
- **real-3 interrupt#2 三选一（AC-S3-07，参数化 3 态）**：真实链路用不可修复（CUDA OOM=hardware）
  一回合即 interrupt#2 → `Command(resume)` 三态：`terminate`→END(cancelled_by_user) /
  `revise_plan`→planning(fix_loop_count 清零，真实 planning 会再触发 interrupt#1) /
  `export_code`→reporting(degraded)。
- **real-4 code_only（AC-S3-06）**：planning interrupt#1 选 `code_only` → coding(真实) →
  skip_execution → reporting code_only；sandbox prepare 计数 == 0（execution 真未被触达的强旁证）+
  execution_result is None + 报告含「仅生成代码」。
- **real-5 降级（AC-S3-09 ③）**：可修复失败 + `graph.update_state` 把 retry_budget_remaining 压到
  `< DEV_LOOP_MIN_CALLS_PER_ROUND(2)` → execution 入口预算门直接降级 → reporting degraded（不回
  coding、不 interrupt，sandbox 仅 1 次）。

### fixture / 凭证 skip 装配方式（沿用 sp2 范式）

- 类级双 mark：`@pytest.mark.e2e` + `@skip_if_no_creds`（`pytest.mark.skipif(not _has_credentials(), reason=...)`）。
- `_has_credentials()` 读 `config.get_llm_api_key()` / `get_deepxiv_token()`（运行时求值，不写凭证值）。
- 凭证从 `tests/conftest.py` 自动加载 `.env`（项目根 > `~/.env`，override=False）——凭证缺失时整类 skip。
- `_make_real_llm_config()` 从 config getter 取 base_url/model/api_key（不硬编码）。
- `_make_wal_saver(db_path)` 真实 SqliteSaver(WAL)，每条用唯一 `uuid4` thread_id（`_new_e2e_config`）防串。
- `_run_to_planning_pause` helper：真实跑到 planning interrupt#1 暂停 + 断言 interrupt_kind=planning。

## 执行范围

- 命令 1（mock 8 条不受影响）：`.venv/bin/pytest tests/test_sprint3_e2e.py -m "not e2e" -v`
- 命令 2（真实 e2e collect-only 确认收集）：`.venv/bin/pytest tests/test_sprint3_e2e.py -m e2e --collect-only -q`
- 命令 3（sprint3 范围回归）：`.venv/bin/pytest tests/test_sprint3_e2e.py tests/test_sprint3_f1.py -m "not e2e" -q`
- 命令 4（skip 装配静态验证）：python 内联断言 `_has_credentials()` env 清空时为 False + 类上 `e2e`/`skipif` mark 齐全
- 命令 5（真实 e2e 图调用骨架 mock smoke）：python 内联用 mock 上游节点 + `_patch_sandbox_real` 跑 real-1/4/5 图调用骨架
- 是否包含真实 e2e 调用：**否**（绝不跑 `-m e2e` 真实链路，省 deepxiv 配额 + LLM 凭证）
- 凭证来源（仅记 env 变量名）：`LLM_API_KEY` + `DEEPXIV_TOKEN`（本环境 `.env` 已就绪——故 collect 时 skipif=False，真实 e2e 处「可跑待补跑」态）

## 结果摘要

- mock 8 条 e2e（`-m "not e2e"`）：**8 passed / 7 deselected**（真实 e2e 被正确 deselect），未被本次改动破坏
- 真实 e2e collect-only（`-m e2e`）：**7 个 test item 正确收集**（real-1/2/4/5 各 1 + real-3 参数化 3）
- sprint3 范围回归（e2e mock + f1）：**27 passed / 7 deselected**
- skip 装配静态验证：env 凭证清空时 `_has_credentials()=False`（skipif 条件成立 → 会 skip）；
  类上 `pytestmark` = `['skipif','e2e']`，skipif reason 正确
- 真实 e2e 图调用骨架 mock smoke：real-1（prepare=1,run=1,END,报告落盘）/ real-4（prepare=0 execution 跳过,END）/
  real-5（prepare=1 预算门降级,END,报告）全部按预期落点 —— 证明 `_patch_sandbox_real` patch 入口名正确 +
  `build_graph()` 图调用骨架可执行 + `FakeRunResult`/`FakePrepareResult` 与真实 sandbox 返回对象鸭子类型兼容
- 跳过：真实 e2e 7 项本次**未运行**（本次范围 = 只写代码不跑真实链路）
- 警告：1（`LangChainPendingDeprecationWarning`，langgraph `checkpoint/serde/encrypted.py:5` 库级预存，与本次改动无关）
- 总耗时：mock 回归 ~1s（mock 套件极快）

## 失败排查

无失败。

### 过程中的一处红线风险（已处置，非生产 BUG）

- **现象**：尝试用 `env -u LLM_API_KEY -u DEEPXIV_TOKEN HOME=/nonexistent .venv/bin/pytest -m e2e` 验 skip
  逻辑时，后台任务开始运行 `test_real_1`（已开始真实链路）。
- **根因**：`tests/conftest.py` 会 `load_dotenv(PROJECT_ROOT / ".env", override=False)`，把被 `-u` 清空的
  凭证从项目根 `.env` 文件重新读回 → `_has_credentials()=True` → skipif 不生效 → 真实 e2e 真跑。
- **处置**：立即 `pkill -f test_sprint3_e2e.py` 终止（仅触达 paper_intake 起步阶段，未完成完整真实链路）。
  改用**不依赖 .env 加载**的安全方式验 skip 装配：python 内联在进程内 `os.environ.pop` 凭证后断言
  `_has_credentials()=False` + 类上 mark 装配 + 用 mock 上游节点跑真实 e2e 的图调用骨架。
- **结论**：非生产 BUG，纯测试验证方法选择问题；已避免进一步真实调用。**主控补跑须注意**：本环境 `.env`
  含真实凭证，凭证缺失场景的 skip 验证不能用 `-u` 清 env（会被 conftest 从 `.env` 读回），需临时移走 `.env`
  或在主控自身环境（无 `.env`）验。

## real-1 作为 smoke 首选的精确入口

```
.venv/bin/pytest "tests/test_sprint3_e2e.py::TestRealChainE2E::test_real_1_happy_path_b_grade_success" -m e2e -v -s
```
理由：单条真实链路即可 fail-fast 验 LLM 凭证有效 + deepxiv 可达 + 全链路装配正确，最省 deepxiv 配额
（HippoRAG 大概率已缓存，只读已缓存章节 + mock sandbox 不真跑训练）。

## 待主控补跑清单（CP-F2-2 / CP-F2-3）

凭证 + deepxiv 日配额就绪后，由主控统一 smoke + 补跑（沿用 sp2 `c7a9c4b`/`3ed97d3` 补跑范式）：

1. **smoke 首选**：先单跑 real-1（上方精确入口），fail-fast 验凭证有效 + deepxiv 可达 + 装配正确。
2. **全量真实 e2e**：`.venv/bin/pytest tests/test_sprint3_e2e.py -m e2e -v -s`（7 个 item：real-1/2/4/5 + real-3×3）。
3. **复跑达标（dev-plan §674）**：real-2（修复循环）/ real-3（interrupt#2）属 LLM 服从度 + 重跑幂等类风险，
   复现率视实测 —— 复现率高（≥50%）连跑 3 次全绿，复现率低（10%~50%）连跑 5 次全绿且含全量回归。
4. **归档 CP-F2-3**：补跑后落盘 e2e 报告到本目录（含跑数 / 耗时 / token 观测 / 复现率）。
5. 补跑全绿后才把 dev-plan 的 **CP-F2-2 / CP-F2-3** 由 `[ ]` 改为 `[x]`（本次不勾，仅标「代码就绪待补跑」）。

## 风险与偏差

- **风险 1（LLM 服从度）**：real-2/real-3 依赖真实 coding ReAct 在「可修复失败反馈」下的服从行为。real-3 用
  **不可修复（CUDA OOM）一回合即 interrupt#2** 规避了「真实 coding 是否真能反复触发可修复失败」的不确定性，
  最省真实 coding 调用；real-2 仍需真实 coding 在 3 个修复回合内持续触发失败（mock sandbox 固定返回失败 →
  无论 coding 写啥都失败，可控性较高，但每回合真实 coding LLM 调用消耗预算）。
- **风险 2（real-3 revise_plan 真实回流）**：revise_plan 真实回 planning 会再触发真实 planning 节点
  interrupt#1，本测试只断言「路由回到 planning + fix_loop_count 清零」，不再二次 approve（省真实链路开销）。
  若真实 planning 行为与预期不符，主控补跑时此条可能需微调断言（已在 docstring 注明）。
- **风险 3（deepxiv 配额）**：7 个真实 e2e 均读 HippoRAG，若缓存失效会触发多次 read_section 配额消耗；
  建议主控补跑前确认 HippoRAG 已缓存或预热一次。
- **偏差**：无文档硬偏差。dev-plan §667 的「mock sandbox」边界已严格遵守（真实 e2e 不真跑训练）。

## 后续动作

- 主控补跑 CP-F2-2 / CP-F2-3（见上方清单），补跑全绿后回填 dev-plan 勾选 + 本目录归档 e2e 报告。
- F3（Prompt Cache 守门 + handoff）待 F2 补跑后启动。
