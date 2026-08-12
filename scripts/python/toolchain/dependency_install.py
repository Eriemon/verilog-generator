"""管理依赖适配和缺失 skill 安装流程。"""

# future annotations 避免运行期解析复杂类型标注。
from __future__ import annotations

# 标准库负责安装执行、解释器上下文和路径类型声明。
import subprocess
import sys
from pathlib import Path
from typing import Any

# dependency_state 子模块提供安装前依赖报告和状态读写 helper。
from scripts.python.toolchain.dependency_state import (
    _selected_install_specs,
    check_dependencies,
    read_skill_dependency_settings,
    read_state,
    write_state,
)

# 安装命令还需要目录定位、仓库 slug 和时间戳 helper。
from scripts.python.toolchain.dependency_state import (
    default_installer_script,
    default_plugin_cache,
    default_skills_root,
    github_repo_slug,
    utc_now,
)

# resolve_remote_helper_path 兼容当前与历史 erie-remote-ssh 安装布局。
def resolve_remote_helper_path(path_skill: Path) -> Path | None:
    """返回已安装 erie-remote-ssh 的可用 CLI 入口。

    参数：path_skill 为 erie-remote-ssh 技能根目录。
    返回：优先返回当前 runtime 入口；仅旧副本存在时返回历史入口；均缺失时返回 None。
    """

    # 当前布局优先，历史布局只用于兼容尚未升级的安装副本。
    tuple_candidates = (  # 当前和历史 helper 候选路径
        path_skill / "scripts" / "python" / "runtime" / "remote_ssh.py",  # 当前 runtime 入口
        path_skill / "scripts" / "remote_ssh.py",  # 历史兼容入口
    )

    # 返回首个真实文件，稳定保持当前入口优先级。
    return next((path_candidate for path_candidate in tuple_candidates if path_candidate.is_file()), None)

# adapt_dependencies 把已安装依赖的 helper 路径写入项目本地状态。
def adapt_dependencies(
    settings: dict,
    *,
    skills_root: Path | None = None,
    plugin_cache: Path | None = None,
    state_path: Path | None = None,
) -> dict:
    """发现已安装依赖的 helper 路径并写入状态文件。

    :param settings: 已加载的 defaults.json 配置。
    :param skills_root: 可选 Codex skills 根目录。
    :param plugin_cache: 可选 Codex plugin cache 根目录。
    :param state_path: 可选依赖状态文件路径。
    :return: 返回 adapted、blocked 和 state_path 字段；数组 shape/dtype/unit 不适用。
    """

    # defaults.json 中的依赖配置提供状态路径。
    dict_dependency_settings = read_skill_dependency_settings(settings)  # 依赖适配状态配置

    # skills_root 用于定位已安装 remote-ssh skill。
    path_skills_root = (skills_root or default_skills_root()).expanduser()  # remote helper 搜索使用的 skills 根

    # plugin_cache 用于定位插件内依赖。
    path_plugin_cache = (plugin_cache or default_plugin_cache()).expanduser()  # remote helper 搜索使用的插件缓存

    # state_path 用于保存适配结果。
    path_state = (state_path or dict_dependency_settings["state_path"]).expanduser()  # remote 适配结果写入文件

    # 先复用 check_dependencies 判断 required 是否满足。
    dict_report = check_dependencies(  # 适配前确认 required 依赖是否完整
        settings,  # 当前 defaults 配置提供 required 依赖清单
        skills_root=path_skills_root,  # 复查 remote-ssh skill 的 skills 根
        plugin_cache=path_plugin_cache,  # 复查 remote-ssh skill 的插件缓存
        state_path=path_state,  # 复查时读取相同状态文件
    )

    # required 缺失时不写入半适配状态。
    if dict_report["missing_required"]:

        # blocked 只返回缺失 required 的依赖 id。
        list_blocked = [dict_item["id"] for dict_item in dict_report["missing_required"]]  # 阻塞适配的 required 依赖

        # 返回阻塞信息，供 CLI JSON 输出。
        return {"adapted": [], "blocked": list_blocked, "state_path": str(path_state)}

    # 读取状态文件并准备 adaptations 分区。
    dict_state = read_state(path_state)  # 将要合并 adaptations 的现有状态

    # adaptations 保存依赖 helper 的绝对路径。
    dict_adaptations = dict_state.setdefault("adaptations", {})  # 依赖 helper 适配信息

    # adapted 记录本轮成功写入的依赖 id。
    list_adapted: list[str] = []  # 本轮完成适配的依赖 id

    # remote_status 决定 adapt 是否能写入 remote_ssh.py 和 defaults.json 的绝对路径。
    dict_remote_status = next(  # adaptations.remote 的 helper/settings 路径来源
        (dict_item for dict_item in dict_report["required"] if dict_item["id"] == "erie-remote-ssh"),  # 可提供远程 helper 的依赖状态候选
        None,  # 报告缺少远程依赖项时保持未适配状态
    )

    # remote-ssh present 时才写入 helper 和 settings 路径。
    if dict_remote_status and dict_remote_status.get("present"):

        # skill_paths 中保存 erie-remote-ssh 的真实安装目录。
        path_skill = Path(dict_remote_status["skill_paths"]["erie-remote-ssh"])  # 已安装 erie-remote-ssh skill 目录

        # helper 解析同时支持当前 runtime 入口和历史安装副本。
        path_helper = resolve_remote_helper_path(path_skill)  # remote-ssh helper 脚本路径

        # 兼容 config/defaults.json 和 assets/defaults.json 两种布局。
        list_remote_settings_candidates = [  # remote-ssh settings 候选路径
            path_skill / "config" / "defaults.json",  # 新版 remote-ssh 默认配置位置
            path_skill / "assets" / "defaults.json",  # 旧版 remote-ssh 默认配置位置
        ]

        # 选择第一个存在的 remote settings 文件。
        path_remote_settings = next(  # remote-ssh settings 实际路径
            (path_candidate for path_candidate in list_remote_settings_candidates if path_candidate.is_file()),  # 首个存在的 defaults 候选
            None,  # 两种布局都缺失时保持阻塞
        )

        # helper 和 settings 同时存在时才能适配 remote。
        if path_helper is not None and path_remote_settings is not None:

            # remote adaptation 写入绝对路径，避免后续工作目录变化影响调用。
            dict_adaptations["remote"] = {
                "helper": str(path_helper.resolve()),  # remote_ssh.py 绝对路径
                "settings": str(path_remote_settings.resolve()),  # remote 默认配置的绝对路径
            }

            # 记录 remote 依赖已适配。
            list_adapted.append("erie-remote-ssh")

        # 已安装但布局不完整时必须阻塞适配。
        else:

            # 返回缺失 helper 或 settings 的明确原因。
            return {
                "adapted": [],  # 未写入任何适配项
                "blocked": ["erie-remote-ssh"],  # 因 remote-ssh 布局不完整而阻塞
                "reason": (
                    "Installed erie-remote-ssh is missing a supported remote_ssh.py entrypoint "
                    "or a supported defaults.json under config/ or assets."
                ),  # 布局缺失原因
                "state_path": str(path_state),  # 本次读取的状态路径
            }

    # 适配写回时补齐状态 schema，兼容旧状态文件。
    dict_state.setdefault("version", 1)

    # 记录适配写入时间。
    dict_state["updated_at"] = utc_now()  # adaptations 最近写入时间

    # 远程 helper 路径确认后写回同一个状态文件。
    write_state(path_state, dict_state)

    # 返回适配摘要。
    return {"adapted": list_adapted, "blocked": [], "state_path": str(path_state)}

