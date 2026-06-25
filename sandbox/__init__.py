"""sandbox 包 -- 论文复现代码的本地隔离执行环境（S3-01）。

提供 ``local_venv`` 模块：在 WORKSPACE_DIR 下创建/复用 venv、安装依赖、
以独立子进程执行命令（4 项护栏：超时杀子树 / 输出截断 / cwd 限定 / 子进程隔离）。

本包为纯基础设施层：无 LLM、无 GlobalState 依赖，只接收路径/命令/护栏参数，
返回 dataclass，供 execution 节点消费。
"""
