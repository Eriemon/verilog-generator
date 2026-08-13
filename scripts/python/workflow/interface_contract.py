"""提取 Python 与 Verilog 产物的静态接口契约。"""

# 启用延迟注解，减少运行时类型导入耦合
from __future__ import annotations

# Python 语法树和稳定摘要依赖
import ast
import hashlib
import json
import re

# 文件系统路径类型
from pathlib import Path

# 结构化契约允许不同字段类型
from typing import Any

# 向量契约复用运行时已有扫描器
from .vectors import extract_vector_hashes, find_vector_contracts

# 限定接口审计允许处理的产物类型
INTERFACE_TARGETS = ("python", "rtl")  # 支持的接口契约目标集合

# 对外接口契约审计入口
def audit_interface(target: str, root: Path) -> dict[str, Any]:
    """
    根据目标类型提取接口契约并追加稳定摘要。

    :param target: 接口目标类型，只允许 python 或 rtl。
    :param root: 待扫描产物根目录。
    :return: 带 interface_sha256 字段的接口契约字典。
    :raises ValueError: 当 target 不属于允许的接口目标集合时抛出。
    """

    # 将调用方输入压缩到内部固定目标名
    str_normalized_target = _require_target(target)  # 规范化后的接口目标名

    # 按目标语言选择对应的契约扫描策略
    if str_normalized_target == "python":

        # Python 产物契约来自公开函数和向量元信息
        dict_contract = _python_contract(root)  # Python 接口契约载荷

    # RTL 目标走 Verilog module/port/instance 扫描路径
    else:

        # Verilog 分支需要解析 module 声明和连线实例关系
        dict_contract = _rtl_contract(root)  # RTL 扫描得到的接口结构

    # 把稳定哈希写回契约，便于上游比较接口漂移
    dict_contract["interface_sha256"] = _stable_hash(dict_contract)  # 接口契约稳定摘要

    # 返回完整契约给审计或报告流程
    return dict_contract

# 接口目标名校验
def _require_target(target: str) -> str:
    """
    校验并规范化接口契约目标名。

    :param target: 调用方传入的接口目标文本。
    :return: 小写形式的接口目标名。
    :raises ValueError: 当目标不在 INTERFACE_TARGETS 中时抛出。
    """

    # 统一大小写，避免 CLI 或调用方传入大小写差异
    str_normalized_target = target.lower()  # 小写接口目标名

    # 阻断未知目标，避免后续分支产生含糊契约
    if str_normalized_target not in INTERFACE_TARGETS:

        # 报告可接受目标，便于用户修正输入
        raise ValueError(f"> ERR: [Python] Interface target must be one of {', '.join(INTERFACE_TARGETS)}.")

    # 返回已经确认合法的目标名
    return str_normalized_target

