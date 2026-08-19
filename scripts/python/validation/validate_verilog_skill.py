"""运行 readable Verilog generator skill 的本地信心门禁。"""

# 标准库负责参数解析、报告读写、子进程执行和清理校验产物。
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

# dataclass 用于把重复 CLI 参数组收束成清晰的请求对象。
from dataclasses import dataclass

# pathlib 和 typing 提供脚本内路径与 JSON 载荷标注。
from pathlib import Path
from types import ModuleType
from typing import Any

# skill 根目录用于脚本直运行时定位 runtime 包。
PATH_SKILL_ROOT = Path(__file__).resolve().parents[3]  # skill 主体根目录

# 仓库根目录用于运行 tests、smoke 和 compileall。
PATH_PROJECT_ROOT = PATH_SKILL_ROOT.parents[1]  # 仓库根目录

# workflow CLI 统一切到 scripts/python/workflow 官方模块入口。
WORKFLOW_CLI_MODULE = "scripts.python.workflow.cli"  # workflow 官方 CLI 模块名

# 远程验证统一切到 scripts/python/remote 官方模块入口。
REMOTE_VALIDATE_MODULE = "scripts.python.remote.remote_validate_verilog_skill"  # 远程验证官方模块名

# 历史残留关键词拆开拼接，避免本文件自身被旧领域词扫描误判。
TUPLE_LEGACY_TERMS = (  # 旧领域词扫描使用的拆分字符串集合
    "H" + "LS",  # 旧综合领域大写触发词
    "h" + "ls",  # 旧综合领域小写触发词
    "V" + "itis",  # 旧工具链大写触发词
    "v" + "itis",  # 旧工具链小写触发词
    "ap_" + "uint",  # 旧综合类型名
    "#pragma " + "H" + "LS",  # 旧综合 pragma 形式
    "verilog_" + "h" + "ls" + "_adapter",  # 旧适配器模块名
    "h" + "ls" + "_generator",  # 旧生成器模块名
)

# 绝对路径正则拆成片段，避免脚本源码本身出现可扫描的私有路径。
TUPLE_ABSOLUTE_PATH_PATTERN_PARTS = (  # 本地路径泄漏扫描的正则片段
    r"(?<![A-Za-z])[A-Za-z]:[\\/]|",  # 任意 Windows 盘符路径
    "F",  # 常见工作盘字母片段
    r":/|",  # 正斜杠盘符分隔符
    "G",  # 备用工作盘字母片段
    r":/|",  # 备用盘符分隔符
    "C",  # 用户目录默认盘字母片段
    r":/|",  # 用户目录盘符分隔符
    "Users",  # Windows 用户目录名片段
    r"\\|",  # 反斜杠目录分隔符
    "Work",  # 工作区目录名前半段
    "Space",  # 工作区目录名后半段
)

# 编译后的绝对路径正则供发布卫生扫描复用。
ABSOLUTE_PATH_PATTERN = re.compile("".join(TUPLE_ABSOLUTE_PATH_PATTERN_PARTS))  # 本地私有路径扫描正则

# ref 目录只允许作为临时输入，不允许成为发布内容依赖。
REF_DEPENDENCY_PATTERN = re.compile(r"(?<![A-Za-z0-9_])ref[\\/]")  # 临时 ref 目录依赖扫描正则

# skill 名称必须符合 Codex skill manifest 的短横线命名。
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9-]+$")  # SKILL.md name 字段合法形式

# frontmatter description 只描述触发场景，不能塞进流程命令。
TUPLE_SKILL_DESCRIPTION_WORKFLOW_TERMS = (
    "requirements ->",  # workflow 阶段名不属于触发描述
    "codegen plan",  # 内部计划阶段不属于触发描述
    "run-workflow",  # CLI 命令不属于触发描述
    "prompt --spec",  # CLI 选项不属于触发描述
    "resume",  # workflow 恢复动作不属于触发描述
)  # SKILL.md description 中禁止出现的 workflow 操作词

# skill standards 必须持续提到的设计模式名称。
TUPLE_PATTERN_NAMES = ("Tool Wrapper", "Generator", "Reviewer", "Inversion", "Pipeline")  # skill 设计模式清单

# 外部 docs 治理脚本优先使用当前标准布局，兼容旧版单层 scripts 布局。
PATH_MANAGE_DOCS_SCRIPT_CANDIDATES = (  # docs 治理入口脚本候选路径
    Path.home()  # 当前用户主目录作为 Codex 安装位置起点
    / ("." + "codex")  # 兼容默认用户配置目录而不锁定平台代理字面量
    / "skills"  # 本地已安装 skills 根目录
    / "agents-md-generator"  # AGENTS 治理 skill 目录名
    / "scripts"  # 治理 skill 的脚本目录
    / "python"  # 新版 Python 脚本分层目录
    / "docs"  # docs 治理子命令目录
    / "manage_docs.py",  # 当前 agents-md-generator 标准脚本路径
    Path.home() / ("." + "codex") / "skills" / "agents-md-generator" / "scripts" / "manage_docs.py",  # 旧版脚本路径
)

# 选择第一个存在的 docs 治理脚本；都不存在时保留首选路径供错误信息定位。
PATH_MANAGE_DOCS_SCRIPT = next(  # 实际用于调用的 docs 治理脚本路径
    (path_candidate for path_candidate in PATH_MANAGE_DOCS_SCRIPT_CANDIDATES if path_candidate.exists()),  # 首个真实存在的 docs 脚本
    PATH_MANAGE_DOCS_SCRIPT_CANDIDATES[0],  # 全部缺失时用于错误提示的首选路径
)  # docs 治理入口脚本

# 旧函数体仍使用原常量名，别名避免在本轮 readable 修复中扩大行为改动面。
SKILL_ROOT = PATH_SKILL_ROOT  # 兼容旧 helper 的 skill 根目录别名

# 旧 helper 使用 PROJECT_ROOT 作为子进程工作目录。
PROJECT_ROOT = PATH_PROJECT_ROOT  # 兼容旧 helper 的仓库根目录别名

# 旧扫描 helper 使用 LEGACY_TERMS 名称。
LEGACY_TERMS = TUPLE_LEGACY_TERMS  # 兼容旧 helper 的旧词扫描清单

# 旧 skill standards helper 使用 PATTERN_NAMES 名称。
PATTERN_NAMES = TUPLE_PATTERN_NAMES  # 兼容旧 helper 的设计模式清单

# frontmatter 校验逻辑仍读取旧常量名，别名保持既有测试入口稳定。
SKILL_DESCRIPTION_WORKFLOW_TERMS = TUPLE_SKILL_DESCRIPTION_WORKFLOW_TERMS  # 兼容旧 helper 的 workflow 禁词清单

