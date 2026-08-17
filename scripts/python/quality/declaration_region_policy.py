"""统一 formatter 与区域门禁使用的声明归属优先级。"""

# future annotations 延迟解析区域策略的容器类型。
from __future__ import annotations

# re 仅在结构引用尚未丰富时确认简单 actual 标识符，Mapping 接收异构事实。
import re
from typing import Any, Mapping

# 声明同时命中多个类别时按此顺序选择唯一归属。
DECLARATION_REGION_PRIORITY = (  # 冻结声明区域冲突消解顺序
    "output_internal",  # 输出代理信号优先于其他语义前缀
    "counter_signal",  # 计数用途优先于普通寄存器类别
    "state_signal",  # 状态信号保持独立区域
    "flag_signal",  # 控制标志优先于底层声明类型
    "encoder_signal",  # 编码中间量进入编码区域
    "decoder_signal",  # 译码中间量进入译码区域
    "register_signal",  # 普通寄存器在实例连接之前判定
    "instance_signal",  # 实例 actual 引用归入连接信号区域
    "other_signal",  # 未命中专用语义时使用兜底区域
)

# 内部区域键与 formatter 中文横幅共享同一标题表。
DECLARATION_REGION_TITLES = {  # 区域规则报告使用的稳定标题映射
    "output_internal": "输出信号",  # 输出代理信号横幅标题
    "counter_signal": "计数信号",  # 计数变量横幅标题
    "state_signal": "状态机信号",  # 状态寄存器横幅标题
    "flag_signal": "标志信号",  # 控制标志横幅标题
    "encoder_signal": "编码信号",  # 编码路径横幅标题
    "decoder_signal": "译码信号",  # 译码路径横幅标题
    "register_signal": "寄存器信号",  # 普通寄存器横幅标题
    "instance_signal": "模块实例化信号",  # 子模块连接横幅标题
    "other_signal": "其他信号",  # 其余内部信号横幅标题
}

# resolve_declaration_region 按配置和结构事实返回单一声明区域。
def resolve_declaration_region(
    str_name: str,
    str_kind: str,
    set_output_signal_names: set[str],
    set_instance_signal_names: set[str],
    dict_naming: Mapping[str, Any],
) -> str:
    """按固定冲突优先级返回声明的唯一内部区域键。

    参数:
        str_name: formatter 已解析的变量声明名称。
        str_kind: 声明类型，如 reg、wire 或 integer。
        set_output_signal_names: 由模块输出端口映射得到的内部信号集合。
        set_instance_signal_names: 由实例 actual 引用得到的连接信号集合。
        dict_naming: 风格资产中的命名前缀与输出后缀配置。
    返回:
        `DECLARATION_REGION_PRIORITY` 中的一个区域键。
    数组形状、数据类型与单位:
        shape=不适用，dtype=str，unit=无；集合成员均为字符串标识符。
    """

    # 小写副本只用于不区分大小写的语义前缀比较。
    str_lowered_name = str_name.lower()  # 当前声明名称的小写匹配文本

    # 输出后缀缺失时保持 formatter 的既有 `_o` 合同。
    str_output_suffix = str(dict_naming.get("internal_output_suffix") or "_o")  # 内部输出识别后缀

    # 输出端口映射或 `_o` 后缀具有最高归属优先级。
    if str_name in set_output_signal_names or str_name.endswith(str_output_suffix):

        # 返回输出代理区域，禁止后续前缀覆盖该结论。
        return "output_internal"

    # 专用语义前缀按批准顺序绑定到各自区域。
    tuple_prefix_regions = (  # 配置键、兼容默认值和目标区域的有序映射
        ("counter_prefix", "cnt_", "counter_signal"),  # 计数器前缀归属
        ("state_signal_prefix", "state_", "state_signal"),  # 状态信号前缀归属
        ("flag_prefix", "flag_", "flag_signal"),  # 控制标志前缀归属
        ("encoder_prefix", "enc_", "encoder_signal"),  # 编码变量前缀归属
        ("decoder_prefix", "dec_", "decoder_signal"),  # 译码变量前缀归属
        ("register_prefix", "reg_", "register_signal"),  # 普通寄存器前缀归属
    )

    # 顺序扫描确保同一名称只采用最高优先级的专用前缀。
    for str_config_key, str_fallback, str_region in tuple_prefix_regions:

        # 每个前缀都允许规则资产覆盖，同时保留安装兼容默认值。
        str_prefix = str(dict_naming.get(str_config_key) or str_fallback).lower()  # 当前类别匹配前缀

        # 完整前缀命中即可确定专用区域。
        if str_lowered_name.startswith(str_prefix):

            # 返回当前最高优先级命中的区域键。
            return str_region

    # 没有专用前缀的 reg 仍属于普通寄存器区域。
    if str_kind.lower() == "reg":

        # 声明类型证据优先于实例连接用途。
        return "register_signal"

    # 非 reg 信号只有真实出现在实例 actual 中才属于连接区域。
    if str_name in set_instance_signal_names:

        # 返回模块实例化信号区域。
        return "instance_signal"

    # 未命中任何专用证据时使用唯一兜底区域。
    return "other_signal"

