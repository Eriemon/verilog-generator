"""管理 FPGA developer 路由与旧 FPGA-Agent 清理流程。"""

# future annotations 避免运行期解析复杂类型标注。
from __future__ import annotations

# 标准库负责时间戳、路径类型和 FPGA 路由状态的结构化处理。
import time
from pathlib import Path
from typing import Any

# dependency_state 子模块提供状态读写、skill 发现和路由配置解析 helper。
from scripts.python.toolchain.dependency_state import (
    default_plugin_cache,
    default_skills_root,
    find_skill,
    read_state,
    write_state,
)

# FPGA 路由还需要读取 vendor 映射和统一时间戳 helper。
from scripts.python.toolchain.dependency_state import (
    read_fpga_developer_routing_settings,
    read_skill_dependency_settings,
    utc_now,
)

# TUPLE_FPGA_AGENT_CHILD_SKILLS 记录 cleanup 需要迁移的历史 FPGA-Agent 子技能目录名。
TUPLE_FPGA_AGENT_CHILD_SKILLS = (
    "vivado-tcl",  # 旧 Vivado Tcl 子技能目录
    "vivado-sim",  # 旧 Vivado 仿真子技能目录
    "vivado-synth",  # 旧 Vivado 综合子技能目录
    "vivado-impl",  # 旧 Vivado 实现子技能目录
    "vivado-analysis",  # 旧 Vivado 分析子技能目录
    "vivado-constraints",  # 旧 Vivado 约束子技能目录
    "vivado-debug",  # 旧 Vivado 调试子技能目录
    "vitis-hls-synthesis",  # 旧 FPGA-Agent 历史路由里承接 Vitis HLS 综合流程的目录名
)

# FPGA_AGENT_CHILD_SKILLS 保留旧公开常量名，兼容历史读取方。
FPGA_AGENT_CHILD_SKILLS = TUPLE_FPGA_AGENT_CHILD_SKILLS  # 兼容旧调用方的 FPGA-Agent 子技能集合

