"""仓库质量评分口径的单一事实源（Q-S2-09：planning 与 resource_scout 同口径）。

只放「评分 prompt 段落 + 权重」这一份共享常量，不放节点业务逻辑，避免
planning <-> resource_scout 互相 import 形成环依赖（两节点都只依赖本模块）。

落地约束（架构 §2.13.3）：
    - module-level 静态常量，**禁止动态拼接**（Prompt Cache 字节冻结约束，§4.5 / §4.8）；
    - planning 与 resource_scout 的评分 system prompt 段落均引用本常量，保证口径
      字节级一致（AC-S2-22 可比较性的根本保证）。
"""

# 同口径质量评分段落。**module-level 静态常量，禁止动态拼接**
# （Prompt Cache 字节冻结约束，§4.5 / §4.8）。
REPO_QUALITY_SCORING_SECTION = """【质量评分（你给每个克隆成功的仓库打 0.0~1.0 分，写入 RepoInfo.quality_score）】
权重建议（最终自由判断）：
- is_official（owner 与 paper_meta.authors 重叠则判定 True）-- 权重 0.35
- last_commit_date（近半年；为 None 表示读不到数据，按缺失处理不加分，勿当最旧）-- 权重 0.20
- commit_count_recent（>=10 加分；为 None 表示读不到数据，用 is None 判缺失，勿当 0 活跃）-- 权重 0.15
- has_readme + has_requirements -- 权重 0.15
- dir_structure 含 src/ models/ train.py 等 ML 标准目录 -- 权重 0.15
"""
