"""远程验证 authority 归一化与命令上下文构造。"""

# 标准库负责编码、JSON、POSIX 相对路径和类型协议。
import base64
import json
from pathlib import PurePosixPath
from typing import Any, Callable, Mapping

# _shell_module_text 保留安全 dotted module 的可发现调用文本。
def _shell_module_text(str_module: str, func_quote: Callable[[str], str]) -> str:
    """返回可嵌入 shell 的 workflow module 文本。

    参数:
        str_module: authority 声明的 Python dotted module。
        func_quote: shell 参数转义回调。
    返回:
        安全 identifier 原样文本，否则返回 shell quoted 文本。
    """

    # 只有每一段都是 Python identifier 时才允许保留可发现的未 quote 形式。
    tuple_parts = tuple(str(str_module).split("."))  # 用于判断 workflow module 是否可直接传给 -m

    # 只有合法标识符才可在 `python -m` 中保持原文。
    bool_safe = bool(tuple_parts) and all(str_part.isidentifier() for str_part in tuple_parts)  # 可发现 module 调用标记

    # 非法或空 module 继续走统一 shell quoting。
    if not bool_safe:

        # 保持 authority 值不被 shell 重新解释。
        return func_quote(str(str_module))

    # 返回可直接被 `python -m` 发现的 module 文本。
    return str(str_module)

# 复制完整 settings、remote 或 validation 输入映射。
def _input_mapping(dict_input: Mapping[str, Any]) -> dict[str, Any]:
    """复制完整 settings、remote 或 validation 输入映射。

    参数:
        dict_input: 调用方传入的 authority 映射。
    返回:
        不会修改调用方的独立普通字典。
    """

    # 完整 settings 优先切换到 validation 子树。
    dict_remote = dict_input.get("remote")  # 调用方 remote 子树

    # 只在 remote 子树是对象时切换输入根。
    if isinstance(dict_remote, Mapping):

        # 兼容没有 validation 包装层的扁平 remote 映射。
        dict_input = dict_remote.get("validation", dict_remote)  # 选择后续布局归一化的 authority 输入

    # 返回独立对象，防止修改调用方 settings。
    return dict(dict_input)  # 返回不会回写调用方的 authority 副本

# 合并 authority layout，并迁移兼容的扁平路径字段。
def _layout(dict_normalized: dict[str, Any], dict_bundled: Mapping[str, Any]) -> dict[str, Any]:
    """合并 authority layout，并迁移兼容的扁平路径字段。

    参数:
        dict_normalized: 调用方 authority 工作副本。
        dict_bundled: bundled validation 默认配置。
    返回:
        canonical 远端相对路径布局。
    """

    # 读取调用方显式 layout。
    dict_layout = dict(dict_normalized.get("layout", {}))  # 复制调用方 layout，供路径字段归一化

    # 扁平字段只在嵌套字段缺失时补入。
    for str_key in ("remote_root", "workspace_root", "reports_root"):

        # 保留调用方显式路径。
        if str_key not in dict_layout and str_key in dict_normalized:

            # 将扁平路径迁移到 canonical layout。
            dict_layout[str_key] = dict_normalized[str_key]  # 将扁平路径纳入 canonical layout

    # bundled layout 只提供可替换默认值。
    dict_layout = {**dict_bundled["layout"], **dict_layout}  # 让显式 layout 覆盖 bundled 默认路径

    # 缺失边界保持空值，等待上层安全校验。
    dict_layout["remote_root"] = dict_layout.get("remote_root", "")  # 远端写入根作为路径安全校验输入

    # 记录源码 workspace 相对根，供上传和 shell cwd 复用。
    dict_layout["workspace_root"] = dict_layout.get("workspace_root", "")  # workspace 根用于源码路径拼接

    # 记录报告相对根，供阶段日志和 receipt 复用。
    dict_layout["reports_root"] = dict_layout.get("reports_root", "")  # reports 根用于证据路径拼接

    # 返回已经合并并清理缺省边界的 layout。
    return dict_layout  # 返回可用于远端路径拼接的 canonical layout