# fpga_developer_status 汇总 vendor developer skill 的可用性和选择状态。
def fpga_developer_status(
    settings: dict,
    *,
    skills_root: Path | None = None,
    plugin_cache: Path | None = None,
    state_path: Path | None = None,
) -> dict:
    """汇总 FPGA developer skill 的 vendor 可用性和用户选择状态。

    :param settings: 已加载的 defaults.json 配置。
    :param skills_root: 可选 Codex skills 根目录。
    :param plugin_cache: 可选 Codex plugin cache 根目录。
    :param state_path: 可选依赖状态文件路径。
    :return: 返回 vendor、available_vendors、selection_required 等路由字段；数组 shape/dtype/unit 不适用。
    """

    # routing 包含 vendor 到 skill id 的映射。
    dict_routing = read_fpga_developer_routing_settings(settings)  # FPGA developer 路由配置

    # 该根目录决定每个厂商开发技能是否被视为本地已安装。
    path_skills_root = (skills_root or default_skills_root()).expanduser()  # 厂商开发技能可用性判定的本地搜索根

    # plugin_cache 用于查找插件内 developer skill。
    path_plugin_cache = (plugin_cache or default_plugin_cache()).expanduser()  # 插件随附 vendor skill 搜索根

    # state_path 用于读取用户选择的 vendor。
    path_state = (state_path or dict_routing["state_path"]).expanduser()  # vendor 选择状态读取路径

    # state 中可能保存 fpga_developer_selection。
    dict_state = read_state(path_state)  # 包含已保存 vendor 选择的依赖状态

    # selected 只在结构为 dict 时有效。
    dict_selected = _fpga_selection_from_state(dict_state)  # 已保存的 FPGA vendor 选择

    # vendors 保存每个 vendor 的安装状态。
    dict_vendors: dict[str, dict[str, Any]] = {}  # vendor 到状态报告的映射

    # available_vendors 按配置顺序记录可用 vendor。
    list_available_vendors: list[str] = []  # 已安装 developer skill 的 vendor id 列表

    # vendor 循环按配置顺序构造可用厂商清单。
    for str_vendor_id, dict_vendor in dict_routing["vendors"].items():

        # skill_paths 保存该 vendor 已发现的 skill 路径。
        dict_skill_paths: dict[str, str] = {}  # 当前 vendor 已安装 skill 路径

        # 一个 vendor 可配置多个等价 developer skill。
        for str_skill in dict_vendor["skills"]:

            # find_skill 同时查找 skills_root 和 plugin_cache。
            path_found = find_skill(str_skill, path_skills_root, path_plugin_cache)  # developer skill 发现路径

            # 找到即记录路径。
            if path_found:

                # skill path 以字符串形式进入 JSON 报告。
                dict_skill_paths[str_skill] = str(path_found)  # 记录该 vendor skill 的发现路径

        # selected_skill 取该 vendor 中第一个已安装 skill。
        str_selected_skill = next(  # 当前 vendor 选中的 developer skill
            (str_skill for str_skill in dict_vendor["skills"] if str_skill in dict_skill_paths),  # 按配置优先级选择 skill
            None,  # 当前 vendor 没有任何 developer skill
        )

        # present 表示该 vendor 至少一个 developer skill 可用。
        bool_present = str_selected_skill is not None  # 当前 vendor 是否可用

        # 可用 vendor 进入选择候选。
        if bool_present:

            # 保持配置顺序，便于稳定提示。
            list_available_vendors.append(str_vendor_id)

        # vendor 状态保留安装证据、首选 skill 和路径映射，供 route 决定是否 ready。
        dict_vendors[str_vendor_id] = {  # 决定厂商是否可选以及最终调用哪个开发技能
            "label": dict_vendor["label"],  # 提示用户选择厂商时展示的名称
            "skills": dict_vendor["skills"],  # 按优先级寻找开发技能的候选名称
            "present": bool_present,  # 该厂商至少发现一个开发技能时为真
            "selected_skill": str_selected_skill,  # 当前厂商实际路由到的开发技能名称
            "skill_paths": dict_skill_paths,  # 已发现开发技能到安装目录文本的映射
        }

    # selected_vendor 只有 state 中结构正确时读取。
    str_selected_vendor = dict_selected.get("vendor") if isinstance(dict_selected, dict) else None  # 状态文件保存的 vendor id

    # selection_valid 要求保存的 vendor 当前仍然可用。
    bool_selection_valid = bool(str_selected_vendor in list_available_vendors)  # 已保存 vendor 是否仍有效

    # selection_stale 表示用户选过 vendor，但当前环境不再可用。
    bool_selection_stale = bool(str_selected_vendor and not bool_selection_valid)  # 已保存选择是否失效

    # 多个 vendor 可用且没有有效选择时需要用户选择。
    bool_selection_required = len(list_available_vendors) > 1 and not bool_selection_valid  # 是否需要用户选择 vendor

    # 返回 FPGA developer 路由状态。
    return {
        "state_path": str(path_state),  # 状态文件路径
        "available_vendors": list_available_vendors,  # 可用 vendor 列表
        "vendors": dict_vendors,  # vendor 状态映射
        "selected_vendor": str_selected_vendor if bool_selection_valid else None,  # 当前有效 vendor
        "selection_required": bool_selection_required,  # 多 vendor 可用但未选择时为 True
        "selection_stale": bool_selection_stale,  # 已保存 vendor 当前不可用时为 True
        "fpga_agent_required_when_developer_present": dict_routing["fpga_agent_required_when_developer_present"],  # fallback 策略
    }