# Python 产物契约提取
def _python_contract(root: Path) -> dict[str, Any]:
    """
    扫描 Python 文件中的公开函数和向量契约。

    :param root: Python 产物根目录。
    :return: 描述公开函数、测试入口、向量 case 和诊断问题的契约字典。
    """

    # 收集每个公开顶层函数的名称、参数和来源文件
    list_functions: list[dict[str, Any]] = []  # 公开函数契约条目

    # 记录语法解析失败等接口提取问题
    list_issues: list[dict[str, str]] = []  # Python 契约扫描问题

    # 按稳定路径顺序扫描所有 Python 文件
    for path_file in sorted(root.glob("**/*.py")):

        # 将文件路径改成相对形式，保证契约在不同工作区可比较
        str_relative_path = path_file.relative_to(root).as_posix()  # 契约中的相对源码路径

        # 捕获单个文件语法错误，避免整个接口审计中断
        try:

            # 解析 Python 源码，供后续读取顶层公开函数
            ast_tree = ast.parse(path_file.read_text(encoding="utf-8", errors="ignore"))  # Python 模块语法树

        # 语法错误只登记为契约问题，其他文件仍继续扫描
        except SyntaxError as exc:

            # 将解析异常压缩成稳定结构，供报告侧展示
            dict_issue = {
                "severity": "error",  # 问题严重程度
                "source": "current_module_issue",  # 运行时静态扫描问题来源
                "message": f"Python parse error: {exc}",  # Python 解析失败文本
                "path": str_relative_path,  # 触发问题的相对文件路径
            }  # Python 解析问题条目

            # 保留语法失败文件，便于上游定位接口缺失原因
            list_issues.append(dict_issue)

            # 跳过当前坏文件，继续扫描同目录其他文件
            continue

        # 只提取顶层公开函数，避免把内部 helper 当作外部契约
        for ast_node in ast_tree.body:

            # 公开函数以非下划线命名作为稳定接口候选
            if isinstance(ast_node, ast.FunctionDef) and not ast_node.name.startswith("_"):

                # 收集函数参数名，保留原始公开签名顺序
                list_argument_names = [arg.arg for arg in ast_node.args.args]  # 公开函数参数名序列

                # 公开函数契约只存稳定字段，避免嵌入完整 AST
                dict_function = {
                    "name": ast_node.name,  # 公开函数名
                    "args": list_argument_names,  # 公开函数参数顺序
                    "path": str_relative_path,  # 公开函数来源文件
                }  # 公开函数接口条目

                # 将该公开函数加入 Python 接口契约
                list_functions.append(dict_function)

    # 读取同一根目录下的向量契约，补充 case 与哈希证据
    list_vector_contracts = find_vector_contracts(root)  # 向量契约扫描结果

    # 汇总 Python 接口契约，供后续稳定哈希和报告使用
    dict_contract = {
        "version": 1,  # 接口契约格式版本
        "target": "python",  # 当前契约目标类型
        "source_root": root.name,  # 被扫描根目录名
        "top": list_functions[0]["name"] if list_functions else None,  # 第一个公开函数作为默认入口提示
        "exported_functions": list_functions,  # 公开函数契约列表
        "has_run_tests": any(item["name"] == "run_tests" for item in list_functions),  # 是否暴露 run_tests 入口
        "case_ids": _case_ids(list_vector_contracts),  # 向量契约中的 case 标识
        "vector_hashes": _vector_hashes(list_vector_contracts),  # 向量契约中的内容哈希
        "issues": list_issues,  # Python 解析或扫描问题
    }  # Python 产物接口契约

    # Python 分支产出的契约已经包含公开函数和向量证据
    return dict_contract

# Verilog 产物接口扫描入口
def _rtl_contract(root: Path) -> dict[str, Any]:
    """
    扫描 Verilog 文件中的 module、port、instance 和向量契约。

    :param root: RTL 产物根目录。
    :return: 描述 RTL 顶层、端口、实例和向量信息的契约字典。
    """

    # 读取所有 Verilog 文件为相对路径到源码文本的映射
    dict_text_by_path = _read_files(root, ("*.v",))  # Verilog 文件文本映射

    # 保存每个 module 的端口和实例信息
    list_modules: list[dict[str, Any]] = []  # RTL module 契约条目

    # 按文件顺序提取 module，保持报告稳定
    for str_relative_path, str_file_text in dict_text_by_path.items():

        # 将当前文件中的 module 条目追加到全局列表
        list_modules.extend(_extract_rtl_modules(str_file_text, str_relative_path))

    # 优先选择非 testbench module 作为顶层候选
    dict_top_module = next(  # 非 testbench module 优先作为接口顶层
        (item for item in list_modules if not item["name"].lower().endswith("_tb")),  # 首个非测试平台模块
        list_modules[0] if list_modules else None,  # 没有普通模块时保留首个 module
    )  # RTL 顶层候选 module 契约

    # 读取向量契约补充 case 和哈希证据
    list_vector_contracts = find_vector_contracts(root)  # RTL 关联向量契约

    # 拼接源码文本用于兜底扫描 case 标识
    str_combined_text = "\n".join(dict_text_by_path.values())  # 所有 Verilog 文本拼接结果

    # 汇总 RTL 接口契约，端口来自顶层，实例来自全部 module
    dict_contract = {  # Verilog 扫描结果的报告主体
        "version": 1,  # 下游解析该结构时使用的格式版本
        "target": "rtl",  # 区分 Python 契约分支的目标标记
        "source_root": root.name,  # 报告中展示的产物目录名
        "top": dict_top_module["name"] if dict_top_module else None,  # 默认用于综合或仿真的顶层名
        "modules": list_modules,  # module 契约列表
        "ports": dict_top_module["ports"] if dict_top_module else [],  # 顶层端口契约
        "instances": [item for module in list_modules for item in module.get("instances", [])],  # 子模块实例列表
        "case_ids": _case_ids(list_vector_contracts) or _scan_case_ids(str_combined_text),  # RTL 验证场景标识
        "vector_hashes": _vector_hashes(list_vector_contracts) or _scan_vector_hashes(dict_text_by_path),  # 激励文件内容摘要
        "issues": [],  # RTL 接口扫描暂未产生错误条目
    }  # Verilog 顶层审计载荷

    # 返回 RTL 契约给上层审计入口
    return dict_contract

