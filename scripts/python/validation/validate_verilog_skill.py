"""运行 readable Verilog generator skill 的本地信心门禁。"""

# 标准库负责参数解析、报告读写、子进程执行和清理校验产物。
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

# dataclass 用于把重复 CLI 参数组收束成清晰的请求对象。
from dataclasses import dataclass

# pathlib 和 typing 提供脚本内路径、处理器签名与 JSON 载荷标注。
from collections.abc import Callable
from pathlib import Path
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
    / ".codex"  # Codex 用户配置目录
    / "skills"  # 本地已安装 skills 根目录
    / "agents-md-generator"  # AGENTS 治理 skill 目录名
    / "scripts"  # 治理 skill 的脚本目录
    / "python"  # 新版 Python 脚本分层目录
    / "docs"  # docs 治理子命令目录
    / "manage_docs.py",  # 当前 agents-md-generator 标准脚本路径
    Path.home() / ".codex" / "skills" / "agents-md-generator" / "scripts" / "manage_docs.py",  # 旧版脚本路径
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
    from scripts.python.workflow.config import load_settings, path_setting

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

    # 临时目录带进程号和时间戳，便于并行 worker 隔离。
    path_smoke_dir = path_smoke_root / f"validate-{os.getpid()}-{int(time.time())}"  # 本轮 smoke 子目录

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

    # 每轮验证开始前清理同名临时产物和旧 pycache。
    cleanup_residuals(validation_context_current.dict_settings, validation_context_current.path_smoke_dir)

    # 外部治理工具只有显式要求时运行，保持安装包内脚本可用。
    run_optional_external_audit(validation_context_current)

    # skill 自包含门禁覆盖配置、文档、CLI 和效果评估。
    path_effectiveness_report = run_self_contained_gates(validation_context_current)  # eval-skill 本地报告路径

    # 发布卫生检查确认旧领域词、硬编码路径和临时 ref 依赖没有回流。
    run_release_hygiene_gates(validation_context_current)

    # 远程门禁按 --with-remote / --require-remote 的旧语义执行。
    run_remote_gate_if_requested(validation_context_current, path_effectiveness_report)

    # 所有门禁结束后再次清理临时产物。
    cleanup_residuals(validation_context_current.dict_settings, validation_context_current.path_smoke_dir)

    # 清理后确认 skill 目录和 smoke 根没有残留禁止项。
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
        run(build_remote_validation_command(validation_context.path_settings, str_remote_server), cwd=PATH_SKILL_ROOT)

        # report-runs 命令只读取最近一次远端运行证据，不重新执行远端流程。
        list_remote_report_command = build_remote_validation_command(  # 远端运行报告命令
            validation_context.path_settings,  # 当前 validate 使用的 settings 文件
            str_remote_server,  # 已解析出的远程服务器标识
            report_runs=True,  # 切换到远端运行证据查询模式
        )

        # report-runs 模式回收最近一次远端运行证据。
        completed_process_remote_runs = run(list_remote_report_command, cwd=PATH_SKILL_ROOT)  # 远端运行报告命令结果

        # stdout 末尾 JSON 是远程运行证据载荷。
        dict_remote_runs_report = parse_json_object(completed_process_remote_runs.stdout)  # 远程运行证据

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

@dataclass(frozen=True)
class VerifyExistingRequest:
    """描述一次 verify-existing CLI 调用需要的路径和模式。"""

    # source 是 verify-existing 的待检查 RTL 文件。
    path_source: Path  # verify-existing 待检查 RTL 文件

    # out_dir 承载该调用生成的报告、patch 和判定文件。
    path_out_dir: Path  # verify-existing 输出目录

    # spec_source 提供自然语言规格约束。
    path_spec_source: Path  # verify-existing 规格文件

    # automation_mode 直接映射 CLI 参数。
    str_automation_mode: str  # 自动化模式

    # tb_mode 控制 verify-existing 是否生成或增强 testbench。
    str_tb_mode: str  # testbench 处理模式

    # testbench_source 仅 augment 模式需要。
    path_testbench_source: Path | None = None  # 显式 testbench 来源

    # decision_source 仅恢复已确认 patch 时需要。
    path_decision_source: Path | None = None  # 用户决策 JSON

    # 本地 smoke 可显式声明严格 CLI 非零属于被验证的人工确认边界。
    bool_allow_strict_exit_failure: bool = False  # 是否允许预期内 strict 非零退出

@dataclass(frozen=True)
class ExistingPatchCase:
    """描述一个 existing RTL 修复确认流程 fixture。"""

    # case 名称同时用于 smoke 子目录。
    str_case_dir: str  # smoke 子目录名称

    # source fixture 是被复制到 smoke 目录后执行修复的 RTL。
    str_source_name: str  # existing_rtl 原始 RTL 文件名

    # spec fixture 提供 verify-existing 规格输入。
    str_spec_name: str  # existing_rtl 规格文件名

    # copy_name 保持旧脚本写入 smoke 的文件名。
    str_copy_name: str  # smoke 中的 RTL 副本名

    # automation mode 保持原 CLI 场景。
    str_automation_mode: str  # 首次 verify-existing 自动化策略

    # decision evidence 写入决策文件，保持旧报告可读性。
    str_decision_evidence: str  # 决策证据文本

    # expected_category 为空时只检查 patch/intervention 产物。
    str_expected_category: str | None = None  # 预期 patch 分类

    # error_label 保留旧断言消息中的场景描述。
    str_error_label: str = "RTL fix"  # 断言错误场景标签

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

    # 本轮 CLI gate 先清理 smoke 子树，避免旧产物影响断言。
    remove_inside_smoke_root(settings, smoke_dir)

    # canonical 流程覆盖 scaffold、prompt、workflow 和 validate 基线。
    run_canonical_cli_flow(path_example_spec, smoke_dir)

    # use-case 示例验证模板选择能贯穿 requirements 和 codegen plan。
    run_use_case_cli_flows(path_use_case_examples_dir, smoke_dir)

    # existing RTL 流程验证半自动边界和 testbench augment 产物。
    run_existing_rtl_boundary_flows(smoke_dir)

    # patch resume 流程验证三类 RTL 修复都必须经决策文件恢复。
    run_existing_rtl_patch_flows(smoke_dir)

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

# read_json_file 集中处理 UTF-8 JSON 读取。
def read_json_file(path_json: Path) -> dict[str, Any]:
    """读取验证产物中的 JSON 对象。

    :param path_json: 需要读取的 JSON 文件路径。
    :return: 返回字典对象；内容来自“读取验证产物中的 JSON 对象。”阶段解析出的治理状态。
    """

    # JSON 文本全部按 UTF-8 读取，匹配 runtime 报告写入方式。
    str_payload = path_json.read_text(encoding="utf-8")  # JSON 文件文本

    # 返回对象保持原脚本 dict 访问语义。
    return json.loads(str_payload)

# run_canonical_cli_flow 覆盖最小端到端生成与验证链。
def run_canonical_cli_flow(path_example_spec: Path, path_smoke_dir: Path) -> None:
    """运行 canonical scaffold/prompt/workflow/validate 流程。

    :param path_example_spec: 示例需求文件路径，供 CLI prompt 或 workflow 阶段读取。
    :param path_smoke_dir: 本轮验证使用的临时 smoke 目录。
    :return: 不返回业务值；执行完成即表示“运行 canonical scaffold/prompt/workflow/validate 流程。”对应步骤未发现阻断。
    :raises AssertionError: 当“运行 canonical scaffold/prompt/workflow/validate 流程。”阶段发现配置、产物或执行结果不满足门禁要求时抛出。
    """

    # 根 CLI 冒烟目录承载 scaffold、prompt 和 validate 的完整链路。
    path_cli_dir = path_smoke_dir / "cli"  # 根 CLI 冒烟链路目录

    # 根 workflow 目录承载 codegen plan 和生成物审计。
    path_workflow_dir = path_smoke_dir / "workflow"  # 根 workflow 规划产物目录

    # validate 报告路径用于检查 warnings 计数。
    path_canonical_report = path_cli_dir / "validation-report.json"  # canonical validate 报告文件

    # scaffold 命令验证 spec 生成入口可用。
    run_verilog_cli("scaffold", "--name", "erie_adapter", "--out", str(path_cli_dir / "spec.json"), "--no-state")

    # prompt 命令验证 canonical spec 能渲染提示词。
    run_verilog_cli("prompt", "--spec", str(path_example_spec), "--out", str(path_cli_dir / "prompt.md"), "--no-state")

    # run-workflow 使用 mock provider，避免本地门禁依赖真实模型。
    run_verilog_cli(
        "run-workflow",
        "--spec",
        str(path_example_spec),
        "--out-dir",
        str(path_workflow_dir),
        "--model-provider",
        "mock",
        "--no-external",
    )

    # validate 检查 mock workflow 生成目录不产生 warning。
    run_verilog_cli(
        "validate",
        "--spec",
        str(path_example_spec),
        "--path",
        str(path_workflow_dir / "attempt-001" / "rtl" / "generated"),
        "--no-external",
        "--report-json",
        str(path_canonical_report),
        "--no-state",
    )

    # 读取 validate 报告并保持旧 warnings==0 断言。
    dict_canonical_payload = read_json_file(path_canonical_report)  # canonical validate 报告的 warning 计数载荷

    # canonical 示例不允许产生 warning。
    if dict_canonical_payload.get("warnings") != 0:

        # 保持旧错误文本中的报告载荷。
        raise AssertionError(f"> ERR: [Python] Canonical validate emitted warnings: {dict_canonical_payload}")

# run_use_case_cli_flows 遍历 use-case 示例并验证模板贯穿。
def run_use_case_cli_flows(path_use_case_examples_dir: Path, path_smoke_dir: Path) -> None:
    """运行所有 use-case 示例的 prompt/workflow/validate 流程。

    :param path_use_case_examples_dir: use-case 示例目录，逐个驱动 prompt/workflow/validate。
    :param path_smoke_dir: 本轮验证使用的临时 smoke 目录。
    :return: 不返回业务值；执行完成即表示“运行所有 use-case 示例的 prompt/workflow/validate 流程。”对应步骤未发现阻断。
    """

    # 每个 JSON 示例都应被独立验证。
    for path_example_spec in sorted(path_use_case_examples_dir.glob("*.json")):

        # family 来自文件名，用于模板 id 断言。
        str_family = path_example_spec.stem  # use-case 模板标识

        # family_dir 隔离每个 use-case 的所有产物。
        path_family_dir = path_smoke_dir / "cli-use-case" / str_family  # 当前 use-case 产物目录

        # 单个 use-case 的 validation-report 用来判断该模板是否产生 warnings。
        path_family_report = path_family_dir / "validation-report.json"  # 该模板 validate 结果文件

        # 单个 use-case 运行完整模板链。
        run_single_use_case_flow(path_example_spec, str_family, path_family_dir, path_family_report)

# run_single_use_case_flow 验证单个 use-case JSON 的产物。
def run_single_use_case_flow(
    path_example_spec: Path,
    str_family: str,
    path_family_dir: Path,
    path_family_report: Path,
) -> None:
    """运行单个 use-case 示例并检查模板 id、计划和 validate 报告。

    :param path_example_spec: 示例需求文件路径，供 CLI prompt 或 workflow 阶段读取。
    :param str_family: use-case family 标识，用于定位模板和报告目录。
    :param path_family_dir: 单个 use-case family 的 smoke 输出目录。
    :param path_family_report: 单个 use-case family 的 validate 报告路径。
    :return: 不返回业务值；执行完成即表示“运行单个 use-case 示例并检查模板 id、计划和 validate 报告。”对应步骤未发现阻断。
    :raises AssertionError: 当“运行单个 use-case 示例并检查模板 id、计划和 validate 报告。”阶段发现配置、产物或执行结果不满足门禁要求时抛出。
    """

    # prompt 输出必须包含 Use-case template 段和 family id。
    path_prompt = path_family_dir / "prompt.md"  # 当前 use-case prompt 文件

    # prompt 命令验证模板段落渲染。
    run_verilog_cli("prompt", "--spec", str(path_example_spec), "--out", str(path_prompt), "--no-state")

    # 读取 prompt 文本用于模板段落断言。
    str_prompt_text = path_prompt.read_text(encoding="utf-8")  # use-case prompt 渲染文本

    # prompt 必须显式呈现所选 use-case 模板。
    if "## Use-case template" not in str_prompt_text or str_family not in str_prompt_text:

        # 保持旧错误文本中的 family 信息。
        raise AssertionError(f"> ERR: [Python] Prompt missing use-case template section for {str_family}.")

    # use-case workflow 目录保存模板专属的 requirements 与 plan。
    path_workflow_dir = path_family_dir / "workflow"  # 模板专属 workflow 目录

    # mock workflow 验证 requirements 和 plan 的模板 id 贯穿。
    run_verilog_cli(
        "run-workflow",
        "--spec",
        str(path_example_spec),
        "--out-dir",
        str(path_workflow_dir),
        "--model-provider",
        "mock",
        "--no-external",
    )

    # 读取 workflow_result 以定位最后一次 attempt。
    dict_workflow_result = read_json_file(path_workflow_dir / "workflow_result.json")  # use-case attempts 与 artifact 路径汇总

    # 最后一次 attempt 是旧脚本校验的生成产物来源。
    dict_attempt = dict_workflow_result["attempts"][-1]  # 最新 workflow 尝试记录

    # requirements artifact_path 指向包含模板选择字段的 workflow 中间产物。
    str_requirements_artifact = (
        dict_attempt["stage_outputs"]["requirements"]["artifact_path"]  # workflow_result 中 requirements 产物路径
    )  # requirements JSON 中 selected_use_case_template_id 的来源路径

    # requirements artifact 记录模板选择结果。
    path_requirements = project_artifact_path(str_requirements_artifact)  # requirements 阶段产物路径

    # codegen plan artifact_path 指向用于复核模板贯穿的计划 JSON。
    str_plan_artifact = dict_attempt["stage_outputs"]["codegen_plan"]["artifact_path"]  # codegen plan 中的模板选择证据路径

    # plan artifact 是模板 id 在规划阶段延续的检查点。
    path_plan = project_artifact_path(str_plan_artifact)  # 模板规划检查点路径

    # requirements 阶段先确认模板 id 已进入 workflow 状态。
    assert_selected_use_case_id(read_json_file(path_requirements), str_family, "Requirements")

    # codegen plan 阶段再确认模板 id 没有被规划步骤丢失。
    assert_selected_use_case_id(read_json_file(path_plan), str_family, "Codegen plan")

    # artifact_dir 是 validate 的生成物输入路径。
    path_generated_dir = project_artifact_path(dict_attempt["artifact_dir"])  # use-case RTL 生成目录

    # validate 报告不允许产生 warning。
    run_verilog_cli(
        "validate",
        "--spec",
        str(path_example_spec),
        "--path",
        str(path_generated_dir),
        "--no-external",
        "--report-json",
        str(path_family_report),
        "--no-state",
    )

    # 读取模板 validate JSON，只关心 warnings 是否为空。
    dict_payload = read_json_file(path_family_report)  # 模板 warnings 校验载荷

    # 每个 use-case 示例都必须 warning-free。
    if dict_payload.get("warnings") != 0:

        # 保持旧错误文本中的 family 和报告载荷。
        raise AssertionError(f"> ERR: [Python] Validate emitted warnings for {str_family}: {dict_payload}")

# assert_selected_use_case_id 保持 requirements/plan 两处一致的错误语义。
def assert_selected_use_case_id(dict_payload: dict[str, Any], str_family: str, str_label: str) -> None:
    """检查 workflow 阶段产物是否保留 use-case 模板 id。

    :param dict_payload: 待检查的 JSON 载荷，通常来自 workflow 或 validate 产物。
    :param str_family: use-case family 标识，用于定位模板和报告目录。
    :param str_label: 错误提示中的阶段标签。
    :return: 不返回业务值；执行完成即表示“检查 workflow 阶段产物是否保留 use-case 模板 id。”对应步骤未发现阻断。
    :raises AssertionError: 当“检查 workflow 阶段产物是否保留 use-case 模板 id。”阶段发现配置、产物或执行结果不满足门禁要求时抛出。
    """

    # 产物中记录的模板 id 必须等于当前示例文件名。
    if dict_payload.get("selected_use_case_template_id") != str_family:

        # 错误消息保留阶段标签，完整载荷留在 workflow 产物文件中。
        raise AssertionError("> ERR: [Python] use-case workflow did not preserve selected template id.")