# select_fpga_vendor 持久化用户选择的 FPGA vendor。
def select_fpga_vendor(
    settings: dict,
    vendor_id: str,
    *,
    skills_root: Path | None = None,
    plugin_cache: Path | None = None,
    state_path: Path | None = None,
) -> dict:
    """保存用户选择的 FPGA developer vendor。

    :param settings: 已加载的 defaults.json 配置。
    :param vendor_id: 用户选择的 vendor id。
    :param skills_root: 可选 Codex skills 根目录。
    :param plugin_cache: 可选 Codex plugin cache 根目录。
    :param state_path: 可选依赖状态文件路径。
    :return: 返回 selected_vendor、selected_skill 和 state_path。
    :raises ValueError: vendor 未知或当前未安装对应 developer skill 时抛出。
    数组合同：本函数只更新 JSON 状态字段；shape、dtype 和 unit 不适用。
    """

    # 先读取当前 vendor 可用性，避免写入不可用选择。
    dict_status = fpga_developer_status(  # 写入选择前的 vendor 可用性报告
        settings,  # 当前配置用于解析 vendor 到 skill 的映射
        skills_root=skills_root,  # 可选覆盖的 skills 搜索根
        plugin_cache=plugin_cache,  # 可选覆盖的插件缓存根
        state_path=state_path,  # 可选覆盖的状态文件
    )

    # vendor 状态从报告中按 id 提取。
    dict_vendor = dict_status["vendors"].get(vendor_id)  # 用户指定 vendor 状态

    # 未知 vendor 不能写入状态。
    if not dict_vendor:

        # 明确报告未知 vendor id。
        raise ValueError(f"> ERR: [Python] Unknown FPGA vendor: {vendor_id}")

    # 未安装 developer skill 的 vendor 不能作为选择。
    if not dict_vendor["present"]:

        # 明确报告该 vendor 当前不可用。
        raise ValueError(f"> ERR: [Python] FPGA vendor {vendor_id} has no installed developer skill.")

    # state_path 显式参数优先，否则使用状态报告里的路径。
    path_state = Path(state_path or dict_status["state_path"]).expanduser()  # vendor 选择写入目标状态文件

    # 读取原状态以保留其他分区。
    dict_state = read_state(path_state)  # 保留 skip/adaptation 分区的原状态

    # fpga_developer_selection 保存 vendor 和实际 skill。
    dict_state["fpga_developer_selection"] = {
        "vendor": vendor_id,  # 用户选择的 vendor id
        "skill": dict_vendor["selected_skill"],  # 当前 vendor 对应的 developer skill
        "updated_at": utc_now(),  # vendor 选择更新时间
    }

    # vendor 选择写回前补齐状态 schema。
    dict_state.setdefault("version", 1)

    # 总状态更新时间同步刷新。
    dict_state["updated_at"] = utc_now()  # vendor 选择后的总状态更新时间

    # 写回用户选择。
    write_state(path_state, dict_state)

    # 返回选择结果供 CLI 输出或测试断言。
    return {
        "selected_vendor": vendor_id,  # 已保存 vendor id
        "selected_skill": dict_vendor["selected_skill"],  # 已保存 vendor 对应的 developer skill
        "state_path": str(path_state),  # 本次写入的状态文件路径
    }