# 迁移扁平 artifact 别名并补齐 authority 默认值。
def _artifacts(dict_normalized: dict[str, Any], dict_bundled: Mapping[str, Any]) -> dict[str, Any]:
    """迁移扁平 artifact 别名并补齐 authority 默认值。

    参数:
        dict_normalized: 调用方 authority 工作副本。
        dict_bundled: bundled validation 默认配置。
    返回:
        canonical artifact 文件名映射。
    """

    # 复制嵌套或扁平 artifact 映射。
    dict_artifacts = dict(dict_normalized.get("artifacts", dict_normalized.get("artifact_paths", {})))  # 复制工件映射供别名归一化

    # 每个 authority artifact 支持常见后缀和前缀别名。
    for str_key, value_default in dict_bundled["artifacts"].items():

        # 生成当前 artifact 的兼容别名集合。
        tuple_aliases = tuple(  # 生成当前 artifact 的 alias tuple
            str_key + suffix for suffix in ("", "_path", "_json", "_artifact")  # 拼出后缀别名
        ) + ("artifact_" + str_key,)  # 追加前缀别名供兼容解析

        # 首个显式值覆盖 bundled 默认。
        for str_alias in tuple_aliases:

            # 只迁移存在的显式值。
            if str_alias in dict_normalized:

                # 保存调用方 artifact 值。
                dict_artifacts[str_key] = dict_normalized[str_alias]  # 保存调用方显式 artifact 路径

                # 当前键已有显式值，不再尝试后续别名。
                break

        # 缺失值回退到 bundled authority。
        dict_artifacts.setdefault(str_key, str(value_default))  # 缺失时保留 authority 默认工件路径

    # 返回独立 artifact 映射。
    return dict_artifacts  # 返回 canonical artifact 映射

