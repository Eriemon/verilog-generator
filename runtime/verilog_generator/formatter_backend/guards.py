"""检查 formatter 输出是否破坏 RTL 结构边界。"""

# 延迟类型注解求值，保持 guard 模块导入轻量
from __future__ import annotations

# 标准库依赖用于正则提取模块名和预处理指令
import re
from dataclasses import dataclass

# GuardReport 承载格式化前后结构保护检查的结果
@dataclass(frozen=True)
class GuardReport:
    """记录 formatter 输出结构保护检查的结果。"""

    # ok 表示所有 guard 检查是否通过
    ok: bool  # guard 总体通过状态

    # failed_checks 保存失败检查项名称，供调用方决定是否阻止写回
    failed_checks: list[str]  # 失败 guard 名称列表

    # details 保存结构对比证据，供质量报告展示
    details: dict[str, object]  # guard 结构对比详情

    # 字典转换供 JSON 报告写入器复用
    def to_dict(self) -> dict[str, object]:
        """
        转换为可序列化字典。

        :param self: 当前 guard 报告对象。
        :return: 包含 ok、failed_checks 和 details 的报告字典。
        """

        # 返回字段名保持 formatter quality report 的既有契约
        return {
            "ok": self.ok,
            "failed_checks": self.failed_checks,
            "details": self.details,
        }

# 模块名提取前先去注释，避免注释里的 module 文本干扰结构保护
def _module_names(source: str) -> list[str]:
    """
    提取 RTL 源码中的真实 module 名称。

    :param source: 待检查的 Verilog/SystemVerilog 源码文本。
    :return: 按出现顺序排列的 module 名称列表。
    """

    # 去掉注释后的文本用于匹配真实 module 声明
    str_stripped_source = _strip_comments(source)  # 去注释后的 RTL 文本

    # 返回格式化前后需要保持一致的 module 名称列表
    return re.findall(r"\bmodule\s+([A-Za-z_][A-Za-z0-9_$]*)\b", str_stripped_source)

# 注释剥离只服务 guard，不改变 formatter 主流程文本
def _strip_comments(source: str) -> str:
    """
    移除 RTL 注释以便 guard 提取结构声明。

    :param source: 包含代码和注释的 RTL 源码文本。
    :return: 去掉块注释和行注释后的源码文本。
    """

    # 先移除块注释，避免其中的换行和 module 文本进入后续匹配
    without_block_comments = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)  # 去块注释文本

    # 再移除行注释，保留真实代码区域供结构提取
    return re.sub(r"//.*", "", without_block_comments)

# 预处理指令需要在格式化前后保持存在，避免破坏编译环境
def _preprocessor_directives(source: str) -> list[str]:
    """
    收集影响 Verilog 编译边界的预处理指令。

    :param source: 待检查的 Verilog/SystemVerilog 源码文本。
    :return: 原始顺序保留的关键预处理指令行。
    """

    # 只跟踪会影响编译语义或条件编译边界的常见预处理指令
    pattern_preprocessor_directive: re.Pattern[str] = re.compile(  # 影响编译语义的预处理指令匹配器
        r"^`(?:timescale|default_nettype|include|define|undef|ifdef|ifndef|elsif|else|endif|celldefine|endcelldefine)\b"  # 受保护的 Verilog 指令集合
    )

    # 返回源码中出现的预处理指令行，顺序用于后续缺失检查
    return [line.strip() for line in source.splitlines() if pattern_preprocessor_directive.match(line.strip())]

# guard_formatted_text 是 formatter 写出前的最后结构保护门
def guard_formatted_text(source: str, formatted: str, *, action: str, decision: str) -> GuardReport:
    """
    检查 formatter 输出是否保留关键 RTL 结构。

    :param source: 格式化前的 RTL 文本。
    :param formatted: formatter 产出的 RTL 文本。
    :param action: formatter 决策动作名称。
    :param decision: formatter 路由决策名称。
    :return: 描述模块名和预处理指令保护结果的 GuardReport。
    """

    # failed_checks 记录结构保护失败的检查项名称
    list_failed_checks: list[str] = []  # 失败的 guard 检查项

    # details 保留 action/decision，方便报告定位是哪个路由分支触发保护
    dict_details: dict[str, object] = {
        "action": action,  # formatter 路由动作名称
        "decision": decision,  # formatter 路由决策名称
    }  # guard 诊断详情

    # module 名称必须在格式化前后完全一致
    list_source_modules = _module_names(source)  # 原始 RTL module 名称

    # formatted module 名称用于与原始文本做结构对比
    list_formatted_modules = _module_names(formatted)  # 格式化后 module 名称

    # 记录 module 对比详情，便于质量报告展示差异
    dict_details["source_modules"] = list_source_modules  # 原始 module 名称列表

    # 记录 formatter 输出中的 module 名称
    dict_details["formatted_modules"] = list_formatted_modules  # 格式化后 module 名称列表

    # module 名称变化说明 formatter 可能误改了 RTL 结构
    if list_source_modules != list_formatted_modules:

        # 标记 module_names guard 失败
        list_failed_checks.append("module_names")

    # 提取原始文本中的预处理指令，后续检查是否被 formatter 丢失
    list_source_directives = _preprocessor_directives(source)  # 原始预处理指令

    # 提取 formatter 输出中的预处理指令
    list_formatted_directives = _preprocessor_directives(formatted)  # 格式化后预处理指令

    # 写入原始预处理指令详情
    dict_details["source_preprocessor_directives"] = list_source_directives  # 原始预处理指令列表

    # formatter 结果里的指令列表用于确认编译条件没有被删掉
    dict_details["formatted_preprocessor_directives"] = list_formatted_directives  # 格式化后预处理指令列表

    # 只检查原始指令是否缺失，不要求 formatter 保持完全相同排序之外的额外指令
    list_missing_directives = [
        directive  # 原始文本中需要在格式化后继续存在的指令
        for directive in list_source_directives  # 原始 RTL 中出现过的预处理指令
        if directive not in list_formatted_directives  # 格式化后缺失时纳入失败证据
    ]  # 格式化后缺失的预处理指令

    # 缺失指令详情会直接支撑写回前的结构保护报告
    dict_details["missing_preprocessor_directives"] = list_missing_directives  # 缺失的预处理指令列表

    # 预处理指令缺失会影响编译语义，必须标记 guard 失败
    if list_missing_directives:

        # 缺失编译指令会阻止 formatter 将结果安全写回
        list_failed_checks.append("preprocessor_directives")

    # ok 字段在无失败检查项时才为 True
    return GuardReport(ok=not list_failed_checks, failed_checks=list_failed_checks, details=dict_details)