# fpga_route 报告当前 FPGA workflow 应使用的 developer skill 路由。
def fpga_route(
    settings: dict,
    *,
    skills_root: Path | None = None,
    plugin_cache: Path | None = None,
    state_path: Path | None = None,
) -> dict:
    """报告当前 FPGA workflow 应使用的 developer skill 或 fallback 状态。

    :param settings: 已加载的 defaults.json 配置。
    :param skills_root: 可选 Codex skills 根目录。
    :param plugin_cache: 可选 Codex plugin cache 根目录。
    :param state_path: 可选依赖状态文件路径。
    :return: 返回 ready、selection_required、selection_stale 或 manual_fallback_available 状态；数组 shape/dtype/unit 不适用。
    """

    # 读取当前 developer skill 安装和选择状态。
    dict_status = fpga_developer_status(  # 当前 FPGA developer 安装和选择状态
        settings,  # 当前配置用于解析 developer vendor
        skills_root=skills_root,  # 路由查询使用的 skills 根
        plugin_cache=plugin_cache,  # route 查找插件内 developer skill
        state_path=state_path,  # 路由查询读取的状态文件
    )

    # available_vendors 是可路由的 vendor 候选。
    list_available = dict_status["available_vendors"]  # 可直接路由的 vendor id 列表

    # 没有 developer skill 时回落到手动 FPGA-Agent fallback。
    if not list_available:

        # 依赖配置中保存 FPGA-Agent fallback 的完整 skill 列表。
        dict_dependency_settings = read_skill_dependency_settings(settings)  # FPGA-Agent fallback 依赖配置

        # fpga-agent-skills 是唯一 FPGA 手动 fallback 聚合依赖。
        dict_fpga = next(  # 手动 fallback 的 FPGA-Agent 聚合依赖配置
            (
                dict_item  # manual_fallback 候选依赖条目
                for dict_item in dict_dependency_settings.get("manual_fallback", [])  # 遍历手动 fallback 依赖配置
                if dict_item["id"] == "fpga-agent-skills"  # 只取旧 FPGA-Agent 聚合依赖
            ),  # manual_fallback 中的 FPGA-Agent 配置项
        )

        # 返回手动 fallback 可用但需要用户批准的状态。
        return {
            "status": "manual_fallback_available",  # 没有 developer skill 时的路由状态
            "fallback_skills": dict_fpga["skills"],  # fallback 需要的 skill 列表
            "requires_explicit_approval": True,  # fallback 必须显式批准
        }

    # 已保存 vendor 失效时要求用户重新选择。
    if dict_status["selection_stale"]:

        # stale 状态只报告当前可用 vendor。
        return {"status": "selection_stale", "available_vendors": list_available}

    # 多 vendor 可用且未选择时要求用户选择。
    if dict_status["selection_required"]:

        # selection_required 状态只报告候选 vendor。
        return {"status": "selection_required", "available_vendors": list_available}

    # 单 vendor 可用时可自动选择，否则使用已保存 vendor。
    str_selected_vendor = dict_status["selected_vendor"] or list_available[0]  # 当前生效 vendor id

    # vendor 状态中包含 selected_skill 和 skill_paths。
    dict_vendor = dict_status["vendors"][str_selected_vendor]  # 当前生效 vendor 状态

    # selected_skill 是实际路由目标。
    str_selected_skill = dict_vendor["selected_skill"]  # 实际交给 workflow 使用的 developer skill id

    # ready 状态返回可直接使用的 skill 路径。
    return {
        "status": "ready",  # developer skill 可直接使用的路由状态
        "selected_vendor": str_selected_vendor,  # 当前 vendor id
        "selected_skill": str_selected_skill,  # workflow 应调用的 developer skill id
        "skill_path": dict_vendor["skill_paths"][str_selected_skill],  # developer skill 安装路径
    }