# run_existing_rtl_boundary_flows 覆盖半自动确认边界和 augment 产物。
def run_existing_rtl_boundary_flows(path_smoke_dir: Path) -> None:
    """验证 existing RTL 的 semi_auto 边界和 testbench augment 产物。

    :param path_smoke_dir: 本轮验证使用的临时 smoke 目录。
    :return: 不返回业务值；执行完成即表示“验证 existing RTL 的 semi_auto 边界和 testbench augment 产物。”对应步骤未发现阻断。
    :raises AssertionError: 当“验证 existing RTL 的 semi_auto 边界和 testbench augment 产物。”阶段发现配置、产物或执行结果不满足门禁要求时抛出。
    """

    # existing RTL 示例目录集中承载 source、spec 和 testbench fixture。
    path_existing_examples_dir = PATH_SKILL_ROOT / "assets" / "examples" / "existing_rtl"  # existing RTL 示例资产目录

    # ready_valid_slice fixture 同时用于 semi_auto 和 augment。
    path_existing_fixture = path_existing_examples_dir / "ready_valid_slice.v"  # ready_valid_slice 示例 RTL 文件

    # 对应规格约束用于 verify-existing。
    path_existing_spec = path_existing_examples_dir / "ready_valid_slice_spec.md"  # ready_valid_slice 规格文档

    # augment 模式显式传入原 testbench。
    path_existing_tb = path_existing_examples_dir / "ready_valid_slice_tb.v"  # ready_valid_slice 原始 testbench 文件

    # semi_auto 目录用于证明源文件未绕过人工确认。
    path_verify_existing_dir = path_smoke_dir / "cli-verify-existing"  # 人工确认边界目录

    # semi_auto 必须要求确认，不能自动修改源。
    run_verify_existing(
        VerifyExistingRequest(
            path_source=path_existing_fixture,
            path_out_dir=path_verify_existing_dir,
            path_spec_source=path_existing_spec,
            str_automation_mode="semi_auto",
            str_tb_mode="generate",
            bool_allow_strict_exit_failure=True,
        )
    )

    # 读取 semi_auto 验证结果。
    dict_verification_result = read_json_file(path_verify_existing_dir / "verification_result.json")  # semi_auto 验证结果

    # source_mutation.confirmation_required 必须为真。
    if not dict_verification_result.get("source_mutation", {}).get("confirmation_required"):

        # 错误文本前缀保持旧语义，载荷帮助定位 source_mutation 状态。
        str_confirmation_prefix = "verify-existing did not preserve semi-auto confirmation boundary: "  # semi_auto 失败前缀

        # 完整错误消息包含验证结果载荷，便于定位 confirmation_required。
        str_confirmation_error = f"{str_confirmation_prefix}{dict_verification_result}"  # semi_auto 确认边界失败诊断

        # 半自动确认边界丢失时阻塞 validate，避免异常直接输出完整 JSON 载荷。
        raise AssertionError("> ERR: [Python] verify-existing did not preserve semi-auto confirmation boundary.")

    # augment 目录用于证明外部 testbench 来源被记录。
    path_augment_dir = path_smoke_dir / "cli-verify-existing-augment"  # testbench 来源合同目录

    # augment 模式必须产生 plan/diff 并保存原 testbench 路径。
    run_verify_existing(
        VerifyExistingRequest(
            path_source=path_existing_fixture,
            path_out_dir=path_augment_dir,
            path_spec_source=path_existing_spec,
            str_automation_mode="conservative",
            str_tb_mode="augment",
            path_testbench_source=path_existing_tb,
            bool_allow_strict_exit_failure=True,
        )
    )

    # 检查 augment plan 和 diff 产物。
    assert_files_exist(
        [path_augment_dir / "tb_augment_plan.json", path_augment_dir / "tb_augment_diff.txt"],
        "verify-existing augment did not emit plan and diff artifacts.",
    )

    # tb_contract 记录原始 testbench 来源。
    dict_augment_contract = read_json_file(path_augment_dir / "tb_contract.json")  # augment testbench 来源合同

    # 原 testbench 路径必须保持显式输入值。
    if dict_augment_contract.get("original_testbench_path") != str(path_existing_tb):

        # 错误文本前缀保持旧语义，合同载荷用于定位 original_testbench_path。
        str_augment_source_prefix = "verify-existing augment did not preserve explicit testbench source: "  # augment 来源错误前缀

        # 完整错误消息包含 tb_contract 载荷，便于定位来源路径漂移。
        str_augment_source_error = f"{str_augment_source_prefix}{dict_augment_contract}"  # augment testbench 来源失败诊断

        # augment 未保留显式 testbench 来源时阻塞 validate。
        raise AssertionError("> ERR: [Python] verify-existing augment did not preserve explicit testbench source.")

# run_existing_rtl_patch_flows 覆盖三类 RTL patch 决策恢复流程。
def run_existing_rtl_patch_flows(path_smoke_dir: Path) -> None:
    """运行 reset、control 和 timing 三类 existing RTL patch 恢复验证。

    :param path_smoke_dir: 本轮验证使用的临时 smoke 目录。
    :return: 不返回业务值；执行完成即表示“运行 reset、control 和 timing 三类 existing RTL patch 恢复验证。”对应步骤未发现阻断。
    """

    # 三个 fixture 保持旧脚本顺序和断言语义。
    tuple_patch_cases = (
        ExistingPatchCase(  # reset 类低风险 patch fixture
            str_case_dir="cli-verify-existing-rtl-fix",  # reset fixture 的 smoke 子目录
            str_source_name="reset_gap_counter.v",  # 带复位缺口的输入 RTL
            str_spec_name="reset_gap_counter_spec.md",  # reset 修复规格说明
            str_copy_name="reset_gap_counter.v",  # 工作目录内复用的 RTL 文件名
            str_automation_mode="conservative",  # reset patch 使用保守自动化策略
            str_decision_evidence="approved low-risk reset patch",  # 恢复决策必须包含的低风险证据
        ),
        ExistingPatchCase(  # FSM control 场景覆盖 case default 风险
            str_case_dir="cli-verify-existing-rtl-control",  # FSM control 补丁验收目录
            str_source_name="fsm_without_default.v",  # 缺少 case default 的 FSM 输入
            str_spec_name="fsm_without_default_spec.md",  # control patch 的规格说明
            str_copy_name="fsm_without_default.v",  # 工作副本保持原 RTL 文件名
            str_automation_mode="auto_apply",  # control 场景验证自动应用路径
            str_decision_evidence="approved control logic patch",  # 恢复决策必须包含的控制补丁证据
            str_expected_category="case_default_completion",  # control patch 期望的分类标签
            str_error_label="control logic patch",  # control 场景失败消息前缀
        ),
        ExistingPatchCase(  # 输出寄存器补丁场景覆盖 timing 风险
            str_case_dir="cli-verify-existing-rtl-timing",  # 输出寄存器场景的 smoke 子目录
            str_source_name="missing_output_register.v",  # 输出寄存器缺失的 RTL 输入
            str_spec_name="missing_output_register_spec.md",  # 输出寄存器补全规格说明
            str_copy_name="missing_output_register.v",  # 输出寄存器工作副本文件名
            str_automation_mode="auto_apply",  # 输出寄存器场景验证自动应用路径
            str_decision_evidence="approved timing register patch",  # 恢复决策必须包含的时序寄存器证据
            str_expected_category="output_register_completion",  # 输出寄存器补全期望分类标签
            str_error_label="timing patch",  # 输出寄存器场景失败消息前缀
        ),
    )  # existing RTL patch 场景清单

    # 每个 patch 场景都先生成干预，再通过 decision 恢复应用。
    for patch_case in tuple_patch_cases:

        # 单个 patch 场景独立运行，避免产物互相影响。
        run_existing_rtl_patch_case(path_smoke_dir, patch_case)

# run_existing_rtl_patch_case 执行单个 patch fixture 的确认恢复。
def run_existing_rtl_patch_case(path_smoke_dir: Path, patch_case: ExistingPatchCase) -> None:
    """执行一个 existing RTL patch 场景的首次检查和 decision 恢复。

    :param path_smoke_dir: 本轮验证使用的临时 smoke 目录。
    :param patch_case: existing RTL patch 场景配置对象。
    :return: 不返回业务值；执行完成即表示“执行一个 existing RTL patch 场景的首次检查和 decision 恢复。”对应步骤未发现阻断。
    :raises AssertionError: 当“执行一个 existing RTL patch 场景的首次检查和 decision 恢复。”阶段发现配置、产物或执行结果不满足门禁要求时抛出。
    """

    # existing_rtl fixture 根目录集中维护。
    path_existing_root = PATH_SKILL_ROOT / "assets" / "examples" / "existing_rtl"  # existing RTL 样例根目录

    # 源 fixture 被复制到 smoke 目录，避免修改仓库样例。
    path_source_fixture = path_existing_root / patch_case.str_source_name  # 原始 RTL 样例文件

    # spec fixture 作为 verify-existing 的规格输入。
    path_spec_fixture = path_existing_root / patch_case.str_spec_name  # 原始规格样例文件

    # 每个场景使用固定 smoke 子目录。
    path_case_dir = path_smoke_dir / patch_case.str_case_dir  # patch 场景输出目录

    # 源文件副本是 verify-existing 的可变目标。
    path_source_copy = path_case_dir / patch_case.str_copy_name  # smoke 中的 RTL 副本

    # 复制前先确保父目录存在。
    path_source_copy.parent.mkdir(parents=True, exist_ok=True)

    # 复制 fixture 文本，保持 UTF-8。
    path_source_copy.write_text(path_source_fixture.read_text(encoding="utf-8"), encoding="utf-8")

    # 首次 verify-existing 生成 patch plan、diff 和 intervention。
    run_verify_existing(
        VerifyExistingRequest(
            path_source=path_source_copy,
            path_out_dir=path_case_dir,
            path_spec_source=path_spec_fixture,
            str_automation_mode=patch_case.str_automation_mode,
            str_tb_mode="generate",
            bool_allow_strict_exit_failure=True,
        )
    )

    # 所有 patch 场景都必须生成 plan 和 diff。
    assert_files_exist(
        [path_case_dir / "rtl_patch_plan.json", path_case_dir / "rtl_patch_diff.txt"],
        "verify-existing RTL fix did not emit patch plan/diff artifacts.",
    )

    # 首次检查必须在应用前生成 intervention 记录。
    assert_files_exist(
        [path_case_dir / "rtl_intervention.json"],
        "verify-existing RTL fix did not emit intervention before apply.",
    )

    # auto_apply 风险场景还要检查降级策略和 patch 分类。
    assert_patch_category_when_expected(path_case_dir, patch_case)

    # decision 文件模拟用户确认应用 patch。
    path_decision = path_case_dir / "decision.json"  # patch 恢复决策文件

    # 写入旧脚本兼容的 resolved 决策载荷。
    write_patch_decision(path_decision, patch_case.str_decision_evidence)

    # 带 decision-source 的第二次运行必须应用 patch。
    run_verify_existing(
        VerifyExistingRequest(
            path_source=path_source_copy,
            path_out_dir=path_case_dir,
            path_spec_source=path_spec_fixture,
            str_automation_mode=patch_case.str_automation_mode,
            str_tb_mode="generate",
            path_decision_source=path_decision,
            bool_allow_strict_exit_failure=True,
        )
    )

    # 恢复后结果必须显示 rtl_mutation.applied。
    dict_resumed_result = read_json_file(path_case_dir / "verification_result.json")  # decision 恢复结果

    # applied 字段必须为真值，保持旧流程的成功判定。
    if not dict_resumed_result.get("rtl_mutation", {}).get("applied"):

        # 恢复失败只报告场景标签，完整 verification_result 已落到场景目录。
        raise AssertionError("> ERR: [Python] RTL patch did not apply after decision resume.")

# run_verify_existing 统一组装 verify-existing 命令。
def run_verify_existing(request: VerifyExistingRequest) -> None:
    """按请求对象运行 verify-existing CLI。

    :param request: verify-existing CLI 请求对象。
    :return: 不返回业务值；执行完成即表示“按请求对象运行 verify-existing CLI。”对应步骤未发现阻断。
    """

    # verify-existing 基础参数覆盖输入 RTL、规格、自动化策略、testbench 模式和本地无外部工具约束。
    list_command_args = [  # 每个 existing RTL fixture 共用的本地修复验证命令参数
        "verify-existing",  # existing RTL 分析与修复子命令
        "--source",  # 输入 RTL 源文件参数名
        str(request.path_source),  # 本次 verify-existing 输入 RTL
        "--out-dir",  # 运行产物目录参数名
        str(request.path_out_dir),  # 本次 verify-existing 输出目录
        "--spec-source",  # 规格约束输入参数名
        str(request.path_spec_source),  # 本次 verify-existing 使用的规格文件
        "--automation-mode",  # 自动化策略参数名
        request.str_automation_mode,  # conservative/semi_auto 等兼容策略
        "--tb-mode",  # testbench 处理模式参数名
        request.str_tb_mode,  # 生成或增强测试平台的处理模式
        "--tb-language",  # testbench 语言参数名
        "verilog",  # 本地门禁固定使用 Verilog testbench
        "--no-external",  # 禁用外部工具以保持本地门禁可重复
        "--no-state",  # 不写 workflow-state，减少 validate 残留
    ]  # verify-existing CLI 参数

    # augment 模式需要显式传入原 testbench。
    if request.path_testbench_source is not None:

        # testbench-source 插入到命令末尾不影响 argparse 语义。
        list_command_args.extend(["--testbench-source", str(request.path_testbench_source)])

    # decision 恢复模式需要显式传入用户决策文件。
    if request.path_decision_source is not None:

        # decision-source 必须放在 verify-existing 参数末尾以模拟用户恢复操作。
        list_command_args.extend(["--decision-source", str(request.path_decision_source)])

    # 统一通过 scripts.python.workflow.cli 官方 CLI 入口运行。
    run_verilog_cli(*list_command_args, allow_failure=request.bool_allow_strict_exit_failure)

# assert_files_exist 保持旧脚本对关键产物缺失的硬失败语义。
def assert_files_exist(list_paths: list[Path], str_message: str) -> None:
    """确认一组关键验证产物已经写出。

    :param list_paths: 必须存在的验证产物路径列表。
    :param str_message: 产物缺失时展示给调用方的错误消息。
    :return: 不返回业务值；执行完成即表示“确认一组关键验证产物已经写出。”对应步骤未发现阻断。
    :raises AssertionError: 当“确认一组关键验证产物已经写出。”阶段发现配置、产物或执行结果不满足门禁要求时抛出。
    """

    # 任一关键文件缺失都表示对应 CLI 子流程失效。
    if not all(path_item.exists() for path_item in list_paths):

        # 调用方传入旧错误文本，但异常只保留固定门禁摘要。
        raise AssertionError("> ERR: [Python] required validation artifacts are missing.")

# assert_patch_category_when_expected 只检查高风险 patch 场景的降级分类。
def assert_patch_category_when_expected(path_case_dir: Path, patch_case: ExistingPatchCase) -> None:
    """检查 auto_apply 场景是否降级为确认并标出预期 patch 分类。

    :param path_case_dir: existing RTL patch 场景的运行目录。
    :param patch_case: existing RTL patch 场景配置对象。
    :return: 不返回业务值；执行完成即表示“检查 auto_apply 场景是否降级为确认并标出预期 patch 分类。”对应步骤未发现阻断。
    :raises AssertionError: 当“检查 auto_apply 场景是否降级为确认并标出预期 patch 分类。”阶段发现配置、产物或执行结果不满足门禁要求时抛出。
    """

    # reset_gap_counter 场景没有旧脚本中的 category 断言。
    if patch_case.str_expected_category is None:

        # 无需分类检查的场景直接返回。
        return

    # 首次验证结果应显示 auto_apply 被降级为确认。
    dict_fix_result = read_json_file(path_case_dir / "verification_result.json")  # 首次 patch 验证结果

    # patch plan 中记录具体 patch 分类。
    dict_patch_plan = read_json_file(path_case_dir / "rtl_patch_plan.json")  # RTL patch 计划载荷

    # mutation 子对象持有 policy 和 applied 状态。
    dict_mutation = dict_fix_result.get("rtl_mutation", {})  # RTL 修改策略状态

    # 高风险 patch 必须要求确认，且首次运行不能直接应用。
    if dict_mutation.get("policy") != "confirm_before_apply" or dict_mutation.get("applied"):

        # 高风险 patch 不能绕过人工确认边界。
        str_policy_error = (  # 高风险 patch 未降级时的简要诊断
            f"{patch_case.str_error_label} did not downgrade auto_apply to confirmation."  # 场景标签与策略错误说明
        )

        # 策略边界失效时终止 patch fixture 验证。
        raise AssertionError("> ERR: [Python] high-risk RTL patch bypassed confirmation policy.")

    # patch 分类必须匹配 fixture 对应风险类型。
    if dict_patch_plan.get("patch_category") != patch_case.str_expected_category:

        # 仅报告预期分类，完整 patch plan 已落到场景目录。
        raise AssertionError("> ERR: [Python] expected RTL patch category was not detected.")