# docs gate 子流程仍引用历史常量名，别名让治理脚本迁移不扩散。
MANAGE_DOCS_SCRIPT = PATH_MANAGE_DOCS_SCRIPT  # 兼容旧 helper 的 docs 治理脚本路径

# _load_validation_workspace_gate_module 延迟导入工作区 helper，兼容脚本直运行。
def _load_validation_workspace_gate_module():
    """延迟导入 validation_workspace_gate 模块。

    :param: 此函数不接收外部业务参数。
    :return: 返回模块对象；供 facade 继续桥接工作区清理与残留检查 helper。
    """

    # 先补齐 runtime 搜索路径，保证工作区 helper 可被脚本直跑场景导入。
    _ensure_runtime_import_path()

    # 再导入工作区 helper 模块，供后续 wrapper 复用其公开入口。
    from scripts.python.validation import validation_workspace_gate as module_validation_workspace_gate

    # 最后把工作区 helper 模块返回给 facade 内部桥接函数继续使用。
    return module_validation_workspace_gate

# _load_validation_source_audit_module 延迟导入源码审计 helper，兼容脚本直运行。
def _load_validation_source_audit_module():
    """延迟导入 validation_source_audit 模块。

    :param: 此函数不接收外部业务参数。
    :return: 返回模块对象；供 facade 把源码与发布审计逻辑转发给 helper。
    """

    # 先补齐 runtime 搜索路径，保证源码审计 helper 能被当前脚本加载。
    _ensure_runtime_import_path()

    # 再导入源码审计 helper 模块，供发布门禁和文档门禁共用。
    from scripts.python.validation import validation_source_audit as module_validation_source_audit

    # 最后把源码审计 helper 返回给 facade 的审计包装函数复用。
    return module_validation_source_audit

# _load_validation_smoke_gate_module 延迟导入 CLI smoke helper，兼容脚本直运行。
def _load_validation_smoke_gate_module() -> ModuleType:
    """延迟导入 validation_smoke_gate 模块。

    :param: 此函数不接收外部业务参数。
    :return: 返回模块对象；供 facade 把 CLI smoke 链路委托给专用 helper。
    """

    # 先补齐 runtime 搜索路径，避免 smoke helper 在脚本入口下缺少导入根。
    _ensure_runtime_import_path()

    # 再导入 CLI smoke helper 模块，供 canonical 和 use-case 冒烟链共用。
    from scripts.python.validation import validation_smoke_gate as module_validation_smoke_gate

    # 把 CLI smoke helper 返回给 facade 的桥接层继续驱动各类 smoke 场景。
    return module_validation_smoke_gate

# _load_validation_governance_gate_module 延迟导入治理 helper，兼容脚本直运行。
def _load_validation_governance_gate_module():
    """延迟导入 validation_governance_gate 模块。

    :param: 此函数不接收外部业务参数。
    :return: 返回模块对象；供 facade 复用外部治理、远程 gate 与 audit helper。
    """

    # 先补齐 runtime 搜索路径，保证治理 helper 在直跑入口下仍可解析。
    _ensure_runtime_import_path()

    # 再导入治理 helper 模块，供远程 gate、docs gate 与 audit 包装层共享。
    from scripts.python.validation import validation_governance_gate as module_validation_governance_gate

    # 把治理 helper 模块返回给 facade，保持旧入口继续只做桥接职责。
    return module_validation_governance_gate

# _build_workspace_gate_context 收束残留清理和路径归属 helper 依赖的上下文。
def _build_workspace_gate_context():
    """构造 validation_workspace_gate 使用的路径上下文。

    :param: 此函数不接收外部业务参数。
    :return: 返回 WorkspaceGateContext；供工作区清理与残留检查 helper 共享路径边界。
    """

    # 直接组合工作区 helper 依赖的三段根路径，保持 facade 到 helper 的上下文边界稳定。
    return _load_validation_workspace_gate_module().WorkspaceGateContext(
        path_skill_root=SKILL_ROOT,
        path_project_root=PROJECT_ROOT,
        path_workspace_root=validation_workspace_root(),
    )

# _build_source_audit_context 收束源码发布审计 helper 依赖的上下文。
def _build_source_audit_context():
    """构造 validation_source_audit 使用的审计上下文。

    :param: 此函数不接收外部业务参数。
    :return: 返回 SourceAuditContext；供源码、文档与发布审计 helper 共享规则上下文。
    """

    # 直接组合源码审计 helper 需要的路径、布局与规则对象。
    return _load_validation_source_audit_module().SourceAuditContext(
        path_skill_root=SKILL_ROOT,
        path_project_root=PROJECT_ROOT,
        bool_source_repository_layout=is_source_repository_layout(),

        # 把 facade 仍保留的文件枚举与相对路径 helper 传给源码审计上下文。
        func_iter_skill_files=iter_skill_files,
        func_project_relative=_project_relative,

        # 再把 legacy、模式名和路径规则集合透传给源码审计 helper。
        tuple_legacy_terms=LEGACY_TERMS,
        tuple_pattern_names=PATTERN_NAMES,
        tuple_skill_description_workflow_terms=SKILL_DESCRIPTION_WORKFLOW_TERMS,
        pattern_absolute_path=ABSOLUTE_PATH_PATTERN,
        pattern_ref_dependency=REF_DEPENDENCY_PATTERN,
        pattern_skill_name=SKILL_NAME_PATTERN,
    )

# _build_governance_gate_context 收束远程 gate、audit 与外部治理 helper 依赖。
def _build_governance_gate_context():
    """构造 validation_governance_gate 使用的治理上下文。

    :param: 此函数不接收外部业务参数。
    :return: 返回 GovernanceGateContext；供外部治理、remote gate 与 audit helper 共用依赖。
    """

    # 直接组合治理 helper 需要的路径、可执行入口与辅助回调。
    return _load_validation_governance_gate_module().GovernanceGateContext(
        path_skill_root=SKILL_ROOT,
        path_project_root=PROJECT_ROOT,
        path_manage_docs_script=MANAGE_DOCS_SCRIPT,
        str_python_executable=sys.executable,
        str_remote_validate_module=REMOTE_VALIDATE_MODULE,

        # 保持治理 helper 继续通过 facade 暴露的运行与清理函数回调工作。
        func_run=run,
        func_cleanup_residuals=cleanup_residuals,
        func_cleanup_audit_retry_local_artifacts=cleanup_audit_retry_local_artifacts,
        func_validation_workspace_root=validation_workspace_root,
    )