# 归一化嵌套、列表和 case_* 形式的案例目录。
def _case_catalog(dict_normalized: dict[str, Any], dict_bundled: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    """归一化嵌套、列表和 case_* 形式的案例目录。

    参数:
        dict_normalized: 调用方 authority 工作副本。
        dict_bundled: bundled validation 默认配置。
    返回:
        canonical 案例目录和来源路径。
    """

    # 读取可能是映射或路径字符串的 catalog 值。
    value_catalog = dict_normalized.get("case_catalog")  # authority 案例目录输入

    # 映射形式保留 simulation/all/prechecks 结构。
    if isinstance(value_catalog, Mapping):

        # 复制调用方 canonical catalog。
        dict_catalog = dict(value_catalog)  # 复制 authority 提供的 canonical 案例目录

    # 非映射形式从 cases 或 case_* 收集。
    else:

        # 优先采用列表形式的 cases。
        list_cases = dict_normalized.get("cases") or dict_normalized.get("fixture_cases")  # 读取可替换的扁平案例列表

        # 缺少列表时收集 case_* 字符串字段。
        if not isinstance(list_cases, list):

            # 初始化案例收集器。
            list_cases = []  # 初始化 case_* 案例列表

            # 遍历 authority 字段。
            for str_key, value in dict_normalized.items():

                # 排除 catalog 本身，只保留案例字符串。
                if str_key.startswith("case_") and str_key != "case_catalog" and isinstance(value, str):

                    # 保存可执行案例标识。
                    list_cases.append(value)  # 记录 authority 声明的单个案例

        # 空列表回退 bundled 仿真案例。
        list_cases = list_cases or list(dict_bundled["case_catalog"]["simulation"])  # 缺省时复用 bundled 仿真案例集合

        # 同一 authority 列表作为 simulation/all 案例。
        dict_catalog = {"simulation": list(list_cases), "all": list(list_cases)}  # 生成 canonical 仿真和 retained 案例目录

    # prechecks 缺失时保留稳定空映射。
    dict_catalog.setdefault("prechecks", {})  # 保证远端内联脚本可读取 precheck 入口

    # 保存 catalog 来源路径供 shell receipt 追踪。
    str_catalog_path = str(dict_normalized.get("case_catalog_path", value_catalog or ""))  # 保存案例 manifest 来源路径

    # 返回案例目录和 authority 来源路径。
    return dict_catalog, str_catalog_path  # 返回归一化后的案例元数据

# 将完整、嵌套或扁平 remote authority 归一化为 canonical 映射。
def normalize_remote_validation_authority(
    dict_input: Mapping[str, Any],
    dict_bundled_authority: Mapping[str, Any],
) -> dict[str, Any]:
    """将完整、嵌套或扁平 remote authority 归一化为 canonical 映射。

    参数:
        dict_input: 调用方 settings、remote 或 validation 映射。
        dict_bundled_authority: bundled validation 默认配置。
    返回:
        可供远端命令生成使用的 canonical authority。
    """

    # 复制输入并合并 layout、artifact 和案例目录。
    dict_normalized = _input_mapping(dict_input)  # authority 工作副本

    # 先归一化远端布局，供后续路径字段复用。
    dict_normalized["layout"] = _layout(dict_normalized, dict_bundled_authority)  # 写回 canonical 远端路径布局

    # 再归一化 artifact 文件名，绑定 evidence 路径。
    dict_normalized["artifacts"] = _artifacts(dict_normalized, dict_bundled_authority)  # 写回 canonical evidence 工件映射

    # 最后归一化案例目录和来源路径。
    tuple_case_result = _case_catalog(dict_normalized, dict_bundled_authority)  # 读取 canonical 案例目录和来源路径

    # 拆出案例目录，供后续 shell fixture 名称复用。
    dict_case_catalog = tuple_case_result[0]  # 保存 canonical 案例目录供 fixture 循环

    # 拆出案例来源路径，供 evidence receipt 追踪。
    str_catalog_path = tuple_case_result[1]  # 保存 catalog 来源路径

    # 写回案例目录，保持 shell fixture 名称可追溯。
    dict_normalized["case_catalog"] = dict_case_catalog  # 写回 canonical 案例目录

    # 写回案例来源路径，保持 evidence receipt 可追溯。
    dict_normalized["case_catalog_path"] = str_catalog_path  # 写回案例 manifest 来源

    # bundled 运行字段只在调用方缺失时补齐。
    for str_key in (
        "workflow_module",
        "workflow_spec",
        "fixture_asset_root",
        "quality_gate_module",
        "testbench_prefix",
        "testbench_suffix",
        "vivado_settings_globs",
        "simulator_backends",
    ):

        # authority 显式字段优先于 bundled 默认。
        dict_normalized.setdefault(str_key, dict_bundled_authority[str_key])  # 补齐缺失运行字段

    # 归一化身份字段和环境变量。
    value_identity_field = (  # 选择 completion 身份字段
        dict_normalized.get("identity_field")  # authority 显式身份字段
        or dict_normalized.get("remote_identity_field")  # 兼容旧身份字段
        or "remote_identity"  # 通用身份字段兜底
    )  # 绑定 completion 的动态身份键

    # 选择 shell receipt 使用的身份环境变量。
    value_identity_env = (  # 选择 shell receipt 身份环境变量
        dict_normalized.get("identity_env")  # authority 显式环境变量
        or dict_normalized.get("remote_identity_env")  # 兼容旧环境变量
        or "REMOTE_IDENTITY"  # 通用环境变量兜底
    )  # 绑定远端身份传递名称

    # 读取本轮 authority 绑定的身份值。
    value_identity = dict_normalized.get("identity") or dict_normalized.get("remote_identity", "")  # 选择本轮身份值

    # 将身份信息写回 canonical authority。
    dict_normalized["remote_identity_field"] = str(value_identity_field)  # 写回 completion 身份字段

    # 写回 shell receipt 使用的环境变量名称。
    dict_normalized["remote_identity_env"] = str(value_identity_env)  # 写回远端身份环境变量

    # 写回本轮 retained run 的身份值。
    dict_normalized["remote_identity"] = str(value_identity)  # 写回本轮远端身份值

    # 返回完成字段补齐的 authority。
    return dict_normalized  # 返回 canonical authority

# 准备远端 bash 主体所需的命令片段。
def prepare_remote_validation_context(
    str_remote_skill: str,
    str_remote_python: str,
    dict_options: dict[str, Any],
    *,
    dict_bundled_authority: Mapping[str, Any],
    dict_helpers: Mapping[str, Callable[..., Any]],
) -> dict[str, Any]:
    """准备远端 bash 主体所需的命令片段。

    参数:
        str_remote_skill: 远端上传后的 skill 工作区路径。
        str_remote_python: 远端 Python 命令。
        dict_options: 兼容旧关键词的执行选项。
        dict_bundled_authority: bundled validation 默认配置。
        dict_helpers: 主模块注入的 quoting 和片段构造回调。
    返回:
        供 remote_validation_command 使用的上下文映射。
    """

    # 先拒绝历史 archive 上传选项。
    dict_helpers["ensure_manifest_only_options"](dict_options)

    # 选择显式 authority、完整 settings、remote 子树或 bundled 默认。
    value_authority = dict_options.get("validation_authority") or dict_options.get("settings")  # 选择显式或完整 settings authority

    # 没有显式值时兼容 remote 子树和历史键。
    value_authority = value_authority or dict_options.get("remote") or dict_options.get("remote_validation")  # 选择兼容 remote authority

    # 判断 options 是否以扁平字段表达 authority。
    bool_flat_authority = any(  # 记录扁平 authority 判定
        str_key in dict_options  # 检查单个扁平 authority 字段
        for str_key in ("remote_root", "workspace_root", "reports_root", "case_catalog")  # 遍历扁平字段名
    )  # 扁平 authority 字段判定

    # 扁平 authority 只在没有嵌套来源时生效。
    if value_authority is None and bool_flat_authority:

        # 扁平 options 本身就是 authority。
        value_authority = dict_options  # 使用扁平 options 作为 authority

    # 归一化 authority 和主段。
    dict_authority = normalize_remote_validation_authority(  # 归一化本轮 authority
        value_authority or dict_bundled_authority,  # 选择当前 authority 输入
        dict_bundled_authority,  # 提供缺省布局和案例
    )  # 生成 canonical authority

    # 提取远端相对路径布局。
    dict_layout = dict_authority["layout"]  # 读取远端相对路径布局供 shell 拼接

    # 提取 authority 案例目录。
    dict_catalog = dict_authority["case_catalog"]  # 读取 authority 案例目录供 fixture 循环

    # 读取调用方已确认的工具链选择。
    dict_toolchain = dict_options.get("toolchain_selection") or {}  # 读取工具链选择

    # 保存 shell quoting 回调，避免 helper 反向导入主模块。
    func_quote = dict_helpers["sh_quote"]  # 保存 shell quoting 回调

    # 读取选择值和路径值。
    str_selected_vivado = str(dict_toolchain.get("vivado_settings64") or "")  # 保存 Vivado 激活路径

    # 保存已确认的 simulator backend。
    str_selected_backend = str(dict_toolchain.get("simulator_backend") or "")  # 保存已确认的仿真后端名称

    # 记录 runtime 配置缺失时的诊断路径。
    str_runtime_config = dict_options.get("remote_runtime_config_path")  # runtime 配置提示路径

    # 记录本轮直接报告根。
    str_report_root = str(dict_options.get("report_root", dict_layout.get("reports_root", "")))  # 直接报告根

    # 记录 Agent review 相对路径。
    str_review_path = str(dict_options.get("agent_review_path", dict_layout["agent_review_relative"]))  # 保存 Agent review 证据路径

    # 拆分 execute authority 路径，供 completion 和 implement 证据复用。
    str_execute_root = str(dict_layout["execute_root"])  # 保存 execute 工件根用于拆分归档路径

    # 保存 execute 路径的 POSIX 分段。
    tuple_execute_parts = PurePosixPath(str_execute_root).parts  # execute 路径分段

    # 记录 execute 归档根和 attempt 目录。
    str_execute_archive = tuple_execute_parts[0]  # 保存 execute 归档根目录名称

    # 保存 attempt 目录名称，供 workflow 产物定位。
    str_execute_attempt = tuple_execute_parts[-1]  # 保存 execute attempt 目录名称

    # 记录 execute validation 文件，供远端完成标记读取 metrics。
    str_execute_validation = str(dict_layout["execute_validation"])  # 保存 execute 阶段 validation 证据文件

    # 将 precheck authority 序列化为稳定 JSON。
    str_precheck = json.dumps(dict_catalog.get("prechecks", {}), sort_keys=True)  # 序列化 authority precheck 规则 JSON

    # 用 base64 保护 precheck JSON 的 shell 传输边界。
    str_precheck_b64 = base64.b64encode(str_precheck.encode("utf-8")).decode("ascii")  # 编码 precheck 供 shell 环境变量传输

    # 生成独立 shell 片段。
    str_py = func_quote(str_remote_python)  # 生成 shell Python 命令

    # 生成三阶段 pytest 命令映射。
    dict_commands = dict_helpers["build_remote_pytest_commands"](str_py)  # 保存 targeted、regression、full 命令

    # 生成清理、工具链、质量门和阶段 runner 片段。
    bool_cleanup_outputs = bool(dict_options.get("cleanup_outputs"))  # 记录 smoke 输出清理策略

    # 生成清理 smoke 输出的远端片段。
    str_cleanup_snippet = dict_helpers["remote_output_cleanup_snippet"](bool_cleanup_outputs, str_remote_python)  # 保存 smoke cleanup 脚本

    # 生成 simulator backend 优先级导出片段。
    str_priority_snippet = dict_helpers["simulator_priority_export_snippet"](str_selected_backend)  # 保存后端优先级脚本

    # 生成与 authority 工具链选择一致的 Vivado 激活片段。
    str_vivado_snippet = dict_helpers["vivado_activation_snippet"](  # 保存 Vivado 激活脚本
        str_selected_vivado,  # 传入 authority 选择的 settings64 路径
        str_selected_backend,  # 传入 authority 选择的 simulator backend
        str_runtime_config,  # 传入 runtime 配置诊断路径
        validation_authority=dict_authority,  # 传入本轮完整 authority
    )  # 绑定 authority 工具链配置

    # 生成 RTL 文档约束回归片段。
    str_rtl_md_snippet = dict_helpers["rtl_md_constraint_remote_snippet"](str_remote_python)  # 保存 RTL 文档约束脚本

    # 生成文件名交付门禁回归片段。
    str_filename_gate_snippet = dict_helpers["filename_gate_remote_snippet"](  # 保存 authority 文件名门禁脚本
        str_remote_python,  # 传入远端 Python 命令
        validation_authority=dict_authority,  # 传入 authority 案例目录
    )  # 绑定 authority 案例目录

    # 生成 retained workspace 的 bytecode 清理片段。
    str_bytecode_cleanup = dict_helpers["remote_bytecode_cleanup_snippet"](str_remote_python)  # 保存 bytecode 清理脚本

    # 生成写入阶段摘要和退出码的 pytest runner。
    str_phase_runner = dict_helpers["build_remote_phase_runner"](str_py)  # 保存阶段摘要 runner 脚本

    # 将 shell 片段映射到父模块既有字段名，保持渲染接口稳定。
    dict_snippets = {
        "str_cleanup_snippet": str_cleanup_snippet,  # 父模块读取 smoke cleanup 片段
        "str_simulator_priority_snippet": str_priority_snippet,  # 父模块读取 simulator 优先级片段
        "str_vivado_snippet": str_vivado_snippet,  # 父模块读取 Vivado 激活片段
        "str_rtl_md_snippet": str_rtl_md_snippet,  # 父模块读取 RTL 文档约束片段
        "str_filename_gate_snippet": str_filename_gate_snippet,  # 父模块读取文件名门禁片段
        "str_bytecode_cleanup": str_bytecode_cleanup,  # 父模块读取 bytecode 清理片段
        "str_phase_runner": str_phase_runner,  # 父模块读取 pytest 阶段 runner
    }  # 完成父模块 shell 字段映射

    # 计算本轮动态身份，供 completion receipt 绑定。
    str_remote_identity = str(  # 计算 completion receipt 的远端身份文本
        dict_options.get(  # 读取调用方显式身份
            dict_authority["remote_identity_field"],  # authority 身份字段名
            dict_authority.get("remote_identity", ""),  # authority 默认身份值
        )  # 保存 canonical identity text
    )  # 记录远端身份值

    # 返回原有字段名，保持 remote_validation_command 的渲染接口。
    return {
        "str_remote_skill": str_remote_skill,
        "str_py": str_py,
        "str_report_root_quoted": func_quote(str_report_root),
        "str_agent_review_path_quoted": func_quote(str_review_path),
        "str_run_id": str(dict_options.get("run_id", "")),
        "str_source_digest": str(dict_options.get("source_digest", "")),
        "str_remote_identity": str_remote_identity,  # completion 绑定身份
        "str_identity_field": str(dict_authority["remote_identity_field"]),
        "str_identity_env": str(dict_authority["remote_identity_env"]),
        "str_remote_root": func_quote(str(dict_layout["remote_root"])),
        "str_workspace_root": func_quote(str(dict_layout["workspace_root"])),
        "str_reports_root": func_quote(str(dict_layout["reports_root"])),
        "str_case_catalog_path": func_quote(str(dict_authority.get("case_catalog_path", ""))),
        "str_completion_path": func_quote(str(dict_authority["artifacts"]["completion"])),
        "str_agent_review_file": func_quote(str(dict_authority["artifacts"]["agent_review"])),
        "str_pytest_summary_path": func_quote(str(dict_authority["artifacts"]["pytest_summary"])),
        "str_workflow_module": str(dict_authority["workflow_module"]),
        "str_workflow_module_quoted": _shell_module_text(
            str(dict_authority["workflow_module"]), func_quote
        ),
        "str_targeted_pytest_command": dict_commands["targeted"],
        "str_regression_pytest_command": dict_commands["regression"],
        "str_full_pytest_command": dict_commands["full"],
        "str_fixture_names": " ".join(dict_catalog["simulation"]),
        "str_workflow_spec": func_quote(str(dict_authority["workflow_spec"])),
        "str_fixture_asset_root": func_quote(str(dict_authority["fixture_asset_root"])),
        "str_quality_gate_module": func_quote(str(dict_authority["quality_gate_module"])),
        "str_fixture_scratch_root": func_quote(str(dict_layout["fixture_scratch_root"])),
        "str_execute_validation": str_execute_validation,
        "str_execute_archive_root": str_execute_archive,
        "str_execute_attempt": str_execute_attempt,
        "str_implement_archive_root": str_execute_archive.replace("execute", "implement"),
        "str_implement_validation": str_execute_validation.replace("execute", "implement"),
        "str_fixture_archive_root": str(dict_layout["fixture_root"]),
        "str_testbench_prefix": func_quote(str(dict_authority["testbench_prefix"])),
        "str_testbench_suffix": func_quote(str(dict_authority["testbench_suffix"])),
        "str_precheck_manifest_b64": func_quote(str_precheck_b64),
        **dict_snippets,
    }
