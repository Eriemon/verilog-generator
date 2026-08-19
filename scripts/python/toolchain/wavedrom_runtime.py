"""管理固定版本 WaveDrom CLI 的检查、安装和 SVG 渲染。

本模块只负责可观测的命令调用和原子 SVG 发布；导入时不探测环境，也不执行命令。
命令行标准输出是单行 JSON，错误输出统一使用 ``> ERR: [Python]`` 前缀。
本模块声明 machine-readable stdout protocol，供自动化调用方解析 JSON 结果。
"""

# 延迟解析类型标注，避免运行期为注解导入额外依赖。
from __future__ import annotations

# 标准库负责参数解析、命令调用、JSON 处理和临时文件管理。
import argparse
import json
import re

# 进程和临时文件工具负责外部命令边界与原子发布。
import shutil
import subprocess
import sys
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from xml.etree import ElementTree

# 固定包名，确保依赖检查和安装命令使用同一标识。
WAVEDROM_PACKAGE = "wavedrom"  # WaveDrom npm 包的规范名称

# 固定版本，避免图形渲染结果因全局升级而漂移。
WAVEDROM_VERSION = "3.6.1"  # 规范文档允许的 WaveDrom 版本

# 视口左侧安全区，抵消不同 SVG 查看器替代字体造成的信号名宽度误差。
WAVEDROM_LEFT_PADDING_PX = Decimal("32")  # WaveDrom SVG 左边界安全区像素

# SVG 命名空间用于严格定位根节点和画布背景。
SVG_NAMESPACE = "http://www.w3.org/2000/svg"  # SVG 元素命名空间

# xlink 命名空间用于序列化时保持 WaveDrom defs 引用不变。
XLINK_NAMESPACE = "http://www.w3.org/1999/xlink"  # SVG xlink 命名空间

# 官方包的运行时前置条件，低于该版本时直接失败闭合。
MIN_NODE_VERSION = (20, 0, 0)  # Node.js 的最低主次补丁版本

# 命令执行器可注入，以便远程报告和隔离验证不触发真实副作用。
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]  # subprocess.run 兼容签名

# 从命令文本提取可比较的三段版本号。
def _parse_version(text: str) -> tuple[int, int, int] | None:
    """
    从 Node/npm 版本输出中提取 ``主.次.补丁`` 数字三元组。

    :param text: 命令输出，例如 ``v20.11.1`` 或 ``10.2.0``。
    :return: 成功时返回整数三元组；没有完整版本时返回 ``None``。
    """

    # 允许版本前有 v 或其他文本，但不接受不完整数字。
    match_version = re.search(r"(?<!\d)(\d+)\.(\d+)\.(\d+)", text)  # 版本匹配结果

    # 没有匹配对象时无法构造可比较的版本。
    if match_version is None:

        # 缺少可比较版本时交给调用方按运行时缺失处理。
        return None

    # 正则捕获组转换为稳定的整数元组，便于版本比较。
    tuple_version = tuple(int(match_version.group(index)) for index in range(1, 4))  # 解析后的版本

    # 返回标准化版本，避免上层重复处理正则对象。
    return tuple_version

# 解析 Unix 命令名和 Windows .cmd 入口的实际路径。
def _find_executable(name: str) -> str | None:
    """
    查找指定命令的可执行文件路径。

    :param name: 命令基名，例如 ``node``、``npm`` 或 ``wavedrom``。
    :return: PATH 中的命令路径，找不到时返回 ``None``。
    """

    # 先查询平台无关名称，保持 Unix 主机上的路径语义简单。
    path_executable = shutil.which(name)  # PATH 中的直接命令路径

    # 直接命令缺失时才尝试 Windows 的 .cmd 入口。
    if path_executable is None:

        # Windows 的 npm 和 WaveDrom 入口通常需要 .cmd 后缀。
        path_windows_executable = shutil.which(f"{name}.cmd")  # Windows 命令路径

        # 只在直接命令缺失时采用 Windows 入口，避免覆盖已解析路径。
        path_executable = path_windows_executable  # Windows 入口解析结果

    # 返回统一字符串路径，缺失时保留 None 供失败闭合使用。
    return path_executable