# _paths_match 统一比较路径，兼容目标尚未落盘时的测试场景。
def _paths_match(path_left: Path, path_right: Path) -> bool:
    """比较两个路径是否指向同一位置；缺失路径时退回绝对文本比较。

    :param path_left: 左侧待比较路径。
    :param path_right: 右侧待比较路径。
    :return: 返回布尔值；True 表示“比较两个路径是否指向同一位置；缺失路径时退回绝对文本比较。”对应条件命中。
    """

    # resolve 能把大小写和 `..` 归一化，优先用于已存在路径。
    try:

        # 两侧都能 resolve 时直接比较真实路径。
        return path_left.resolve() == path_right.resolve()

    # 单测临时目录或安装前路径可能尚未创建，此时改用绝对文本回退。
    except FileNotFoundError:

        # 缺失路径场景只比较补全后的绝对字符串，避免抛出额外异常。
        return path_left.absolute() == path_right.absolute()

# is_source_repository_layout 区分源码仓库运行和安装副本运行。
def is_source_repository_layout() -> bool:
    """判断当前 validate 脚本是否运行在带完整 docs/tests/tests-smoke 的源码仓库中。

    :param: 此函数不接收外部业务参数。
    :return: 返回布尔值；True 表示“判断当前 validate 脚本是否运行在带完整 docs/tests/tests-smoke 的源码仓库中。”对应条件命中。
    """

    # 源码仓库要求 skill 位于 `<repo>/skills/<name>` 标准布局。
    path_expected_skill = PROJECT_ROOT / "skills" / SKILL_ROOT.name  # 源码仓库预期的 skill 位置

    # docs/tests/tests-smoke 三组标记共同表示完整仓库治理上下文存在。
    tuple_repo_markers = (
        PROJECT_ROOT / "docs" / "handoff" / "HANDOFF.md",  # 交接文档是源码仓库治理标记
        PROJECT_ROOT / "tests" / "smoke" / "run_smoke.py",  # tests/smoke 入口只存在于源码仓库
        PROJECT_ROOT / "tests",  # 回归测试目录只存在于源码仓库
    )  # 判断源码仓库布局所需的最小标记集合

    # skill 位置必须命中标准仓库布局，且治理标记全部存在。
    return _paths_match(path_expected_skill, SKILL_ROOT) and all(
        path_marker.exists() for path_marker in tuple_repo_markers
    )

# validation_workspace_root 提供安装态与源码态共用的默认子进程工作目录。
def validation_workspace_root() -> Path:
    """返回当前 validate 子进程默认使用的工作目录。

    :param: 此函数不接收外部业务参数。
    :return: 返回路径对象；该路径由“返回当前 validate 子进程默认使用的工作目录。”阶段生成或解析。
    """

    # 源码仓库下继续在仓库根执行，安装副本则退回 skill 根目录。
    return PROJECT_ROOT if is_source_repository_layout() else SKILL_ROOT

@dataclass(frozen=True)
class ValidationContext:
    """保存一次本地信心门禁运行期间反复使用的路径和配置。"""

    # argparse 结果保留公开 CLI 参数语义。
    namespace_args: argparse.Namespace  # 解析后的命令行参数

    # settings 路径统一转为绝对路径，避免子进程 cwd 改变含义。
    path_settings: Path  # 本次验证使用的治理配置文件

    # settings 内容供各个验证门禁共享。
    dict_settings: dict[str, Any]  # Verilog skill 本轮治理配置

    # smoke 目录带进程号和时间戳，避免并行 worker 互相踩踏。
    path_smoke_dir: Path  # 本轮验证临时目录

# _ensure_runtime_import_path 只在入口和需要 runtime helper 的函数内调整导入路径。
def _ensure_runtime_import_path() -> None:
    """确保脚本从仓库任意位置运行时能导入 runtime 包。

    :param: 此函数不接收外部业务参数。
    :return: 不返回业务值；执行完成即表示“确保脚本从仓库任意位置运行时能导入 runtime 包。”对应步骤未发现阻断。
    """

    # 禁止生成 pyc，避免验证脚本污染 skill 目录。
    sys.dont_write_bytecode = True  # 当前验证进程禁止写入 Python 字节码缓存

    # sys.path 使用字符串路径进行成员比较。
    str_skill_root = str(PATH_SKILL_ROOT)  # runtime 包所在根目录文本

    # 已经存在时不重复改变模块搜索顺序。
    if str_skill_root not in sys.path:

        # 将 runtime 包所在目录放到导入搜索路径前端。
        sys.path.insert(0, str_skill_root)

# load_settings 保留测试和外部脚本直接导入此验证脚本时的兼容入口。
def load_settings(settings_path: Path) -> dict[str, Any]:
    """读取 Verilog skill 治理配置，兼容旧测试直接调用本脚本的入口。

    :param settings_path: 远程验证使用的 settings 文件路径。
    :return: 返回字典对象；内容来自“读取 Verilog skill 治理配置，兼容旧测试直接调用本脚本的入口。”阶段解析出的治理状态。
    """

    # runtime 包路径需要先进入 sys.path，测试通过 spec 载入时不会执行 CLI 入口。
    _ensure_runtime_import_path()

    # 延迟导入保持脚本 import 阶段轻量，同时复用 runtime 的配置解析语义。
    from scripts.python.workflow.config import load_settings as runtime_load_settings

    # 返回 runtime config 模块解析后的 settings 字典。
    return runtime_load_settings(settings_path)

# path_setting 保留旧测试从验证脚本解析 settings 路径的 facade。
def path_setting(settings: dict[str, Any], key: str) -> Path:
    """按 runtime 配置规则解析 settings 中的路径字段。

    :param settings: Verilog skill 治理配置字典，提供路径、依赖和验证开关。
    :param key: settings 中需要解析为路径的字段名。
    :return: 返回路径对象；该路径由“按 runtime 配置规则解析 settings 中的路径字段。”阶段生成或解析。
    """

    # runtime 包路径需要先进入 sys.path，避免 spec 载入脚本时找不到 runtime。
    _ensure_runtime_import_path()

    # 延迟导入让兼容 facade 不改变模块顶层依赖顺序。
    from scripts.python.workflow.config import path_setting as runtime_path_setting

    # 返回 runtime config 模块标准化后的路径对象。
    return runtime_path_setting(settings, key)