# install_missing 根据缺失报告调用 skill-installer 安装依赖。
def install_missing(
    settings: dict,
    report: dict,
    dependency_id: str | None = None,
    *,
    installer: Path | None = None,
    allow_fpga_agent_fallback: bool = False,
) -> dict:
    """安装报告中缺失的依赖 skill。

    :param settings: 已加载的 defaults.json 配置。
    :param report: check_dependencies 生成的依赖报告。
    :param dependency_id: 可选依赖 id；为空时安装全部缺失 required/recommended。
    :param installer: 可选 skill-installer helper 路径。
    :param allow_fpga_agent_fallback: 是否允许安装 FPGA-Agent 手动 fallback。
    :return: 返回 installed、skipped 和 restart_required 等安装摘要。
    :raises FileNotFoundError: installer helper 不存在时抛出。
    :raises ValueError: 依赖缺少 install spec 时抛出。
    """

    # 依赖配置提供安装 spec 和 fallback 集合。
    dict_dependency_settings = read_skill_dependency_settings(settings)  # 安装命令依赖配置

    # dependencies 建立 id 到依赖配置的索引。
    dict_dependencies = {  # 全部可安装依赖配置索引
        dict_item["id"]: dict_item  # 依赖 id 到 defaults 条目的映射
        for dict_item in [  # 合并 required、recommended 和 fallback 配置
            *dict_dependency_settings["required"],  # required 依赖配置项
            *dict_dependency_settings["recommended"],  # 可由用户跳过的推荐依赖配置
            *dict_dependency_settings.get("manual_fallback", []),  # 显式允许的 fallback 配置项
        ]
    }

    # missing 默认包含 required 和 recommended 缺失项。
    list_missing = [  # 默认参与安装的缺失依赖状态
        *report.get("missing_required", []),  # 必须安装的缺失依赖
        *report.get("missing_recommended", []),  # 用户未跳过的推荐依赖
    ]

    # 显式请求 FPGA-Agent 时才纳入 manual_fallback 缺失项。
    if dependency_id == "fpga-agent-skills":

        # 只追加当前未 present 的 fallback 状态。
        list_missing.extend(
            [
                dict_item  # 尚未安装的 FPGA-Agent fallback 状态
                for dict_item in report.get("manual_fallback", [])
                if not dict_item.get("present")
            ]
        )

    # dependency_id 非空时只安装指定依赖。
    if dependency_id:

        # 保留指定 id 的缺失项。
        list_missing = [dict_item for dict_item in list_missing if dict_item["id"] == dependency_id]  # 指定依赖 id 的缺失项

    # 没有选中的缺失项时返回 no-op 摘要。
    if not list_missing:

        # developer skill 覆盖 FPGA-Agent 时明确说明跳过原因。
        if dependency_id == "fpga-agent-skills" and report.get("fpga_agent_skipped_by_developer_skill"):

            # 返回旧字段合同中的 skipped 和 restart_required。
            return {
                "installed": [],  # 未安装任何 skill
                "skipped": [{"dependency_id": "fpga-agent-skills", "reason": "developer skill is installed"}],  # developer skill 覆盖原因
                "restart_required": False,  # 无安装动作不需要重启
            }

        # 普通 no-op 保持旧 message。
        return {"installed": [], "message": "No missing dependencies selected."}

    # installer 默认指向系统 skill-installer helper。
    path_installer = installer or default_installer_script()  # 实际调用的 skill-installer helper

    # installer 缺失时不能继续安装。
    if not path_installer.is_file():

        # 明确报告 installer 路径缺失。
        raise FileNotFoundError(f"> ERR: [Python] Missing skill installer helper: {path_installer}")

    # installed 记录本轮成功安装的 skill id。
    list_installed: list[str] = []  # 已安装 skill id 列表

    # skipped 记录未安装但被策略跳过的依赖。
    list_skipped: list[dict[str, str]] = []  # 安装跳过原因列表

    # 逐个缺失依赖执行安装或策略跳过。
    for dict_status in list_missing:

        # developer skill 存在时不再安装 FPGA-Agent fallback。
        if dict_status["id"] == "fpga-agent-skills" and report.get("fpga_agent_skipped_by_developer_skill"):

            # 记录 developer skill 覆盖原因。
            list_skipped.append({"dependency_id": "fpga-agent-skills", "reason": "developer skill is installed"})

            # 当前依赖已处理，继续下一个缺失项。
            continue

        # FPGA-Agent fallback 需要额外显式批准。
        if dict_status["id"] == "fpga-agent-skills" and not allow_fpga_agent_fallback:

            # 记录缺少 fallback 批准。
            list_skipped.append({"dependency_id": "fpga-agent-skills", "reason": "manual fallback approval required"})

            # 缺少 fallback 批准时跳过当前依赖，继续处理其他缺失项。
            continue

        # dependency 配置提供 GitHub URL 和 install_specs。
        dict_dependency = dict_dependencies[dict_status["id"]]  # 待安装依赖配置

        # repo slug 是 installer 接收的 owner/repo 形式。
        str_repo = github_repo_slug(dict_dependency["url"])  # 传给 installer --repo 的仓库 slug

        # selected_specs 只包含缺失 skill 对应的安装规格。
        list_selected_specs = _selected_install_specs(dict_dependency, dict_status["missing_skills"])  # 本依赖需要安装的 spec 列表

        # 按 spec 调用 installer helper。
        for dict_spec in list_selected_specs:

            # installer 命令把依赖 URL 和 source_path 转换为 skill-installer CLI 参数。
            list_command = [  # 安装器启动时使用的解释器、仓库和来源目录清单
                sys.executable,  # 保持安装器使用当前 Python 解释器运行
                str(path_installer),  # 实际执行的 skill-installer 脚本路径
                "--repo",  # 后续字符串解释为仓库标识的选项名
                str_repo,  # 从依赖 URL 解析出的 GitHub 仓库标识
                "--path",  # 后续字符串解释为仓库子目录的选项名
                str(dict_spec["source_path"]),  # 依赖 skill 在仓库中的来源子目录
            ]

            # dest_name 存在时通过 --name 覆盖安装名。
            if dict_spec.get("dest_name"):

                # installer 的 --name 用于聚合仓库中的子 skill。
                list_command.extend(["--name", str(dict_spec["dest_name"])])

            # 调用 installer，失败时让 CalledProcessError 直接暴露。
            subprocess.run(list_command, check=True)

            # 成功后记录 skill 名称。
            list_installed.append(str(dict_spec["skill"]))

    # 返回安装摘要，restart_required 只在实际安装后为真。
    return {"installed": list_installed, "skipped": list_skipped, "restart_required": bool(list_installed)}