# 文件读取辅助函数
def _read_files(root: Path, suffix_globs: tuple[str, ...]) -> dict[str, str]:
    """
    按后缀 glob 读取根目录下的文本文件。

    :param root: 待扫描根目录。
    :param suffix_globs: 文件名 glob 后缀集合，例如 *.v。
    :return: 相对路径到文件文本的映射。
    """

    # 保存审计用的相对路径源码快照
    dict_texts: dict[str, str] = {}  # 相对路径到源码文本的映射

    # 逐个 glob 模式扫描，允许调用方传入多个后缀
    for str_pattern in suffix_globs:

        # 同一模式下按路径排序，保证契约稳定
        for path_file in sorted(root.glob(f"**/{str_pattern}")):

            # 将源码保存到相对路径键下，忽略坏字符保持审计不中断
            dict_texts[path_file.relative_to(root).as_posix()] = path_file.read_text(  # 相对路径对应源码文本
                encoding="utf-8",  # Verilog/Python 源码按 UTF-8 读取
                errors="ignore",  # 坏字符不阻断接口契约扫描
            )  # 当前文件源码文本

    # 返回全部读取到的源码文本
    return dict_texts

# RTL module 头部解析
def _extract_rtl_modules(text: str, rel_path: str) -> list[dict[str, Any]]:
    """
    从 Verilog 文本中提取 module、端口和实例契约。

    :param text: Verilog 源码文本。
    :param rel_path: 当前 Verilog 文件相对路径。
    :return: 当前文件内的 module 契约列表。
    """

    # 保存当前文件中识别出的 module 契约
    list_modules: list[dict[str, Any]] = []  # 当前文件 module 契约列表

    # 移除注释后再匹配 module 头，避免注释文本干扰正则
    str_clean_text = _strip_verilog_comments(text)  # 去注释后的 Verilog 文本

    # 匹配带可选参数块的 module 声明头
    pattern_regex_module_header: re.Pattern[str] = re.compile(  # 带可选参数块的 module 头匹配器
        r"\bmodule\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:#\s*\([^;]*?\)\s*)?\((.*?)\)\s*;",  # module 名和端口头捕获模式
        re.DOTALL,  # 允许参数块和端口头跨行
    )  # Verilog module 声明正则

    # 遍历每个 module 头部匹配，提取后续 body
    for match_module in pattern_regex_module_header.finditer(str_clean_text):

        # module 名后续会参与顶层候选和实例报告
        str_module_name = match_module.group(1)  # RTL 设计单元名称

        # 端口头文本保留给端口提取函数
        str_header = match_module.group(2)  # module 端口头文本

        # body 起点从 module 声明结束处开始
        int_body_start = match_module.end()  # module 声明后的正文起点

        # 查找最近的 endmodule，当前正则不做嵌套语义
        match_endmodule = re.search(  # 当前 module 的结束位置匹配
            r"\bendmodule\b",  # Verilog module 结束关键字
            str_clean_text[int_body_start:],  # 从当前 module body 起点之后搜索
            re.DOTALL,  # 保持跨行 body 搜索能力
        )  # endmodule 匹配

        # 截取 module body；缺失 endmodule 时保守使用空 body
        str_body = (  # 当前 module 的正文文本
            str_clean_text[int_body_start : int_body_start + match_endmodule.start()]  # endmodule 之前的正文
            if match_endmodule  # 找到 endmodule 时截取真实 body
            else ""  # 缺失结束关键字时不猜测正文范围
        )  # 当前设计单元的正文切片

        # 汇总当前 module 的外部端口和内部实例信息
        dict_module = {  # 当前声明块的接口条目
            "name": str_module_name,  # 声明头捕获到的设计单元标识
            "path": rel_path,  # 该声明所在的相对文件
            "ports": _extract_ports(str_header, str_body),  # module 端口契约
            "instances": _extract_instances(str_body),  # body 中识别出的子模块实例
        }  # 文件级 module 扫描结果

        # 将当前 module 加入文件扫描结果
        list_modules.append(dict_module)

    # 返回当前文件内的全部 module 契约
    return list_modules