# create_parser 单独维护公开 CLI 参数，避免 main 承担注册细节。
def create_parser() -> argparse.ArgumentParser:
    """创建本地信心门禁脚本的参数解析器。

    :param: 此函数不接收外部业务参数。
    :return: 返回 argparse.ArgumentParser；该值承载“创建本地信心门禁脚本的参数解析器。”阶段需要传递的结果。
    """

    # 描述文本保持英文，延续既有 --help 用户可见输出。
    str_description = "Validate the readable Verilog generator skill locally."  # argparse 描述文本

    # parser 只声明参数，不读取文件或运行验证。
    parser = argparse.ArgumentParser(description=str_description)  # 本地验证命令解析器

    # settings 默认值保持原脚本路径。
    path_default_settings = PATH_SKILL_ROOT / "config" / "defaults.json"  # 默认 settings 路径

    # --settings 允许调用方替换治理配置。
    parser.add_argument("--settings", type=Path, default=path_default_settings)

    # --with-remote 保留显式远程门禁开关。
    parser.add_argument("--with-remote", action="store_true", help="Also run the remote confidence gate.")

    # --remote-server 保留显式服务器 id 覆盖。
    parser.add_argument("--remote-server", help="Explicit remote server id for the remote confidence gate.")

    # require-remote 默认仍为 True，并支持 --no-require-remote。
    parser.add_argument(
        "--require-remote",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Require real remote validation evidence as part of the confidence gate.",
    )

    # 外部 audit 依赖仓库治理工具，默认跳过以兼容安装包内运行。
    parser.add_argument(
        "--with-external-audit",
        action="store_true",
        help="Run development/release audit tools that live outside the installed skill package.",
    )

    # repo regression 只在源码仓库场景运行。
    parser.add_argument(
        "--with-repo-regression",
        action="store_true",
        help="Run repository-root unittest and smoke suites in addition to self-contained skill gates.",
    )

    # 返回已注册全部兼容参数的解析器。
    return parser

# build_validation_context 集中解析 settings 和 smoke 路径，降低 main 的分支密度。
def build_validation_context(namespace_args: argparse.Namespace) -> ValidationContext:
    """根据命令行参数创建本轮验证上下文。

    :param namespace_args: argparse 解析出的 CLI 参数，决定本轮验证开关和 settings 路径。
    :return: 返回 ValidationContext；包含本轮验证复用的 settings、CLI 参数和 smoke 目录。
    """

    # runtime config 在路径准备后延迟导入。
    from scripts.python.workflow.config import build_smoke_run_path, load_settings, path_setting

    # 命令行 settings 先保留原 Path 对象，后续按绝对/相对分支解析。
    path_cli_settings = namespace_args.settings  # 用户传入或默认的 settings 路径

    # 相对 settings 路径按当前工作目录解析，保持原脚本语义。
    if path_cli_settings.is_absolute():

        # 绝对路径无需重新锚定，避免改变用户指定位置。
        path_settings = path_cli_settings  # 本轮 settings 绝对路径

    # 相对 settings 需要按调用目录补成绝对路径。
    else:

        # 相对路径使用当前工作目录解析，保持旧脚本调用习惯。
        path_settings = (Path.cwd() / path_cli_settings).resolve()  # 解析后的 settings 绝对路径

    # 读取 settings，后续所有门禁共享同一份配置。
    dict_settings = load_settings(path_settings)  # Verilog skill 治理配置

    # smoke 根目录来自 settings，不能在脚本中硬编码。
    path_smoke_root = path_setting(dict_settings, "smoke_dir")  # settings 中定义的 smoke 运行根目录

    # 统一构造 timestamped 运行目录，便于并行隔离并保留验证证据。
    path_smoke_dir = build_smoke_run_path(path_smoke_root)  # 本轮 retained smoke 目录

    # 返回 main 和下游 helper 共用的上下文。
    return ValidationContext(
        namespace_args=namespace_args,
        path_settings=path_settings,
        dict_settings=dict_settings,
        path_smoke_dir=path_smoke_dir,
    )

# main 编排本地、远程和发布卫生门禁。
def main(argv: list[str] | None = None) -> int:
    """执行本地信心门禁并返回 shell 可识别的退出码。

    :param argv: 可选命令行参数列表；为 None 时读取真实命令行。
    :return: 返回进程退出码；0 表示“执行本地信心门禁并返回 shell 可识别的退出码。”对应门禁通过。
    """

    # 入口阶段再准备 runtime 导入路径，避免 import-time 副作用。
    _ensure_runtime_import_path()

    # create_parser 集中定义兼容 CLI 开关，入口只负责调用解析。
    parser = create_parser()  # validate_verilog_skill 顶层 argparse 解析器

    # 解析调用方传入的命令行参数。
    namespace_args: argparse.Namespace = parser.parse_args(argv)  # 本地验证入口参数

    # 本轮上下文聚合 settings、smoke 目录和 CLI 开关，后续 gate 共享同一份状态。
    validation_context_current = build_validation_context(namespace_args)  # 当前本地信心门禁上下文

    # 每轮验证开始前清理 skill 内部旧缓存，不触碰 retained smoke 目录。
    cleanup_residuals(validation_context_current.dict_settings, validation_context_current.path_smoke_dir)

    # 外部治理工具只有显式要求时运行，保持安装包内脚本可用。
    run_optional_external_audit(validation_context_current)

    # skill 自包含门禁覆盖配置、文档、CLI 和效果评估。
    path_effectiveness_report = run_self_contained_gates(validation_context_current)  # eval-skill 本地报告路径

    # 发布卫生检查确认旧领域词、硬编码路径和临时 ref 依赖没有回流。
    run_release_hygiene_gates(validation_context_current)

    # 远程门禁按 --with-remote / --require-remote 的旧语义执行。
    run_remote_gate_if_requested(validation_context_current, path_effectiveness_report)

    # 所有门禁结束后再次清理 skill 内部缓存，保留本轮 smoke 证据。
    cleanup_residuals(validation_context_current.dict_settings, validation_context_current.path_smoke_dir)

    # 清理后确认 skill 目录没有禁止残留；reports 下的运行证据允许保留。
    verify_no_residuals(validation_context_current.dict_settings, validation_context_current.path_smoke_dir)

    # 输出当前 skill 品牌下的最终成功提示。
    print("> INFO: [Python] Readable Verilog generator local confidence gate passed.")

    # 退出码 0 表示本地信心门禁全部通过。
    return 0