# cleanup_fpga_agent_skills 在 developer skill 可用后迁移旧 FPGA-Agent 子目录。
def cleanup_fpga_agent_skills(
    settings: dict,
    *,
    skills_root: Path | None = None,
    plugin_cache: Path | None = None,
    backup_root: Path | None = None,
    yes: bool = False,
) -> dict:
    """在 developer skill 可用后迁移旧 FPGA-Agent 子技能目录。

    :param settings: 已加载的 defaults.json 配置。
    :param skills_root: 可选 Codex skills 根目录。
    :param plugin_cache: 可选 Codex plugin cache 根目录。
    :param backup_root: 可选备份根目录。
    :param yes: 是否确认执行移动操作。
    :return: 返回 moved、backup_dir、skills_root、plugin_cache 和 developer_vendors。
    :raises ValueError: 缺少确认、没有 developer skill 或路径安全检查失败时抛出。
    """

    # cleanup 会移动目录，必须由调用方显式确认。
    if not yes:

        # 缺少确认时立即阻塞。
        raise ValueError("> ERR: [Python] cleanup-fpga-agent-skills requires --yes.")

    # skills_root 优先使用调用方覆盖。
    path_skills_root = (skills_root or default_skills_root()).expanduser()  # 旧 FPGA-Agent 子目录所在根

    # plugin_cache 传给 developer 状态检查。
    path_plugin_cache = (plugin_cache or default_plugin_cache()).expanduser()  # developer 替代 skill 的插件搜索根

    # backup_root 默认放在 skills 根目录同级 skill-backups。
    path_backup_root = (backup_root or (default_skills_root().parent / "skill-backups")).expanduser()  # 备份根目录

    # 只有 developer skill 可用时才迁移旧 FPGA-Agent 子技能。
    dict_developer_status = fpga_developer_status(  # 清理前确认 developer skill 已可替代旧集合
        settings,  # 当前配置中的 vendor 路由策略
        skills_root=path_skills_root,  # 本地 developer skill 搜索根
        plugin_cache=path_plugin_cache,  # 插件 developer skill 搜索根
    )

    # 没有 developer vendor 时拒绝清理。
    if not dict_developer_status["available_vendors"]:

        # 阻止用户在无替代 skill 时移走 FPGA-Agent。
        raise ValueError("> ERR: [Python] Refusing cleanup because no FPGA developer skill is installed.")

    # 解析 skills 根目录用于 relative_to 安全检查。
    path_resolved_skills_root = path_skills_root.resolve()  # 规范化 skills 根目录

    # 解析备份根目录用于目标边界检查。
    path_resolved_backup_root = path_backup_root.resolve()  # 规范化备份根目录

    # 备份目录带时间戳，避免覆盖历史迁移。
    path_backup_dir = path_backup_root / f"fpga-agent-skills.bak.{time.strftime('%Y%m%dT%H%M%S')}"  # 本轮备份目录

    # moved 记录实际迁移的 FPGA-Agent skill。
    list_moved: list[str] = []  # 已移动 FPGA-Agent skill 列表

    # 遍历旧 FPGA-Agent skill 集合。
    for str_skill in TUPLE_FPGA_AGENT_CHILD_SKILLS:

        # source 是可能存在的旧子 skill 目录。
        path_source = path_skills_root / str_skill  # 旧子 skill 源目录

        # 源目录不存在时无需移动。
        if not path_source.exists():

            # 当前子 skill 未安装，继续检查下一个。
            continue

        # 源路径必须仍在 skills_root 内。
        path_source.resolve().relative_to(path_resolved_skills_root)

        # 缺少 SKILL.md 的目录不按 skill 迁移。
        if not (path_source / "SKILL.md").is_file():

            # 阻止误移动非 skill 目录。
            raise ValueError(
                f"> ERR: [Python] Refusing to move unexpected skill directory without SKILL.md: {path_source}"
            )

        # 需要移动时才创建备份目录。
        path_backup_dir.mkdir(parents=True, exist_ok=True)

        # target 是备份目录中的同名 skill 目录。
        path_target = path_backup_dir / str_skill  # skill 备份目标目录

        # 目标路径必须位于备份根目录内。
        path_target.parent.resolve().relative_to(path_resolved_backup_root)

        # 目标已存在时拒绝覆盖历史备份。
        if path_target.exists():

            # 阻止覆盖已存在的备份目录。
            raise ValueError(f"> ERR: [Python] Backup target already exists: {path_target}")

        # 使用 rename 保持同一文件系统内的快速移动。
        path_source.rename(path_target)

        # 记录已移动的 skill id。
        list_moved.append(str_skill)

    # 返回清理摘要。
    return {
        "moved": list_moved,  # 实际移动的子 skill id
        "backup_dir": str(path_backup_dir),  # 备份目录路径
        "skills_root": str(path_skills_root),  # 旧 FPGA-Agent skill 来源根
        "plugin_cache": str(path_plugin_cache),  # 本次确认 developer skill 的插件缓存
        "developer_vendors": dict_developer_status["available_vendors"],  # 允许清理的已安装 vendor
    }

# _fpga_selection_from_state 从状态文件提取已保存的 vendor 选择。
def _fpga_selection_from_state(state: dict) -> dict:
    """从依赖状态中提取 FPGA developer 选择。

    :param state: read_state 返回的依赖状态字典。
    :return: 返回 selection 字典；字段不存在或类型不对时返回空字典。
    """

    # selection 旧状态中可能不存在或被用户手动写错。
    dict_selection = state.get("fpga_developer_selection", {})  # 原始 FPGA 选择字段

    # 只有 dict 结构可继续作为选择状态。
    if isinstance(dict_selection, dict):

        # 返回结构正确的 selection。
        return dict_selection

    # 非 dict selection 视为无选择。
    return {}