# instance_signal_names_from_module 收集实例 actual 的结构化引用。
def instance_signal_names_from_module(dict_module: Mapping[str, Any]) -> set[str]:
    """从 formatter 实例关联事实收集模块连接信号名称。

    参数:
        dict_module: 含 `instances.port_associations.actual.references` 的模块事实。
    返回:
        当前模块全部非空实例 actual 引用名称集合。
    """

    # 集合去除同一连接信号在多个端口关联中的重复出现。
    set_names: set[str] = set()  # 当前模块的实例连接信号名称

    # 每个实例都只读取 formatter 已确认的结构化端口关联。
    for dict_instance in dict_module.get("instances", []) or []:

        # 命名门禁只需要端口 actual，不把参数覆盖当作信号声明。
        for dict_association in dict_instance.get("port_associations", []) or []:

            # actual 字典承载表达式解析器提取的引用集合。
            dict_actual = dict_association.get("actual") or {}  # 当前端口连接的 actual 事实

            # 已丰富表达式优先提供完整引用集合。
            list_references = list(dict_actual.get("references", []) or [])  # 当前 actual 的结构化引用

            # AST 初次序列化发生在表达式丰富之前，此时只接受单一标识符 actual。
            if not list_references:

                # text 字段来自实例 association parser，不重新切分任意表达式。
                str_actual_text = str(dict_actual.get("text") or "").strip()  # 当前 actual 原始结构文本

                # 简单标识符可作为唯一引用，复杂表达式继续等待表达式事实丰富。
                if re.fullmatch(r"[A-Za-z_]\w*", str_actual_text):

                    # 单一标识符 actual 的引用集合只有自身。
                    list_references.append(str_actual_text)

            # 一个表达式可以引用多个模块内部信号。
            for str_reference in list_references:

                # 空引用不是可用于区域归属的变量名称。
                if str_reference:

                    # 保存规范字符串，供声明分类器执行精确成员判断。
                    set_names.add(str(str_reference))

    # 返回去重后的实例连接信号集合。
    return set_names

# declaration_region_title 把内部键转换成公开中文标题。
def declaration_region_title(str_region: str) -> str:
    """返回指定声明区域键对应的稳定中文横幅标题。

    参数:
        str_region: `DECLARATION_REGION_TITLES` 中的内部区域键。
    返回:
        区域门禁和 formatter 共同使用的中文标题。
    异常:
        KeyError: 调用方传入未登记区域键时抛出。
    """

    # 精确索引让未知区域键立即失败，禁止静默归入其他信号。
    return DECLARATION_REGION_TITLES[str_region]