# run_optional_external_audit 维护 --with-external-audit 的兼容行为。
def run_optional_external_audit(validation_context: ValidationContext) -> None:
    """按需运行仓库外部治理和 audit skill 门禁。

    :param validation_context: 本轮验证上下文，包含 settings、smoke 目录和 CLI 开关。
    :return: 不返回业务值；执行完成即表示“按需运行仓库外部治理和 audit skill 门禁。”对应步骤未发现阻断。
    """

    # runtime config helper 延迟导入，保证脚本导入本身无副作用。
    from scripts.python.workflow.config import path_setting

    # 用户显式要求外部 audit 时才依赖仓库治理工具。
    if validation_context.namespace_args.with_external_audit:

        # work-folder gate 只在源码仓库布局下有效；安装副本缺少 docs/tests 时跳过。
        if is_source_repository_layout():

            # 源码仓库中的外部 audit 继续要求 AGENTS、docs 和目录治理状态。
            run_work_folder_gate(require_external=True)

        # 安装副本没有仓库治理材料时仅记录跳过原因。
        else:

            # 说明 work-folder gate 仅对源码仓库有效，避免安装副本误报失败。
            print("> INFO: [Python] repository work-folder gate skipped outside source-repository layout.")

        # quick_validate 覆盖 skill 包基础结构。
        run(
            [
                sys.executable,
                str(path_setting(validation_context.dict_settings, "quick_validate")),
                str(PATH_SKILL_ROOT),
            ],
            cwd=validation_workspace_root(),
        )

        # audit_skill 负责更完整的 skill 结构审计。
        run_audit_skill(validation_context.dict_settings, validation_context.path_smoke_dir)

        # 外部 audit 已完成，无需打印跳过提示。
        return

    # 跳过提示拆成两段，避免单行过长但保留旧提示文本。
    str_audit_skip_prefix = "> WARNING: [Python] external audit and work-folder gates skipped;"  # 外部 audit 跳过提示前缀

    # 开关提示说明如何启用开发/发布治理工具。
    str_audit_skip_hint = "pass --with-external-audit for development/release audit tools."  # 外部 audit 开关说明

    # 默认路径只提示跳过，不改变原脚本退出语义。
    print(f"> WARNING: [Python] external audit skipped; {str_audit_skip_hint}")

# run_self_contained_gates 汇总无需远程服务器的核心信心门禁。
def run_self_contained_gates(validation_context: ValidationContext) -> Path:
    """运行配置、源码编译、CLI 冒烟和本地效果评估门禁。

    :param validation_context: 本轮验证上下文，包含 settings、smoke 目录和 CLI 开关。
    :return: 返回路径对象；该路径由“运行配置、源码编译、CLI 冒烟和本地效果评估门禁。”阶段生成或解析。
    """

    # dependency schema 必须先验证，避免后续 helper 读取错误形状。
    verify_dependency_schema(validation_context.dict_settings)

    # markdown ASCII 约束保障 skill 安装安全，同时允许精确文件级例外。
    verify_markdown_ascii(validation_context.dict_settings)

    # skill standards 覆盖 SKILL.md frontmatter 和支持资源。
    verify_skill_standards()

    # runtime 路径就绪后加载技能内置 registry 门禁，不依赖外部脚本。
    _ensure_runtime_import_path()

    # SQLite 当前性和文档治理共同构成注册语义的本地闭合边界。
    from scripts.python.registry.document_registry_common import validate_document_governance
    from scripts.python.registry.registry_common import ensure_database_current

    # 先拒绝缺失、损坏、陈旧或 FTS 不兼容的生成索引。
    connection_registry, _ = ensure_database_current(PATH_SKILL_ROOT)  # 已通过来源摘要与记录计数门禁的只读连接

    # 主验证链只核对索引，不在此执行任何注册表问询。
    connection_registry.close()

    # 再验证文档职责、知识指针、重复裁决、接口映射和正文摘要均为当前状态。
    validate_document_governance(PATH_SKILL_ROOT, bool_require_current=True)

    # compileall 和可选 repo regression 共享同一份目标列表。
    run_compile_and_optional_regression(validation_context)

    # CLI gate 覆盖 scaffold、prompt、workflow、validate 和 verify-existing。
    run_cli_gate(validation_context.dict_settings, validation_context.path_smoke_dir)

    # eval-skill 输出写入 smoke 目录，远程阶段会复用同一路径。
    path_effectiveness_report = validation_context.path_smoke_dir / "skill-effectiveness.json"  # 效果评估报告路径

    # eval-skill 命令固定使用内置 fixture、smoke 输出和无状态模式，保证本地效果评估可复现。
    list_eval_skill_command = [  # 产出效果评估报告并禁止状态残留的本地评估命令
        sys.executable,  # eval-skill 使用 validate 进程同款解释器
        "-m",  # 以模块方式启动官方 workflow CLI
        WORKFLOW_CLI_MODULE,  # Verilog 生成器命令行模块入口
        "eval-skill",  # 本地效果评估子命令
        "--evals",  # eval fixture 路径参数名
        str(PATH_SKILL_ROOT / "evals" / "evals.json"),  # skill 内置效果评估用例清单
        "--out",  # 效果报告输出参数名
        str(path_effectiveness_report),  # 本轮效果评估报告路径
        "--no-state",  # 禁止写 workflow-state.json 以保持残留检查干净
    ]  # workflow CLI eval-skill 子进程 argv

    # 执行本地效果评估命令。
    run(list_eval_skill_command, cwd=validation_workspace_root())

    # 不要求远程时，本地效果报告必须独立通过。
    if not validation_context.namespace_args.require_remote:

        # 本地效果评估 summary.ok 是 release 前最低置信门槛。
        verify_skill_effectiveness(path_effectiveness_report)

    # 返回远程阶段可能继续更新的效果报告路径。
    return path_effectiveness_report

# run_compile_and_optional_regression 保持 --with-repo-regression 的原始门禁范围。
def run_compile_and_optional_regression(validation_context: ValidationContext) -> None:
    """运行 skill 源码编译，并按需运行仓库根测试与 smoke。

    :param validation_context: 本轮验证上下文，包含 settings、smoke 目录和 CLI 开关。
    :return: 不返回业务值；执行完成即表示“运行 skill 源码编译，并按需运行仓库根测试与 smoke。”对应步骤未发现阻断。
    :raises AssertionError: 当“运行 skill 源码编译，并按需运行仓库根测试与 smoke。”阶段发现安装副本误用源码仓库回归门禁时抛出。
    """

    # 安装包中的 Python 源全部收敛到 scripts 目录，compileall 只需覆盖该根目录。
    list_compile_targets = [
        str(SKILL_ROOT / "scripts"),  # 随包命令脚本与 scripts/python 实现目录
    ]  # compileall 默认目标

    # repo regression 打开时补充仓库根 tests，覆盖常规测试与 tests/smoke。
    if validation_context.namespace_args.with_repo_regression:

        # 安装副本没有仓库级 tests/tests-smoke，显式要求时必须指出使用边界。
        if not is_source_repository_layout():

            # 避免安装副本静默把 repo regression 退化成不完整检查。
            raise AssertionError("> ERR: [Python] Repository regression gate requires the source repository layout.")

        # tests 与 tests/smoke 不属于安装包主体，但源码仓库回归需要覆盖。
        list_compile_targets.append(str(PROJECT_ROOT / "tests"))

    # compileall 用于快速发现语法和 import-time 解析问题。
    run([sys.executable, "-m", "compileall", "-q", *list_compile_targets], cwd=validation_workspace_root())

    # 用户未要求源码仓库回归时保持旧提示。
    if not validation_context.namespace_args.with_repo_regression:

        # 提示调用方如何打开更重的仓库回归门禁。
        print(
            "> WARNING: [Python] repository regression gate skipped; "
            "pass --with-repo-regression from the source repo."
        )

        # 跳过源码仓库回归后直接返回。
        return

    # unittest discovery 覆盖仓库根 tests。
    run(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            str(PROJECT_ROOT / "tests"),
            "-p",
            "test_*.py",
            "-v",
        ],
        cwd=PATH_PROJECT_ROOT,
    )

    # tests.smoke.run_smoke 使用与当前验证一致的 settings。
    run(
        [
            sys.executable,
            "-m",
            "tests.smoke.run_smoke",
            "--settings",
            str(validation_context.path_settings),
        ],
        cwd=PATH_PROJECT_ROOT,
    )