# Verilog 注释剥离
def _strip_verilog_comments(text: str) -> str:
    """
    删除 Verilog 行注释和块注释，保留可解析代码文本。

    :param text: Verilog 源码文本。
    :return: 去除注释后的源码文本。
    """

    # 用空字符串替换块注释和行注释，方便后续正则扫描
    str_clean_text = re.sub(r"/\*.*?\*/|//[^\n\r]*", "", text, flags=re.DOTALL)  # 去注释源码文本

    # 返回去注释后的文本
    return str_clean_text

# 端口契约提取
def _extract_ports(header: str, body: str) -> list[dict[str, Any]]:
    """
    从 module 头和 body 声明中提取端口方向与宽度。

    :param header: module 括号中的端口头文本。
    :param body: module 声明后的主体文本。
    :return: 端口契约列表，每项包含 name、direction 和 width。
    """

    # 先用 header 中的端口名建立稳定顺序
    dict_ports: dict[str, dict[str, Any]] = {}  # 端口名到端口契约的映射

    # 解析逗号分隔端口名，兼容 ANSI 与非 ANSI 风格
    for str_raw_port in header.split(","):

        # 从端口片段末尾提取标识符
        match_name = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*(?://.*)?$", str_raw_port.strip())  # 端口名匹配

        # 找到端口名后先占位，方向和宽度稍后补齐
        if match_name:

            # 端口名作为稳定 key，避免重复端口破坏顺序
            str_port_name = match_name.group(1)  # 端口名称

            # 先登记 header 顺序，后续声明负责补足方向宽度
            dict_ports[str_port_name] = {  # module header 端口占位
                "name": str_port_name,  # 端口标识符
                "direction": None,  # body 或 ANSI 声明尚未补齐方向
                "width": None,  # body 或 ANSI 声明尚未补齐位宽
            }  # 端口初始契约

    # 匹配 body 中 input/output/inout 声明
    pattern_regex_declaration: re.Pattern[str] = re.compile(  # 非 ANSI body 端口声明匹配器
        r"\b(input|output|inout)\b\s*(?:reg\s+|wire\s+)?(\[[^\]]+\]\s*)?([^;]+);"  # 方向、位宽和端口名捕获模式
    )  # body 端口声明正则

    # 从 body 声明中补齐端口方向和宽度
    for match_declaration in pattern_regex_declaration.finditer(body):

        # 端口方向来自声明关键字
        str_direction = match_declaration.group(1)  # 端口方向

        # 将位宽范围转换成整数宽度
        int_width = _width_from_range(match_declaration.group(2))  # 端口位宽

        # 同一声明可能列出多个端口名
        for str_port_name in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", match_declaration.group(3)):

            # 跳过声明修饰词，避免把 reg/wire 当作端口
            if str_port_name in {"reg", "wire"}:

                # 当前 token 是声明修饰词，不属于接口端口
                continue

            # 确保声明区端口也存在于契约映射中
            dict_ports.setdefault(str_port_name, {"name": str_port_name})

            # 用声明信息更新端口方向和宽度
            dict_ports[str_port_name].update(
                {
                    "direction": str_direction,  # body 声明确认的端口方向
                    "width": int_width,  # body 声明折算后的端口位宽
                }
            )

    # 匹配 ANSI 风格头部中的方向和端口名
    pattern_regex_inline: re.Pattern[str] = re.compile(  # ANSI 风格端口声明匹配器
        r"\b(input|output|inout)\b\s*(?:reg\s+|wire\s+)?(\[[^\]]+\]\s*)?([A-Za-z_][A-Za-z0-9_]*)"  # 头部方向和端口名捕获模式
    )  # header 内联端口捕获器

    # 从 header 原文中读取 ANSI 端口声明
    for match_inline in pattern_regex_inline.finditer(header):

        # 第三个捕获组保存 ANSI 端口的名称部分
        str_port_name = match_inline.group(3)  # header 中声明的端口名

        # 确保 inline 端口存在于契约映射中
        dict_ports.setdefault(str_port_name, {"name": str_port_name})

        # 用 ANSI 声明补齐 header 端口的方向和位宽
        dict_ports[str_port_name].update(
            {  # header 内联声明提供的属性
                "direction": match_inline.group(1),  # input/output/inout 方向关键字
                "width": _width_from_range(match_inline.group(2)),  # 方括号范围折算后的位数
            }
        )

    # 返回按发现顺序整理好的端口契约
    return list(dict_ports.values())

# 实例契约提取
def _extract_instances(body: str) -> list[dict[str, str]]:
    """
    从 module body 中提取简单子模块实例。

    :param body: module 主体文本。
    :return: 子模块实例契约列表，每项包含 module 和 instance。
    """

    # 保存 module body 中识别到的实例条目
    list_instances: list[dict[str, str]] = []  # 子模块实例契约列表

    # 匹配普通实例和带参数实例的开头
    pattern_regex_instance: re.Pattern[str] = re.compile(  # 子模块实例起始语句匹配器
        r"\b([A-Za-z_][A-Za-z0-9_]*)\s+(?:#\s*\([^;]*?\)\s*)?([A-Za-z_][A-Za-z0-9_]*)\s*\(",  # module 类型和实例名捕获模式
        re.DOTALL,  # 支持参数化实例跨行
    )  # Verilog 实例声明正则

    # 遍历潜在实例声明
    for match_instance in pattern_regex_instance.finditer(body):

        # 提取 module 类型名和实例名
        str_module_name = match_instance.group(1)  # 被实例化的 module 名称

        # 实例名称用于连线审计定位
        str_instance_name = match_instance.group(2)  # 子模块实例名称

        # 排除控制语句和连续赋值误匹配
        if str_module_name in {"if", "for", "while", "case", "always", "assign"}:

            # 当前匹配是 Verilog 语句关键字，不是子模块实例
            continue

        # 保存可信实例信息
        list_instances.append({"module": str_module_name, "instance": str_instance_name})

    # 返回当前 module body 中的实例契约
    return list_instances

# 位宽范围转换
def _width_from_range(value: str | None) -> int | None:
    """
    将 Verilog 位宽范围转换为整数宽度。

    :param value: 形如 [7:0] 的位宽范围文本，缺失时表示单 bit。
    :return: 位宽整数；无法解析表达式范围时返回 None。
    """

    # 缺失范围表示单 bit 端口或信号
    if not value:

        # 单 bit 宽度直接返回 1
        return 1

    # 仅解析纯数字上下界，表达式宽度留给后续工具处理
    match_range = re.search(r"\[\s*(\d+)\s*:\s*(\d+)\s*\]", value)  # 数字位宽范围匹配

    # 表达式范围无法静态折算成确定整数
    if not match_range:

        # 返回 None 表示宽度未知
        return None

    # 读取高低位边界
    int_high = int(match_range.group(1))  # 位宽高位索引

    # 低位索引用于计算包含端点的宽度
    int_low = int(match_range.group(2))  # 位宽低位索引

    # 返回包含两端点的绝对宽度
    return abs(int_high - int_low) + 1

# 向量 case 标识去重
def _case_ids(vector_contracts: list[dict[str, Any]]) -> list[str]:
    """
    从向量契约中提取稳定去重的 case 标识。

    :param vector_contracts: find_vector_contracts 返回的向量契约列表。
    :return: 按首次出现顺序去重后的 case 标识列表。
    """

    # 按扫描顺序保存唯一 case 标识
    list_case_ids: list[str] = []  # 去重后的 case 标识列表

    # 扫描每份向量元数据中的摘要字段
    for dict_contract in vector_contracts:

        # 逐项读取契约中的 case_ids 字段
        for str_case_id in dict_contract.get("case_ids", []) or []:

            # 只保留首次出现的 case 标识
            if str_case_id not in list_case_ids:

                # 转成字符串，保证契约 JSON 类型稳定
                list_case_ids.append(str(str_case_id))

    # 返回稳定顺序的 case 标识
    return list_case_ids

# 向量哈希去重
def _vector_hashes(vector_contracts: list[dict[str, Any]]) -> list[str]:
    """
    从向量契约中提取稳定去重的 sha256 值。

    :param vector_contracts: find_vector_contracts 返回的向量契约列表。
    :return: 按首次出现顺序去重后的向量哈希列表。
    """

    # 保留向量扫描顺序中的首次 sha256
    list_hash_values: list[str] = []  # 唯一向量 sha256 序列

    # 遍历每份向量契约
    for dict_contract in vector_contracts:

        # 读取契约中可选 sha256 字段
        str_hash_value = dict_contract.get("sha256")  # 向量契约 sha256 文本

        # 只保留非空且尚未出现的哈希
        if str_hash_value and str_hash_value not in list_hash_values:

            # 转成字符串，避免上游类型漂移影响 JSON 结构
            list_hash_values.append(str(str_hash_value))

    # 返回稳定顺序的向量哈希列表
    return list_hash_values

# case 标识兜底扫描
def _scan_case_ids(text: str) -> list[str]:
    """
    从合并源码文本中兜底扫描 case_* 标识。

    :param text: 合并后的源码文本。
    :return: 排序后的 case 标识列表。
    """

    # 用正则从源码中提取 case_* 形式的标识
    list_case_ids = sorted(set(re.findall(r"\bcase_[A-Za-z0-9_]+\b", text)))  # 兜底 case 标识列表

    # 返回排序结果，保证契约稳定
    return list_case_ids

# 向量哈希兜底扫描
def _scan_vector_hashes(text_by_path: dict[str, str]) -> list[str]:
    """
    从源码文本映射中兜底扫描向量哈希。

    :param text_by_path: 相对路径到源码文本的映射。
    :return: 按首次出现顺序去重后的向量哈希列表。
    """

    # 保存去重后的哈希文本
    list_hash_values: list[str] = []  # 兜底扫描得到的向量哈希

    # 逐个文件文本扫描向量哈希
    for str_file_text in text_by_path.values():

        # 复用向量模块中的哈希提取器
        for str_hash_value in extract_vector_hashes(str_file_text):

            # 只保存首次出现的哈希
            if str_hash_value not in list_hash_values:

                # 保留该哈希供契约报告使用
                list_hash_values.append(str_hash_value)

    # 返回稳定顺序的哈希列表
    return list_hash_values

# 稳定契约摘要
def _stable_hash(payload: dict[str, Any]) -> str:
    """
    对接口契约计算稳定 sha256 摘要。

    :param payload: 待摘要的接口契约字典。
    :return: 基于排序 JSON 文本计算得到的 sha256 十六进制字符串。
    """

    # 使用排序键和非 ASCII 保留策略生成稳定 JSON 字节
    bytes_encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")  # 稳定 JSON 字节

    # 返回契约摘要，供接口漂移检测比较
    return hashlib.sha256(bytes_encoded).hexdigest()