# 通过可注入执行器运行命令，并统一捕获文本输出。
def _run(
    command: Sequence[str],
    *,
    runner: CommandRunner | None = None,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """
    执行一条不经过 shell 的 WaveDrom 相关命令。

    :param command: argv 序列；调用方负责提供已拆分的参数。
    :param runner: 可选测试执行器；缺省使用 ``subprocess.run``。
    :param environment: 可选环境变量覆盖。
    :return: 包含退出码、标准输出和标准错误的完成对象。
    """

    # 优先使用注入的执行器，未注入时才落到真实 subprocess.run。
    func_runner = runner or subprocess.run  # 可观测的命令执行函数

    # 显式关闭 shell 并捕获文本，避免路径和引号被二次解释。
    completed_process = func_runner(  # subprocess 返回对象保存三类工具的退出码、标准输出和标准错误，供后续状态判断
        list(command),  # 已拆分的命令参数
        check=False,  # 禁止异常打断结构化报告
        capture_output=True,  # 捕获标准输出和标准错误文本
        text=True,  # 保持输出为字符串
        env=dict(environment) if environment is not None else None,  # 为三类工具提供统一环境变量覆盖
    )  # subprocess 调用完成并返回可审计文本

    # 返回统一类型，供版本检查、安装和渲染共用。
    return completed_process

# 从 npm 全局依赖树中读取精确 WaveDrom 版本。
def _installed_package_version(text: str) -> str | None:
    """
    读取 ``npm list --json`` 输出中的 WaveDrom 版本。

    :param text: npm 输出的 JSON 文本。
    :return: 包版本字符串；输出无效或未安装时返回 ``None``。
    """

    # 先解析 npm 输出，失败时保持依赖状态未知。
    try:

        # npm 输出可能因失败而不是 JSON，解析失败必须保持可诊断。
        dict_payload = json.loads(text)  # npm 全局树对象

    # JSON 解析失败不能被解释为已安装。
    except json.JSONDecodeError:

        # 无法解析的输出不能被猜测成已安装版本。
        return None

    # 全局树把包放在 dependencies 下，先读取标准结构。
    dict_dependencies = dict_payload.get("dependencies", {})  # npm 依赖映射

    # 兼容 npm 直接返回包对象的简化结构。
    dict_package = dict_dependencies.get(WAVEDROM_PACKAGE, {})  # WaveDrom 依赖节点

    # 标准依赖树优先提供精确版本。
    if isinstance(dict_package, dict) and dict_package.get("version"):

        # 标准依赖树中的 version 是最可信的精确版本。
        str_dependency_version = str(dict_package["version"])  # 依赖节点版本文本

        # 返回标准树中的版本，拒绝隐式降级。
        return str_dependency_version

    # 无标准节点时再读取兼容格式的顶层版本。
    if isinstance(dict_payload, dict) and dict_payload.get("version"):

        # 简化包对象仍可提供可审计的顶层版本字段。
        str_top_level_version = str(dict_payload["version"])  # 顶层包版本文本

        # 返回兼容格式中的版本。
        return str_top_level_version

    # 缺少版本字段时保持 None，调用方会报告具体缺失项。
    return None

# 创建固定字段的运行时报告，避免检查逻辑散落到多个分支。
def _new_runtime_report(smoke: bool) -> dict[str, Any]:
    """
    返回未填充命令状态的可序列化运行时报告。

    :param smoke: 是否请求真实 WaveJSON 冒烟。
    :return: 包含基础字段和待填充检查节点的报告字典。
    """

    # 将最低 Node 版本转为人类和机器都稳定的字符串。
    str_minimum_node = ".".join(str(item) for item in MIN_NODE_VERSION)  # 最低版本文本

    # 创建空报告，避免一处大型字典遮蔽字段语义。
    dict_report: dict[str, Any] = {}  # WaveDrom runtime 检查报告

    # 写入包名，供依赖管理器匹配配置。
    dict_report["package"] = WAVEDROM_PACKAGE  # 依赖配置中的 WaveDrom 包标识

    # 写入精确版本，避免报告只给出模糊的可用状态。
    dict_report["required_version"] = WAVEDROM_VERSION  # 版本合同

    # 写入 Node 最低版本文本，供人类和机器读取。
    dict_report["minimum_node_version"] = str_minimum_node  # Node 最低版本

    # 初始化 Node 检查节点，等待路径和版本填充。
    dict_report["node"] = {"path": None, "version": None, "ok": False}  # Node 检查节点

    # 先保留 npm 依赖管理状态，等待命令查询填充。
    dict_report["npm"] = {"path": None, "version": None, "ok": False}  # npm 状态槽位

    # 再保留 WaveDrom 图形状态，等待精确包查询填充。
    dict_report["wavedrom"] = {"path": None, "version": None, "ok": False}  # 图形入口状态槽位

    # 初始化冒烟节点，未请求时直接标记跳过。
    dict_report["smoke"] = {  # 冒烟检查节点
        "requested": smoke,  # 是否请求真实冒烟
        "ok": not smoke,  # 未请求时默认可通过
        "status": "skipped" if not smoke else "pending",  # 冒烟状态
    }

    # 初始化缺失项列表，后续只追加可操作标识。
    dict_report["missing"] = []  # 依赖缺失清单

    # 返回深度固定的报告，便于依赖管理器读取。
    return dict_report

# 检查 Node、npm、WaveDrom 版本和入口命令是否齐备。
def check_runtime(
    *,
    runner: CommandRunner | None = None,
    environment: Mapping[str, str] | None = None,
    smoke: bool = False,
) -> dict[str, Any]:
    """
    检查 WaveDrom 运行时是否满足固定版本合同。

    :param runner: 可选外部命令执行器，便于远程报告和隔离测试。
    :param environment: 可选 npm 环境覆盖，支持临时 prefix。
    :param smoke: 是否额外执行最小 WaveJSON 到 SVG 的真实冒烟渲染。
    :return: 包含版本、路径、缺失项和 ``ok`` 判定的报告字典。
    """

    # 先解析命令路径，缺失时不启动会抛异常的子进程。
    path_node = _find_executable("node")  # Node 可执行文件路径

    # 为 npm 查询和安装保留独立命令路径。
    path_npm = _find_executable("npm")  # npm 依赖管理入口

    # 用 WaveDrom 入口验证最终可执行文件是否注册。
    path_wavedrom = _find_executable("wavedrom")  # WaveDrom 图形入口

    # 初始化报告后写入已解析的命令路径。
    dict_report = _new_runtime_report(smoke)  # 基础运行时报告

    # 将 Node 的实际路径写入诊断节点。
    dict_report["node"]["path"] = path_node  # Node 命令定位

    # 将 npm 的实际路径写入依赖管理节点。
    dict_report["npm"]["path"] = path_npm  # npm PATH 解析结果

    # 将 WaveDrom 的实际路径写入渲染节点。
    dict_report["wavedrom"]["path"] = path_wavedrom  # 图形入口 PATH 解析结果

    # Node 命令存在时读取并比较最低运行时版本。
    if path_node is not None:

        # 只有命令存在时才读取 Node 版本。
        completed_process_node = _run(  # Node 版本完成结果
            [path_node, "--version"], runner=runner, environment=environment  # Node 版本参数
        )

        # 将 Node 输出转换为可比较版本。
        tuple_node = _parse_version(completed_process_node.stdout)  # Node 版本元组

        # 将解析结果写为稳定文本，缺失时保留 None。
        dict_report["node"]["version"] = (  # Node 版本文本
            ".".join(str(item) for item in tuple_node) if tuple_node else None  # 可读版本值
        )

        # Node 必须成功退出并满足最低版本合同。
        bool_node_ok = (  # Node 版本检查结果
            completed_process_node.returncode == 0  # 命令退出码
            and tuple_node is not None  # 版本解析成功
            and tuple_node >= MIN_NODE_VERSION  # 版本满足下限
        )

        # 将 Node 合同结果单独保存，避免重复执行版本判断。
        dict_report["node"]["ok"] = bool_node_ok  # Node 合同结果

    # npm 路径可用时同时读取版本和全局包树。
    if path_npm is not None:

        # 读取 npm 自身版本，确认依赖管理器能启动。
        completed_process_npm = _run(  # npm 版本探测的子进程结果
            [path_npm, "--version"], runner=runner, environment=environment  # npm 版本命令参数
        )

        # 将 npm 的版本文本解析为三段数字。
        tuple_npm = _parse_version(completed_process_npm.stdout)  # npm 版本数字三元组

        # 将 npm 版本转换为报告字段可序列化文本。
        dict_report["npm"]["version"] = (  # npm 版本序列化文本
            ".".join(str(item) for item in tuple_npm) if tuple_npm else None  # npm 报告版本值
        )

        # npm 版本不设固定值，但命令必须成功且输出完整。
        bool_npm_ok = completed_process_npm.returncode == 0 and tuple_npm is not None  # npm 命令可用标记

        # 保存 npm 基础可用性，后续缺失项只读取该字段。
        dict_report["npm"]["ok"] = bool_npm_ok  # npm 节点最终状态

        # 全局 npm 树必须精确匹配 WaveDrom 版本。
        completed_process_package = _run(  # 全局包查询完成结果
            [path_npm, "list", "--global", "--depth=0", "--json", WAVEDROM_PACKAGE],  # 全局包查询参数
            runner=runner,  # 注入的命令执行器
            environment=environment,  # 全局查询使用的 npm 环境
        )

        # 从查询输出读取安装版本并写入报告。
        str_package_version = _installed_package_version(completed_process_package.stdout)  # 包版本文本

        # 把全局树版本放入 WaveDrom 检查节点。
        dict_report["wavedrom"]["version"] = str_package_version  # WaveDrom 版本证据

        # 只有查询成功、版本精确且入口存在才通过。
        bool_package_ok = (  # WaveDrom 包合同结果
            completed_process_package.returncode == 0  # 查询命令成功
            and str_package_version == WAVEDROM_VERSION  # 版本精确匹配
            and path_wavedrom is not None  # 入口路径存在
        )

        # 记录精确版本、入口和查询退出码的合并结果。
        dict_report["wavedrom"]["ok"] = bool_package_ok  # WaveDrom 安装结果

    # 将 Node 缺失或版本过低统一转换为稳定字符串，供依赖管理器和 CLI 给出可操作提示。
    if not dict_report["node"]["ok"]:

        # 写入 Node 运行时缺失标识。
        dict_report["missing"].append("node>=20")

    # npm 缺失会阻止包查询和显式安装。
    if not dict_report["npm"]["ok"]:

        # 写入 npm 依赖缺失标识。
        dict_report["missing"].append("npm")

    # WaveDrom 入口或版本不满足时需要重新安装。
    if not dict_report["wavedrom"]["ok"]:

        # 写入精确 WaveDrom 包缺失标识。
        dict_report["missing"].append("wavedrom@3.6.1")

    # 仅在依赖清单为空时运行真实渲染冒烟。
    if smoke and not dict_report["missing"]:

        # 基础依赖齐备后运行真实 SVG 链路。
        with tempfile.TemporaryDirectory(prefix="wavedrom-check-") as str_temp_dir:

            # 临时目录隔离冒烟输入和输出，退出时自动清理。
            path_temp_dir = Path(str_temp_dir)  # 冒烟临时目录

            # 输入文件承载最小时钟 WaveJSON。
            path_input = path_temp_dir / "smoke.json5"  # 最小 WaveJSON 输入

            # 输出文件承载 WaveDrom 生成的 SVG。
            path_output = path_temp_dir / "smoke.svg"  # 冒烟 SVG 输出

            # 写入一条最小时钟波形，验证真实 CLI 输出是 SVG。
            path_input.write_text(
                '{"signal":[{"name":"clk","wave":"p..."}]}', encoding="utf-8"
            )

            # 复用原子渲染路径，保持冒烟与正式发布一致。
            dict_smoke = render_waveform(  # 冒烟渲染报告
                path_input,  # 冒烟 WaveJSON 路径
                path_output,  # 冒烟阶段的 SVG 目标文件
                runner=runner,  # 注入执行器
                executable=path_wavedrom,  # 已解析 WaveDrom 入口
                environment=environment,  # 冒烟环境覆盖
            )

            # 记录冒烟成功和输出字节数。
            dict_report["smoke"] = {  # smoke 节点保存请求标志、通过状态、状态文本和 SVG 字节数，供总体可用性计算
                "requested": True,  # 冒烟请求已执行
                "ok": True,  # 冒烟输出已验证
                "status": "passed",  # 冒烟状态文本
                "bytes": dict_smoke["bytes"],  # 冒烟 SVG 的最终字节数量
            }  # smoke 节点完整记录本次冒烟证据

    # 只有基础检查和可选冒烟都通过才允许发布 spec。
    bool_report_ok = not dict_report["missing"] and bool(dict_report["smoke"]["ok"])  # 总体运行时结果

    # 将基础检查和冒烟结果合并为最终可发布标志。
    dict_report["ok"] = bool_report_ok  # 运行时最终判定

    # 返回机器可读报告，调用方不得从缺失字段推断成功。
    return dict_report

# 安装精确 WaveDrom 版本，不自动卸载或回滚其他全局包。
def install_runtime(
    *,
    runner: CommandRunner | None = None,
    environment: Mapping[str, str] | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """
    通过 npm 全局安装固定的 WaveDrom 版本。

    :param runner: 可选外部命令执行器，便于隔离 npm 安装副作用。
    :param environment: 可选 npm prefix 等环境变量。
    :param confirm: 是否显式确认安装；未确认时拒绝执行。
    :return: 安装命令和安装后检查报告。
    :raises ValueError: 未提供显式确认时抛出。
    :raises FileNotFoundError: npm 不在 PATH 中时抛出。
    :raises RuntimeError: npm 返回非零退出码或安装后检查失败时抛出。
    """

    # 未获得显式确认时禁止全局 npm 写入。
    if not confirm:

        # 全局安装属于外部写入，必须由调用方显式确认。
        raise ValueError("> ERR: [Python] WaveDrom installation requires explicit confirmation.")

    # npm.cmd 由统一路径解析器处理，避免 shell 解释路径。
    path_npm = _find_executable("npm")  # npm 安装命令路径

    # npm 路径缺失时立即拒绝构造全局写入命令。
    if path_npm is None:

        # 缺少 npm 时不构造无法执行的安装命令。
        raise FileNotFoundError("> ERR: [Python] npm executable was not found.")

    # 固定安装顺序和 --yes 参数，确保远程审计可复现。
    list_command = [  # npm 全局安装 argv
        path_npm,  # npm 全局入口
        "install",  # npm 安装动作
        "--global",  # 选择全局 npm 前缀
        f"{WAVEDROM_PACKAGE}@{WAVEDROM_VERSION}",  # 固定 WaveDrom 包版本
        "--yes",  # 禁止 npm 安装再次询问
    ]

    # 执行安装并保留 stdout/stderr 供失败诊断。
    completed_process_install = _run(  # npm 安装完成结果
        list_command, runner=runner, environment=environment  # 安装命令参数
    )

    # npm 非零退出说明全局依赖没有完成安装。
    if completed_process_install.returncode != 0:

        # 优先展示 npm 错误，空 stderr 时使用稳定兜底文本。
        str_stderr = completed_process_install.stderr.strip() or "npm install failed"  # 安装错误文本

        # 非零退出必须失败闭合，不得继续执行后续发布。
        raise RuntimeError(f"> ERR: [Python] WaveDrom installation failed: {str_stderr}")

    # 安装完成后重新检查精确版本，识别部分安装状态。
    dict_postcheck = check_runtime(  # 安装后运行时报告
        runner=runner, environment=environment, smoke=False  # 安装后检查参数
    )

    # 安装后仍需重新确认固定版本和入口可用。
    if not dict_postcheck["ok"]:

        # 安装命令成功不等于入口和版本已经可用。
        raise RuntimeError(
            "> ERR: [Python] WaveDrom installation finished but the runtime check is not ready."
        )

    # 返回 argv 与安装后报告，供调用方写入审计证据。
    dict_result = {  # npm 安装与复核结果节点
        "command": list_command,  # 留存实际 npm 调用参数
        "ok": True,  # 安装和复核均通过
        "postcheck": dict_postcheck,  # 安装后重新检查节点
    }

    # 将结构化安装结果交给依赖管理器。
    return dict_result

# 将 SVG 数值属性解析为有限 Decimal，阻止不完整几何进入发布阶段。
def _parse_svg_decimal(str_value: str | None, str_field: str) -> Decimal:
    """
    解析 WaveDrom SVG 的十进制几何字段。

    :param str_value: SVG 属性原文；缺失值用 ``None`` 表示。
    :param str_field: 用于错误诊断的字段名称。
    :return: 可参与精确几何计算的有限 Decimal。
    :raises RuntimeError: 属性缺失、格式非法或数值非有限时抛出。
    """

    # 缺少属性时无法证明 SVG 的发布边界。
    if str_value is None or not str_value.strip():

        # 将缺失字段定位到 WaveDrom 输出几何合同。
        raise RuntimeError(
            "> ERR: [Python] WaveDrom SVG geometry is invalid: missing {}.".format(
                str_field
            )
        )

    # Decimal 避免浮点舍入改变 viewBox 的边界值。
    try:

        # 只接受属性原文中的十进制数，不隐式解释单位或表达式。
        decimal_value = Decimal(str_value.strip())  # 解析后的 SVG 几何数值

    # 非数字属性必须阻断发布，而不是猜测单位或默认值。
    except InvalidOperation as exc:

        # 保留原始解析异常作为调试链路，同时给出稳定的业务错误。
        raise RuntimeError(
            "> ERR: [Python] WaveDrom SVG geometry is invalid: {} is not numeric.".format(
                str_field
            )
        ) from exc

    # NaN 和无穷值不能构成可渲染的 SVG 视口。
    if not decimal_value.is_finite():

        # 非有限数值会让不同查看器采用不一致的裁剪结果。
        raise RuntimeError(
            "> ERR: [Python] WaveDrom SVG geometry is invalid: {} is not finite.".format(
                str_field
            )
        )

    # 返回已经通过有限性检查的精确数值。
    return decimal_value

# 将 Decimal 几何值格式化为稳定且无科学计数法的 SVG 属性文本。
def _format_svg_decimal(decimal_value: Decimal) -> str:
    """
    格式化 SVG 几何数值，避免无意义的小数或指数表示。

    :param decimal_value: 已通过有限性检查的 Decimal 几何值。
    :return: 可直接写入 SVG 属性的十进制文本。
    """

    # 零值统一输出为单个字符，避免负零和尾随小数进入 viewBox。
    if decimal_value == 0:

        # SVG 对零的几何语义与符号无关，统一保持可读输出。
        return "0"

    # 使用普通十进制格式，保留真实小数精度并禁止科学计数法。
    str_formatted = format(decimal_value, "f")  # SVG 几何文本

    # 只有带小数点的值需要去除无意义的尾随零。
    if "." in str_formatted:

        # 保留整数部分，同时删除不会改变几何意义的末尾字符。
        str_formatted = str_formatted.rstrip("0").rstrip(".")  # 规范化后的几何文本

    # 防止极端输入留下空文本或负零。
    if str_formatted in {"", "-0"}:

        # 空文本不能成为合法 SVG 属性，因此回退为规范零值。
        return "0"

    # 返回稳定的十进制属性文本。
    return str_formatted

# 在原子写入前扩展 WaveDrom SVG 左侧视口并同步画布背景。
def _expand_svg_left_boundary(str_svg_text: str) -> str:
    """
    为 WaveDrom SVG 增加固定左侧安全区并保留图形内容。

    :param str_svg_text: WaveDrom CLI 返回的 SVG 文本。
    :return: 扩展根 viewBox、根宽度和背景后的 SVG 文本。
    :raises RuntimeError: XML、几何字段或背景画布不满足发布合同。
    """

    # XML 解析先于任何输出文件操作，确保失败不会覆盖旧 SVG。
    try:

        # ElementTree 只在内存中解析 CLI 输出，不读取外部资源。
        element_root = ElementTree.fromstring(  # 解析 SVG 以校验根视口和画布合同
            str_svg_text  # 传入待校验的 WaveDrom SVG 文本
        )

    # 非 XML 输出不得进入原子发布阶段。
    except ElementTree.ParseError as exc:

        # 将解析错误收敛为当前 runtime 的可审计异常类型。
        raise RuntimeError(
            "> ERR: [Python] WaveDrom SVG geometry is invalid: malformed XML."
        ) from exc

    # 只接受 SVG 命名空间下的根节点。
    if element_root.tag != "{{{}}}svg".format(SVG_NAMESPACE):

        # HTML 或其他 XML 文档不能伪装成 WaveDrom 图形。
        raise RuntimeError(
            "> ERR: [Python] WaveDrom SVG geometry is invalid: root is not svg."
        )

    # 读取根宽度，后续用于保持像素宽度与 viewBox 宽度一致。
    decimal_root_width = _parse_svg_decimal(  # SVG 根像素宽度
        element_root.attrib.get("width"), "svg.width"  # 读取根宽度属性
    )

    # 读取根高度，后续用于确认纵向几何没有被修改。
    decimal_root_height = _parse_svg_decimal(  # SVG 根像素高度
        element_root.attrib.get("height"), "svg.height"  # 读取根高度属性
    )

    # 读取根 viewBox 原文，保留查看器使用的二维坐标合同。
    str_viewbox = element_root.attrib.get("viewBox", "")  # SVG 根视口文本

    # 将空格或逗号分隔的 viewBox 统一拆成四个边界值。
    list_viewbox_parts = str_viewbox.replace(",", " ").split()  # viewBox 边界字段

    # viewBox 必须由 x、y、宽度和高度四个数值组成。
    if len(list_viewbox_parts) != 4:

        # 缺少任一边界值时不能推导安全的左扩展结果。
        raise RuntimeError(
            "> ERR: [Python] WaveDrom SVG geometry is invalid: viewBox must have four numbers."
        )

    # 解析 viewBox 的 x 边界，后续使用 Decimal 做精确加减。
    decimal_viewbox_x = _parse_svg_decimal(  # 原始水平起点
        list_viewbox_parts[0], "svg.viewBox.x"  # 读取水平起点
    )

    # 解析 viewBox 的 y 边界，保证垂直起点保持不变。
    decimal_viewbox_y = _parse_svg_decimal(  # 原始垂直起点
        list_viewbox_parts[1], "svg.viewBox.y"  # 读取垂直起点
    )

    # 解析 viewBox 宽度，作为根宽度和右边界的基准。
    decimal_viewbox_width = _parse_svg_decimal(  # 原始水平范围
        list_viewbox_parts[2], "svg.viewBox.width"  # 读取水平范围
    )

    # 解析 viewBox 高度，作为纵向不变性的校验基准。
    decimal_viewbox_height = _parse_svg_decimal(  # 原始垂直范围
        list_viewbox_parts[3], "svg.viewBox.height"  # 读取垂直范围
    )

    # 宽高必须为正数，零宽或零高无法形成可视图形。
    if (
        decimal_root_width <= 0
        or decimal_root_height <= 0
        or decimal_viewbox_width <= 0
        or decimal_viewbox_height <= 0
    ):

        # 拒绝会导致查看器按空画布处理的几何合同。
        raise RuntimeError(
            "> ERR: [Python] WaveDrom SVG geometry is invalid: width and height must be positive."
        )

    # 根尺寸与 viewBox 尺寸不一致时，无法保证新增 32px 不改变缩放比例。
    if (
        decimal_root_width != decimal_viewbox_width
        or decimal_root_height != decimal_viewbox_height
    ):

        # 保持固定 WaveDrom 输出合同，避免猜测查看器的缩放策略。
        raise RuntimeError(
            "> ERR: [Python] WaveDrom SVG geometry is invalid: root size and viewBox size differ."
        )

    # 画布背景位于 WaveDrom 的 waves 分组内，需要从整个 SVG 树中精确定位。
    list_background_rects: list[ElementTree.Element] = []  # 匹配画布尺寸的白色背景集合

    # 仅接受同时匹配画布宽高且声明白色填充的 rect，排除 defs 中的波形原语。
    for element_candidate in element_root.iter(
        "{{{}}}rect".format(SVG_NAMESPACE)
    ):

        # 没有 style 的 rect 不是 WaveDrom 发布画布的可靠候选。
        str_candidate_style = element_candidate.attrib.get("style", "")  # 候选背景样式

        # 删除样式空白以稳定识别 fill:white 的画布声明。
        str_compact_style = str_candidate_style.replace(" ", "").lower()  # 归一化背景样式

        # 不含白色填充的矩形可能是 defs 原语或波形状态，不得修改。
        if "fill:white" not in str_compact_style:

            # 当前候选不属于发布背景，继续检查后续 rect。
            continue

        # 画布宽度和高度必须与原 viewBox 完全一致。
        if (
            element_candidate.attrib.get("width")
            != _format_svg_decimal(decimal_viewbox_width)
            or element_candidate.attrib.get("height")
            != _format_svg_decimal(decimal_viewbox_height)
        ):

            # 尺寸不同的白色矩形不代表整张 WaveDrom 画布。
            continue

        # 保存唯一候选，稍后统一验证数量并更新边界。
        list_background_rects.append(element_candidate)

    # 缺失或多重背景都会让扩展结果变得不可审计。
    if len(list_background_rects) != 1:

        # 不猜测应修改的矩形，直接阻断并保留已有 SVG。
        raise RuntimeError(
            "> ERR: [Python] WaveDrom SVG geometry is invalid: background rect is missing or ambiguous."
        )

    # 向左扩展固定安全区，保持原图形右边界位置不变。
    decimal_new_viewbox_x: Decimal = (  # 扩展后的左边界
        decimal_viewbox_x - WAVEDROM_LEFT_PADDING_PX  # 左边界左移固定像素
    )

    # 增加安全区宽度，使根宽度和 viewBox 仍然保持一比一。
    decimal_new_viewbox_width: Decimal = (  # 扩展后的视口宽度
        decimal_viewbox_width + WAVEDROM_LEFT_PADDING_PX  # 增加固定安全区
    )

    # 分别格式化四个边界，避免拼接时改变原有坐标语义。
    list_new_viewbox_parts: list[str] = [  # 扩展后的 viewBox 四段文本
        _format_svg_decimal(decimal_new_viewbox_x),  # 左边界文本
        _format_svg_decimal(decimal_viewbox_y),  # 垂直起点文本
        _format_svg_decimal(decimal_new_viewbox_width),  # 新视口宽度文本
        _format_svg_decimal(decimal_viewbox_height),  # 视口高度文本
    ]

    # 组合扩展后的 viewBox，供根 SVG 和查看器共同使用。
    str_new_viewbox = " ".join(list_new_viewbox_parts)  # 扩展后的 viewBox 属性

    # 根宽度使用与 viewBox 相同的像素数，避免缩放波形主体。
    str_new_width = _format_svg_decimal(decimal_new_viewbox_width)  # 扩展后的根宽度文本

    # 选出已经验证过的唯一画布背景元素。
    element_background_rect: ElementTree.Element = list_background_rects[0]  # WaveDrom 画布背景

    # 根宽度增加安全区，保持原波形的像素比例。
    element_root.set("width", str_new_width)

    # 根 viewBox 左移并变宽，使左侧信号名称落入可见范围。
    element_root.set("viewBox", str_new_viewbox)

    # 背景向左延伸，覆盖新增安全区的左侧区域。
    element_background_rect.set("x", _format_svg_decimal(decimal_new_viewbox_x))

    # 背景宽度同步扩展，避免输出 SVG 出现透明色带。
    element_background_rect.set("width", str_new_width)

    # 注册默认 SVG 命名空间，避免序列化生成 ns0 前缀。
    ElementTree.register_namespace("", SVG_NAMESPACE)

    # 注册 xlink 命名空间，保持 WaveDrom defs 的引用属性不变。
    ElementTree.register_namespace("xlink", XLINK_NAMESPACE)

    # 返回完整 SVG 文本，调用方随后仍使用同目录临时文件原子替换。
    return ElementTree.tostring(element_root, encoding="unicode")

# 把 WaveJSON 渲染成 SVG，并在验证通过后才发布目标文件。
def render_waveform(
    input_path: Path,
    output_path: Path,
    *,
    runner: CommandRunner | None = None,
    executable: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """
    使用 ``wavedrom --input`` 将 JSON5 输入转换为 SVG。

    :param input_path: 已写入的 WaveJSON/JSON5 文件路径。
    :param output_path: 期望发布的 SVG 路径。
    :param runner: 可选外部命令执行器。
    :param executable: 可选 wavedrom 可执行文件路径。
    :param environment: 可选外部命令环境变量。
    :return: 包含命令、SVG 字节数和输出路径的报告。
    :raises FileNotFoundError: 输入或 wavedrom 命令缺失时抛出。
    :raises RuntimeError: 命令失败或输出不是有效 SVG 时抛出。
    """

    # 输入路径缺失时立即失败闭合。
    if not input_path.is_file():

        # 让调用方看到明确的 WaveJSON 路径诊断。
        raise FileNotFoundError(f"> ERR: [Python] WaveJSON input does not exist: {input_path}")

    # 允许测试替身或调用方固定 wavedrom 可执行文件路径。
    path_executable = executable or _find_executable("wavedrom")  # 渲染命令候选路径

    # 没有入口时不启动不可诊断的子进程。
    if path_executable is None:

        # 返回明确的 WaveDrom 入口缺失错误。
        raise FileNotFoundError("> ERR: [Python] wavedrom executable was not found.")

    # 官方 CLI 从 stdout 输出 SVG，因此命令不携带输出路径参数。
    list_command = [  # WaveDrom 渲染 argv
        path_executable,  # WaveDrom 可执行入口
        "--input",  # WaveDrom 输入开关
        str(input_path),  # 传入 WaveJSON 文件
        "--indent",  # 输出缩进参数
        "2",  # 使用稳定的 SVG 缩进宽度
    ]

    # 执行渲染并捕获 SVG 文本。
    completed_process_render = _run(  # WaveDrom 渲染完成结果
        list_command, runner=runner, environment=environment  # 渲染命令参数
    )

    # WaveDrom 非零退出时不覆盖既有 SVG。
    if completed_process_render.returncode != 0:

        # 使用 WaveDrom 的 stderr 保留最接近根因的诊断信息。
        str_stderr = completed_process_render.stderr.strip() or "wavedrom render failed"  # 渲染错误文本

        # 非零退出不得产生或覆盖目标文件。
        raise RuntimeError(f"> ERR: [Python] WaveDrom render failed: {str_stderr}")

    # 只接受包含 svg 根节点的输出，防止错误文本写成图像。
    str_svg = completed_process_render.stdout.strip()  # 去除边界空白后的 SVG 内容

    # SVG 根节点缺失表示渲染输出合同不成立。
    if "<svg" not in str_svg.lower():

        # 拒绝把非图像文本写入目标文件。
        raise RuntimeError("> ERR: [Python] WaveDrom did not produce SVG output.")

    # 扩展 SVG 左边界并在内存中完成几何校验，失败时不触碰目标路径。
    str_svg = _expand_svg_left_boundary(str_svg)  # 通过安全区校正后的 SVG 文本

    # 先创建父目录，再用同目录临时文件实现原子替换。
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 在目标目录创建临时 SVG，保证替换动作具备同目录原子性。
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        suffix=".svg",
        dir=output_path.parent,
        delete=False,
    ) as file_temp:

        # 临时文件写完整 SVG，避免发布半写内容。
        file_temp.write(str_svg)

        # 保存临时路径，离开上下文后再执行原子替换。
        path_temp_file = Path(file_temp.name)  # 同目录临时 SVG 路径

    # 替换目标文件，失败时旧文件仍保持不变。
    path_temp_file.replace(output_path)

    # 返回发布后的字节数与命令，便于审计和 Markdown 追溯。
    dict_result = {  # SVG 原子发布结果节点
        "command": list_command,  # WaveDrom 调用参数留痕
        "output": str(output_path),  # SVG 发布路径
        "bytes": output_path.stat().st_size,  # 已发布 SVG 字节数
    }

    # 将结构化渲染结果交给 spec bundle 发布器。
    return dict_result

# CLI 入口把三类 runtime 操作暴露为机器可读 JSON。
def main(argv: Sequence[str] | None = None) -> int:
    """
    执行 wavedrom runtime 命令行入口。

    :param argv: 可选命令参数序列；为空时读取进程参数。
    :return: 成功返回 0，输入或运行时错误返回 2。
    """

    # 子命令解析器只描述参数，不在构造期执行环境操作。
    parser = argparse.ArgumentParser(description="Check and render the pinned WaveDrom runtime.")  # runtime CLI 参数解析器

    # 三个子命令分别覆盖检查、显式安装和渲染。
    sub_parsers_action_subparsers: argparse._SubParsersAction = parser.add_subparsers(  # 注册子命令容器
        dest="command", required=True  # 要求调用方选择子命令
    )  # CLI 子命令集合

    # 注册不接受额外参数且默认执行真实冒烟的 check 子命令及其帮助文本。
    sub_parsers_action_subparsers.add_parser("check", help="Check Node, npm, and WaveDrom.")

    # install 需要显式 --yes 才允许全局写入。
    argument_parser_install_parser: argparse.ArgumentParser = sub_parsers_action_subparsers.add_parser(  # 创建 install 解析器
        "install", help="Install wavedrom@3.6.1 globally."  # 安装命令帮助文本
    )  # 安装子解析器

    # 注册 install 的显式确认开关。
    argument_parser_install_parser.add_argument(
        "--yes", action="store_true", help="Confirm the global npm installation."
    )

    # render 要求输入 WaveJSON 和输出 SVG 路径。
    argument_parser_render_parser: argparse.ArgumentParser = sub_parsers_action_subparsers.add_parser(  # 构造 render 专用参数对象
        "render", help="Render one WaveJSON file to SVG."  # 渲染命令帮助文本
    )  # render 命令解析器完成构建

    # 注册 render 的输入路径参数。
    argument_parser_render_parser.add_argument("--input", required=True, type=Path)

    # 注册 render 的输出路径参数。
    argument_parser_render_parser.add_argument("--output", required=True, type=Path)

    # 解析命令行参数，显式传入 argv 便于远程包装器复用。
    namespace_args: argparse.Namespace = parser.parse_args(argv)  # 解析后的 CLI 参数

    # 统一捕获依赖和输入错误，保护机器可读 stdout 协议。
    try:

        # check 子命令必须通过真实冒烟才返回成功。
        if namespace_args.command == "check":

            # check 始终包含冒烟，以证明真实 SVG 生成链路。
            dict_payload = check_runtime(smoke=True)  # 检查命令结果

        # install 子命令只在调用方显式确认时执行全局写入。
        elif namespace_args.command == "install":

            # install 的确认状态只由 --yes 控制。
            dict_payload = install_runtime(confirm=bool(namespace_args.yes))  # 安装命令结果

        # 其余情况属于 render，使用已校验的输入输出参数。
        else:

            # render 使用解析后的输入和输出路径。
            dict_payload = render_waveform(namespace_args.input, namespace_args.output)  # 渲染命令结果

    # 将可预期的运行时错误转换为带前缀的人类诊断。
    except (FileNotFoundError, RuntimeError, ValueError) as exc:

        # 人类错误写 stderr，保持 stdout 为空以保护机器协议。
        print("> ERR: [Python] {}".format(exc), file=sys.stderr)

        # 输入、依赖或渲染失败统一返回 2。
        return 2

    # stdout 只输出一个 JSON 对象，供上游脚本稳定解析。
    print(json.dumps(dict_payload, ensure_ascii=False, sort_keys=True))

    # 成功完成命令后返回零。
    return 0

# 直接执行模块时把返回码交还操作系统。
if __name__ == "__main__":

    # 使用 SystemExit 保留 CLI 的明确退出码。
    raise SystemExit(main())