# write_patch_decision 写出 verify-existing 恢复流程需要的决策 JSON。
def write_patch_decision(path_decision: Path, str_evidence: str) -> None:
    """写入用户确认应用 RTL patch 的决策文件。

    :param path_decision: 写入 patch 决策 JSON 的目标路径。
    :param str_evidence: 用户确认 patch 的证据文本。
    :return: 不返回业务值；执行完成即表示“写入用户确认应用 RTL patch 的决策文件。”对应步骤未发现阻断。
    """

    # 决策载荷字段和值保持旧脚本兼容。
    dict_decision = {
        "version": 1,  # decision 文件格式版本
        "status": "resolved",  # 表示用户已完成 patch 决策
        "decision": "apply_rtl_patch",  # 恢复流程应应用 RTL patch
        "evidence": [str_evidence],  # 用户确认 patch 的证据文本
        "constraints": ["preserve interface"],  # patch 应保持模块接口不变
        "affected_subfunctions": ["*"],  # 当前 fixture 允许 patch 覆盖全部内部逻辑
    }  # verify-existing decision JSON 载荷

    # 写入 UTF-8 JSON，ensure_ascii=False 保留旧脚本输出格式。
    path_decision.write_text(json.dumps(dict_decision, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

# build_remote_validation_command 保持远程 gate 子进程命令的兼容拼装顺序。
def build_remote_validation_command(
    settings_path: Path,
    remote_server: str | None,
    *,
    report_runs: bool = False,
) -> list[str]:
    """组装远程验证脚本命令，保持外层 gate 调用参数顺序稳定。

    :param settings_path: 远程验证使用的 settings 文件路径。
    :param remote_server: 显式指定或已确认的远程服务器标识。
    :param report_runs: 是否要求远程验证脚本报告保留运行目录。
    :return: 返回列表对象；元素顺序服务于“组装远程验证脚本命令，保持外层 gate 调用参数顺序稳定。”阶段的后续调用。
    """

    # 远程验证脚本既支持源码仓库，也支持安装副本，因此统一走模块入口。
    list_command = [
        sys.executable,  # 远程 gate 子进程沿用当前解释器
        "-m",  # 远程 gate 子进程使用模块入口执行
        REMOTE_VALIDATE_MODULE,  # 当前 skill 内的远程验证官方模块
        "--settings",  # 远程验证 settings 参数名
        str(settings_path),  # 本轮 validate 使用的 settings 文件
    ]  # 传给 remote validate 模块的基础命令

    # 显式服务器只在调用者指定时透传，避免覆盖项目已确认选择。
    if remote_server:

        # --server 参数只表达本次运行覆盖，不写回项目远程选择状态。
        list_command.extend(["--server", remote_server])

    # 报告模式只拉取最近一次 run，避免完整远程验证时产生额外遍历成本。
    if report_runs:

        # --max-runs=1 让报告模式输出可预测且不会枚举历史 run 目录。
        list_command.extend(["--report-runs", "--max-runs", "1"])

    # 返回 list 形式供 run() 直接传给 subprocess，避免 shell 转义差异。
    return list_command

# resolve_remote_server 只提取已确认选择中的 server_id。
def resolve_remote_server(settings: dict) -> str | None:
    """读取项目已确认远程服务器；未配置时返回 None 让调用者跳过远程 gate。

    :param settings: Verilog skill 治理配置字典，提供路径、依赖和验证开关。
    :return: 返回字符串结果；用于“读取项目已确认远程服务器；未配置时返回 None 让调用者跳过远程 gate。”阶段向调用方传递解析后的文本值。
    """

    # 远程选择 helper 延迟导入，避免脚本导入阶段读取本地私有状态。
    from scripts.python.remote.remote_selection import resolve_confirmed_remote_server

    # remote_selection 返回完整选择对象，这里只暴露 validate 脚本需要的 server_id。
    dict_selection = resolve_confirmed_remote_server(settings)  # 项目本地远程选择载荷

    # 未确认远程服务器时，调用方负责决定是否跳过远程验证。
    if not dict_selection:

        # None 与旧逻辑保持一致，表示没有可用默认远程服务器。
        return None

    # server_id 是远程脚本命令行唯一需要的选择字段。
    return str(dict_selection["server_id"])

# resolve_required_remote_validation_state 负责远程 gate 的前置状态校验。
def resolve_required_remote_validation_state(
    settings: dict,
    *,
    explicit_server: str | None = None,
) -> dict[str, str | dict]:
    """校验远程验证的本地选择状态，并返回远程运行所需的最小上下文。

    :param settings: Verilog skill 治理配置字典，提供路径、依赖和验证开关。
    :param explicit_server: 命令行显式指定的远程服务器标识。
    :return: 返回字典对象；内容来自“校验远程验证的本地选择状态，并返回远程运行所需的最小上下文。”阶段解析出的治理状态。
    :raises AssertionError: 当“校验远程验证的本地选择状态，并返回远程运行所需的最小上下文。”阶段发现配置、产物或执行结果不满足门禁要求时抛出。
    """

    # 远程配置 helper 延迟导入，保证无远程 gate 时不触碰私有配置。
    from scripts.python.workflow.config import remote_setting
    from scripts.python.remote.remote_selection import (
        remote_runtime_config_relpath,
        resolve_confirmed_remote_server,
    )

    # 元数据标记 legacy 状态时，必须要求用户迁移到项目本地 .settings 选择文件。
    dict_meta = settings.get("__verilog_settings_meta__", {})  # 配置加载器注入的远程状态元数据

    # legacy_remote_state 表示仍存在旧位置的远程选择状态。
    bool_has_legacy_remote_state = isinstance(dict_meta, dict) and bool(dict_meta.get("legacy_remote_state"))  # 是否检测到旧远程状态

    # local_settings_loaded 表示项目本地 settings 已覆盖旧状态。
    bool_has_project_settings = isinstance(dict_meta, dict) and bool(dict_meta.get("local_settings_loaded"))  # 是否已加载项目本地配置

    # legacy 状态只在没有项目本地 settings 覆盖时阻塞，避免误伤已迁移配置。
    if bool_has_legacy_remote_state and not bool_has_project_settings:

        # 错误文本提示用户完成远程选择迁移，而不是静默读旧状态。
        str_legacy_state_message = (
            "Remote validation found legacy .erie-verilog-generator-state remote settings. "  # 旧状态文件命中说明
            "Migrate the selected server into .settings/remote-selection.local.json and regenerate "  # 目标选择文件提示
            ".settings/server_list.local.json before running the remote gate."  # 连接清单重建要求
        )  # legacy 远程状态迁移失败说明

        # 远程 gate 必须在明确项目本地选择后运行，防止误连旧服务器。
        raise AssertionError(
            "> ERR: [Python] legacy remote validation state must be migrated before running remote gate."
        )

    # 命令行参数优先；为空时回退到项目已确认的远程服务器选择。
    str_server_id = (explicit_server or "").strip()  # 本次远程验证最终使用的服务器标识

    # 未传 --remote-server 时，从项目本地确认文件中读取默认服务器。
    if not str_server_id:

        # 项目本地选择载荷提供 require-remote 缺省服务器。
        dict_selection = resolve_confirmed_remote_server(settings)  # remote-selection.local.json 解析结果

        # 没有显式服务器也没有确认选择时，远程 gate 无法安全运行。
        if not dict_selection:

            # 保留原错误语义，明确列出两种合法来源。
            str_missing_selection_message = (
                "Remote validation requires an explicit --remote-server or a confirmed project-local "  # 显式参数或项目选择二选一
                ".settings/remote-selection.local.json selection."  # 项目本地选择文件路径
            )  # 缺少远程服务器选择时的诊断文本

            # 抛出 AssertionError 让上层验证脚本按 gate 失败处理。
            raise AssertionError(
                "> ERR: [Python] remote validation requires an explicit or confirmed server selection."
            )

        # server_id 读取后转成字符串以统一远程命令参数。
        str_server_id = str(dict_selection["server_id"])  # 远程命令使用的 server id

    # server_list.local.json 承载连接参数，远程 gate 运行前必须已由治理流程生成。
    path_server_list = Path(remote_setting(settings, "server_list"))  # 本地私有远程服务器清单路径

    # 连接清单缺失时阻止远程 gate，避免 remote_validate 进入半配置状态。
    if not path_server_list.exists():

        # 错误文本指向治理生成物，便于用户补齐本地私有配置。
        str_missing_server_list_message = (
            "Remote validation requires .settings/server_list.local.json before the remote gate can run."  # 私有连接清单缺失诊断
        )  # 缺少 server_list.local.json 时的诊断文本

        # 本地连接清单是远程执行的硬前置条件。
        raise AssertionError("> ERR: [Python] remote validation requires server_list.local.json.")

    # 返回值保持旧 wire shape：server_id 与 runtime config 相对路径。
    return {
        "server_id": str_server_id,
        "remote_runtime_config": remote_runtime_config_relpath(settings),
    }

# run_work_folder_gate 桥接 agents-md-generator 文档治理脚本。
def run_work_folder_gate(*, require_external: bool = True) -> dict[str, str]:
    """运行 AGENTS 文档治理 gate；可选依赖缺失时按调用方策略跳过。

    :param require_external: 是否要求外部 docs 治理工具必须可用。
    :return: 返回字典对象；内容来自“运行 AGENTS 文档治理 gate；可选依赖缺失时按调用方策略跳过。”阶段解析出的治理状态。
    :raises FileNotFoundError、SystemExit: 当“运行 AGENTS 文档治理 gate；可选依赖缺失时按调用方策略跳过。”阶段发现配置、产物或执行结果不满足门禁要求时抛出。
    """

    # 外部治理脚本缺失时，发布路径必须失败，开发路径可记录 skipped 状态。
    if not MANAGE_DOCS_SCRIPT.exists():

        # require_external=True 用于严格验证路径，缺失治理脚本必须暴露。
        if require_external:

            # 严格路径不能静默跳过治理脚本，否则 validate 结果会误报通过。
            raise FileNotFoundError("> ERR: [Python] Missing manage_docs.py gate script.")

        # 本地开发环境允许没有外部治理脚本，但必须在摘要中留下 skipped 原因。
        str_skip_message = (
            "[warn] optional work-folder gate skipped because manage_docs.py is unavailable: "  # 开发期跳过原因前缀
            f"{MANAGE_DOCS_SCRIPT}"  # 缺失的治理脚本绝对路径
        )  # 外部治理脚本缺失时的开发期提示

        # 打印 skipped 说明，让人工日志能看到该 gate 没有实际执行。
        print("> INFO: [Python] optional work-folder gate skipped because manage_docs.py is unavailable.")

        # skipped 状态进入最终验证摘要，提示该 gate 未实际执行。
        return {"status": "skipped", "reason": "missing_external_governance"}

    # work-folder-gate 命令保持 manage_docs.py 的既有参数顺序。
    list_work_folder_gate_command = [
        sys.executable,  # 文档治理子进程沿用当前解释器
        str(MANAGE_DOCS_SCRIPT),  # agents-md-generator 文档治理入口
        "work-folder-gate",  # 文档治理 gate 子命令
        ".",  # 当前仓库根作为治理项目
        "--skill-dir",  # skill 主体目录参数名
        "skills/readable-verilog-generator",  # 当前 skill 主体相对路径
        "--mode",  # 治理模式参数名
        "development",  # 开发期允许 dirty worktree advisory
    ]  # work-folder-gate 子进程命令

    # work-folder-gate 输出由 manage_docs 控制；这里只归一化本脚本需要的状态。
    completed_process_result = run(  # work-folder-gate 子进程执行结果
        list_work_folder_gate_command,  # manage_docs work-folder-gate 命令参数列表
        cwd=PROJECT_ROOT,  # 治理脚本必须从仓库根解析 AGENTS 和 docs
        allow_failure=True,  # 返回码交给本函数区分 advisory 与 hard fail
    )

    # 外部治理脚本零退出码表示 AGENTS、目录和分支前置检查已通过。
    if completed_process_result.returncode == 0:

        # passed 状态用于最终摘要，不泄露 manage_docs 的内部 JSON 结构。
        return {"status": "passed", "reason": "external_governance_ok"}

    # stdout/stderr 合并后先识别瞬时产物竞态，兼容“stdout 有 JSON、stderr 有 traceback”的混合失败。
    str_combined_output = (  # 合并治理脚本的 stdout/stderr 以识别混合型瞬态缺失
        f"{completed_process_result.stdout}\n{completed_process_result.stderr}"  # 保留首次失败的完整文本诊断
    )  # work-folder-gate 首次失败的完整诊断

    # 只有 validate 会主动清理的临时产物缺失才执行一次重试。
    if _is_transient_work_folder_gate_failure(str_combined_output):

        # 重试仍然 allow_failure，由下面的解析和退出码分支保留真实结果。
        completed_process_retry = run(  # work-folder-gate 瞬态缺失后的单次重试结果
            list_work_folder_gate_command,  # work-folder-gate 的重试命令参数
            cwd=PROJECT_ROOT,  # 重试仍在仓库根目录执行外部治理脚本
            allow_failure=True,  # 保留重试退出码供后续 advisory/failed 分支继续判断
        )  # 瞬时产物失败后的 work-folder-gate 重试结果

        # 重试成功时直接返回 passed，说明首次失败确实只是瞬时竞态。
        if completed_process_retry.returncode == 0:

            # 重试成功表示外部治理 gate 已经实际通过。
            return {"status": "passed", "reason": "external_governance_ok"}

        # 后续解析和 advisory 判断统一以重试结果为准。
        completed_process_result = completed_process_retry  # 重试后仍失败时改用最新诊断

    # traceback 风格重试之后，再尝试解析 manage_docs 的结构化 JSON 诊断。
    try:

        # 非零退出码优先尝试解析 JSON 诊断，兼容 manage_docs 的既有输出。
        dict_payload = parse_json_object(completed_process_result.stdout)  # work-folder-gate 失败时的 JSON 诊断

    # 没有 JSON 时说明治理脚本既失败又未给出结构化摘要，直接透传退出码。
    except ValueError:

        # 说明 manage_docs 未给出可解析诊断；瞬时竞态已在上方处理过，这里直接保留失败。
        raise SystemExit(completed_process_result.returncode)

    # 只有脏树分支治理失败可降级，其他治理失败继续终止脚本。
    if _is_advisory_work_folder_gate_failure(dict_payload):

        # advisory 提示只覆盖 dirty worktree 开发期例外，其他失败不会进入这里。
        str_advisory_message = (
            "[warn] work-folder-gate reported only in-progress branch governance issues; "  # dirty worktree 降级说明
            "continuing development validation."  # 开发期继续执行验证提示
        )  # dirty worktree advisory 降级提示

        # 输出 advisory 提示，明确本次继续验证是开发期例外。
        print("> WARNING: [Python] work-folder gate reported only in-progress branch governance issues.")

        # advisory 表示治理脚本运行过，但当前开发脏树被接受为进行中状态。
        return {"status": "advisory", "reason": "dirty_worktree_only"}

    # 非 advisory 的外部治理失败保留原退出码，便于 CI 或调用者定位。
    raise SystemExit(completed_process_result.returncode)

# _is_advisory_work_folder_gate_failure 只放行当前开发期脏树提示。
def _is_advisory_work_folder_gate_failure(payload: dict) -> bool:
    """识别仅由 dirty worktree 触发的分支治理失败。

    :param payload: 外部 gate 返回的 JSON 载荷。
    :return: 返回布尔值；True 表示“识别仅由 dirty worktree 触发的分支治理失败。”对应条件命中。
    """

    # 只允许单一错误降级，避免吞掉文档缺失或结构治理失败。
    list_errors = payload.get("errors", [])  # manage_docs 返回的错误列表

    # 多个错误意味着不只是脏树，需要保持 gate 失败。
    if not isinstance(list_errors, list) or len(list_errors) != 1:

        # 非单一错误不能按 dirty worktree advisory 降级。
        return False

    # 唯一错误文本用于匹配 branch-gate 的 dirty worktree 诊断。
    str_error = list_errors[0]  # branch-gate 失败消息文本

    # 非字符串错误无法可靠匹配治理脚本的人类可读诊断。
    if not isinstance(str_error, str):

        # 结构化或空错误不参与文本片段匹配。
        return False

    # 错误文本来自 manage_docs.py；两个片段同时存在才视为开发期可接受脏树提示。
    # branch-gate 前缀确认该错误来自分支治理而非文档或目录治理。
    bool_mentions_branch_gate = "branch-gate:" in str_error  # 是否来自分支治理 gate

    # dirty worktree 片段是唯一允许降级为 advisory 的治理诊断。
    str_dirty_worktree_fragment = "worktree must be clean before continuing under strict branch governance"  # 脏工作树诊断片段

    # 同时命中 branch-gate 和 dirty worktree 才能继续开发期验证。
    bool_mentions_dirty_worktree = str_dirty_worktree_fragment in str_error  # 是否明确指向脏工作树

    # 两个文本特征缺一不可，防止其他 branch-gate 错误被误放行。
    if not bool_mentions_branch_gate or not bool_mentions_dirty_worktree:

        # 不是严格脏树治理错误时保持失败。
        return False

    # branch_gate 子对象承载 strict branch governance 的结构化决策。
    dict_branch_gate = payload.get("branch_gate", {})  # 分支治理决策详情

    # branch_gate 必须是结构化对象，避免仅凭错误字符串放行。
    if not isinstance(dict_branch_gate, dict):

        # 缺少结构化决策时不能确认是可降级场景。
        return False

    # 只有 blocked 决策才对应当前 strict branch governance 的脏树阻塞。
    if dict_branch_gate.get("decision") != "blocked":

        # 非 blocked 决策不符合 advisory 降级模型。
        return False

    # reasons 必须完全等于 dirty worktree 原因，不能把其他 blocked 理由一起放行。
    list_reasons = dict_branch_gate.get("reasons", [])  # 分支治理给出的阻塞原因列表

    # 精确匹配单一原因，防止其他分支治理失败被误判为 advisory。
    return (
        isinstance(list_reasons, list)
        and list_reasons == ["worktree must be clean before continuing under strict branch governance"]
    )

# _has_transient_artifact_marker 统一识别 validate 会主动清理的局部运行产物路径。
def _has_transient_artifact_marker(text: str) -> bool:
    """判断文本是否命中 validate 可自动清理的瞬时产物路径。

    :param text: 需要检查的诊断文本。
    :return: 返回布尔值；True 表示文本里已经出现可自动清理的瞬时产物路径。
    """

    # validate 只会自动清理 smoke 目录和 Python 缓存目录，其他路径都不应触发重试。
    return "_smoke_runs" in text or "__pycache__" in text

# _is_dirty_worktree_branch_gate_message 统一匹配开发期允许 advisory 的脏树分支治理文案。
def _is_dirty_worktree_branch_gate_message(message: str) -> bool:
    """判断错误消息是否对应 dirty worktree 的 branch-gate advisory。

    :param message: 需要检查的单条错误消息文本。
    :return: 返回布尔值；True 表示消息匹配开发期允许 advisory 的 dirty worktree branch-gate 文案。
    """

    # dirty worktree advisory 必须同时带 branch-gate 前缀和固定治理片段。
    return (
        "branch-gate:" in message
        and "worktree must be clean before continuing under strict branch governance" in message
    )

# _payload_has_only_transient_artifact_errors 判断 JSON 载荷是否只包含可安全重试的瞬态工件错误。
def _payload_has_only_transient_artifact_errors(payload: dict) -> bool:
    """判断 JSON 诊断是否只包含瞬态运行产物错误与 dirty worktree advisory。

    :param payload: 外部治理工具返回的 JSON 诊断载荷。
    :return: 返回布尔值；True 表示 errors 里只包含可安全补偿重试的瞬态工件错误与 dirty worktree advisory。
    """

    # errors 列表是治理工具暴露阻塞问题的统一入口。
    list_errors = payload.get("errors", [])  # JSON 载荷中的错误列表

    # 没有列表型 errors 时无法确认属于可安全重试的瞬态工件失败。
    if not isinstance(list_errors, list) or not list_errors:

        # 非标准错误列表不参与瞬态工件重试判定。
        return False

    # 只有至少一个瞬态工件错误时，整个 JSON 载荷才值得执行一次补偿重试。
    bool_saw_transient_artifact = False  # 当前错误列表里是否已命中过瞬态工件路径

    # 逐条校验错误文本，拒绝把其他真实治理失败与瞬态工件错误一起吞掉。
    for value_error in list_errors:

        # 非字符串错误不具备稳定的文本判定语义。
        if not isinstance(value_error, str):

            # 结构异常时宁可保持失败，也不盲目重试。
            return False

        # 瞬态工件路径错误允许通过单次重试自我修复。
        if _has_transient_artifact_marker(value_error):

            # 标记至少发现过一个真正的瞬态工件错误。
            bool_saw_transient_artifact = True  # 当前 errors 列表已经命中过真正的瞬态工件路径

            # 继续检查后续错误，确保整份 errors 列表没有夹带其他真实治理失败。
            continue

        # dirty worktree advisory 允许与瞬态工件错误共存，重试后仍会走 advisory 分支。
        if _is_dirty_worktree_branch_gate_message(value_error):

            # 开发期脏树 advisory 本身不阻止本次瞬态工件补偿重试。
            continue

        # 只要夹带任意其他治理失败，就不能把整次失败当成瞬态工件问题。
        return False

    # 必须真实看到过瞬态工件错误，不能只因为 dirty worktree advisory 就触发重试。
    return bool_saw_transient_artifact

# _is_transient_work_folder_gate_failure 只识别可安全重试的治理脚本瞬时产物缺失。
def _is_transient_work_folder_gate_failure(output: str) -> bool:
    """判断 work-folder-gate 失败是否属于瞬时运行产物缺失。

    :param output: 子命令输出文本，包含可能嵌入的 JSON 对象。
    :return: 返回布尔值；True 表示“判断 work-folder-gate 失败是否属于瞬时运行产物缺失。”对应条件命中。
    """

    # work-folder-gate 的瞬时缺失模式与 audit 一致，共享同一组可重试产物判据。
    return _is_transient_audit_artifact_failure(output)

# verify_skill_effectiveness 检查 skill-effectiveness JSON 摘要是否通过。
def verify_skill_effectiveness(report_path: Path) -> None:
    """读取 effectiveness 报告并在 summary.ok 非 True 时失败。

    :param report_path: 需要读取并校验的报告文件路径。
    :return: 不返回业务值；执行完成即表示“读取 effectiveness 报告并在 summary.ok 非 True 时失败。”对应步骤未发现阻断。
    :raises AssertionError: 当“读取 effectiveness 报告并在 summary.ok 非 True 时失败。”阶段发现配置、产物或执行结果不满足门禁要求时抛出。
    """

    # 报告文件由 runtime 工具生成，这里只读取 JSON 并检查摘要字段。
    dict_payload = json.loads(report_path.read_text(encoding="utf-8"))  # skill-effectiveness 报告载荷

    # summary 保留原始对象用于失败消息，便于定位具体 gate。
    dict_summary = dict_payload.get("summary", {})  # effectiveness 报告摘要字段

    # summary.ok 必须是布尔 True，字符串或数字真值不能通过 gate。
    value_summary_ok = dict_summary.get("ok")  # effectiveness 报告中的原始 ok 字段

    # 严格布尔判定复刻旧 `is True` 语义，同时避开布尔身份比较。
    bool_gate_ok = isinstance(value_summary_ok, bool) and value_summary_ok  # ok 字段是否为严格布尔通过值

    # summary.ok 不是 True 时，验证脚本必须暴露报告摘要。
    if not bool_gate_ok:

        # 失败消息保留 summary 载荷，便于定位具体 effectiveness 项。
        raise AssertionError(f"> ERR: [Python] Skill-effectiveness gate failed: {dict_summary}")

# verify_audit_skill_report 检查 skill audit JSON 是否包含阻塞错误。
def verify_audit_skill_report(output: str) -> None:
    """解析 audit 输出中的 JSON 对象，并在 errors 非空时失败。

    :param output: 子命令输出文本，包含可能嵌入的 JSON 对象。
    :return: 不返回业务值；执行完成即表示“解析 audit 输出中的 JSON 对象，并在 errors 非空时失败。”对应步骤未发现阻断。
    :raises AssertionError: 当“解析 audit 输出中的 JSON 对象，并在 errors 非空时失败。”阶段发现配置、产物或执行结果不满足门禁要求时抛出。
    """

    # audit 命令可能输出普通日志，parse_json_object 会从尾部寻找 JSON 对象。
    dict_payload = parse_json_object(output)  # audit 命令输出中的结构化 JSON 载荷

    # errors 字段承载 skill audit 的阻塞问题列表。
    list_errors = dict_payload.get("errors", [])  # audit 报告中的阻塞错误列表

    # 只有列表型且非空的 errors 需要终止 validate。
    if isinstance(list_errors, list) and list_errors:

        # audit 错误摘要合并到单行，保持 AssertionError 文本紧凑。
        str_joined_errors = "; ".join(str(item) for item in list_errors)  # 拼接后的 audit 错误摘要

        # 错误摘要进入 AssertionError，保持 validate 的失败语义。
        raise AssertionError("> ERR: [Python] Skill audit reported blocking errors: " + str_joined_errors)

# run_audit_skill 执行 skill audit，并处理 smoke 目录瞬时残留导致的一次重试。
def run_audit_skill(settings: dict, smoke_dir: Path) -> None:
    """运行 skill audit；遇到 _smoke_runs 瞬时缺失时清理后重试一次。

    :param settings: Verilog skill 治理配置字典，提供路径、依赖和验证开关。
    :param smoke_dir: 本轮验证使用的临时 smoke 目录。
    :return: 不返回业务值；执行完成即表示“运行 skill audit；遇到 _smoke_runs 瞬时缺失时清理后重试一次。”对应步骤未发现阻断。
    :raises SystemExit: 当“运行 skill audit；遇到 _smoke_runs 瞬时缺失时清理后重试一次。”阶段发现配置、产物或执行结果不满足门禁要求时抛出。
    """

    # audit_skill 路径来自 runtime config，延迟导入避免普通 import 产生路径副作用。
    from scripts.python.workflow.config import path_setting

    # audit_skill 路径来自配置，保持 validate 脚本对工具位置的可配置性。
    list_command = [
        sys.executable,  # audit 子进程沿用 validate 的解释器
        str(path_setting(settings, "audit_skill")),  # 配置指定的 audit_skill 脚本
        str(SKILL_ROOT),  # 当前 skill 主体目录
    ]  # skill audit 子命令

    # audit 前先清理旧残留，减少历史运行产物干扰。
    cleanup_residuals(settings, smoke_dir)

    # allow_failure=True 让本函数解析 stdout 后决定是否需要重试或失败。
    completed_process_result = run(list_command, cwd=validation_workspace_root(), allow_failure=True)  # 首次 audit 执行结果

    # audit 成功时仍需解析 JSON，确认内部 errors 字段为空。
    if completed_process_result.returncode == 0:

        # 成功退出码不代表 audit JSON 无错误，需要继续检查报告载荷。
        verify_audit_skill_report(completed_process_result.stdout)

        # 首次运行通过后不再触发清理重试。
        return

    # stdout/stderr 合并后用于识别 Windows 下 smoke 目录瞬时缺失类失败。
    str_combined_output = f"{completed_process_result.stdout}\n{completed_process_result.stderr}"  # audit 失败诊断全文

    # 只有明确命中临时运行产物的 FileNotFoundError 才执行一次清理重试。
    if _is_transient_audit_artifact_failure(str_combined_output):

        # 清理 audit 运行产物后重试，避免偶发残留让本地门禁误失败。
        cleanup_audit_retry_local_artifacts(settings, smoke_dir)

        # 重试仍然 allow_failure，由下面分支保留真实退出码。
        completed_process_retry = run(list_command, cwd=validation_workspace_root(), allow_failure=True)  # 清理后的 audit 重试结果

        # 重试成功时同样检查 audit JSON 内部错误。
        if completed_process_retry.returncode == 0:

            # audit JSON 的 errors 字段为空才算最终通过。
            verify_audit_skill_report(completed_process_retry.stdout)

            # 重试通过后结束 audit gate。
            return

        # 重试仍失败时保留重试子进程的退出码。
        raise SystemExit(completed_process_retry.returncode)

    # 非瞬时 smoke 失败直接透传首次 audit 退出码。
    raise SystemExit(completed_process_result.returncode)

# _is_transient_audit_artifact_failure 匹配 audit 运行产物被并发清理的偶发错误。
def _is_transient_audit_artifact_failure(output: str) -> bool:
    """判断 audit 失败是否属于瞬时运行产物缺失。

    :param output: 子命令输出文本，包含可能嵌入的 JSON 对象。
    :return: 返回布尔值；True 表示“判断 audit 失败是否属于瞬时运行产物缺失。”对应条件命中。
    """

    # 缺少 validate 可自动清理的局部运行产物路径时，不应触发补偿重试。
    if not _has_transient_artifact_marker(output):

        # 不包含瞬态工件路径时直接判定为不可重试。
        return False

    # traceback 风格的 FileNotFoundError 仍然沿用既有重试语义。
    if "FileNotFoundError" in output:

        # traceback 已经明确指向瞬态工件缺失，可立即进入补偿重试。
        return True

    # 没有 traceback 时再尝试解析 JSON，覆盖 stdout-only 的治理诊断载荷。
    try:

        # parse_json_object 会从混合日志尾部提取最后一个 JSON 对象。
        dict_payload = parse_json_object(output)  # 瞬态工件失败场景的 JSON 诊断载荷

    # 非 JSON 输出又没有 traceback 时，说明本次失败不属于已知可重试模式。
    except ValueError:

        # 未命中结构化 JSON 诊断时保持失败，避免误放行其他问题。
        return False

    # 只有 JSON errors 里仅包含瞬态工件错误时，才执行一次补偿重试。
    return _payload_has_only_transient_artifact_errors(dict_payload)

# parse_json_object 从混合日志尾部提取最后一个 JSON 对象。
def parse_json_object(output: str) -> dict:
    """从命令输出中向后搜索并解析 JSON object。

    :param output: 子命令输出文本，包含可能嵌入的 JSON 对象。
    :return: 返回字典对象；内容来自“从命令输出中向后搜索并解析 JSON object。”阶段解析出的治理状态。
    :raises ValueError: 当“从命令输出中向后搜索并解析 JSON object。”阶段发现配置、产物或执行结果不满足门禁要求时抛出。
    """

    # 记录所有左花括号位置，从尾部开始尝试可避免前置日志干扰。
    list_starts = [index for index, char in enumerate(output) if char == "{"]  # 可能的 JSON object 起点

    # 从最后一个左花括号倒序尝试，优先解析命令尾部 JSON 摘要。
    for int_start in reversed(list_starts):

        # candidate 是从当前左花括号到输出末尾的 JSON 候选片段。
        str_candidate = output[int_start:].strip()  # 待尝试解析的 JSON 字符串片段

        # 空候选片段不可能构成 JSON object。
        if not str_candidate:

            # 空片段没有可解析内容，继续尝试更早的左花括号。
            continue

        # 某些日志中包含花括号，解析失败时继续尝试更早的候选起点。
        try:

            # 当前候选片段若为 JSON object，将作为子命令结构化报告返回。
            dict_payload = json.loads(str_candidate)  # 成功解析出的 JSON 候选载荷

        # 当前候选不是合法 JSON 时，换用更早的候选片段。
        except json.JSONDecodeError:

            # 日志中的普通花括号不应中断向前搜索。
            continue

        # validate 只接受 JSON object，数组或标量都不是预期报告形态。
        if isinstance(dict_payload, dict):

            # 返回第一个从尾部成功解析的对象，保持旧报告解析策略。
            return dict_payload

    # 遍历所有候选起点后仍无 JSON object，说明子命令输出格式异常。
    raise ValueError("> ERR: [Python] No JSON object found in command output.")

# verify_markdown_ascii 防止 Markdown 文档无边界地引入安装环境不稳定的非 ASCII 字符。
def verify_markdown_ascii(settings: dict[str, Any] | None = None) -> None:
    """确认 skill 包内 Markdown 文件默认保持 ASCII-only，仅允许精确白名单例外。

    :param settings: validate 加载后的 settings 字典；当缺省时按空配置处理。
    :return: 不返回业务值；执行完成即表示“确认 skill 包内 Markdown 文件默认保持 ASCII-only，仅允许精确白名单例外。”对应步骤未发现阻断。
    :raises AssertionError: 当“确认 skill 包内 Markdown 文件默认保持 ASCII-only，仅允许精确白名单例外。”阶段发现配置、产物或执行结果不满足门禁要求时抛出。
    """

    # dict_settings 统一兜底成字典，避免调用方省略 settings 时出现空对象分支。
    dict_settings = settings or {}  # Markdown ASCII 门禁使用的 settings 视图

    # 先提取 validation 段配置，避免在集合推导里内联过长的嵌套读取。
    dict_validation_settings = dict_settings.get("validation", {})  # Markdown 相关校验配置

    # 再提取 Markdown 非 ASCII 白名单原始值，保持文件级精确白名单边界。
    list_allowlist_values = dict_validation_settings.get("markdown_non_ascii_allowlist", [])  # Markdown 非 ASCII 精确白名单原始值

    # 先初始化精确白名单集合，后续逐条规范化路径并放入集合。
    set_allowlist: set[str] = set()  # 允许出现非 ASCII 的 Markdown 精确路径集合

    # 逐条规范化白名单路径，避免路径分隔符差异导致白名单失效。
    for path_value in list_allowlist_values:

        # 把配置值统一成 skill 相对 POSIX 路径后写入精确白名单集合。
        set_allowlist.add(str(path_value).replace("\\", "/").lstrip("./"))

    # 违规列表记录文件和行号，便于直接定位非 ASCII 字符。
    list_violations: list[str] = []  # Markdown 非 ASCII 字符位置列表

    # 只扫描 skill 包文件，缓存和报告目录由 iter_skill_files 过滤。
    for path_file in iter_skill_files():

        # 非 Markdown 文件不受 ASCII-only 安装兼容约束。
        if path_file.suffix.lower() != ".md":

            # 只有 Markdown 文档进入安装可读性编码检查。
            continue

        # skill 相对路径用于错误消息，避免暴露本机绝对路径。
        str_rel = path_file.relative_to(SKILL_ROOT).as_posix()  # 当前 Markdown 文件的 skill 相对路径

        # 白名单文件允许保留非 ASCII，用于精确放行已审计的中文参考文档。
        if str_rel in set_allowlist:

            # 白名单命中后跳过当前 Markdown 的非 ASCII 行扫描。
            continue

        # 按行扫描可以输出精确行号，不需要在报告中展示原文。
        for int_line_number, str_line in enumerate(path_file.read_text(encoding="utf-8").splitlines(), start=1):

            # 任意非 ASCII 字符都会影响安装包在窄环境下的安全性。
            if any(ord(str_char) > 127 for str_char in str_line):

                # 只记录位置，不复制文档内容到错误消息。
                list_violations.append(f"{str_rel}:{int_line_number}")

    # Markdown 非 ASCII 命中即阻塞安装安全检查。
    if list_violations:

        # 非 ASCII 位置排序后输出，减少不同平台遍历顺序造成的报告差异。
        str_violation_summary = ", ".join(sorted(list_violations))  # 排序后的 Markdown 非 ASCII 位置

        # 错误前缀保持旧语义，便于外层工具识别。
        raise AssertionError(
            "> ERR: [Python] Markdown files must be ASCII-only for install safety: " + str_violation_summary
        )

# verify_skill_standards 串联发布前的 skill 元数据和资源约束。
def verify_skill_standards() -> None:
    """检查 SKILL.md 和配套标准资源是否满足发布约束。

    :param: 此函数不接收外部业务参数。
    :return: 不返回业务值；执行完成即表示“检查 SKILL.md 和配套标准资源是否满足发布约束。”对应步骤未发现阻断。
    """

    # SKILL.md 是 skill 包元数据和 progressive disclosure 的唯一入口。
    str_skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")  # SKILL.md 完整文本

    # 前置元数据约束先检查，后续 helper 复用解析结果。
    str_frontmatter = parse_skill_frontmatter(str_skill_text)  # SKILL.md 前置元数据文本

    # frontmatter 字段、名称和 description 语义必须满足 Codex skill 约束。
    verify_skill_frontmatter(str_skill_text, str_frontmatter)

    # Load 资源引用必须全部存在。
    verify_skill_load_resources(str_skill_text)

    # standards 和 design goals 必须保留 skill 设计模式证据。
    verify_skill_design_documents()

    # eval runtime 资产必须存在，防止发布包缺少效果评估入口。
    verify_skill_eval_assets()

# parse_skill_frontmatter 提取 SKILL.md 顶部 YAML 片段。
def parse_skill_frontmatter(str_skill_text: str) -> str:
    """解析 SKILL.md 的 YAML frontmatter 文本。

    :param str_skill_text: SKILL.md 完整文本。
    :return: 返回字符串结果；用于“解析 SKILL.md 的 YAML frontmatter 文本。”阶段向调用方传递解析后的文本值。
    :raises AssertionError: 当“解析 SKILL.md 的 YAML frontmatter 文本。”阶段发现配置、产物或执行结果不满足门禁要求时抛出。
    """

    # SKILL.md 必须以 YAML frontmatter 起始。
    if not str_skill_text.startswith("---\n"):

        # 保持旧错误文本。
        raise AssertionError("> ERR: [Python] SKILL.md must start with YAML frontmatter.")

    # split 只切前两个分隔符，body 内容不参与 frontmatter 校验。
    try:

        # 第二段是 frontmatter，第三段只用于确认正文分隔符存在。
        _, str_frontmatter, _str_skill_body = str_skill_text.split("---", 2)  # SKILL.md 前置元数据与正文切分结果

    # frontmatter 分隔符不足时，SKILL.md 结构无法继续校验。
    except ValueError as exc:

        # frontmatter 三段结构缺失时直接报告 YAML 头部格式错误。
        raise AssertionError("> ERR: [Python] SKILL.md frontmatter is malformed.") from exc

    # frontmatter 长度上限来自 skill package 标准。
    if len(str_frontmatter) > 1024:

        # 长 frontmatter 会让 skill manifest 变成流程文档，沿用旧错误文本。
        raise AssertionError("> ERR: [Python] SKILL.md frontmatter must stay within 1024 characters.")

    # 返回供字段级 helper 继续检查。
    return str_frontmatter

# verify_skill_frontmatter 检查 name/description 的公开 manifest 语义。
def verify_skill_frontmatter(str_skill_text: str, str_frontmatter: str) -> None:
    """检查 SKILL.md frontmatter 字段、name 和 description。

    :param str_skill_text: SKILL.md 完整文本。
    :param str_frontmatter: SKILL.md YAML frontmatter 文本。
    :return: 不返回业务值；执行完成即表示“检查 SKILL.md frontmatter 字段、name 和 description。”对应步骤未发现阻断。
    :raises AssertionError: 当“检查 SKILL.md frontmatter 字段、name 和 description。”阶段发现配置、产物或执行结果不满足门禁要求时抛出。
    """

    # 顶层字段顺序用于确认 manifest 只暴露 name 与 description。
    list_fields: list[str] = []  # frontmatter 顶层字段顺序

    # 只接受顶层字段，缩进行属于 folded description 内容。
    for str_line in str_frontmatter.splitlines():

        # 空行、缩进行和无冒号行都不是 frontmatter 顶层字段。
        if not str_line.strip() or str_line.startswith(" ") or ":" not in str_line:

            # 非顶层字段不参与字段顺序约束。
            continue

        # 冒号左侧是 manifest 字段名。
        str_field_name = str_line.split(":", 1)[0].strip()  # frontmatter 顶层字段名

        # 字段名按出现顺序加入列表，后续做精确顺序比较。
        list_fields.append(str_field_name)

    # 字段顺序必须精确为 name/description。
    if list_fields != ["name", "description"]:

        # 保持旧错误文本并展示实际字段。
        raise AssertionError(
            f"> ERR: [Python] SKILL.md frontmatter fields must be exactly name/description, got {list_fields}."
        )

    # name 字段必须是单行短横线命名。
    match_name = re.search(r"^name:\s*([^\n]+)$", str_frontmatter, flags=re.MULTILINE)  # SKILL.md name 行匹配结果

    # description 必须使用 folded block，便于控制长度和触发语义。
    match_description = re.search(  # SKILL.md 折叠 description 块匹配结果
        r"description:\s*>-\s*\n((?:\s{2}.+\n?)*)",  # 捕获两空格缩进的 folded description 内容
        str_skill_text,  # 在完整 SKILL.md 文本内匹配 folded description
    )

    # name 或 folded description 缺失时无法继续校验。
    if not match_name or not match_description:

        # 缺任一公开 manifest 字段时沿用旧错误文本。
        raise AssertionError("> ERR: [Python] SKILL.md frontmatter must define both name and folded description.")

    # skill 名称用于安装和触发，不允许空格或大写。
    str_skill_name = match_name.group(1).strip()  # 安装和触发使用的 skill 名称

    # folded description 合并成单行用于长度和禁词检查。
    str_description_lines = match_description.group(1).splitlines()  # description 折叠块的缩进行内容

    # description 按 YAML folded block 语义合并为单行触发描述。
    str_description = " ".join(line.strip() for line in str_description_lines).strip()  # 用于触发条件检查的描述文本

    # name 只能使用小写字母、数字和短横线。
    if not SKILL_NAME_PATTERN.fullmatch(str_skill_name):

        # 保持旧错误文本并显示实际 name。
        str_name_error = (  # 报告实际名称和允许字符集合的公开清单诊断文本
            "SKILL.md name must use lowercase letters, numbers, and hyphens only: "  # 命名规则说明
            f"{str_skill_name!r}."  # 实际清单名称值
        )

        # name 字段格式错误会阻塞 skill 发布。
        raise AssertionError("> ERR: [Python] SKILL.md frontmatter name is invalid.")

    # description 必须描述触发条件。
    if not str_description.startswith("Use when"):

        # description 未以触发条件开头时沿用 manifest 约束错误。
        raise AssertionError("> ERR: [Python] SKILL.md description must start with 'Use when'.")

    # description 长度上限避免 frontmatter 变成长工作流说明。
    if len(str_description) > 500:

        # description 过长会让 skill 触发说明难以被 Codex 快速扫描。
        raise AssertionError(
            f"> ERR: [Python] SKILL.md description must stay within 500 characters, got {len(str_description)}."
        )

    # 小写文本用于匹配 workflow 禁词。
    str_lowered_description = str_description.lower()  # 触发描述的小写副本

    # description 不允许混入 workflow 阶段名或命令。
    for str_term in SKILL_DESCRIPTION_WORKFLOW_TERMS:

        # 命中 workflow 词时阻止发布。
        if str_term in str_lowered_description:

            # 保持旧错误文本并显示命中的禁词。
            str_description_term_error = (  # description 混入 workflow 术语的诊断文本
                "SKILL.md description must describe trigger conditions only, "  # description 只能描述触发条件
                f"not workflow term {str_term!r}."  # 实际命中的 workflow 禁词
            )

            # 触发描述混入流程细节时终止发布检查。
            raise AssertionError("> ERR: [Python] SKILL.md description contains workflow-only terms.")

# verify_skill_load_resources 检查 progressive disclosure 引用资源。
def verify_skill_load_resources(str_skill_text: str) -> None:
    """检查 SKILL.md Load 规则引用的资源是否存在。

    :param str_skill_text: SKILL.md 完整文本。
    :return: 不返回业务值；执行完成即表示“检查 SKILL.md Load 规则引用的资源是否存在。”对应步骤未发现阻断。
    :raises AssertionError: 当“检查 SKILL.md Load 规则引用的资源是否存在。”阶段发现配置、产物或执行结果不满足门禁要求时抛出。
    """

    # Load 行是 skill progressive disclosure 的资源导航合同。
    list_load_lines: list[str] = []  # 资源导航规则的原始文本行

    # 逐行保留以 - Load 开头的资源导航规则。
    for str_line in str_skill_text.splitlines():

        # 去掉行首尾空白后再判断规则前缀。
        str_stripped_line = str_line.strip()  # SKILL.md 当前行的去空白文本

        # 只有 Load 规则会携带必须随包存在的资源路径。
        if str_stripped_line.startswith("- Load "):

            # 保留规则文本供后续提取反引号资源路径。
            list_load_lines.append(str_stripped_line)

    # skill 必须暴露至少一条 Load 资源导航。
    if not list_load_lines:

        # 缺少 Load 规则会破坏 progressive disclosure 入口，沿用旧错误文本。
        raise AssertionError(
            "> ERR: [Python] SKILL.md must expose progressive-disclosure Load rules for supporting resources."
        )

    # 收集不存在的 Load 资源路径。
    list_missing_resources: list[str] = []  # 缺失的 Load 资源相对路径

    # 逐行解析反引号中的资源路径。
    for str_line in list_load_lines:

        # Load 行中的第一个反引号路径是资源相对路径。
        match_resource = re.search(r"`([^`]+)`", str_line)  # Load 规则中的资源路径匹配结果

        # 没有反引号路径的行不参与存在性校验。
        if not match_resource:

            # 继续检查下一条 Load 规则。
            continue

        # resource 是相对 skill 根目录的支持文件。
        str_resource = match_resource.group(1)  # Load 资源相对路径

        # 资源必须随 skill 包一起存在。
        if not (SKILL_ROOT / str_resource).exists():

            # 缺失资源延后统一报告，便于一次修复多处引用。
            list_missing_resources.append(str_resource)

    # 任意 Load 资源缺失都阻止发布。
    if list_missing_resources:

        # 保持旧错误文本并去重排序。
        str_missing_resource_summary = ", ".join(sorted(set(list_missing_resources)))  # 缺失 Load 资源汇总

        # 错误文本保留旧前缀，后接去重排序后的资源路径。
        raise AssertionError(
            "> ERR: [Python] SKILL.md Load rules reference missing resources: " + str_missing_resource_summary
        )

# verify_skill_design_documents 检查标准文档和目标文档的设计模式证据。
def verify_skill_design_documents() -> None:
    """检查 standards 和 engineering goals 是否保留设计模式说明。

    :param: 此函数不接收外部业务参数。
    :return: 不返回业务值；执行完成即表示“检查 standards 和 engineering goals 是否保留设计模式说明。”对应步骤未发现阻断。
    :raises AssertionError: 当“检查 standards 和 engineering goals 是否保留设计模式说明。”阶段发现配置、产物或执行结果不满足门禁要求时抛出。
    """

    # skill standards 是发布包内承载评估指标和设计模式说明的标准文档。
    path_standards = SKILL_ROOT / "references" / "skill" / "skill-standards.md"  # 设计标准文档路径

    # standards 文件必须存在。
    if not path_standards.exists():

        # 标准文档缺失会让发布包缺少评估约束，沿用旧错误文本。
        raise AssertionError("> ERR: [Python] references/skill/skill-standards.md is required.")

    # standards 文本用于大小写敏感的模式名检查。
    str_standards_text = path_standards.read_text(encoding="utf-8")  # 设计标准文档原文

    # 小写文本用于固定英文短语检查。
    str_standards_lower = str_standards_text.lower()  # 标准文档的小写副本

    # 设计模式名称必须逐项出现。
    for str_marker in PATTERN_NAMES:

        # standards 中缺任一模式都说明文档退化。
        if str_marker not in str_standards_text:

            # 缺失模式名时保留旧错误文本并显示具体模式。
            raise AssertionError(f"> ERR: [Python] references/skill/skill-standards.md must mention {str_marker!r}.")

    # standards 必须说明渐进加载、通过率变化和有无 skill 对比。
    tuple_required_standard_phrases = (
        "progressive disclosure",  # 渐进加载设计约束
        "pass-rate delta",  # 评估报告的通过率变化指标
        "with and without the skill",  # 有无 skill 对比实验说明
    )  # skill standards 必须保留的评估短语

    # 逐项确认 standards 文档保留核心评估短语。
    for str_marker in tuple_required_standard_phrases:

        # 固定短语按小写文本检查。
        if str_marker not in str_standards_lower:

            # 缺失评估短语时保留旧错误文本并显示具体短语。
            raise AssertionError(f"> ERR: [Python] references/skill/skill-standards.md must mention {str_marker!r}.")

    # engineering goals 文档必须同步保留设计模式。
    str_goals_text = (SKILL_ROOT / "ENGINEERING_DESIGN_GOALS.md").read_text(encoding="utf-8")  # 工程设计目标文本

    # goals 文档逐项检查模式名称。
    for str_marker in PATTERN_NAMES:

        # goals 缺模式名时说明设计说明不完整。
        if str_marker not in str_goals_text:

            # 保持旧错误文本并显示缺失模式。
            raise AssertionError(f"> ERR: [Python] ENGINEERING_DESIGN_GOALS.md must preserve the {str_marker} pattern.")

# verify_skill_eval_assets 检查效果评估 runtime 入口是否存在。
def verify_skill_eval_assets() -> None:
    """检查 skill 效果评估所需 runtime 文件是否存在。

    :param: 此函数不接收外部业务参数。
    :return: 不返回业务值；执行完成即表示“检查 skill 效果评估所需 runtime 文件是否存在。”对应步骤未发现阻断。
    :raises AssertionError: 当“检查 skill 效果评估所需 runtime 文件是否存在。”阶段发现配置、产物或执行结果不满足门禁要求时抛出。
    """

    # eval-skill 资产位于 scripts/python/validation，缺任一项都会让效果评估无法运行。
    list_required_eval_paths = [
        SKILL_ROOT / "scripts" / "python" / "validation" / "evaluation.py",  # eval-skill CLI 评估入口
        SKILL_ROOT / "scripts" / "python" / "validation" / "eval_suite.py",  # eval fixture 执行套件
    ]  # 效果评估运行时必须随包发布的文件

    # 缺失路径转换成 skill 根相对路径。
    list_missing_eval: list[str] = []  # 缺失的 eval 资产

    # 逐个检查必需 eval runtime 文件是否仍随包存在。
    for path_eval in list_required_eval_paths:

        # 只记录缺失文件，存在的文件不进入失败摘要。
        if not path_eval.exists():

            # 错误消息使用 skill 相对路径，避免泄露本机绝对路径。
            list_missing_eval.append(path_eval.relative_to(SKILL_ROOT).as_posix())

    # 任意 eval 资产缺失都阻止发布。
    if list_missing_eval:

        # 保持旧错误文本并列出缺失文件。
        raise AssertionError("> ERR: [Python] Skill evaluation assets are missing: " + ", ".join(list_missing_eval))

# verify_legacy_terms 防止旧版依赖术语泄漏到未豁免文件。
def verify_legacy_terms(settings: dict) -> None:
    """扫描 skill 文件中的 legacy 术语，并应用配置化 allowlist。

    :param settings: Verilog skill 治理配置字典，提供路径、依赖和验证开关。
    :return: 不返回业务值；执行完成即表示“扫描 skill 文件中的 legacy 术语，并应用配置化 allowlist。”对应步骤未发现阻断。
    :raises AssertionError: 当“扫描 skill 文件中的 legacy 术语，并应用配置化 allowlist。”阶段发现配置、产物或执行结果不满足门禁要求时抛出。
    """

    # allowlist 来自 defaults/settings，便于治理文件集中维护兼容例外。
    set_allowlist = set(settings.get("validation", {}).get("legacy_term_allowlist", []))  # 允许包含 legacy 术语的相对路径

    # 违规项记录文件和行号，最终一次性抛出。
    list_violations: list[str] = []  # legacy 术语违规位置列表

    # 对 skill 源文件逐个执行文本扫描。
    for path_file in iter_skill_files():

        # 使用 skill 相对路径匹配 allowlist 和行级例外。
        str_rel = path_file.relative_to(SKILL_ROOT).as_posix()  # 当前扫描文件的 skill 相对路径

        # 文本宽容读取，避免少数非文本资产阻塞术语扫描。
        str_text = path_file.read_text(encoding="utf-8", errors="ignore")  # 当前文件文本内容

        # 文件级 allowlist 命中时完全跳过该文件。
        if str_rel in set_allowlist:

            # allowlist 文件中的旧术语由专门治理说明约束。
            continue

        # 行级扫描可以允许个别依赖说明保留旧术语上下文。
        for int_line_number, str_line in enumerate(str_text.splitlines(), start=1):

            # legacy term 命中表示当前行可能残留旧生成/依赖术语。
            bool_has_legacy_term = any(str_term in str_line for str_term in LEGACY_TERMS)  # 当前行是否包含 legacy 术语

            # 行级 allowlist 用于保留真实依赖 id、工具链路径和兼容参数。
            bool_line_allowed = _allowed_dependency_term_line(str_rel, str_line)  # 当前行是否命中依赖说明例外

            # 未命中行级例外的 legacy 术语需要记录。
            if bool_has_legacy_term and not bool_line_allowed:

                # 违规位置采用 skill 相对路径加行号。
                list_violations.append(f"{str_rel}:{int_line_number}")

    # 任意未豁免 legacy 术语都阻塞 validate。
    if list_violations:

        # legacy 违规位置排序后输出，保持报告稳定。
        str_violation_summary = ", ".join(sorted(list_violations))  # 排序后的 legacy 术语违规位置

        # 错误前缀保持旧语义，后接稳定排序的位置列表。
        raise AssertionError(
            "> ERR: [Python] Legacy generation terms found outside allowlist: " + str_violation_summary
        )

# _line_contains_any 集中表达 allowlist 的行级片段匹配语义。
def _line_contains_any(str_line: str, tuple_markers: tuple[str, ...]) -> bool:
    """判断文本行是否包含任一允许片段。

    :param str_line: 正在检查的单行文本。
    :param tuple_markers: 允许命中的文本片段集合。
    :return: 返回布尔值；True 表示“判断文本行是否包含任一允许片段。”对应条件命中。
    """

    # allowlist 匹配保持大小写敏感，沿用旧扫描函数的精确文本语义。
    return any(str_marker in str_line for str_marker in tuple_markers)

# _allowed_config_dependency_line 解释 defaults.json 中的依赖术语例外。
def _allowed_config_dependency_line(str_line: str) -> bool:
    """判断 defaults.json 中的 legacy 术语是否属于依赖配置。

    :param str_line: 正在检查的单行文本。
    :return: 返回布尔值；True 表示“判断 defaults.json 中的 legacy 术语是否属于依赖配置。”对应条件命中。
    """

    # defaults 必须保留真实 skill id、工具链组合名和依赖源路径字段。
    tuple_config_markers = (
        "fpga-agent-skills",  # 手动降级依赖仓库名
        "Vivado/Vitis",  # 组合工具链显示名
        "vitis-hls-synthesis",  # configuration 推荐组中的 HLS 综合 skill id
        "vitis-developer",  # configuration 推荐组中的 Vitis 开发 skill id
        '"skill": "vitis-',  # 依赖配置中的 vendor skill 字段前缀
        '"source_path": "vitis-',  # 依赖配置中的 vendor 源路径前缀
    )  # defaults.json 允许的依赖配置片段

    # 仅这些依赖配置片段可以触发 legacy 术语豁免。
    return _line_contains_any(str_line, tuple_config_markers)

# _allowed_skill_markdown_dependency_line 解释 SKILL.md 的用户可见依赖说明。
def _allowed_skill_markdown_dependency_line(str_line: str) -> bool:
    """判断 SKILL.md 中的 legacy 术语是否属于依赖路由说明。

    :param str_line: 正在检查的单行文本。
    :return: 返回布尔值；True 表示“判断 SKILL.md 中的 legacy 术语是否属于依赖路由说明。”对应条件命中。
    """

    # 小写副本覆盖英文说明中的 dependency 和 developer routing 语义。
    str_lower_line = str_line.lower()  # SKILL.md 当前行的小写文本

    # SKILL.md 只放行依赖路由、developer skill 调度和 testbench 语言字段。
    return (
        "dependency" in str_lower_line
        or "route to the installed FPGA" in str_line
        or "developer routing" in str_lower_line
        or "verification testbenches may" in str_lower_line
        or "tb_language" in str_line
    )

# _allowed_integration_dependency_line 解释 integration 参考文档的测试夹具说明。
def _allowed_integration_dependency_line(str_line: str) -> bool:
    """判断 integration.md 中的 legacy 术语是否属于 testbench 兼容说明。

    :param str_line: 正在检查的单行文本。
    :return: 返回布尔值；True 表示“判断 integration.md 中的 legacy 术语是否属于 testbench 兼容说明。”对应条件命中。
    """

    # integration allowlist 需要忽略语言名大小写。
    str_lower_line = str_line.lower()  # integration 文本折叠结果

    # integration 文档允许验证 testbench、tb_language 字段和 SystemVerilog 兼容描述。
    return "verification testbench" in str_lower_line or "tb_language" in str_line or "systemverilog" in str_lower_line

# _allowed_configuration_dependency_line 解释 configuration 参考文档的依赖示例。
def _allowed_configuration_dependency_line(str_line: str) -> bool:
    """判断 configuration.md 中的 legacy 术语是否属于依赖治理示例。

    :param str_line: 正在检查的单行文本。
    :return: 返回布尔值；True 表示“判断 configuration.md 中的 legacy 术语是否属于依赖治理示例。”对应条件命中。
    """

    # configuration 文档展示推荐依赖组、必需依赖组和工具链路径样例。
    tuple_configuration_markers = (
        "dependency",  # 依赖章节关键词
        "provides",  # 依赖能力说明
        "recommended groups",  # 推荐依赖组标题
        "required groups",  # 必需依赖组标题
        "Vivado/Vitis",  # 工具链组合名称
        "Vitis/*/settings64.sh",  # 工具链 settings64.sh 路径样例
        "vitis-hls-synthesis",  # 本脚本检查 defaults 推荐依赖时写出的 HLS skill id
        "vitis-developer",  # 本脚本检查 developer 路由时写出的 Vitis skill id
        "developer routing",  # fpga-developer 路由说明
    )  # configuration.md 允许的依赖说明片段

    # 仅依赖治理和工具链示例文本可以触发豁免。
    return _line_contains_any(str_line, tuple_configuration_markers)

# _allowed_validate_script_dependency_line 解释本验证脚本中的自检术语。
def _allowed_validate_script_dependency_line(str_line: str) -> bool:
    """判断 validate_verilog_skill.py 中的 legacy 术语是否属于自检规则。

    :param str_line: 正在检查的单行文本。
    :return: 返回布尔值；True 表示“判断 validate_verilog_skill.py 中的 legacy 术语是否属于自检规则。”对应条件命中。
    """

    # 本脚本需要写出受检依赖 id、工具链路径样例、后端字段和兼容 CLI 参数。
    tuple_validate_script_markers = (
        "FPGA-Agent-skills dependency",  # 依赖 schema 错误文本
        "vitis-hls-synthesis",  # 推荐依赖 skill id
        "vitis-developer",  # 推荐开发者 skill id
        '"skill": "vitis-',  # defaults 依赖字段前缀
        '"source_path": "vitis-',  # defaults 源路径字段前缀
        "vitis_command",  # smoke 工具链字段名
        "VCS+Verdi",  # 支持的仿真后端组合名
        "/tools/Xilinx/Vitis/*/settings64.sh",  # 远程工具链路径样例
        "simulator_backend",  # 仿真后端配置字段
        "systemverilog",  # SV 语言兼容术语
        ".sv",  # SV 文件后缀
        "Vivado",  # 本脚本硬编码路径扫描允许识别的 FPGA 工具名
        "Vitis",  # 硬编码路径扫描允许识别的 Vitis 工具名
        "/tools/Xilinx/",  # Xilinx 工具链根路径样例
        "args.vitis_wrapper",  # 兼容 CLI 参数对象字段
        "--vitis-wrapper",  # 兼容 CLI 参数名
    )  # validate 脚本自检允许的依赖和工具链片段

    # 自检规则中的真实依赖术语不代表旧生成流程残留。
    return _line_contains_any(str_line, tuple_validate_script_markers)

# _allowed_dependency_manager_line 解释依赖管理脚本的真实 skill 标识。
def _allowed_dependency_manager_line(str_line: str) -> bool:
    """判断 manage_skill_dependencies.py 中的 legacy 术语是否属于依赖清单。

    :param str_line: 正在检查的单行文本。
    :return: 返回布尔值；True 表示“判断 manage_skill_dependencies.py 中的 legacy 术语是否属于依赖清单。”对应条件命中。
    """

    # 依赖管理脚本必须识别外部 FPGA skill 来源和 vendor skill id 前缀。
    tuple_dependency_script_markers = (
        "FPGA-Agent",  # 外部技能集合仓库名前缀
        "Vivado/Vitis",  # 工具链组合显示名
        "vitis-developer",  # 依赖管理脚本识别的 Vitis developer skill id
        "vitis-hls-synthesis",  # 依赖管理脚本识别的 HLS 综合 skill id
        '"vivado-',  # Vivado 子技能依赖字段前缀
    )  # 依赖管理脚本允许的真实依赖标识

    # 只有依赖标识本身可以绕过 legacy 术语扫描。
    return _line_contains_any(str_line, tuple_dependency_script_markers)

# _allowed_tb_generator_dependency_line 解释 testbench 生成脚本的语言选项。
def _allowed_tb_generator_dependency_line(str_line: str) -> bool:
    """判断 tb_generator.py 中的 legacy 术语是否属于 testbench 语言兼容项。

    :param str_line: 正在检查的单行文本。
    :return: 返回布尔值；True 表示“判断 tb_generator.py 中的 legacy 术语是否属于 testbench 语言兼容项。”对应条件命中。
    """

    # tb_generator allowlist 同时匹配 CLI 选项和语言名。
    str_lower_line = str_line.lower()  # tb 语言匹配缓存

    # testbench 生成脚本允许描述 SystemVerilog 和 tb-language 兼容选项。
    return "systemverilog" in str_lower_line or "tb-language" in str_lower_line

# _allowed_cli_generation_dependency_line 解释 CLI generation facade 的兼容字段。
def _allowed_cli_generation_dependency_line(str_line: str) -> bool:
    """判断 cli_generation_commands.py 中的 legacy 术语是否属于兼容参数。

    :param str_line: 正在检查的单行文本。
    :return: 返回布尔值；True 表示“判断 cli_generation_commands.py 中的 legacy 术语是否属于兼容参数。”对应条件命中。
    """

    # CLI generation facade 只允许透传旧 wrapper 参数名。
    return "args.vitis_wrapper" in str_line

# _allowed_cli_parser_dependency_line 解释 CLI parser 的兼容参数声明。
def _allowed_cli_parser_dependency_line(str_line: str) -> bool:
    """判断 cli_parser.py 中的 legacy 术语是否属于兼容 CLI 参数。

    :param str_line: 正在检查的单行文本。
    :return: 返回布尔值；True 表示“判断 cli_parser.py 中的 legacy 术语是否属于兼容 CLI 参数。”对应条件命中。
    """

    # CLI parser 只允许声明 --vitis-wrapper 兼容参数。
    return "--vitis-wrapper" in str_line

# _allowed_remote_validate_dependency_line 解释远程验证脚本的工具链发现字段。
def _allowed_remote_validate_dependency_line(str_line: str) -> bool:
    """判断 remote_validate_verilog_skill.py 中的 legacy 术语是否属于远程工具链字段。

    :param str_line: 正在检查的单行文本。
    :return: 返回布尔值；True 表示“判断 remote_validate_verilog_skill.py 中的 legacy 术语是否属于远程工具链字段。”对应条件命中。
    """

    # 远程验证脚本需要保留工具链发现路径和仿真后端字段。
    tuple_remote_validate_markers = (
        "/tools/Xilinx/Vitis/*/settings64.sh",  # 远端 Xilinx settings64.sh 路径样例
        "selected_backend",  # 远程选择结果中的后端字段
        "simulator_backend",  # remote_validate 配置中的仿真后端字段
    )  # 远程验证允许的工具链和后端片段

    # 只有远程工具链发现相关片段可以触发豁免。
    return _line_contains_any(str_line, tuple_remote_validate_markers)

# _allowed_smoke_script_dependency_line 解释 tests/smoke 主脚本的工具链路径样例。
def _allowed_smoke_script_dependency_line(str_line: str) -> bool:
    """判断 tests/smoke/run_smoke.py 中的 legacy 术语是否属于工具链发现逻辑。

    :param str_line: 正在检查的单行文本。
    :return: 返回布尔值；True 表示“判断 tests/smoke/run_smoke.py 中的 legacy 术语是否属于工具链发现逻辑。”对应条件命中。
    """

    # smoke 主脚本需要保留真实工具链路径样例和 developer skill id。
    tuple_smoke_markers = (
        "vitis-hls-synthesis",  # smoke 主脚本校验的 HLS skill 标识
        "vitis-developer",  # smoke 主脚本校验的 Vitis developer 路由标识
        "vitis_command",  # smoke vendor 工具链命令字段
        "/tools/Xilinx/Vitis/2022.2/settings64.sh",  # 固定版本工具链路径样例
        "/tools/Xilinx/Vitis/*/settings64.sh",  # 通配版本工具链路径样例
        "Configured Xilinx settings64.sh",  # 已配置工具链提示文本
        "Multiple Xilinx toolchain settings64.sh candidates",  # 多候选工具链提示文本
    )  # smoke 主脚本允许的工具链发现片段

    # 工具链发现文本是 smoke 行为的一部分，应保留例外。
    return _line_contains_any(str_line, tuple_smoke_markers)

# _allowed_smoke_dependency_gate_line 解释 tests/smoke 依赖 gate 的厂商和 skill 标识。
def _allowed_smoke_dependency_gate_line(str_line: str) -> bool:
    """判断 tests/smoke/dependency_gates.py 中的 legacy 术语是否属于依赖 schema 检查。

    :param str_line: 正在检查的单行文本。
    :return: 返回布尔值；True 表示“判断 tests/smoke/dependency_gates.py 中的 legacy 术语是否属于依赖 schema 检查。”对应条件命中。
    """

    # smoke dependency gate 检查 defaults 里的真实依赖 schema，因此必须列出厂商和外部 skill 标识。
    tuple_smoke_dependency_markers = (  # 依赖门禁扫描默认依赖配置时允许出现的真实标识
        "FPGA-Agent",  # manual_fallback 聚合 skill 的仓库名前缀
        "Vivado/Vitis",  # dependency gate 期望的工具链组合显示名
        "vitis-developer",  # 依赖门禁架构中的 Vitis 开发技能
        "vitis-hls-synthesis",  # dependency gate schema 中的 HLS 综合 skill
        '"vivado-',  # FPGA-Agent-skills 子技能 id 的 vivado 前缀
        "AMD-Xilinx",  # vendor matrix 中的 AMD/Xilinx 名称
        "PangoMicro",  # vendor matrix 中的 PangoMicro 厂商名称
    )  # smoke dependency gate 可接受的厂商矩阵和 skill 标识

    # 依赖 schema 检查中的真实标识可以绕过 legacy 术语扫描。
    return _line_contains_any(str_line, tuple_smoke_dependency_markers)

# _allowed_toolchain_gate_line 解释 tests/smoke 工具链 gate 的后端字段。
def _allowed_toolchain_gate_line(str_line: str) -> bool:
    """判断 tests/smoke/toolchain_gates.py 中的 legacy 术语是否属于工具链 gate。

    :param str_line: 正在检查的单行文本。
    :return: 返回布尔值；True 表示“判断 tests/smoke/toolchain_gates.py 中的 legacy 术语是否属于工具链 gate。”对应条件命中。
    """

    # toolchain gate 读取 smoke 配置中的真实工具链路径、命令和后端字段。
    tuple_toolchain_gate_markers = (  # 工具链门禁扫描发现逻辑时允许出现的真实配置标识
        "vitis-hls-synthesis",  # toolchain gate 关联的 HLS 综合 skill
        "vitis-developer",  # 工具链门禁关联的 Vitis 开发技能
        "vitis_command",  # smoke 工具链配置里的 Vitis 命令字段
        "/tools/Xilinx/Vitis/2022.2/settings64.sh",  # smoke 固定版本 settings64.sh 样例
        "/tools/Xilinx/Vitis/*/settings64.sh",  # 通配版本 settings64.sh 样例
        "Configured Xilinx settings64.sh",  # toolchain gate 的已配置 settings 提示
        "Multiple Xilinx toolchain settings64.sh candidates",  # toolchain gate 的多候选 settings 提示
        "simulator_backend",  # toolchain gate 读取的仿真后端字段
    )  # smoke 工具链 gate 允许的工具链和后端片段

    # 工具链 gate 的字段名和示例路径不应被当作旧生成残留。
    return _line_contains_any(str_line, tuple_toolchain_gate_markers)

# _allowed_eval_dependency_line 解释 eval fixture 的 SystemVerilog 覆盖项。
def _allowed_eval_dependency_line(str_line: str) -> bool:
    """判断 evals/evals.json 中的 legacy 术语是否属于验证 fixture。

    :param str_line: 正在检查的单行文本。
    :return: 返回布尔值；True 表示“判断 evals/evals.json 中的 legacy 术语是否属于验证 fixture。”对应条件命中。
    """

    # eval allowlist 需要同时匹配后缀与语言全称。
    str_lower_line = str_line.lower()  # eval fixture 折叠文本

    # eval 描述允许声明 SystemVerilog fixture 或 .sv 文件。
    return "systemverilog" in str_lower_line or ".sv" in str_lower_line

# _allowed_existing_rtl_improvement_line 解释 existing RTL improvement 的 SV/SVA 支持。
def _allowed_existing_rtl_improvement_line(str_line: str) -> bool:
    """判断 existing_rtl_improvement.py 中的 legacy 术语是否属于 SV/SVA 兼容说明。

    :param str_line: 正在检查的单行文本。
    :return: 返回布尔值；True 表示“判断 existing_rtl_improvement.py 中的 legacy 术语是否属于 SV/SVA 兼容说明。”对应条件命中。
    """

    # improvement allowlist 需要识别 SVA property 示例。
    str_lower_line = str_line.lower()  # improvement 文档折叠文本

    # SV 输入说明覆盖语言名和 .sv 文件后缀。
    bool_mentions_systemverilog = "systemverilog" in str_lower_line or ".sv" in str_lower_line  # SV 输入兼容说明

    # SVA 示例覆盖 assert property 和 property p_ 片段。
    bool_mentions_sva_property = "assert property" in str_lower_line or "property p_" in str_lower_line  # SVA 属性示例

    # existing RTL improvement 允许提到 SV 或 SVA 语义约束。
    return bool_mentions_systemverilog or bool_mentions_sva_property

# _allowed_skill_effectiveness_dependency_line 解释 effectiveness 评估的 SV 覆盖项。
def _allowed_skill_effectiveness_dependency_line(str_line: str) -> bool:
    """判断 skill_effectiveness.py 中的 legacy 术语是否属于测试覆盖项。

    :param str_line: 正在检查的单行文本。
    :return: 返回布尔值；True 表示“判断 skill_effectiveness.py 中的 legacy 术语是否属于测试覆盖项。”对应条件命中。
    """

    # effectiveness allowlist 关注评估样例里的 SV 表述。
    str_lower_line = str_line.lower()  # effectiveness 覆盖项缓存

    # effectiveness 评估允许引用 SystemVerilog 或 .sv 作为测试覆盖项。
    return "systemverilog" in str_lower_line or ".sv" in str_lower_line

# _allowed_verify_repair_dependency_line 解释 verify-repair 的 testbench 语言字段。
def _allowed_verify_repair_dependency_line(str_line: str) -> bool:
    """判断 verify_repair.py 中的 legacy 术语是否属于 SV/testbench 兼容字段。

    :param str_line: 正在检查的单行文本。
    :return: 返回布尔值；True 表示“判断 verify_repair.py 中的 legacy 术语是否属于 SV/testbench 兼容字段。”对应条件命中。
    """

    # verify-repair allowlist 关注修复报告里的 testbench 表述。
    str_lower_line = str_line.lower()  # verify-repair 语言字段缓存

    # 修复验证中的 SV 兼容说明覆盖语言名和 .sv 文件后缀。
    bool_mentions_sv = "systemverilog" in str_lower_line or ".sv" in str_lower_line  # 修复验证 SV 兼容说明

    # testbench 语言字段是 verify-repair 接口兼容的一部分。
    bool_mentions_tb_language = "tb_languages" in str_lower_line or "tb_language" in str_lower_line  # testbench 语言字段说明

    # verify-repair 可保留 SV 和 testbench 语言兼容字段。
    return bool_mentions_sv or bool_mentions_tb_language

# _allowed_test_dependency_line 解释测试文件里的真实工具链和 fixture 术语。
def _allowed_test_dependency_line(str_line: str) -> bool:
    """判断 tests/ 下的 legacy 术语是否属于测试 fixture 或工具链字段。

    :param str_line: 正在检查的单行文本。
    :return: 返回布尔值；True 表示“判断 tests/ 下的 legacy 术语是否属于测试 fixture 或工具链字段。”对应条件命中。
    """

    # 测试文件允许写出后端名、工具链路径和 SV fixture 后缀。
    tuple_test_markers = (
        "systemverilog",  # SV 语言 fixture 关键词
        ".sv",  # tests fixture 使用的 SystemVerilog 文件后缀
        "Vivado",  # 单元测试 fixture 中的 FPGA 工具链名
        "Vitis",  # tests fixture 使用的 Vitis 工具名
        "/tools/Xilinx/",  # 测试中的工具链根路径样例
        "simulator_backend",  # 测试配置 fixture 中的仿真后端字段
    )  # 测试文件允许的后端和工具链标记

    # 大小写敏感匹配保留原有工具链字段语义。
    bool_mentions_test_marker = _line_contains_any(str_line, tuple_test_markers)  # 测试行是否命中工具链标记

    # 小写匹配补充识别 SystemVerilog 和 .sv fixture 描述。
    str_lower_line = str_line.lower()  # 测试当前行的小写文本

    # 测试中的 SV 文件或语言描述可作为额外豁免条件。
    bool_mentions_test_sv = "systemverilog" in str_lower_line or ".sv" in str_lower_line  # 测试 SV fixture 描述

    # 测试文件允许保留真实工具链、仿真后端和 SV fixture 术语。
    return bool_mentions_test_marker or bool_mentions_test_sv

# _build_dependency_term_handlers 构造 legacy 术语扫描的精确路径分派表。
def _build_dependency_term_handlers() -> dict[str, Callable[[str], bool]]:
    """返回 legacy 术语豁免的文件级处理器映射。

    :param: 本函数不接收外部参数；只返回固定的文件级处理器映射。
    :return: 返回从项目相对路径到行级判定函数的映射，调用方据此隔离每个文件的例外范围。
    """

    # 精确路径分派表把各文件的依赖术语豁免规则隔离到对应 helper 中。
    return {
        "config/defaults.json": _allowed_config_dependency_line,  # defaults 依赖配置
        "SKILL.md": _allowed_skill_markdown_dependency_line,  # skill 用户可见依赖说明
        "references/integration/host-integration.md": _allowed_integration_dependency_line,  # 集成说明里的测试平台术语
        "references/integration/configuration.md": _allowed_configuration_dependency_line,  # configuration 依赖示例
        "scripts/python/validation/validate_verilog_skill.py": _allowed_validate_script_dependency_line,  # 本脚本自检术语
        "scripts/python/toolchain/manage_skill_dependencies.py": _allowed_dependency_manager_line,  # 依赖管理脚本术语
        "scripts/python/generation/tb_generator.py": _allowed_tb_generator_dependency_line,  # testbench 语言选项
        "scripts/python/workflow/cli_generation_commands.py": _allowed_cli_generation_dependency_line,  # facade 字段
        "scripts/python/workflow/cli_parser.py": _allowed_cli_parser_dependency_line,  # CLI 兼容参数
        "scripts/python/remote/remote_validate_verilog_skill.py": _allowed_remote_validate_dependency_line,  # 远程后端字段
        "tests/smoke/run_smoke.py": _allowed_smoke_script_dependency_line,  # smoke 工具链发现
        "tests/smoke/dependency_gates.py": _allowed_smoke_dependency_gate_line,  # smoke 依赖架构检查术语
        "tests/smoke/toolchain_gates.py": _allowed_toolchain_gate_line,  # smoke 工具链门禁术语
        "evals/evals.json": _allowed_eval_dependency_line,  # 效果评估用例描述术语
        "scripts/python/existing_rtl/existing_rtl_improvement.py": _allowed_existing_rtl_improvement_line,  # RTL 精化术语
        "scripts/python/validation/skill_effectiveness.py": _allowed_skill_effectiveness_dependency_line,  # 评估覆盖项
        "scripts/python/existing_rtl/verify_repair.py": _allowed_verify_repair_dependency_line,  # verify-repair 语言字段
    }

# _allowed_dependency_term_line 为依赖治理文本保留必要旧术语例外。
def _allowed_dependency_term_line(str_rel: str, str_line: str) -> bool:
    """判断单行 legacy 术语是否属于依赖治理说明例外。

    :param str_rel: 项目相对路径文本，用于匹配 allowlist。
    :param str_line: 正在检查的单行文本。
    :return: 返回布尔值；True 表示“判断单行 legacy 术语是否属于依赖治理说明例外。”对应条件命中。
    """

    # 获取按文件隔离的处理器，避免一个文件的 legacy 豁免污染其他文件。
    dict_exact_handlers = _build_dependency_term_handlers()  # 当前扫描轮次使用的精确路径处理器

    # 精确路径命中时使用对应 helper 的语义规则。
    func_exact_handler = dict_exact_handlers.get(str_rel)  # 当前相对路径命中的行级例外判断函数

    # 已知文件路径交给专属 helper，避免主函数堆叠路径分支。
    if func_exact_handler is not None:

        # helper 返回 True 表示该行 legacy 术语属于可解释的依赖上下文。
        return func_exact_handler(str_line)

    # tests/ 下的 fixture 允许真实工具链、后端和 SystemVerilog 术语。
    if str_rel.startswith("tests/"):

        # 测试目录共享同一套 fixture/toolchain 例外。
        return _allowed_test_dependency_line(str_line)

    # 其他文件默认不允许 legacy 术语例外。
    return False

# verify_dependency_schema 校验 defaults 中跨 skill 依赖和 FPGA 路由约束。
def verify_dependency_schema(settings: dict) -> None:
    """确认依赖 schema、推荐项和 FPGA developer routing 没有漂移。

    :param settings: Verilog skill 治理配置字典，提供路径、依赖和验证开关。
    :return: 不返回业务值；执行完成即表示“确认依赖 schema、推荐项和 FPGA developer routing 没有漂移。”对应步骤未发现阻断。
    :raises AssertionError: 当“确认依赖 schema、推荐项和 FPGA developer routing 没有漂移。”阶段发现配置、产物或执行结果不满足门禁要求时抛出。
    """

    # 配置 helper 延迟导入，避免 validate 脚本加载时提前解析 runtime 设置。
    from scripts.python.workflow.config import fpga_developer_routing_settings, skill_dependency_settings

    # dependencies 是 defaults 中 skill_dependency_settings 的结构化视图。
    dict_dependencies = skill_dependency_settings(settings)  # skill 依赖配置的解析结果

    # routing 是 fpga-developer 协调策略的结构化配置。
    dict_routing = fpga_developer_routing_settings(settings)  # FPGA developer 路由配置

    # 各依赖分组只比较 URL 集合，避免名称或说明文本影响结构检查。
    set_required_urls = {item["url"] for item in dict_dependencies["required"]}  # required 依赖 URL 集合

    # recommended URL 集合锚定推荐安装层级。
    set_recommended_urls = {item["url"] for item in dict_dependencies["recommended"]}  # 推荐依赖地址集合

    # manual_fallback URL 集合锚定手动降级来源。
    set_manual_fallback_urls = {item["url"] for item in dict_dependencies["manual_fallback"]}  # 手动降级依赖地址集合

    # required 依赖必须只包含 remote-ssh，避免运行时强依赖扩大。
    if set_required_urls != {
        "https://github.com/Eriemon/remote-ssh.git",
    }:

        # 失败时输出实际 URL 集合，便于定位 defaults 漂移。
        raise AssertionError(f"> ERR: [Python] Unexpected required dependency URLs: {sorted(set_required_urls)}")

    # recommended 依赖必须保留 superpowers 与 context-engineering。
    if set_recommended_urls != {
        "https://github.com/obra/superpowers.git",
        "https://github.com/muratcankoylan/Agent-Skills-for-Context-Engineering.git",
    }:

        # 失败时输出实际 URL 集合，便于定位推荐依赖被删改的来源。
        raise AssertionError(f"> ERR: [Python] Unexpected recommended dependency URLs: {sorted(set_recommended_urls)}")

    # manual fallback 只允许 FPGA-Agent-skills 一个来源。
    if set_manual_fallback_urls != {"https://github.com/adeleempurpled290/FPGA-Agent-skills.git"}:

        # 失败时输出实际 URL 集合，避免人工猜测配置漂移。
        raise AssertionError(
            f"> ERR: [Python] Unexpected manual fallback dependency URLs: {sorted(set_manual_fallback_urls)}"
        )

    # FPGA-Agent-skills fallback 必须列出完整技能集合，防止降级能力缺项。
    dict_fpga_dependency = next(  # manual_fallback 中的 FPGA-Agent-skills 聚合依赖
        item  # manual_fallback 中唯一的 FPGA-Agent-skills 依赖项
        for item in dict_dependencies["manual_fallback"]  # defaults 里的手动降级依赖集合
        if item["id"] == "fpga-agent-skills"  # 只选择 FPGA-Agent-skills 聚合依赖
    )  # FPGA-Agent-skills manual fallback 依赖项

    # fallback 依赖应包含 8 个 Vivado/Vitis 相关 skill。
    if len(dict_fpga_dependency["skills"]) != 8:

        # 技能数量不足会导致 fpga-developer 无法完整降级路由。
        raise AssertionError("> ERR: [Python] FPGA-Agent-skills dependency must include all 8 Vivado/Vitis skills.")

    # 首次 FPGA workflow 必须询问用户，避免自动选错开发 skill。
    if dict_routing["selection_policy"] != "ask_on_first_fpga_workflow":

        # selection_policy 漂移会改变用户交互契约。
        raise AssertionError("> ERR: [Python] FPGA developer routing must ask on first FPGA workflow.")

    # 只有严格布尔 True 表示 developer 已安装时仍强制 FPGA-Agent。
    value_fpga_required = dict_routing["fpga_agent_required_when_developer_present"]  # routing 原始强制 fallback 字段

    # 严格布尔判定复刻旧 `is True` 语义，避免字符串真值误触发。
    bool_fpga_agent_required = isinstance(value_fpga_required, bool) and value_fpga_required  # 是否明确强制 fallback

    # developer skill 已存在时不能再强制 FPGA-Agent-skills。
    if bool_fpga_agent_required:

        # 强制 fallback 会破坏优先使用已安装 developer skill 的路由策略。
        raise AssertionError(
            "> ERR: [Python] FPGA-Agent-skills must not be required when a developer skill is installed."
        )

    # AMD/Xilinx 路由必须识别 Vivado 与 Vitis 两类 developer skill。
    if dict_routing["vendors"]["amd_xilinx"]["skills"] != [
        "vivado-developer",
        "vitis-developer",
    ]:

        # 厂商路由缺失会导致 FPGA workflow 无法正确分派。
        raise AssertionError(
            "> ERR: [Python] AMD-Xilinx developer routing must recognize vivado-developer and vitis-developer."
        )

    # PangoMicro 路由保持单一 pds-developer skill 映射。
    if dict_routing["vendors"]["pangomicro"]["skills"] != ["pds-developer"]:

        # 厂商映射漂移会影响国产 FPGA workflow 分派。
        raise AssertionError("> ERR: [Python] PangoMicro developer routing must recognize pds-developer.")

# verify_hardcoded_paths 防止 skill 源文件携带本机绝对路径。
def verify_hardcoded_paths() -> None:
    """扫描 skill 源文件中的硬编码绝对路径，允许配置文档中的示例除外。

    :param: 此函数不接收外部业务参数。
    :return: 不返回业务值；执行完成即表示“扫描 skill 源文件中的硬编码绝对路径，允许配置文档中的示例除外。”对应步骤未发现阻断。
    :raises AssertionError: 当“扫描 skill 源文件中的硬编码绝对路径，允许配置文档中的示例除外。”阶段发现配置、产物或执行结果不满足门禁要求时抛出。
    """

    # 允许清单只覆盖配置和配置说明，其他文件不应出现本机绝对路径。
    set_allowed = {
        "config/defaults.json",  # defaults 可携带工具链路径样例
        "references/integration/configuration.md",  # configuration 文档可展示路径配置示例
    }  # 允许包含示例绝对路径的 skill 相对文件

    # 违规列表保存 skill 相对路径，便于失败消息稳定可读。
    list_violations: list[str] = []  # 检出的硬编码绝对路径文件

    # 逐个扫描 skill 源文件，忽略 pycache 和报告目录。
    for path_file in iter_skill_files():

        # 使用 skill 相对路径匹配 allowlist，避免绝对路径随机器变化。
        str_rel = path_file.relative_to(SKILL_ROOT).as_posix()  # 当前被扫描文件的 skill 相对路径

        # 配置和文档文件可保留路径示例。
        if str_rel in set_allowed:

            # 允许的文档示例不参与硬编码路径发布阻断。
            continue

        # 文本按 UTF-8 宽容读取，避免二进制残片让扫描中断。
        str_text = path_file.read_text(encoding="utf-8", errors="ignore")  # 绝对路径扫描使用的文件文本

        # 命中绝对路径模式时记录文件，不在消息中暴露具体本机路径。
        if ABSOLUTE_PATH_PATTERN.search(str_text):

            # 只记录一次文件路径，避免同一文件多处命中造成噪声。
            list_violations.append(str_rel)

    # 任意非允许文件命中绝对路径都视为发布阻塞问题。
    if list_violations:

        # 违规文件排序后输出，避免文件系统遍历顺序影响报告。
        str_violation_summary = ", ".join(sorted(list_violations))  # 排序后的硬编码路径违规文件列表

        # 错误文本保留旧前缀，后续仅拼接稳定的相对路径摘要。
        raise AssertionError(
            "> ERR: [Python] Hardcoded absolute paths found outside config/docs: " + str_violation_summary
        )

# verify_no_ref_dependencies 防止临时 ref 输入泄漏到活动文档或候选发布目录。
def verify_no_ref_dependencies() -> None:
    """扫描活动文件和候选 release，确认没有依赖 ref 临时目录。

    :param: 此函数不接收外部业务参数。
    :return: 不返回业务值；执行完成即表示“扫描活动文件和候选 release，确认没有依赖 ref 临时目录。”对应步骤未发现阻断。
    :raises AssertionError: 当“扫描活动文件和候选 release，确认没有依赖 ref 临时目录。”阶段发现配置、产物或执行结果不满足门禁要求时抛出。
    """

    # 违规列表保存项目相对路径，兼顾活动文件和 dist 候选 release。
    list_violations: list[str] = []  # 检出的 ref 临时目录依赖路径

    # 活动路径默认至少覆盖安装包内对用户可见的 skill 入口。
    list_active_paths = [SKILL_ROOT / "SKILL.md"]  # 需要禁止 ref 泄漏的基础活动文件集合

    # 只有源码仓库布局才扫描 repo 级治理文档、smoke 主脚本和候选发布目录。
    if is_source_repository_layout():

        # 当前版本号只用于定位同版本候选 release 目录。
        from scripts.python.version import __version__

        # candidate_release 只在当前版本发布目录已存在时参与扫描。
        path_candidate_release = PROJECT_ROOT / "dist" / f"readable-verilog-generator-v{__version__}"  # 当前版本候选发布目录

        # repo 级活动文件只在源码仓库场景成立。
        list_active_paths.extend(
            [
                PROJECT_ROOT / "AGENTS.md",  # 根治理说明允许单独的 ref 概念例外
                PROJECT_ROOT / "docs" / "development" / "DEVELOPMENT.md",  # 开发说明不得绑定临时 ref
                PROJECT_ROOT / "docs" / "handoff" / "HANDOFF.md",  # 最新交接不得残留 ref 输入路径
                PROJECT_ROOT / "docs" / "git_manager" / "CHANGELOG.md",  # 变更日志不得记录本地 ref 依赖
                PROJECT_ROOT / "tests" / "smoke" / "run_smoke.py",  # smoke 主脚本不得依赖 ref 临时输入
            ]
        )

    # 安装副本没有源码仓库的 dist 候选目录。
    else:

        # 安装态不扫描 repo 级候选发布目录。
        path_candidate_release = None  # 当前布局下不存在候选 release 扫描目标

    # references 下的规则文档可能被打包进 skill，需要一起扫描。
    list_active_paths.extend(sorted((SKILL_ROOT / "references").glob("*")))

    # scripts 根目录下仍保留的直接入口文件同样不应依赖临时 ref 输入。
    path_scripts_root = SKILL_ROOT / "scripts"  # skill 根目录下的 scripts 入口目录

    # 把 scripts 根目录下仍保留的直接入口文件加入 ref 依赖扫描列表。
    list_active_paths.extend(sorted(path_scripts_root.glob("*")))

    # 新布局的 scripts/python 实现目录也必须参与 ref 依赖扫描。
    path_python_scripts_root = path_scripts_root / "python"  # 新布局 Python 规范实现目录

    # 只有 scripts/python 存在时才递归纳入扫描列表，避免安装态或异常骨架误报。
    if path_python_scripts_root.exists():

        # 递归追加新布局实现文件，保证删除旧 wrapper 后仍覆盖真实脚本主体。
        list_active_paths.extend(sorted(path_python_scripts_root.rglob("*")))

    # 活动文件扫描只处理存在的普通文件，目录由 glob/rglob 后续分支处理。
    for path_file in list_active_paths:

        # 缺失或目录条目不参与文本扫描。
        if not path_file.exists() or not path_file.is_file():

            # release 候选可能已被清理，跳过不可读条目。
            continue

        # 项目相对路径用于 allowlist 和失败报告。
        str_rel = _project_relative(path_file)  # 当前活动文件的项目相对路径

        # 文本宽容读取，避免偶发编码问题阻塞 ref 扫描本身。
        str_text = path_file.read_text(encoding="utf-8", errors="ignore")  # 当前活动文件文本内容

        # 命中 ref 临时目录且未在 allowlist 中时记录违规。
        if REF_DEPENDENCY_PATTERN.search(str_text) and not _allowed_ref_dependency_path(str_rel):

            # 违规路径统一用项目相对形式输出。
            list_violations.append(_project_relative(path_file))

    # 候选 release 存在时也必须无 ref 依赖，防止已生成包携带临时路径。
    if path_candidate_release is not None and path_candidate_release.exists():

        # release 目录递归扫描，跳过 Python 编译缓存。
        for path_file in path_candidate_release.rglob("*"):

            # 只扫描普通文件，目录不包含文本内容。
            if not path_file.is_file():

                # 目录节点没有文本载荷，继续扫描其他 release 条目。
                continue

            # 缓存目录和 pyc/pyo 不属于发布源文本。
            if "__pycache__" in path_file.parts or path_file.suffix.lower() in {".pyc", ".pyo"}:

                # Python 缓存产物不代表发布源码依赖。
                continue

            # 候选 release 文件命中 ref 模式即为违规。
            if REF_DEPENDENCY_PATTERN.search(path_file.read_text(encoding="utf-8", errors="ignore")):

                # release 违规也用项目相对路径，便于用户定位。
                list_violations.append(_project_relative(path_file))

    # 任何 ref 临时目录依赖都阻塞 validate，避免本地输入路径进入发布物。
    if list_violations:

        # ref 违规路径排序后拼入旧错误前缀，便于稳定比对。
        str_violation_summary = ", ".join(sorted(list_violations))  # 排序后的 ref 依赖违规路径摘要

        # 错误前缀保持旧语义，后续追加具体违规路径。
        str_error_prefix = (
            "External temporary reference directory dependencies remain in active skill or candidate release files: "  # ref 泄漏错误前缀
        )  # ref 依赖泄漏错误前缀

        # 拼接前缀和摘要，避免单行过长同时保留原错误含义。
        raise AssertionError("> ERR: [Python] Ref temporary directory dependencies remain in active files.")

# _allowed_ref_dependency_path 记录治理文件中允许出现 ref 字样的兼容例外。
def _allowed_ref_dependency_path(rel: str) -> bool:
    """判断项目相对路径是否允许包含 ref 临时目录提示。

    :param rel: 项目相对路径文本，用于判断 ref 依赖例外。
    :return: 返回布尔值；True 表示“判断项目相对路径是否允许包含 ref 临时目录提示。”对应条件命中。
    """

    # AGENTS.md 的目录治理说明可引用 ref 概念，运行时代码和发布内容不允许。
    return rel == "AGENTS.md"

# verify_no_residuals 确认 validate 结束后没有禁止的临时产物残留。
def verify_no_residuals(settings: dict, smoke_dir: Path) -> None:
    """检查 smoke 目录和 skill 根下的禁止残留文件。

    :param settings: Verilog skill 治理配置字典，提供路径、依赖和验证开关。
    :param smoke_dir: 本轮验证使用的临时 smoke 目录。
    :return: 不返回业务值；执行完成即表示“检查 smoke 目录和 skill 根下的禁止残留文件。”对应步骤未发现阻断。
    :raises AssertionError: 当“检查 smoke 目录和 skill 根下的禁止残留文件。”阶段发现配置、产物或执行结果不满足门禁要求时抛出。
    """

    # smoke 根目录来自 settings，延迟导入保持脚本顶层无 runtime 副作用。
    from scripts.python.workflow.config import path_setting

    # residuals 收集项目或 skill 相对路径，用于最终 AssertionError。
    list_residuals: list[str] = []  # validate 后仍存在的禁止残留路径

    # forbidden_residuals 由配置控制，避免脚本内硬编码业务残留名。
    set_names = set(  # validate 结束后禁止继续存在的残留名集合
        settings.get("validation", {}).get("forbidden_residuals", [])  # defaults 中的 forbidden_residuals 配置项
    )

    # smoke_root 是配置化 smoke 根，用来区分允许清理的运行目录。
    path_smoke_root = path_setting(settings, "smoke_dir").resolve()  # smoke 运行产物根目录

    # 调用方传入的 smoke_dir 本身存在时，说明本轮 smoke 目录没有清掉。
    if smoke_dir.exists():

        # smoke_dir 通常位于仓库根，使用项目相对路径报告。
        list_residuals.append(_project_relative(smoke_dir))

    # 递归检查 skill 根，跳过配置化 smoke 根内部内容。
    for path_file in SKILL_ROOT.rglob("*"):

        # resolve 用于处理符号链接和 Windows 规范路径。
        try:

            # 当前 skill 路径规范化后才能和配置化 smoke 根做边界比较。
            path_resolved = path_file.resolve()  # 当前 skill 路径的规范化绝对路径

            # smoke 根本身由 smoke_dir 检查覆盖，避免重复报告。
            if path_resolved == path_smoke_root:

                # smoke 根目录本体不按 skill 主体残留重复统计。
                continue

            # smoke 根内部运行产物不按 skill 主体残留规则重复检查。
            try:

                # relative_to 成功表示该路径属于 smoke 根内部，可跳过主体残留扫描。
                path_resolved.relative_to(path_smoke_root)

                # smoke 根内部内容由 smoke 清理边界单独负责。
                continue

            # 非 smoke 根内部路径继续按 skill 主体残留规则检查。
            except ValueError:

                # 非 smoke 根内部路径需要继续接受 skill 主体残留规则。
                pass

        # 遍历期间文件被清理时不应让残留检查失败。
        except FileNotFoundError:

            # 遍历期间被清理掉的路径视为无残留。
            continue

        # 文件名或任一路径片段命中 forbidden_residuals 都视为残留。
        if path_file.name in set_names or any(part in set_names for part in path_file.parts):

            # skill 内残留使用 skill 相对路径，避免泄露本机绝对路径。
            list_residuals.append(path_file.relative_to(SKILL_ROOT).as_posix())

    # 发现残留时以排序摘要失败，便于人工快速清理。
    if list_residuals:

        # 残留路径排序后输出，便于人工按列表清理。
        str_residual_summary = ", ".join(sorted(list_residuals))  # 排序后的残留路径摘要

        # 保留旧错误前缀，后续工具可继续匹配该诊断。
        raise AssertionError("> ERR: [Python] Residual validation artifacts remain: " + str_residual_summary)

# cleanup_residuals 清除 validate 过程中允许自动删除的本地残留。
def cleanup_residuals(settings: dict, smoke_dir: Path) -> None:
    """删除 smoke 目录、workflow-state 和 Python 缓存目录。

    :param settings: Verilog skill 治理配置字典，提供路径、依赖和验证开关。
    :param smoke_dir: 本轮验证使用的临时 smoke 目录。
    :return: 不返回业务值；执行完成即表示“删除 smoke 目录、workflow-state 和 Python 缓存目录。”对应步骤未发现阻断。
    """

    # 删除调用方传入的本轮 smoke 目录，内部 helper 会验证它没有越过 smoke 根。
    remove_inside_smoke_root(settings, smoke_dir)

    # workflow-state.json 是旧 workflow 运行状态，可在 validate 前后安全清理。
    remove_inside_skill(SKILL_ROOT / "workflow-state.json")

    # __pycache__ 目录按逆序删除，确保深层缓存先于父目录清理。
    for path_cache_dir in sorted(SKILL_ROOT.rglob("__pycache__"), reverse=True):

        # 每个缓存目录都经过 skill 根边界检查后删除。
        remove_inside_skill(path_cache_dir)

    # 清理完内容后尝试移除空 smoke 根目录壳。
    _prune_empty_smoke_root(settings)

# cleanup_audit_retry_local_artifacts 清理 audit 瞬态重试仅归属当前进程的本地产物。
def cleanup_audit_retry_local_artifacts(settings: dict, smoke_dir: Path) -> None:
    """清理 audit 瞬态重试场景下仅归属当前 validate 进程的本地产物。

    :param settings: Verilog skill 治理配置字典，提供路径、依赖和验证开关。
    :param smoke_dir: 本轮验证使用的临时 smoke 目录。
    :return: 不返回业务值；执行完成即表示“清理 audit 瞬态重试场景下仅归属当前 validate 进程的本地产物。”对应步骤未发现阻断。
    """

    # audit 重试只允许复用当前进程的局部残留清理逻辑，避免并行 worker 互删兄弟 smoke 目录。
    return cleanup_residuals(settings, smoke_dir)

# cleanup_audit_runtime_artifacts 清空审计运行后允许重建的本地运行产物。
def cleanup_audit_runtime_artifacts(settings: dict, smoke_dir: Path) -> None:
    """维护 cleanup_audit_runtime_artifacts 对应的验证辅助流程。

    :param settings: Verilog skill 治理配置字典，提供路径、依赖和验证开关。
    :param smoke_dir: 本轮验证使用的临时 smoke 目录。
    :return: 不返回业务值；执行完成即表示“维护 cleanup_audit_runtime_artifacts 对应的验证辅助流程。”对应步骤未发现阻断。
    """

    # smoke 根目录来自 settings，审计清理阶段才需要读取。
    from scripts.python.workflow.config import path_setting

    # 先复用普通残留清理逻辑，确保 workflow-state 和 pycache 已移除。
    cleanup_residuals(settings, smoke_dir)

    # audit retry 只裁剪配置指定的运行产物根。
    path_smoke_root = path_setting(settings, "smoke_dir").resolve()  # audit 重试清理边界

    # smoke 根不存在说明没有运行产物需要清理。
    if not path_smoke_root.exists():

        # 没有审计运行目录时，调用方无需承担额外清理副作用。
        return

    # 逆序清理让子目录内容先于父目录被移除，减少 Windows 删除失败概率。
    for path_entry in sorted(path_smoke_root.iterdir(), reverse=True):

        # 每个条目仍通过 smoke 根边界检查后再删除。
        remove_inside_smoke_root(settings, path_entry)

    # 清理完子项后，如果 smoke 根为空则删除根目录本身。
    _prune_empty_smoke_root(settings)

# _prune_empty_smoke_root 只在 smoke 根无内容时移除目录壳。
def _prune_empty_smoke_root(settings: dict) -> None:
    """维护 _prune_empty_smoke_root 对应的验证辅助流程。

    :param settings: Verilog skill 治理配置字典，提供路径、依赖和验证开关。
    :return: 不返回业务值；执行完成即表示“维护 _prune_empty_smoke_root 对应的验证辅助流程。”对应步骤未发现阻断。
    """
    # smoke 根目录来自 settings，裁剪空目录时才需要读取。
    from scripts.python.workflow.config import path_setting

    # smoke 根路径来自配置，避免把固定路径写死进脚本。
    path_smoke_root = path_setting(settings, "smoke_dir").resolve()  # 需要按空目录裁剪的 smoke 根

    # 根目录不存在时无需裁剪，也不应创建任何新目录。
    if not path_smoke_root.exists():

        # smoke 根已不存在时，空目录裁剪视为完成。
        return

    # 通过 next 探测目录是否为空，不把所有文件名读入内存。
    try:

        # 只读取一个目录项即可判断 smoke 根仍有内容。
        next(path_smoke_root.iterdir())

    # StopIteration 表示 smoke 根已经没有任何子项。
    except StopIteration:

        # 只有确认为空时才删除 smoke 根目录壳；并发清理抢先删除时也视为成功。
        try:

            # smoke 根目录在确认为空后即可安全删除目录壳。
            path_smoke_root.rmdir()

        # 其他并发清理若已删掉目录壳，本轮清理保持幂等成功。
        except FileNotFoundError:

            # 目录壳已被其他流程删除时，无需继续处理。
            return

    # 并发清理可能在 exists 检查之后抢先删掉根目录，此时同样视为成功。
    except FileNotFoundError:

        # smoke 根目录已消失时，空目录裁剪不应把竞态暴露成失败。
        return

# remove_inside_skill 删除 skill 根内的临时文件，并拒绝越界路径。
def remove_inside_skill(path: Path) -> None:
    """在 skill 根目录边界内安全删除文件或目录。

    :param path: 待解析、删除或展示的路径。
    :return: 不返回业务值；执行完成即表示“在 skill 根目录边界内安全删除文件或目录。”对应步骤未发现阻断。
    :raises AssertionError: 当“在 skill 根目录边界内安全删除文件或目录。”阶段发现配置、产物或执行结果不满足门禁要求时抛出。
    """

    # resolve 失败表示目标在当前平台上不可定位，视为无需删除。
    try:

        # 待删除路径规范化后才能确认是否仍位于 skill 根内。
        path_resolved = path.resolve()  # 待删除路径的规范化绝对路径

    # 已被其他清理步骤移除的目标无需再次处理。
    except FileNotFoundError:

        # 目标路径已消失时，skill 根清理保持幂等成功。
        return

    # 幂等清理允许目标已经被其他步骤删除。
    if not path_resolved.exists():

        # 其他清理步骤已删除目标时，不再重复触发文件系统操作。
        return

    # relative_to 是删除安全边界，防止调用者传入仓库外路径。
    try:

        # relative_to 成功表示目标仍在当前 skill 主体目录内。
        path_resolved.relative_to(SKILL_ROOT.resolve())

    # 越界删除请求必须转化为显式治理失败。
    except ValueError as exc:

        # 越过 skill 根边界表示调用方传入了不允许自动删除的路径。
        raise AssertionError(f"> ERR: [Python] Refusing to remove outside skill root: {path_resolved}") from exc

    # 目录使用带重试的 rmtree，文件直接 unlink；瞬态消失继续视为幂等成功。
    try:

        # 这里先区分目录和文件，避免把刚消失的目录误走到单文件 unlink 分支。
        if path_resolved.is_dir():

            # Windows 下 pycache 可能短暂被占用，目录删除走重试路径。
            _remove_tree_with_retry(path_resolved)

        # 非目录目标只允许按单文件删除。
        else:

            # 单文件删除不需要递归，避免误删同名目录内容。
            path_resolved.unlink()

    # exists/is_dir/unlink 之间被其他清理流程抢先删除时，应保持幂等成功。
    except FileNotFoundError:

        # 目标已在本轮边界检查后消失，说明清理目标已经达成。
        return

# remove_inside_smoke_root 删除 smoke 运行根内的临时文件，并拒绝越界路径。
def remove_inside_smoke_root(settings: dict, path: Path) -> None:
    """在 smoke 根目录边界内安全删除文件或目录。

    :param settings: Verilog skill 治理配置字典，提供路径、依赖和验证开关。
    :param path: 待解析、删除或展示的路径。
    :return: 不返回业务值；执行完成即表示“在 smoke 根目录边界内安全删除文件或目录。”对应步骤未发现阻断。
    :raises AssertionError: 当“在 smoke 根目录边界内安全删除文件或目录。”阶段发现配置、产物或执行结果不满足门禁要求时抛出。
    """

    # smoke 根目录来自 settings，删除前用于越界保护。
    from scripts.python.workflow.config import path_setting

    # smoke 根来自 settings，保持测试和默认配置的目录隔离。
    path_smoke_root = path_setting(settings, "smoke_dir").resolve()  # 删除边界使用的 smoke 根目录

    # resolve 失败表示目标已不可访问，清理函数保持幂等。
    try:

        # 待删除 smoke 产物规范化后才能执行边界判断。
        path_resolved = path.resolve()  # 待删除 smoke 产物的规范化绝对路径

    # smoke 产物可能已被前一轮清理删除。
    except FileNotFoundError:

        # smoke 产物已消失时无需继续边界检查或删除。
        return

    # 目标不存在时直接返回，避免清理流程因竞态中断。
    if not path_resolved.exists():

        # 并发清理已移除目标时，本函数视为完成。
        return

    # 删除前确认目标仍位于 smoke 根目录内。
    try:

        # relative_to 成功表示目标属于允许自动清理的 smoke 根。
        path_resolved.relative_to(path_smoke_root)

    # smoke 根外路径不允许由该 helper 自动删除。
    except ValueError as exc:

        # smoke 根之外的路径绝不允许由该 helper 自动删除。
        raise AssertionError(f"> ERR: [Python] Refusing to remove outside smoke root: {path_resolved}") from exc

    # 目录和文件分支分开处理；瞬态消失继续视为幂等成功。
    try:

        # 目录目标走递归删除路径，文件目标走单文件删除路径。
        if path_resolved.is_dir():

            # smoke 子目录可能包含多层运行工件，递归删除前已完成 smoke 边界检查。
            _remove_tree_with_retry(path_resolved)

        # 单个 smoke 文件直接删除即可。
        else:

            # smoke 根内单文件可以直接删除。
            path_resolved.unlink()

    # smoke 产物在边界检查后被并发删除时，当前清理视为幂等成功。
    except FileNotFoundError:

        # 目标已被其他流程删除时，本 helper 不应把竞态暴露成失败。
        return

# _remove_tree_with_retry 缓解 Windows 文件句柄短暂占用导致的 rmtree 失败。
def _remove_tree_with_retry(path: Path, *, attempts: int = 5, delay_s: float = 0.1) -> None:
    """带短暂重试地删除目录树，保留最终 OSError 供调用者诊断。

    :param path: 待解析、删除或展示的路径。
    :param attempts: 目录删除最多重试次数。
    :param delay_s: 两次删除重试之间的等待秒数。
    :return: 不返回业务值；执行完成即表示“带短暂重试地删除目录树，保留最终 OSError 供调用者诊断。”对应步骤未发现阻断。
    :raises last_error: 当“带短暂重试地删除目录树，保留最终 OSError 供调用者诊断。”阶段发现配置、产物或执行结果不满足门禁要求时抛出。
    """

    # 记录最后一次 OSError，循环耗尽后重新抛出原始异常。
    value_last_error: OSError | None = None  # 最后一次 rmtree 失败的系统异常

    # 删除重试只处理瞬时文件占用，不吞掉持续存在的权限或路径错误。
    for _ in range(attempts):

        # 每次尝试都完整执行 rmtree，成功后立即结束清理。
        try:

            # 递归删除由上层边界检查保护的目录树。
            shutil.rmtree(path)

            # rmtree 成功表示无需继续重试。
            return

        # 目录被其他流程删除时，当前清理可视为成功。
        except FileNotFoundError:

            # 目录已不存在时视为清理成功，保持调用方幂等。
            return

        # 其他系统错误需要保存后重试。
        except OSError as exc:

            # 保存异常后再检查路径，区分“已被删掉”和“仍然失败”。
            value_last_error = exc  # 最近一次目录删除失败的 OSError

            # 其他进程可能刚刚删掉该目录，此时无需继续重试。
            if not path.exists():

                # 失败后复查发现目录已消失，说明清理目标已经达成。
                return

            # 短暂等待给 Windows 释放文件句柄的时间。
            time.sleep(delay_s)

    # 重试耗尽后保留原始异常类型和消息。
    if value_last_error is not None:

        # 保留原系统异常作为 cause，异常文本使用 current-project 前缀。
        raise OSError("> ERR: [Python] directory removal failed after retries.") from value_last_error

# iter_skill_files 枚举可参与发布/残留检查的 skill 源文件。
def iter_skill_files() -> list[Path]:
    """列出 skill 根下除缓存和临时报告外的普通文件。

    :param: 此函数不接收外部业务参数。
    :return: 返回列表对象；元素顺序服务于“列出 skill 根下除缓存和临时报告外的普通文件。”阶段的后续调用。
    """

    # 忽略目录集合对应运行时缓存和本地报告，避免把 transient 文件纳入发布检查。
    set_ignored_parts = {"__pycache__", "_smoke_runs", "reports"}  # skill 文件枚举时跳过的路径片段

    # release receipt 属于打包元数据，不应参与源码 hygiene 扫描。
    set_ignored_names = {"RELEASE_RECEIPT.json"}  # skill 文件枚举时跳过的生成元数据文件名

    # 返回 Path 对象列表，调用方负责转换成项目相对路径或读取内容。
    list_files: list[Path] = []  # 通过过滤条件的 skill 文件路径

    # rglob 遍历 skill 主体目录，保留既有深度递归行为。
    for path_entry in SKILL_ROOT.rglob("*"):

        # 目录不参与文本检查和发布文件枚举。
        if not path_entry.is_file():

            # 非普通文件不属于发布文本扫描目标。
            continue

        # 路径片段集合用于一次性判断是否落在忽略目录内。
        set_rel_parts = set(path_entry.relative_to(SKILL_ROOT).parts)  # 当前文件相对 skill 根的路径片段

        # 命中缓存或报告目录时跳过整个文件。
        if set_rel_parts & set_ignored_parts:

            # 缓存和报告目录不属于发布文本扫描范围。
            continue

        # release receipt 只记录打包 provenance，不属于源码文本约束范围。
        if path_entry.name in set_ignored_names:

            # 跳过生成元数据，避免安装副本把 receipt 当成活动源码。
            continue

        # 编译产物不属于 skill 源内容，也不应进入文本检查。
        if path_entry.suffix.lower() in {".pyc", ".pyo"}:

            # Python 字节码产物不能作为 skill 源文件返回。
            continue

        # 通过所有过滤条件的文件才交给上层检查。
        list_files.append(path_entry)

    # 保持原返回类型为 list[Path]，不在此处排序以沿用 rglob 顺序。
    return list_files

# _project_relative 统一把路径呈现为项目相对形式。
def _project_relative(path: Path) -> str:
    """返回用于错误信息和摘要的项目相对路径。

    :param path: 待解析、删除或展示的路径。
    :return: 返回字符串结果；用于“返回用于错误信息和摘要的项目相对路径。”阶段向调用方传递解析后的文本值。
    """

    # 项目内文件用 POSIX 分隔符，便于跨平台报告比对。
    try:

        # 成功相对化的项目内路径用于稳定错误消息。
        return path.relative_to(PROJECT_ROOT).as_posix()

    # 项目外路径只能保留原始文本，避免误导定位。
    except ValueError:

        # 项目外路径保留绝对形式，避免丢失诊断信息。
        return str(path)

# project_artifact_path 解析命令行或配置中出现的项目工件路径。
def project_artifact_path(path: str | Path) -> Path:
    """把相对路径锚定到项目根，绝对路径保持原样。

    :param path: 待解析、删除或展示的路径。
    :return: 返回路径对象；该路径由“把相对路径锚定到当前 validate 工作根，绝对路径保持原样。”阶段生成或解析。
    """

    # 允许调用方传入字符串或 Path，统一转换后判断是否需要项目根补全。
    path_candidate = Path(path)  # 待解析的项目工件路径

    # 绝对路径通常来自用户显式参数，应保持不变。
    if path_candidate.is_absolute():

        # 调用方给出的绝对路径不再叠加项目根。
        return path_candidate

    # 相对路径统一锚定到当前 validate 工作根，避免安装态把工件错误拼到 `.codex` 父层。
    return validation_workspace_root() / path_candidate

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