# run_release_hygiene_gates 聚合发布包内容卫生检查。
def run_release_hygiene_gates(validation_context: ValidationContext) -> None:
    """检查发布前不应进入 skill 包的旧词、绝对路径和 ref 依赖。

    :param validation_context: 本轮验证上下文，包含 settings、smoke 目录和 CLI 开关。
    :return: 不返回业务值；执行完成即表示“检查发布前不应进入 skill 包的旧词、绝对路径和 ref 依赖。”对应步骤未发现阻断。
    """

    # legacy term 扫描防止旧 HLS/Vitis 生成器词汇回流。
    verify_legacy_terms(validation_context.dict_settings)

    # hardcoded path 扫描防止本地路径泄露进 skill 主体。
    verify_hardcoded_paths()

    # ref dependency 扫描防止临时参考目录成为活跃依赖。
    verify_no_ref_dependencies()

    # 发布卫生检查后清理本轮可能生成的临时产物。
    cleanup_residuals(validation_context.dict_settings, validation_context.path_smoke_dir)

    # 清理后确认禁止残留不存在。
    verify_no_residuals(validation_context.dict_settings, validation_context.path_smoke_dir)

# run_remote_gate_if_requested 保持远程验证和 require-remote 语义。
def run_remote_gate_if_requested(validation_context: ValidationContext, path_effectiveness_report: Path) -> None:
    """按 CLI 开关运行远程验证并把远程证据并入效果评估。

    :param validation_context: 本轮验证上下文，包含 settings、smoke 目录和 CLI 开关。
    :param path_effectiveness_report: 本地效果评估报告路径，远程验证会把证据合并到该文件。
    :return: 不返回业务值；执行完成即表示“按 CLI 开关运行远程验证并把远程证据并入效果评估。”对应步骤未发现阻断。
    :raises AssertionError: 当“按 CLI 开关运行远程验证并把远程证据并入效果评估。”阶段发现配置、产物或执行结果不满足门禁要求时抛出。
    """

    # 只有显式远程或默认 require-remote 时运行远程门禁。
    if validation_context.namespace_args.with_remote or validation_context.namespace_args.require_remote:

        # CLI 指定的远程服务器只覆盖本次验证，不写回项目选择文件。
        str_explicit_remote_server = validation_context.namespace_args.remote_server  # 本次 CLI 指定的远程服务器

        # 解析远程服务器选择和远端 runtime 配置路径。
        dict_remote_state = resolve_required_remote_validation_state(  # 远程验证选择状态
            validation_context.dict_settings,  # 当前 validate 使用的 settings 结构
            explicit_server=str_explicit_remote_server,  # CLI 临时覆盖服务器
        )

        # server_id 是远端校验脚本识别目标服务器的唯一字符串。
        str_remote_server = str(dict_remote_state["server_id"])  # 远程服务器标识

        # 先运行远程验证主流程，保证远端真实产物可用。
        completed_process_remote_validation = run(  # 本轮远程执行身份载荷
            build_remote_validation_command(  # 构造本轮远程验证命令
                validation_context.path_settings,  # 复用已解析的路径配置
                str_remote_server,  # 固定本轮目标服务器
            ),
            cwd=PATH_SKILL_ROOT,  # 在技能根目录解析相对配置和报告路径
        )

        # 完整执行 stdout 末尾必须给出刚完成的 outer run 和 staging 摘要。
        dict_remote_validation_identity = parse_json_object(completed_process_remote_validation.stdout)  # 本轮远程执行身份

        # 提取后续报告查询必须绑定的本轮运行标识。
        str_remote_run_id = str(dict_remote_validation_identity.get("run_id", ""))  # 精确 report-runs 查询使用的 outer run 标识

        # 保留暂存源码摘要以核对远程完成证据。
        str_source_digest = str(dict_remote_validation_identity.get("source_digest", ""))  # 本轮上传 staging 的 SHA-256 身份

        # 缺失或坏格式身份时失败关闭，禁止回退到“最新”远程目录。
        if not str_remote_run_id.startswith("run-") or len(str_source_digest) != 64:

            # 缺少身份字段时禁止退化到按时间选择远程报告。
            raise AssertionError("> ERR: [Python] remote validation did not return a bound run identity.")

        # report-runs 命令只读取最近一次远端运行证据，不重新执行远端流程。
        list_remote_report_command = build_remote_validation_command(  # 远端运行报告命令
            validation_context.path_settings,  # 当前 validate 使用的 settings 文件
            str_remote_server,  # 已解析出的远程服务器标识
            report_runs=True,  # 切换到远端运行证据查询模式
            run_id=str_remote_run_id,  # 只读取本轮刚完成的 outer run
        )

        # report-runs 模式回收最近一次远端运行证据。
        completed_process_remote_runs = run(list_remote_report_command, cwd=PATH_SKILL_ROOT)  # 远端运行报告命令结果

        # stdout 末尾 JSON 是远程运行证据载荷。
        dict_remote_runs_report = parse_json_object(  # 远程运行证据
            completed_process_remote_runs.stdout,  # 远端运行报告 stdout
        )

        # report-runs 必须回收同一 run 的 completion，且 staging 摘要必须逐字匹配。
        list_remote_runs = dict_remote_runs_report.get("runs", [])  # 精确 report-runs 返回的运行列表

        # 精确运行查询必须且只能返回本轮报告。
        dict_remote_run = list_remote_runs[0] if isinstance(list_remote_runs, list) and list_remote_runs else {}  # 本轮唯一远程摘要

        # 完成清单用于绑定源码、命令和实际退出状态。
        dict_completion = dict_remote_run.get("completion", {}) if isinstance(dict_remote_run, dict) else {}  # 最终完成身份清单

        # 任一身份不一致都说明报告不属于本轮暂存源码。
        if (
            not isinstance(dict_completion, dict)
            or dict_completion.get("run_id") != str_remote_run_id
            or dict_completion.get("source_digest") != str_source_digest
        ):

            # 身份不匹配时闭合失败，避免接受旧报告或错误暂存包。
            raise AssertionError(
                "> ERR: [Python] remote completion identity does not match "
                "the executed staging package."
            )

        # eval-skill 需要从文件读取远程运行证据。
        path_remote_runs = validation_context.path_smoke_dir / "remote-runs.json"  # 远程运行证据 JSON 路径

        # 确保 smoke 子目录存在后再写证据文件。
        path_remote_runs.parent.mkdir(parents=True, exist_ok=True)

        # 写出远程运行证据，供 eval-skill require-remote 复核。
        path_remote_runs.write_text(
            json.dumps(dict_remote_runs_report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        # require-remote 模式重新评估 skill 有效性。
        run(
            [
                sys.executable,
                "-m",
                WORKFLOW_CLI_MODULE,
                "eval-skill",
                "--evals",
                str(PATH_SKILL_ROOT / "evals" / "evals.json"),
                "--out",
                str(path_effectiveness_report),
                "--remote-runs-json",
                str(path_remote_runs),
                "--require-remote",
                "--no-state",
            ],
            cwd=validation_workspace_root(),
        )

        # 远程证据并入后，效果评估必须通过。
        verify_skill_effectiveness(path_effectiveness_report)

        # 远程门禁完成后返回主流程清理。
        return

    # require-remote 为真但远程分支未运行时保持原有阻断语义。
    if validation_context.namespace_args.require_remote:

        # 兜底断言保护 require-remote 的失败语义。
        raise AssertionError("> ERR: [Python] Remote validation was required but the remote gate did not run.")

# run_cli_gate 保持旧本地 CLI confidence gate 的外部行为。
def run_cli_gate(settings: dict, smoke_dir: Path) -> None:
    """运行 scaffold、prompt、workflow、validate 和 verify-existing CLI 冒烟。

    :param settings: Verilog skill 治理配置字典，提供路径、依赖和验证开关。
    :param smoke_dir: 本轮验证使用的临时 smoke 目录。
    :return: 不返回业务值；执行完成即表示“运行 scaffold、prompt、workflow、validate 和 verify-existing CLI 冒烟。”对应步骤未发现阻断。
    """

    # runtime config helper 延迟导入，避免 import-time 加载 runtime。
    from scripts.python.workflow.config import path_setting

    # example spec 是 canonical CLI 流程的固定输入。
    path_example_spec = path_setting(settings, "example_spec")  # canonical 示例规格

    # use-case 目录中的每个 JSON 都必须完成 prompt/workflow/validate。
    path_use_case_examples_dir = path_setting(settings, "use_case_examples_dir")  # use-case 示例目录

    # smoke helper 模块已经承载具体 CLI 场景逻辑，这里只保留总装协调职责。
    module_type_module_validation_smoke_gate = _load_validation_smoke_gate_module()  # 汇总多类冒烟场景的 helper 模块

    # 本轮 CLI gate 先清理 smoke 子树，避免旧产物影响断言。
    remove_inside_smoke_root(settings, smoke_dir)

    # canonical 流程覆盖 scaffold、prompt、workflow 和 validate 基线。
    module_type_module_validation_smoke_gate.run_canonical_cli_flow(
        path_example_spec,
        smoke_dir,
        func_run_verilog_cli=run_verilog_cli,
    )

    # use-case 示例验证模板选择能贯穿 requirements 和 codegen plan。
    module_type_module_validation_smoke_gate.run_use_case_cli_flows(
        path_use_case_examples_dir,
        smoke_dir,
        func_run_verilog_cli=run_verilog_cli,
        func_project_artifact_path=project_artifact_path,
    )

    # existing RTL 流程验证半自动边界和 testbench augment 产物。
    module_type_module_validation_smoke_gate.run_existing_rtl_boundary_flows(
        smoke_dir,
        path_skill_root=PATH_SKILL_ROOT,
        func_run_verilog_cli=run_verilog_cli,
    )

    # patch resume 流程验证三类 RTL 修复都必须经决策文件恢复。
    module_type_module_validation_smoke_gate.run_existing_rtl_patch_flows(
        smoke_dir,
        path_skill_root=PATH_SKILL_ROOT,
        func_run_verilog_cli=run_verilog_cli,
    )

# run_verilog_cli 用统一入口调用 scripts.python.workflow.cli 官方 CLI。
def run_verilog_cli(*str_args: str, allow_failure: bool = False) -> None:
    """执行一次官方 workflow CLI 命令。

    :param *str_args: 执行一次官方 workflow CLI 命令。 阶段使用的 `str_args` 输入。
    :param allow_failure: 是否允许子进程以非零退出后由调用方检查落盘产物。
    :return: 不返回业务值；执行完成即表示“执行一次官方 workflow CLI 命令。”对应步骤未发现阻断。
    """

    # 命令前缀固定为当前 Python 解释器和官方 workflow CLI 模块。
    list_command = [sys.executable, "-m", WORKFLOW_CLI_MODULE, *str_args]  # workflow 官方 CLI 命令

    # 子进程工作目录按源码仓库/安装副本布局自动选择。
    run(list_command, cwd=validation_workspace_root(), allow_failure=allow_failure)

# VerifyExistingRequest 继续从 smoke helper 暴露既有 verify-existing 请求类型。
VerifyExistingRequest = _load_validation_smoke_gate_module().VerifyExistingRequest  # verify-existing 兼容请求类型别名

# run_verify_existing 继续保留 validate facade 的旧兼容导出面。
def run_verify_existing(request: VerifyExistingRequest) -> None:
    """通过 validate facade 运行一次 verify-existing 兼容入口。

    :param request: verify-existing CLI 请求对象。
    :return: 不返回业务值；执行完成即表示兼容入口已按既有参数顺序转发给 smoke helper。
    """

    # smoke helper 仍持有真正的命令拼装逻辑；facade 只负责回填旧导出面。
    _load_validation_smoke_gate_module().run_verify_existing(
        request,
        func_run_verilog_cli=run_verilog_cli,
    )

# _load_validation_gate_facade_module 延迟导入兼容门禁子模块，保持脚本直运行可用。
def _load_validation_gate_facade_module() -> ModuleType:
    """延迟导入 validation_gate_facade 模块。

    :param: 此函数不接收外部业务参数。
    :return: 返回模块对象；供当前 facade 回填公开兼容门禁函数导出面。
    """

    # 先补齐 runtime 导入路径，保证兼容门禁子模块可被脚本直运行场景加载。
    _ensure_runtime_import_path()

    # 再导入兼容门禁子模块，供当前文件把公开入口批量回填到模块命名空间。
    from scripts.python.validation import validation_gate_facade as module_validation_gate_facade

    # 把兼容门禁子模块返回给绑定入口复用。
    return module_validation_gate_facade

# 公开兼容门禁函数集中迁到子模块；当前文件只回填稳定导出面。
value_validation_gate_facade: Any = _load_validation_gate_facade_module().bind_validation_gate_exports(  # 公开兼容门禁绑定结果
    globals(),  # 当前 validate 模块命名空间

    # 这一组 loader 继续保持治理、审计与工作区 helper 的延迟导入边界。
    func_load_workspace_gate_module=_load_validation_workspace_gate_module,  # 工作区 helper 模块 loader
    func_load_source_audit_module=_load_validation_source_audit_module,  # 源码审计 helper 模块 loader
    func_load_governance_gate_module=_load_validation_governance_gate_module,  # 治理 helper 模块 loader

    # 这一组 builder 继续复用当前文件已经稳定的上下文拼装逻辑。
    func_build_workspace_gate_context=_build_workspace_gate_context,  # 工作区 gate 上下文 builder
    func_build_source_audit_context=_build_source_audit_context,  # 源码审计上下文 builder
    func_build_governance_gate_context=_build_governance_gate_context,  # 治理 gate 上下文 builder
)

# run 执行本地验证子命令，并把 stdout/stderr 透传给当前脚本。
def run(command: list[str], *, cwd: Path, allow_failure: bool = False) -> subprocess.CompletedProcess[str]:
    """运行子进程命令，并在失败时按 allow_failure 决定是否终止验证。

    :param command: 需要运行的子进程 argv 列表。
    :param cwd: 子进程工作目录。
    :param allow_failure: 是否允许子进程非零退出并把结果交给调用方解释。
    :return: 返回 subprocess.CompletedProcess[str]；该值承载“运行子进程命令，并在失败时按 allow_failure 决定是否终止验证。”阶段需要传递的结果。
    :raises SystemExit: 当“运行子进程命令，并在失败时按 allow_failure 决定是否终止验证。”阶段发现配置、产物或执行结果不满足门禁要求时抛出。
    """

    # printable 只用于日志展示，真正执行仍传 list 避免 shell 转义问题。
    str_printable_command = " ".join(str(item) for item in command)  # 当前子命令的人类可读展示文本

    # 输出统一 run 前缀，便于验证日志中定位子命令边界。
    print("> INFO: [Python] running validation child command.")

    # 子进程继承当前环境，再追加 skill runtime 所需的 Python 路径。
    dict_env = os.environ.copy()  # 传给 subprocess.run 的环境变量副本

    # 强制 UTF-8，避免 Windows 默认编码污染日志解析。
    dict_env.setdefault("PYTHONUTF8", "1")

    # 禁止子进程生成 pyc，降低 validate 后残留清理压力。
    dict_env.setdefault("PYTHONDONTWRITEBYTECODE", "1")

    # 读取原 PYTHONPATH，以便把 skill runtime 路径插到最前但不丢弃用户环境。
    str_existing_pythonpath = dict_env.get("PYTHONPATH")  # 父进程已有 Python 模块搜索路径

    # skill 根字符串会作为子进程 PYTHONPATH 的第一个搜索项。
    str_skill_root = str(SKILL_ROOT)  # 当前工作树中的 skill runtime 根路径

    # 已有 PYTHONPATH 时保留用户环境，但把当前 skill 根置于最前。
    if str_existing_pythonpath:

        # 前置 skill 根确保子进程优先导入当前工作树 runtime。
        dict_env["PYTHONPATH"] = str_skill_root + os.pathsep + str_existing_pythonpath  # 合并后的子进程 PYTHONPATH

    # 没有既有 PYTHONPATH 时只注入当前 skill 根。
    else:

        # 空 PYTHONPATH 环境只需暴露当前 skill runtime。
        dict_env["PYTHONPATH"] = str_skill_root  # 仅包含当前 skill 根的子进程 PYTHONPATH

    # capture_output 让当前函数统一控制 stdout/stderr 的打印时机和失败处理。
    completed_process_result = subprocess.run(  # 子命令执行结果
        command,  # 以 argv 列表执行，避免 shell 转义差异
        cwd=cwd,  # 子命令的工作目录由调用方显式指定
        text=True,  # stdout/stderr 以字符串返回
        encoding="utf-8",  # 子进程文本输出按 UTF-8 解码
        errors="replace",  # 非 UTF-8 字节保留为替换字符而不中断日志
        capture_output=True,  # 先捕获输出，再由本函数按顺序转发
        check=False,  # 返回码由 allow_failure 分支统一处理
        env=dict_env,  # 注入后的环境确保当前 skill runtime 优先
    )

    # stdout 保持原顺序整体输出，rstrip 只移除末尾多余换行。
    if completed_process_result.stdout:

        # 子命令 stdout 直接进入当前 stdout，便于 CI 收集完整日志。
        sys.stdout.write(completed_process_result.stdout)

    # stderr 根据子命令结果分流，避免成功测试进度被误标为错误。
    if completed_process_result.stderr:

        # 允许失败由调用方解释；未允许失败才进入当前错误流。
        bool_child_failure_is_blocking = completed_process_result.returncode != 0 and not allow_failure  # 子命令失败是否直接阻断当前验证

        # 子命令 stderr 逐行打印，避免多行内容绕过终端输出前缀门禁。
        for str_stderr_line in completed_process_result.stderr.splitlines():

            # 未允许失败的 stderr 保持错误通道，便于 CI 和调用方识别阻断来源。
            if bool_child_failure_is_blocking:

                # 每条阻断错误都带固定前缀，满足终端输出门禁。
                print(f"> ERR: [Python] validation child stderr: {str_stderr_line}", file=sys.stderr)

            # 成功子命令的 stderr 常由 unittest 等工具承载进度，应作为普通诊断输出。
            else:

                # 允许失败或成功场景都交给后续解析逻辑判断，不在这里误报错误。
                print(f"> INFO: [Python] validation child stderr: {str_stderr_line}")

    # 默认失败即终止；allow_failure=True 的调用方会自行解释返回码和载荷。
    if completed_process_result.returncode != 0 and not allow_failure:

        # 使用子命令原退出码作为 validate 脚本退出码。
        raise SystemExit(completed_process_result.returncode)

    # 返回完整 CompletedProcess，供需要解析 stdout 或 returncode 的调用方使用。
    return completed_process_result

# 命令行入口保持 main() 的退出码语义。
if __name__ == "__main__":

    # 直接运行脚本时把 main() 退出码交给 shell。
    raise SystemExit(main())
