"""工作流 mock provider 的 RTL 生成辅助逻辑。"""

# future annotations 让 RTL helper 的类型互引保持延迟求值
from __future__ import annotations

# 格式化伪路径仍然沿用 pathlib
from pathlib import Path

# RTL helper 需要数据类和最小类型注解
from dataclasses import dataclass
from typing import Any, cast

# 共享 header 合同用于统一 mock provider、formatter 与测试夹具的字面输出。
from ..header_contract import default_header_paths, reference_dependency_blocks, render_bilingual_header

# 文本宽度对齐沿用 formatter backend 的现有实现
from scripts.python.quality.formatter_backend.banners import display_width

# 端口契约类型
from .model_provider import MockPortSpec

# RTL 命名与内部输出注释 helper
from .model_provider_mock_comments import (
    _internal_output_name,
    _mock_internal_output_comment,
)

# 端口与桥接语义说明 helper
from .model_provider_mock_comments import (
    _mock_output_bridge_comment,
    _mock_port_comment,
)

# 前缀注释、语义注释和未使用输出说明 helper
from .model_provider_mock_comments import (
    _mock_prefix_comments,
    _mock_semantic_comment,
    _mock_unused_output_comment,
)

# 位宽与常零字面量工具单独分组，避免导入块过密
from .model_provider_mock_comments import _width_text, _zero_literal

@dataclass(frozen=True)
class MockPortLayout:
    """汇总 mock RTL 与 testbench 共同依赖的端口语义。"""

    # 顶层模块名。
    top: str  # 当前 mock 设计的模块名

    # 全量有效端口。
    ports: list[MockPortSpec]  # 参与模块声明的端口列表

    # 工作时钟名；组合型 mock 保持空字符串。
    clock_name: str  # 时序块使用的时钟端口

    # 工作复位名；组合型 mock 保持空字符串。
    reset_name: str  # 时序块使用的复位端口

    # 普通输入端口。
    inputs: list[MockPortSpec]  # 除时钟复位外的输入端口

    # 普通输出端口。
    outputs: list[MockPortSpec]  # 需要桥接或观测的输出端口

    # 代表性数据输入。
    data_input: MockPortSpec | None  # 优先承载 data 语义的输入口

    # 代表性 valid 输入。
    valid_input: MockPortSpec | None  # 触发采样的 valid 输入口

    # 代表性数据输出。
    data_output: MockPortSpec | None  # 优先承载 data 语义的输出口

    # 代表性 valid 输出。
    valid_output: MockPortSpec | None  # 用来观察输出握手状态的 valid 端口

    # 内部数据输出寄存器名。
    data_output_internal: str  # 数据输出桥接前的内部寄存器名

    # 内部 valid 输出寄存器名。
    valid_output_internal: str  # valid 输出桥接前的内部寄存器名

    # 外部数据输出端口名。
    str_data_output_name: str  # 模块数据输出口名

    # 外部 valid 输出端口名。
    str_valid_output_name: str  # 模块 valid 输出口名

    # 是否需要独立 valid 输出。
    bool_has_distinct_valid_output: bool  # data 输出与 valid 输出是否分离

    # 输入数据缓存寄存器名。
    str_data_register_name: str = "reg_data_hold"  # 输入数据保持寄存器

    # 输入 valid 缓存寄存器名。
    str_valid_register_name: str = "flag_valid_hold"  # 输入有效保持寄存器

# mock provider 的双语 header 统一复用共享合同，避免 fixture 和 formatter 再次分叉。
def _mock_erie_header_text(str_module_name: str) -> str:
    """
    返回 mock provider 使用的固定双语 header 文本。

    参数:
        str_module_name: 当前 mock RTL 的模块名称。
    返回:
        返回不带尾随空行的稳定 header 文本。
    """

    # 先按模块名重建英中双语的固定 Description/Simulations 路径。
    dict_header_paths = default_header_paths(str_module_name)  # 双语 header 固定路径集合

    # mock provider 默认输出 None 模式的 References/Dependencies 区块。
    dict_reference_dependency_block = reference_dependency_blocks(mode="none")  # mock provider 缺省使用 None 模式头部区块

    # 统一用共享 header renderer 生成 mock RTL 的稳定头部行列表。
    list_header_lines = render_bilingual_header(  # 共享 header renderer 输出行列表
        english_values={  # English 段固定字段取值
            "copyright_owner": "Erie",  # English 段 Company 默认公司名
            "developer": "Erie",  # English 段 Engineer 默认开发人员
            "create_date": "2026/05/03 12:00:00",  # English 段 Create Date 示例时间
            "design_name": str_module_name,  # English 段 Design Name 使用当前模块名
            "module_name": str_module_name,  # 让 Module Name 与最终 module 声明共享同一名称
            "description": str(dict_header_paths["english"]["description"]),  # English 段 Description 固定路径
            "simulations": str(dict_header_paths["english"]["simulations"]),  # English 段 Simulations 固定小写路径
            "version": "V1.0",  # English 段 Version 示例值
            "revision_date": "2026/05/03 12:00:00",  # 固定英文修订时间以验证 Revision Date 行格式
        },
        chinese_values={  # 中文段与英文段对照的固定字段取值
            "copyright_owner": "Erie",  # 中文段版权归属默认公司名
            "developer": "Erie",  # 中文段开发人员默认署名
            "create_date": "2026年05月03日",  # 中文段创建日期示例值
            "design_name": str_module_name,  # 中文段设计名称使用当前模块名
            "module_name": str_module_name,  # 中文段模块名称使用当前模块名
            "description": str(dict_header_paths["chinese"]["description"]),  # 中文段模块说明固定路径
            "simulations": str(dict_header_paths["chinese"]["simulations"]),  # 中文段仿真工程固定路径
            "version": "V1.0",  # 中文段当前版本示例值
            "revision_date": "2026年05月03日",  # 中文段修订日期示例值
        },
        english_history_lines=["2026/05/03       V1.0        Erie              Create file."],  # English 历史示例行
        chinese_history_lines=["2026年05月03日   V1.0        Erie              创建文件"],  # 中文历史示例行
        reference_dependency_block=dict_reference_dependency_block,  # None 模式参考资料与依赖区块
        include_timescale=True,  # mock provider 头部保留首行 timescale
    )

    # 返回不带尾随空行的稳定双语 header 文本。
    return "\n".join(list_header_lines)

# 时序型 mock RTL 模板所需的派生文本。
@dataclass(frozen=True)
class MockSequentialRtlParts:
    """保存时序型 mock RTL 模板中反复复用的派生文本。"""

    # module 端口列表文本。
    port_block: str  # module 端口块正文

    # DATA_WIDTH 参数默认值。
    data_width: int  # mock RTL 数据参数位宽

    # 输出寄存器声明文本。
    output_decl_block: str  # 输出寄存器声明区域

    # 输出 assign 桥接文本。
    assign_block: str  # 输出端口 assign 区域

    # 输入数据缓存寄存器的 Verilog 声明行。
    data_register_decl: str  # reg_data_hold 缓存采样数据的声明文本

    # 输入有效缓存标志的 Verilog 声明行。
    valid_register_decl: str  # flag_valid_hold 锁存有效状态的声明文本

    # 输出处理区完整文本。
    output_processing_block: str  # 按接口顺序渲染的输出处理区域

    # 输入数据采样表达式。
    data_sample_expr: str  # 数据缓存寄存器采样源

    # valid 采样表达式。
    valid_sample_expr: str  # valid 缓存寄存器采样源

# 准备时序型 mock RTL 模板的派生文本。
def _mock_rtl_parts(layout: MockPortLayout) -> MockSequentialRtlParts:
    """
    计算时序型 mock RTL 模板需要复用的端口、位宽和赋值片段。

    :param layout: 已整理好的 mock 端口语义布局。
    :return: 供时序 RTL 模板直接插值的派生文本集合。
    """

    # 模块端口块需要按时钟、复位、业务端口顺序展示。
    list_ordered_ports = _ordered_mock_ports(layout.ports)  # 时钟复位优先的端口顺序

    # str_port_block 保留 formatter 处理前的端口声明区域文本。
    str_port_block = _mock_port_block(list_ordered_ports)  # module 声明端口正文

    # 数据输出保持寄存器优先沿用输出口位宽。
    str_data_register_width = _width_text(layout.data_output or layout.data_input)  # 数据保持寄存器位宽前缀

    # 独立 valid 输出保持寄存器优先沿用 valid 输出口位宽。
    dict_valid_width_port = layout.valid_output or layout.valid_input  # valid 保持寄存器位宽来源

    # str_valid_register_width 表示 valid 内部寄存器声明位宽。
    str_valid_register_width = _width_text(dict_valid_width_port)  # valid 保持寄存器位宽前缀

    # str_data_sample_expr 显式处理输入与缓存寄存器之间的位宽差异。
    str_data_sample_expr = _mock_data_sample_expr(layout)  # 数据缓存采样表达式

    # str_valid_sample_expr 在缺少 valid 输入时退化为常高，避免 mock 链路停摆。
    str_valid_sample_expr = _mock_port_name_or_default(layout.valid_input, "1'b1")  # valid 缓存采样表达式

    # DATA_WIDTH 参数优先继承业务数据端口位宽。
    dict_data_width_port = layout.data_output or layout.data_input or {"width": 8}  # 数据参数位宽来源

    # int_data_width 是模板里 C_DATA_WIDTH 的默认值。
    int_data_width = int(dict_data_width_port.get("width", 8))  # 从业务数据端口提取的参数化数据位宽

    # 输出声明区域同时覆盖 data 和可选 valid 内部寄存器。
    str_output_decl_block = _mock_output_decl_block(layout, str_data_register_width, str_valid_register_width)  # 输出保持寄存器声明文本

    # assign 区域把内部保持寄存器桥接回用户输出口。
    str_assign_block = _mock_output_assign_block(layout)  # 用户输出口桥接 assign 文本

    # str_data_register_decl 是输入数据缓存的完整声明行。
    str_data_register_decl = (  # 输入数据缓存寄存器 RTL 声明
        f"\treg {str_data_register_width}{layout.str_data_register_name} = DATA_RESET_VALUE;"
        "\t//输入数据缓存寄存器"
    )

    # str_valid_register_decl 是输入 valid 缓存的完整声明行。
    str_valid_register_decl = (  # 输入有效缓存寄存器 RTL 声明
        f"\treg {str_valid_register_width}{layout.str_valid_register_name} = 1'b0;"
        "\t//输入有效缓存标志"
    )

    # 输出保持语句单独生成，避免模板源代码行过长。
    str_data_output_hold_assignment = (  # 输出数据保持分支赋值语句
        f"\t\t\t{layout.data_output_internal} <= "
        f"{layout.data_output_internal};\t//无有效输入时保持输出数据"
    )

    # 输出处理区要按外部接口顺序和分组标签统一渲染。
    str_output_processing_block = _mock_output_processing_block(  # 输出处理区域完整文本
        layout,  # 当前 mock 端口布局
        str_data_output_hold_assignment,  # 数据输出保持分支赋值语句
    )

    # 返回模板插值所需的派生文本集合。
    return MockSequentialRtlParts(
        # 模块声明和参数默认值。
        port_block=str_port_block,
        data_width=int_data_width,

        # 输出端口保持逻辑。
        output_decl_block=str_output_decl_block,
        assign_block=str_assign_block,
        output_processing_block=str_output_processing_block,

        # 输入缓存声明和采样表达式。
        data_register_decl=str_data_register_decl,
        valid_register_decl=str_valid_register_decl,
        data_sample_expr=str_data_sample_expr,
        valid_sample_expr=str_valid_sample_expr,
    )

# 生成数据缓存寄存器的位宽安全采样表达式。
def _mock_data_sample_expr(layout: MockPortLayout) -> str:
    """根据数据输入与缓存寄存器位宽生成显式 Verilog 表达式。

    :param layout: 已整理好的 mock 端口语义布局。
    :return: 与数据缓存寄存器位宽一致的采样表达式。
    """

    # 没有数据输入时沿用参数化复位值，避免生成空信号名。
    if layout.data_input is None:

        # 返回参数化复位值作为无输入场景的有效表达式。
        return "DATA_RESET_VALUE"

    # 缓存寄存器优先继承数据输出口位宽，与模板声明保持一致。
    dict_target_port = layout.data_output or layout.data_input  # 数据缓存寄存器位宽来源

    # 读取数据输入位宽以判断是否需要显式扩展或截断。
    int_input_width = int(layout.data_input.get("width", 1) or 1)  # 数据输入位宽

    # 读取缓存寄存器目标位宽以生成定宽表达式。
    int_target_width = int(dict_target_port.get("width", 1) or 1)  # 数据缓存寄存器目标位宽

    # 提取端口名称供最终 Verilog 表达式直接引用。
    str_input_name = str(layout.data_input["name"])  # 数据输入端口名称

    # 等宽路径直接采样端口，保留最简 RTL。
    if int_input_width == int_target_width:

        # 返回原端口名，避免为等宽路径增加冗余运算。
        return str_input_name

    # 窄输入使用定宽零值参与按位或，显式把表达式扩展到目标位宽。
    if int_input_width < int_target_width:

        # 返回目标位宽的显式零扩展表达式。
        return f"{_zero_literal(int_target_width)} | {str_input_name}"

    # 宽输入显式截取低位，避免依赖隐式截断语义。
    return f"{str_input_name}[{int_target_width - 1}:0]"

# 读取 mock 端口名称或返回兜底表达式。
def _mock_port_name_or_default(port: MockPortSpec | None, default_expr: str) -> str:
    """
    从端口字典中读取名称，端口缺失时返回指定兜底表达式。

    :param port: 候选 mock 端口。
    :param default_expr: 端口缺失时使用的 Verilog 表达式。
    :return: 端口名或兜底表达式。
    """

    # 缺失端口时不能生成空信号名。
    if port is None:

        # 返回调用方指定的有效兜底表达式。
        return default_expr

    # 返回端口名称供模板直接插值。
    return str(port["name"])

# 生成 mock DUT RTL 文本
def _mock_erie_rtl_source_text(spec: dict[str, Any]) -> str:
    """
    根据 spec 生成最小可审查的 Erie 风格 RTL 模板。

    :param spec: 含端口定义与模块名的规范化规格。
    :return: 经过可选 formatter 处理的 Verilog 源码文本。
    """

    # 汇总 DUT 与 testbench 共用的端口语义。
    mock_port_layout_snapshot = _build_mock_port_layout(spec)  # 当前规范对应的端口布局快照

    # 纯组合规范不生成伪造的时钟复位与寄存器链路。
    if not _layout_has_sequential_controls(mock_port_layout_snapshot):

        # 组合规范走专门的 assign 版 mock 模板。
        return _mock_erie_comb_source_text(mock_port_layout_snapshot)

    # 把模板中复用的派生文本集中计算，避免主模板函数承担路径推断细节。
    mock_sequential_rtl_parts_mock_rtl_parts = _mock_rtl_parts(  # 门禁命名认可的派生值容器
        mock_port_layout_snapshot  # 当前时序型 DUT 端口布局
    )

    # 三引号模板内部使用短别名，避免 Verilog 占位符行过长。
    mock_rtl_parts = mock_sequential_rtl_parts_mock_rtl_parts  # RTL 模板插值短别名

    # 数据缓存寄存器名单独缩短，避免模板中的占位符物理行过长。
    str_data_register_name = mock_port_layout_snapshot.str_data_register_name  # 输入数据缓存寄存器短别名

    # valid 缓存寄存器名也缩成局部别名，保持模板段落可读。
    str_valid_register_name = mock_port_layout_snapshot.str_valid_register_name  # 输入有效缓存寄存器短别名

    # 拼接完整的原始 RTL 模板。
    raw_rtl = f"""{_mock_erie_header_text(mock_port_layout_snapshot.top)}

module {mock_port_layout_snapshot.top}
#(
\tparameter C_DATA_WIDTH = 32'd{mock_rtl_parts.data_width}\t//数据总线位宽
)
(
{mock_rtl_parts.port_block}
);

\t//---------------配置参数区域---------------//
\t//默认配置参数
\tlocalparam DATA_RESET_VALUE = {{C_DATA_WIDTH{{1'b0}}}};\t//数据复位默认值

\t//----------------寄存器信号----------------//
\t//输入缓存寄存器
{mock_rtl_parts.data_register_decl}

\t//-----------------标志信号-----------------//
\t//握手缓存标志
{mock_rtl_parts.valid_register_decl}

\t//-----------------输出信号-----------------//
{mock_rtl_parts.output_decl_block}

\t//---------------输出信号连线---------------//
{mock_rtl_parts.assign_block}
{mock_rtl_parts.output_processing_block}

\t//-------------主要任务处理区域-------------//
\t//输入数据缓存寄存器更新逻辑
\talways@(posedge {mock_port_layout_snapshot.clock_name} or negedge {mock_port_layout_snapshot.reset_name})begin
\t\tif({mock_port_layout_snapshot.reset_name} == 1'b0)begin
\t\t\t{str_data_register_name} <= DATA_RESET_VALUE;\t//复位时清空输入数据缓存
\t\tend else if({mock_rtl_parts.valid_sample_expr} == 1'b1)begin
\t\t\t{str_data_register_name} <= {mock_rtl_parts.data_sample_expr};\t//输入有效时缓存输入数据
\t\tend else begin
\t\t\t{str_data_register_name} <= {str_data_register_name};\t//输入无效时保持缓存数据
\t\tend
\tend

\t//输入有效缓存标志更新逻辑
\talways@(posedge {mock_port_layout_snapshot.clock_name} or negedge {mock_port_layout_snapshot.reset_name})begin
\t\tif({mock_port_layout_snapshot.reset_name} == 1'b0)begin
\t\t\t{str_valid_register_name} <= 1'b0;\t//复位时清除输入有效缓存
\t\tend else begin
\t\t\t{str_valid_register_name} <= {mock_rtl_parts.valid_sample_expr};\t//锁存当前输入有效状态
\t\tend
\tend

endmodule
"""

    # 输出 formatter 归一化后的 RTL 文本。
    return _normalize_mock_erie_rtl(raw_rtl, mock_port_layout_snapshot.top)

# 汇总 mock RTL 的端口布局
def _build_mock_port_layout(spec: dict[str, Any]) -> MockPortLayout:
    """
    提取 mock RTL 与 testbench 共用的端口语义布局。

    :param spec: 含模块名、端口表与工作流接口的规范化规格。
    :return: 经过语义筛选后的端口布局对象。
    """

    # 读取 mock 模块名。
    str_top = str(spec.get("name") or "rtl_module")  # 当前 mock 模块名

    # 读取原始端口候选集合。
    list_raw_ports = spec.get("interfaces", {}).get("ports", [])  # interfaces 中声明的原始端口列表

    # 提取具名端口列表。
    list_ports: list[MockPortSpec] = []  # 已通过具名筛选的 mock 端口集合

    # 只保留具备端口名的字典项，并把它们收敛成当前 mock 端口类型。
    for item in list_raw_ports:

        # 只有具备端口名的字典项才参与后续 mock 端口语义推断。
        if isinstance(item, dict) and item.get("name"):

            # 当前路径只读取 MockPortSpec 里约定的最小字段集合。
            list_ports.append(cast(MockPortSpec, item))

    # 先把端口顺序与 group/section 对齐到 formatter 归一化后的接口真源。
    list_ports = _normalized_mock_layout_ports(str_top, list_ports)  # 对齐模块接口后的 mock 端口集合

    # 筛出普通输入端口。
    list_inputs: list[MockPortSpec] = []  # 用户输入端口

    # 只把非时钟、非复位的输入口纳入普通输入集合。
    for item in list_ports:

        # 满足方向和角色约束时，保留为普通输入端口。
        if str(item.get("direction")) == "input" and item.get("role") not in {"clock", "reset"}:

            # 把当前端口加入普通输入集合。
            list_inputs.append(item)

    # 筛出普通输出端口。
    list_outputs: list[MockPortSpec] = [item for item in list_ports if str(item.get("direction")) == "output"]  # 用户输出端口

    # 优先识别工作时钟名称；纯组合规格保持空值。
    clock_name = next((str(item["name"]) for item in list_ports if item.get("role") == "clock"), "")  # 时钟端口名

    # 优先识别工作复位名称；纯组合规格保持空值。
    reset_name = next((str(item["name"]) for item in list_ports if item.get("role") == "reset"), "")  # 复位端口名

    # 选择最像数据输入的端口。
    data_input = _first_port_by_keyword(list_inputs, "data", fallback_last=False)  # 数据输入端口

    # valid 语义只能由显式 valid 命名端口承担，不能回退成普通数据输入。
    dict_valid_input_port: MockPortSpec | None = _first_port_by_keyword_only(list_inputs, "valid")  # 供握手输入采样路径读取的显式 valid 端口

    # 选择最像数据输出的端口。
    data_output = _first_port_by_keyword(list_outputs, "data", fallback_last=True)  # 数据输出端口

    # valid 输出同样只接受显式 valid 命名，避免把普通状态口误判成握手口。
    dict_valid_output_port: MockPortSpec | None = _first_port_by_keyword_only(list_outputs, "valid")  # 供输出桥接与命名路径读取的显式 valid 端口

    # 推导内部数据寄存器名称。
    data_output_internal = _internal_output_name(str(data_output["name"])) if data_output else "data_o"  # 数据输出的内部寄存器名

    # 推导内部 valid 寄存器名称。
    str_valid_output_internal = (
        _internal_output_name(str(dict_valid_output_port["name"])) if dict_valid_output_port else "valid_o"  # valid 输出对应的内部寄存器名
    )  # valid 输出的内部寄存器名

    # 推导外部数据端口名称。
    str_data_output_name = str(data_output["name"]) if data_output else "o_data"  # 数据输出端口名

    # 推导外部 valid 端口名称。
    str_valid_output_name = str(dict_valid_output_port["name"]) if dict_valid_output_port else "o_valid"  # valid 输出端口名

    # 判断是否需要独立 valid 输出。
    bool_has_distinct_valid_output = bool(dict_valid_output_port and str_valid_output_name != str_data_output_name)  # data 与 valid 是否分离

    # 返回源文件和 testbench 复用的完整端口布局对象。
    return MockPortLayout(
        top=str_top,
        ports=list_ports,
        clock_name=clock_name,
        reset_name=reset_name,

        # 这一组字段描述外部可见的输入输出拓扑。
        inputs=list_inputs,
        outputs=list_outputs,
        data_input=data_input,
        valid_input=dict_valid_input_port,
        data_output=data_output,
        valid_output=dict_valid_output_port,

        # 这一组字段供 mock RTL 生成阶段构造内部寄存器与桥接命名。
        data_output_internal=data_output_internal,
        valid_output_internal=str_valid_output_internal,
        str_data_output_name=str_data_output_name,
        str_valid_output_name=str_valid_output_name,
        bool_has_distinct_valid_output=bool_has_distinct_valid_output,
    )

# 生成用于端口布局推断的最小 module 骨架文本。
def _mock_layout_probe_source(str_top: str, list_ports: list[MockPortSpec]) -> str:
    """
    构造只包含 module 端口声明的最小探针源码。

    参数:
        str_top: 当前 mock 模块名。
        list_ports: 需要参与布局推断的端口列表。
    返回:
        供 formatter 归一化与 AST 提取使用的最小 Verilog 源码。
    """

    # 端口顺序先沿用 mock module 当前真实渲染前的展示次序。
    list_ordered_ports = _ordered_mock_ports(list_ports)  # 端口布局探针使用的显示顺序

    # 探针源码复用与真实 module 相同的端口块渲染逻辑。
    str_port_block = _mock_port_block(list_ordered_ports)  # 端口布局探针的端口声明块

    # 返回只包含端口声明的最小 module 骨架，避免引入无关 body 噪声。
    return f"""module {str_top}
(
{str_port_block}
);

endmodule
"""

# 用 formatter 归一化结果回填 mock 端口的顺序与分组元数据。
def _normalized_mock_layout_ports(str_top: str, list_ports: list[MockPortSpec]) -> list[MockPortSpec]:
    """
    返回与 formatter 归一化接口顺序一致的 mock 端口列表。

    参数:
        str_top: 当前 mock 模块名。
        list_ports: 原始 mock 端口列表。
    返回:
        端口顺序、group 与 section 已对齐 formatter 真源的端口列表。
    """

    # 空端口列表无需进入 formatter 探针路径。
    if not list_ports:

        # 没有端口时直接返回原始空列表。
        return list_ports

    # 探针默认沿用按全局口优先排序后的当前展示顺序。
    list_fallback_ports = _ordered_mock_ports(list_ports)  # formatter 探针失败时回退使用的端口顺序

    # 尝试让 formatter 输出当前端口布局的归一化真源。
    try:

        # 延迟导入 formatter AST，避免普通 helper 路径承担额外导入成本。
        from scripts.python.quality.formatter_ast import normalize_text_with_formatter_ast

        # 构造仅用于端口布局推断的最小 module 源码。
        str_probe_source = _mock_layout_probe_source(str_top, list_ports)  # 当前端口布局探针源码

        # 伪 source_path 只用于 formatter 报告展示，不影响布局语义。
        path_probe_source = Path(f"{str_top}.v")  # 端口布局探针使用的伪源码路径

        # 归一化探针源码，并提取 formatter 二次 AST 报告。
        _, dict_probe_report = normalize_text_with_formatter_ast(  # 当前端口布局探针的 AST 报告
            str_probe_source,  # 当前端口布局探针源码正文
            source_path=path_probe_source,  # 只用于 formatter 报告展示的伪路径
        )

    # 探针失败时保留现有顺序，避免 mock provider 直接失效。
    except Exception:

        # formatter 探针不可用时回退到当前最小稳定顺序。
        return list_fallback_ports

    # modules 缺失时无法提取端口真源，继续使用当前顺序。
    list_modules = dict_probe_report.get("modules") or []  # formatter AST 报告中的 module 列表

    # 没有 module AST 时只能回退到当前端口顺序。
    if not list_modules:

        # 缺少 AST module 结果时不强行猜测端口布局。
        return list_fallback_ports

    # 读取首个 module 的端口 AST，作为后续输出镜像的真实顺序来源。
    list_probe_ports = list_modules[0].get("ports") or []  # formatter AST 暴露的端口条目列表

    # 原始端口名到端口字典的映射用于回填 role、width 等业务字段。
    dict_ports_by_name = {  # 当前探针端口名到原始端口载荷的映射
        str(dict_port.get("name") or ""): dict(dict_port)  # 端口名到原始端口副本的映射项
        for dict_port in list_fallback_ports  # 顺序沿用探针失败时的稳定回退端口表
        if dict_port.get("name")  # 只保留具名端口，避免空键污染映射
    }

    # 记录已经被 formatter AST 回放过的端口名，避免重复追加。
    set_seen_port_names: set[str] = set()  # 已经完成真源回填的端口名集合

    # list_normalized_ports 承接回填好顺序与分组元数据的端口列表。
    list_normalized_ports: list[MockPortSpec] = []  # formatter 归一化后的 mock 端口列表

    # 按 formatter AST 的最终接口顺序回放端口。
    for dict_probe_port in list_probe_ports:

        # 端口名是回填原始业务字段与布局字段的唯一稳定键。
        str_port_name = str(dict_probe_port.get("name") or "")  # 当前 formatter AST 端口名

        # 当前 AST 端口不在原始端口集中时跳过，避免引入合成端口。
        if str_port_name not in dict_ports_by_name:

            # 只接收当前 mock 规格真实声明过的端口。
            continue

        # 复制原始端口载荷，再叠加 formatter 推断出的布局字段。
        dict_port_payload = dict(dict_ports_by_name[str_port_name])  # 当前端口的原始业务字段副本

        # group 字段决定输出镜像区的一级标签。
        dict_port_payload["group"] = str(dict_probe_port.get("group") or "")  # formatter 推断出的 group 标签

        # 这里补齐 section，避免后续声明区和桥接区丢失二级分组语义。
        dict_port_payload["section"] = str(dict_probe_port.get("section") or "")  # formatter 推断出的输出小节标签

        # 当前端口完成回填后追加到最终布局列表。
        list_normalized_ports.append(cast(MockPortSpec, dict_port_payload))

        # 标记当前端口已经按 formatter 顺序回放完成。
        set_seen_port_names.add(str_port_name)

    # formatter 未覆盖到的端口继续按回退顺序补到末尾，避免漏端口。
    for dict_port in list_fallback_ports:

        # 端口名用于判断当前原始端口是否已经被回放。
        str_port_name = str(dict_port.get("name") or "")  # 当前原始端口名

        # 已经按 formatter 顺序回放过的端口不再重复追加。
        if str_port_name in set_seen_port_names:

            # 当前端口已经存在于归一化列表中。
            continue

        # 保留未被 AST 覆盖端口的原始载荷，避免探针边界丢端口。
        list_normalized_ports.append(dict_port)

    # 返回与 formatter 真源顺序对齐后的 mock 端口列表。
    return list_normalized_ports

# 判断当前 mock 端口布局是否具备完整时序控制口。
def _layout_has_sequential_controls(layout: MockPortLayout) -> bool:
    """判断 mock 规范是否显式声明了时钟与复位端口。

    :param layout: mock 端口语义布局对象。
    :return: 同时存在时钟与复位端口时返回 True，否则返回 False。
    """

    # 组合型规范不允许依赖伪造的时钟复位默认名。
    return bool(layout.clock_name and layout.reset_name)

# 判断输出端口是否仍属于组合主数据路径候选。
def _is_comb_primary_output_candidate(output_port: MockPortSpec) -> bool:
    """判断输出端口是否适合作为组合主输出候选。

    :param output_port: 待判定的输出端口描述。
    :return: 不带 parity/flag 语义时返回 True，否则返回 False。
    """

    # 把端口名转成小写后再筛掉 parity/flag 一类状态口。
    str_lowered_name = str(output_port.get("name")).lower()  # 当前输出端口的小写名称

    # 主数据输出不应被 parity 或 flag 一类状态口占位。
    return "parity" not in str_lowered_name and "flag" not in str_lowered_name

# 为组合型 mock 选择主要数据输出端口。
def _select_comb_primary_output(layout: MockPortLayout) -> MockPortSpec | None:
    """在组合型规范里选择最适合承载主数据路径的输出端口。

    :param layout: mock 端口语义布局对象。
    :return: 代表主数据输出的端口；没有输出端口时返回 None。
    """

    # parity/flag 一类单比特状态口不应抢占主数据输出位置。
    list_non_flag_outputs = [item for item in layout.outputs if _is_comb_primary_output_candidate(item)]  # 非标志类输出端口

    # 优先选择多比特数据口，避免把单比特状态口当成主数据路径。
    for output_port in list_non_flag_outputs:

        # 多比特输出更符合组合主数据路径的默认承载角色。
        if int(output_port.get("width", 1) or 1) > 1:

            # 返回首个多比特业务输出口。
            return output_port

    # 非标志输出存在时回退到它们的第一个端口。
    if list_non_flag_outputs:

        # 保持端口原始顺序，避免组合样例的输出重排。
        return list_non_flag_outputs[0]

    # 没有明显业务输出时回退到声明顺序中的第一个输出口。
    if layout.outputs:

        # 输出集合非空时至少返回一个可桥接端口。
        return layout.outputs[0]

    # 完全没有输出端口时让调用方自行走空分支。
    return None

# 为组合型 mock 选择可参与表达式的选择信号与数据输入。
def _is_comb_selector_input(input_port: MockPortSpec) -> bool:
    """判断输入端口是否适合作为组合 selector。

    :param input_port: 待判定的输入端口描述。
    :return: 单比特且名称带 sel/select 语义时返回 True。
    """

    # selector 端口必须是单比特，避免把数据总线误识别成选择信号。
    if int(input_port.get("width", 1) or 1) != 1:

        # 非单比特输入不能承担 selector 角色。
        return False

    # 读取当前输入名称，后续按关键词匹配 selector 语义。
    str_lowered_name = str(input_port.get("name")).lower()  # 当前输入端口的小写名称

    # sel/select 关键词表示当前输入适合作为组合 selector。
    return "sel" in str_lowered_name or "select" in str_lowered_name

# 从组合型输入端口里提取 selector 与两路数据通道。
def _select_comb_input_paths(
    layout: MockPortLayout,
) -> tuple[MockPortSpec | None, MockPortSpec | None, MockPortSpec | None]:
    """为组合型 mock 推断 selector 与两路数据输入。

    :param layout: mock 端口语义布局对象。
    :return: `(selector, first_data, second_data)` 三元组；缺失时对应位置返回 None。
    """

    # 单比特且语义接近 sel/select 的输入优先承担组合选择信号。
    dict_selector_port = next((item for item in layout.inputs if _is_comb_selector_input(item)), None)  # 组合选择信号端口

    # 多比特输入最适合承载组合数据路径。
    list_wide_inputs = [item for item in layout.inputs if int(item.get("width", 1) or 1) > 1]  # 多比特输入端口

    # 至少两路多比特输入时直接使用前两路数据口。
    if len(list_wide_inputs) >= 2:

        # 选择原始顺序中的前两路多比特输入。
        return dict_selector_port, list_wide_inputs[0], list_wide_inputs[1]

    # 只有一路多比特输入时，把它同时当作第一路与回退的第二路输入。
    if len(list_wide_inputs) == 1:

        # 单一路径组合样例继续保持可综合。
        return dict_selector_port, list_wide_inputs[0], list_wide_inputs[0]

    # 没有多比特输入时退回全部输入中的前两路。
    if len(layout.inputs) >= 2:

        # 低配组合样例至少仍可在两个输入间建立简单关系。
        return dict_selector_port, layout.inputs[0], layout.inputs[1]

    # 只有一路输入时把它作为唯一数据源。
    if len(layout.inputs) == 1:

        # 单输入组合样例退化成直接桥接。
        return dict_selector_port, layout.inputs[0], layout.inputs[0]

    # 没有输入端口时保留空值，由调用方输出常零表达式。
    return dict_selector_port, None, None

# 选择组合型 parity 输出端口。
def _select_comb_parity_output(layout: MockPortLayout) -> MockPortSpec | None:
    """
    从组合型输出里选择 parity 语义端口。

    :param layout: mock 端口语义布局对象。
    :return: 显式带 parity 语义的输出端口；缺失时返回 None。
    """

    # parity 输出只接受显式命名，避免把普通单比特状态口误判成奇偶校验口。
    return next(
        (item for item in layout.outputs if "parity" in str(item.get("name")).lower()),
        None,
    )

# 生成组合型 mock RTL 文本。
def _mock_erie_comb_source_text(layout: MockPortLayout) -> str:
    """为无时钟复位的规范生成组合型 mock RTL。

    :param layout: mock 端口语义布局对象。
    :return: 经过 formatter 归一化的组合型 Verilog 源码文本。
    """

    # 组合型模块头仍沿用统一的端口排序和注释渲染逻辑。
    list_ordered_ports = _ordered_mock_ports(layout.ports)  # 组合模块声明的端口顺序

    # 端口块保持与时序 mock 一致的 Erie 样式。
    str_port_block = _mock_port_block(list_ordered_ports)  # 组合模块端口声明文本

    # 主数据输出优先选择多比特业务输出口。
    dict_primary_output = _select_comb_primary_output(layout)  # 主数据输出端口

    # 先拿到组合输入路径三元组，后续再按槽位拆开。
    tuple_input_paths = _select_comb_input_paths(layout)  # 组合输入路径三元组

    # 把首个槽位解释为 selector 输入，供 mux 分支选择使用。
    dict_selector_port = tuple_input_paths[0]  # selector 端口快照

    # 把第二个槽位解释为默认数据输入，供 selector 为 0 的路径复用。
    dict_first_data_input = tuple_input_paths[1]  # 第一条数据输入快照

    # 把第三个槽位解释为高电平 selector 命中的备选数据输入。
    dict_second_data_input = tuple_input_paths[2]  # 第二条数据输入快照

    # parameter 位宽优先跟随主输出，其次跟随输入，再回退到 1。
    dict_width_source = dict_primary_output or dict_first_data_input or {"width": 1}  # 组合模块参数位宽来源

    # 组合主数据路径使用的统一位宽。
    int_data_width = int(dict_width_source.get("width", 1) or 1)  # 组合数据路径位宽

    # 主输出端口名缺失时退回默认名，保证 assign 目标始终存在。
    str_primary_output_name = str(dict_primary_output.get("name")) if dict_primary_output else "o_data"  # 主输出端口名

    # 选择需要 reduction XOR 的 parity 输出端口。
    dict_parity_output = _select_comb_parity_output(layout)  # parity 输出端口

    # 当 selector 和两路输入齐全时生成 mux 表达式，否则退化成单输入直通。
    if (
        dict_selector_port
        and dict_first_data_input
        and dict_second_data_input
        and str(dict_first_data_input.get("name")) != str(dict_second_data_input.get("name"))
    ):

        # 使用 selector 在两路输入间切换，覆盖 remote 组合 fixture 的主要场景。
        str_primary_expr = (  # 组合主输出表达式
            f"{dict_selector_port['name']} ? "
            f"{dict_second_data_input['name']} : "
            f"{dict_first_data_input['name']}"
        )

    # 至少有一路输入时直接桥接到主输出。
    elif dict_first_data_input:

        # 单输入组合样例退化成输入直通。
        str_primary_expr = str(dict_first_data_input["name"])  # 主输出直通表达式

    # 没有输入端口时只能输出固定零值。
    else:

        # 空输入组合模块保持综合可通过。
        str_primary_expr = _zero_literal(int_data_width)  # 主输出常零表达式

    # 构造主输出 assign 语句。
    str_primary_assign_line = f"\tassign {str_primary_output_name} = {str_primary_expr};\t//组合主输出桥接"  # 主数据输出桥接

    # 初始化组合 assign 语句列表。
    list_assign_lines = [str_primary_assign_line]  # 组合输出桥接语句集合

    # parity 输出存在时按主输出做 reduction XOR。
    if dict_parity_output and str(dict_parity_output.get("name")) != str_primary_output_name:

        # parity 输出固定由主输出折叠得到，避免再引入伪造时序状态。
        list_assign_lines.append(
            f"\tassign {dict_parity_output['name']} = ^{str_primary_output_name};\t//奇偶校验输出桥接"
        )

    # 其余未覆盖输出统一拉到零值，保持组合样例闭合。
    for output_port in layout.outputs:

        # 读取当前输出端口名。
        str_output_name = str(output_port.get("name"))  # 当前输出端口名

        # 已覆盖的主输出和 parity 输出不再重复生成 assign。
        if str_output_name in {
            str_primary_output_name,
            str(dict_parity_output.get("name")) if dict_parity_output else "",
        }:

            # 当前输出已经绑定表达式，跳过补零逻辑。
            continue

        # 计算该输出端口需要的零值常量。
        str_zero_literal = _zero_literal(int(output_port.get("width", 1) or 1))  # 其余输出的固定零值

        # 为剩余输出补齐常零桥接。
        list_assign_lines.append(f"\tassign {str_output_name} = {str_zero_literal};\t//未使用输出固定为低电平")

    # 拼接组合型原始 RTL 模板。
    raw_rtl = f"""{_mock_erie_header_text(layout.top)}

module {layout.top}
#(
\tparameter C_DATA_WIDTH = 32'd{int_data_width}\t//数据总线位宽
)
(
{str_port_block}
);

\t//-------------主要任务处理区域-------------//
\t//用户接口
{chr(10).join(list_assign_lines)}

endmodule
"""

    # 组合型源文件同样走统一 formatter 归一化。
    return _normalize_mock_erie_rtl(raw_rtl, layout.top)

# 在端口集合中按关键字选择代表性端口
def _first_port_by_keyword(
    ports: list[MockPortSpec],
    keyword: str,
    *,
    fallback_last: bool,
) -> MockPortSpec | None:
    """
    优先按端口名关键字命中语义端口，未命中时回退边界端口。

    :param ports: 候选端口列表。
    :param keyword: 例如 data 或 valid 这样的语义关键字。
    :param fallback_last: True 表示回退最后一个端口，否则回退第一个端口。
    :return: 语义命中的端口；候选为空时返回 None。
    """

    # 在候选集合里寻找关键字命中项。
    for mock_port_spec_item in ports:

        # 端口名包含关键字时优先返回，保证 data/valid 等语义口先于回退口生效。
        if keyword in str(mock_port_spec_item.get("name")).lower():

            # 输出关键字优先的端口。
            return mock_port_spec_item

    # 候选集合为空时不再构造回退。
    if not ports:

        # 上层按缺省端口处理空结果。
        return None

    # 候选不为空时按 fallback_last 返回首尾端口。
    return ports[-1] if fallback_last else ports[0]

# 在端口集合中只按显式关键字命中，不做首尾回退。
def _first_port_by_keyword_only(
    ports: list[MockPortSpec],
    keyword: str,
) -> MockPortSpec | None:
    """只返回显式命中关键字的端口，未命中时保持空值。

    参数:
        ports: 候选端口列表。
        keyword: 需要显式命中的语义关键字。

    返回:
        命中关键字的首个端口；未命中时返回 `None`。
    """

    # 仅当端口名显式携带目标关键字时才认为语义匹配成功。
    for mock_port_spec_item in ports:

        # 端口名包含关键字时立即返回，避免把普通端口误判成语义端口。
        if keyword in str(mock_port_spec_item.get("name")).lower():

            # 返回当前显式命中的语义端口。
            return mock_port_spec_item

    # 未命中任何显式关键字时保留空值，交给上层走无该语义口的分支。
    return None

# 构造按角色排序的 mock 端口顺序
def _ordered_mock_ports(ports: list[MockPortSpec]) -> list[MockPortSpec]:
    """
    让全局时钟复位端口排在普通用户端口之前。

    :param ports: 原始端口列表。
    :return: 先全局端口、后用户端口的有序列表。
    """

    # 收集时钟和复位端口。
    list_global_ports = [item for item in ports if item.get("role") in {"clock", "reset"}]  # 全局端口

    # 收集普通业务端口。
    list_user_ports = [item for item in ports if item.get("role") not in {"clock", "reset"}]  # 用户端口

    # 返回拼接后的展示顺序。
    return list_global_ports + list_user_ports

# 统一规范 mock RTL 的文本收尾与注释对齐
def _normalize_mock_erie_rtl(raw: str, module_name: str) -> str:
    """
    优先走 formatter AST，对失败场景回退最后一次成功文本或原始文本。

    :param raw: 尚未规范化的 Verilog 文本。
    :param module_name: 仅用于构造伪 source_path 的模块名。
    :return: 去除尾随空白后的 RTL 文本。
    """

    # 默认保留原始 RTL 作为保底结果。
    str_last_successful_rtl = raw  # formatter 失败时的回退文本

    # 尝试调用本地 formatter AST。
    try:
        # 延迟导入 formatter，避免普通路径引入额外开销。
        from scripts.python.quality.formatter_ast import normalize_text_with_formatter_ast

        # 先构造 formatter 使用的伪 source_path，便于报告里显示模块名。
        path_formatter_source = Path(f"{module_name}.v")  # formatter 用来标识当前模块的伪路径

        # 首次 formatter 归一化曾出现过 group/section 标签非幂等收敛。
        int_max_formatter_passes = 3  # 限制 formatter 收敛轮数，避免异常路径无限重试

        # 当前待送入 formatter 的文本从原始 RTL 起步。
        str_candidate_rtl = raw  # 当前归一化候选文本

        # 在有界次数内重跑 formatter，直到文本收敛或遇到失败。
        for _ in range(int_max_formatter_passes):

            # 调用 formatter 规范化当前 RTL 文本。
            str_formatted_rtl, dict_report = normalize_text_with_formatter_ast(  # formatter 返回的文本与执行报告
                str_candidate_rtl,  # 当前轮待收敛的 RTL 文本
                source_path=path_formatter_source,  # 让归一化报告继续绑定当前模块伪路径
            )

            # 失败结果不再继续传播，保留上一轮成功文本。
            if not dict_report.get("ok"):

                # formatter 未成功时直接终止收敛循环。
                break

            # 当前轮次成功时更新最后一次成功文本。
            str_last_successful_rtl = str_formatted_rtl  # 当前最新的成功收敛文本

            # 文本已经稳定时结束额外归一化。
            if str_formatted_rtl == str_candidate_rtl:

                # 当前 formatter 输出已经达到稳定收敛。
                break

            # 尚未收敛时继续以上一轮结果作为下一轮输入。
            str_candidate_rtl = str_formatted_rtl  # 把当前成功结果作为下一轮 formatter 输入

    # formatter 抛异常时继续使用原始文本。
    except Exception:

        # formatter 异常时直接回退到最近一次成功文本的对齐结果。
        return _align_mock_region_comments(str_last_successful_rtl)

    # formatter 未成功给出更好结果时，使用最后一次成功文本的区域注释对齐版本。
    return _align_mock_region_comments(str_last_successful_rtl)

# _align_mock_region_comments 按区域横幅锚点对齐 mock RTL 行尾注释。
def _align_mock_region_comments(text: str) -> str:
    """
    对齐 mock RTL 中区域覆盖范围内的行尾注释。

    :param text: formatter 或原始 mock RTL 文本。
    :return: 区域内行尾注释尽量贴合横幅右侧锚点的 RTL 文本。
    """

    # list_aligned_lines 保存逐行对齐后的文本。
    list_aligned_lines: list[str] = []  # 对齐后的 RTL 行集合

    # int_anchor_column 记录当前区域横幅右侧 // 的显示列。
    int_anchor_column: int | None = None  # 当前区域行尾注释锚点

    # 逐行处理文本。
    for str_line in text.splitlines():

        # 区域横幅刷新后续代码行的锚点。
        int_banner_anchor = _mock_region_banner_anchor_column(str_line)  # 当前行区域横幅锚点

        # 横幅行只刷新锚点，不重排横幅自身。
        if int_banner_anchor is not None:

            # 记录当前区域锚点。
            int_anchor_column = int_banner_anchor  # 当前区域注释锚点显示列

            # 横幅自身不做行尾注释重排。
            list_aligned_lines.append(str_line.rstrip())

        # 不在区域内时只去尾空白。
        elif int_anchor_column is None:

            # 文件头和 module 声明前部不参与区域对齐。
            list_aligned_lines.append(str_line.rstrip())

        # 已进入区域后，对代码行尝试对齐行尾注释。
        else:

            # 当前行按最近区域锚点处理。
            list_aligned_lines.append(_align_mock_line_comment(str_line, int_anchor_column))

    # list_compact_lines 在对齐后恢复 mock 模块体的既有 `//注释` 合同风格。
    list_compact_lines = [_compact_mock_semantic_comment_spacing(str_line) for str_line in list_aligned_lines]  # 恢复 mock 语义注释紧凑前缀后的文本行

    # 统一补末尾换行后，再修正输出连线区分组注释的空行合同。
    return _normalize_mock_assign_group_comment_spacing("\n".join(list_compact_lines) + "\n")

# _normalize_mock_assign_group_comment_spacing 修正输出信号连线区分组注释的空行布局。
def _normalize_mock_assign_group_comment_spacing(text: str) -> str:
    """
    收敛输出信号连线区域纯分组注释的空行布局。

    :param text: 已完成注释对齐的 mock RTL 文本。
    :return: 满足 assign 分组注释空行合同的 RTL 文本。
    """

    # list_normalized_lines 保存逐行收敛后的 RTL 文本。
    list_normalized_lines: list[str] = []  # 输出连线区空行布局修正后的文本行

    # bool_in_output_assign_region 只在输出信号连线区域内启用空行修正。
    bool_in_output_assign_region = False  # 当前是否处于输出信号连线区域

    # 逐行扫描已对齐文本，只在目标区域内做最小空行修正。
    for str_line in text.splitlines():

        # 区域横幅会决定输出信号连线区域的进入和退出。
        int_banner_anchor = _mock_region_banner_anchor_column(str_line)  # 当前行是否为区域横幅

        # 先处理区域横幅，避免普通纯注释误改主区域状态。
        if int_banner_anchor is not None:

            # str_banner_text 只保留横幅正文，便于识别主区域边界。
            str_banner_text = str_line.strip()  # 当前横幅的去缩进文本

            # 输出信号连线横幅开启局部空行修正。
            if "输出信号连线" in str_banner_text:

                # 后续纯分组注释按 assign 子分组规则收敛。
                bool_in_output_assign_region = True  # 进入输出信号连线区域

            # 输出信号处理和主任务区域会结束 assign 分组注释修正。
            elif (
                "输出信号处理区域" in str_banner_text
                or "主要任务处理区域" in str_banner_text
            ):

                # 当前横幅之后不再属于 assign 子分组区域。
                bool_in_output_assign_region = False  # 退出输出信号连线区域

            # 横幅自身只去尾空白，不改写正文。
            list_normalized_lines.append(str_line.rstrip())

            # 当前横幅行已经完成区域状态更新，可以直接读取下一条源码行。
            continue

        # 只有输出信号连线区域内的纯分组注释才需要额外修正。
        if bool_in_output_assign_region and _is_mock_pure_comment_line(str_line):

            # 多行纯注释栈的后续行直接跟随首行，不额外插空行。
            if list_normalized_lines and _is_mock_pure_comment_line(list_normalized_lines[-1]):

                # 连续纯注释属于同一注释栈，保持原有紧邻关系。
                list_normalized_lines.append(str_line.rstrip())

                # 当前注释已经并入同一注释栈，无需再重复做空行修正。
                continue

            # 先清理注释栈前多余空行，保证横幅后零空行、普通代码后最多一空行。
            while list_normalized_lines and not list_normalized_lines[-1].strip():

                # 单独残留的空行没有更多上下文时停止回溯。
                if len(list_normalized_lines) < 2:

                    # 缺少更早上下文时保留当前唯一空行。
                    break

                # 这里取到空行前的真实上下文，用来区分横幅后空行和普通代码后空行。
                str_previous_context = list_normalized_lines[-2]  # 当前空行之前最近的有效上下文行

                # 区域横幅后不允许空行，多余空行要移除。
                if _mock_region_banner_anchor_column(str_previous_context) is not None:

                    # 删除横幅和纯分组注释之间的空行。
                    list_normalized_lines.pop()

                    # 删除横幅后的空行后，要重新检查新的上方上下文是否已经收敛。
                    continue

                # 连续多个空行只保留最后一个。
                if not str_previous_context.strip():

                    # 删除多余的上方空行，继续直到收敛到唯一空行。
                    list_normalized_lines.pop()

                    # 多余空行删掉后继续回看上方上下文，直到只剩唯一空行。
                    continue

                # 已经收敛到“普通代码后一空行”的目标形态。
                break

            # 当前纯分组注释如果前一行是普通代码，需要补齐唯一空行。
            if list_normalized_lines:

                # str_previous_line 用于判断注释栈前是否需要补空行。
                str_previous_line = list_normalized_lines[-1]  # 当前注释栈上一行文本

                # 区域横幅后不补空行，普通代码后补一个空行。
                if (
                    _mock_region_banner_anchor_column(str_previous_line) is None
                    and str_previous_line.strip()
                ):

                    # 让当前分组注释满足“普通代码后恰好一空行”的质量门合同。
                    list_normalized_lines.append("")

            # 追加当前纯分组注释行。
            list_normalized_lines.append(str_line.rstrip())

            # 当前纯分组注释已经完成布局修正，可以进入下一条源码行。
            continue

        # 其他行只去尾空白并按原顺序保留。
        list_normalized_lines.append(str_line.rstrip())

    # 修正后的文本继续保持单个结尾换行。
    return "\n".join(list_normalized_lines) + "\n"

# _compact_mock_semantic_comment_spacing 恢复 mock 模块体语义注释的既有 `//注释` 风格。
def _compact_mock_semantic_comment_spacing(str_line: str) -> str:
    """
    压缩 mock 模块体纯注释行和右侧注释的 `// ` 前缀。

    :param str_line: 已完成对齐的单行 mock RTL 文本。
    :return: 恢复既有语义注释风格后的单行文本。
    """

    # str_compact_line 保存逐步压缩后的行文本。
    str_compact_line = str_line  # 待恢复注释前缀合同的 mock RTL 行

    # 行尾语义注释保留代码前空格，但注释标记回到 `//注释` 形式。
    if " // " in str_compact_line and not str_compact_line.lstrip().startswith("//"):

        # 只压缩首个行尾语义注释前缀，避免误改注释正文。
        str_compact_line = str_compact_line.replace(" // ", " //", 1)  # 行尾语义注释恢复紧凑前缀

    # 缩进后的纯注释行属于模块体语义说明，恢复 `//注释` 形式。
    str_lstripped = str_compact_line.lstrip()  # 去掉缩进后的注释候选文本

    # int_indent_len 区分文件头无缩进行和模块体缩进注释行。
    int_indent_len = len(str_compact_line) - len(str_lstripped)  # 当前行的缩进字符数

    # 只压缩带缩进的纯注释行，文件头 `// 字段` 风格保持不变。
    if int_indent_len > 0 and str_lstripped.startswith("// "):

        # 缩进保留原样，只把注释前缀恢复为紧凑形式。
        str_compact_line = f"{str_compact_line[:int_indent_len]}//{str_lstripped[3:]}"  # 纯注释语义说明恢复紧凑前缀

    # 返回恢复既有 mock 注释风格后的行文本。
    return str_compact_line

# _align_mock_line_comment 对齐单行代码注释。
def _align_mock_line_comment(str_line: str, int_anchor_column: int) -> str:
    """
    对齐一行 mock RTL 的行尾注释。

    :param str_line: 当前 RTL 行。
    :param int_anchor_column: 当前区域注释锚点显示列。
    :return: 对齐后的 RTL 行。
    """

    # int_comment_index 定位真实 // 注释起点。
    int_comment_index = _mock_line_comment_start(str_line)  # 行尾注释起点

    # 无行尾注释、纯注释行或空行不参与对齐。
    if int_comment_index < 0 or not str_line.strip() or str_line.lstrip().startswith("//"):

        # 原样去除尾随空白。
        return str_line.rstrip()

    # str_code 保留需要补齐到锚点前的 Verilog 代码。
    str_code = str_line[:int_comment_index].rstrip()  # 去尾空白后的代码片段

    # str_comment 保留含 // 的完整行尾说明。
    str_comment = str_line[int_comment_index:].strip()  # 行尾注释片段

    # int_code_width 计算代码片段显示宽度。
    int_code_width = _mock_display_width_with_tabs(str_code)  # 注释前代码显示宽度

    # 注释能落在横幅锚点时补齐到锚点。
    if int_code_width < int_anchor_column:

        # str_padding 把当前代码行推到区域横幅锚点列。
        str_padding = " " * (int_anchor_column - int_code_width)  # 区域锚点补齐空白

    # 代码越过锚点时，注释只能紧跟代码后一个空格。
    else:

        # str_padding 保留 Verilog 代码和注释之间的最小间隔。
        str_padding = " "  # 越过锚点后的最小注释间隔

    # 返回对齐后的代码行。
    return f"{str_code}{str_padding}{str_comment}"

# _mock_region_banner_anchor_column 返回 mock 区域横幅右侧 // 的显示列。
def _mock_region_banner_anchor_column(str_line: str) -> int | None:
    """
    返回区域横幅右侧 // 的显示列。

    :param str_line: 当前 RTL 行。
    :return: 横幅右侧 // 显示列；非横幅时返回 None。
    """

    # str_stripped 用于识别标准区域横幅。
    str_stripped = str_line.strip()  # 去缩进后的横幅候选

    # 标准区域横幅必须是双 // 边界并含横线。
    if not (str_stripped.startswith("//") and str_stripped.endswith("//") and "-" in str_stripped):

        # 普通注释不提供区域锚点。
        return None

    # int_anchor_index 是最右侧 // 的原始下标。
    int_anchor_index = str_line.rfind("//")  # 横幅右侧注释边界

    # 没有右侧边界时不作为横幅处理。
    if int_anchor_index <= str_line.find("//"):

        # 非标准横幅不参与对齐。
        return None

    # 返回右侧边界前文本显示宽度。
    return _mock_display_width_with_tabs(str_line[:int_anchor_index])

# _mock_line_comment_start 返回 mock RTL 行中真实 // 起点。
def _mock_line_comment_start(str_line: str) -> int:
    """
    返回未被字符串包裹的 // 起点。

    :param str_line: 当前 RTL 行。
    :return: 真实注释起点；不存在时返回 -1。
    """

    # bool_in_string 表示扫描是否在双引号字符串内。
    bool_in_string = False  # 当前字符串扫描状态

    # bool_escaped 表示当前字符是否被转义。
    bool_escaped = False  # 字符串转义状态

    # 逐字符扫描。
    for int_index, str_char in enumerate(str_line):

        # 被转义字符不参与状态切换。
        if bool_escaped:

            # 转义状态只影响一个字符。
            bool_escaped = False  # 当前转义字符已经消费

            # 已消费转义字面量，后续判断从下一个字符重新开始。
            continue

        # 字符串内反斜杠开启转义。
        if str_char == "\\" and bool_in_string:

            # 下一字符被视为字面量。
            bool_escaped = True  # 下一字符按普通字符处理

            # 转义标记已经建立，当前反斜杠不再参与其他判断。
            continue

        # 双引号切换字符串状态。
        if str_char == '"':

            # 更新字符串状态。
            bool_in_string = not bool_in_string  # 字符串内外状态

            # 引号只改变扫描状态，不会同时作为注释起点。
            continue

        # 字符串内部的普通字符不参与注释判断。
        if bool_in_string:

            # 继续扫描直到离开字符串字面量。
            continue

        # 字符串外的 // 是行注释。
        if str_line.startswith("//", int_index):

            # 返回真实注释起点。
            return int_index

    # 没有真实注释。
    return -1

# _is_mock_pure_comment_line 判断当前行是否为只含缩进和注释的纯注释行。
def _is_mock_pure_comment_line(str_line: str) -> bool:
    """
    判断当前 RTL 行是否为纯注释行。

    :param str_line: 当前 RTL 行。
    :return: 只含缩进和注释时返回 True。
    """

    # 先剔除首尾空白，空行不属于纯注释。
    str_stripped = str_line.strip()  # 去掉首尾空白后的注释候选

    # 空行或非 `//` 起始行都不属于纯注释。
    if not str_stripped or not str_stripped.startswith("//"):

        # 只有 `//` 注释行才参与纯注释判断。
        return False

    # int_comment_index 指向当前行真实 `//` 注释起点。
    int_comment_index = _mock_line_comment_start(str_line)  # 当前行的真实注释起点

    # 没有注释起点或注释前存在非空白代码时，都不是纯注释。
    if int_comment_index < 0 or str_line[:int_comment_index].strip():

        # 代码行尾注释不能按纯分组注释处理。
        return False

    # 当前行只由缩进和纯注释组成。
    return True

# _mock_display_width_with_tabs 计算 mock RTL 显示宽度。
def _mock_display_width_with_tabs(str_text: str) -> int:
    """
    计算包含 Tab 的源码片段显示宽度。

    :param str_text: 需要计算宽度的源码片段。
    :return: Tab 按四列展开后的显示宽度。
    """

    # int_width 累计字符显示宽度。
    int_width = 0  # 当前片段显示宽度

    # 逐字符累计。
    for str_char in str_text:

        # Tab 按 formatter 约定展开为四列。
        if str_char == "\t":

            # 累加 Tab 显示宽度。
            int_width += 4  # Tab 展开后的列宽

        # 非 Tab 字符按中英文显示宽度累计。
        else:

            # 中文宽字符由 banner 工具处理。
            int_width += display_width(str_char)  # 普通字符显示宽度

    # 返回累计宽度。
    return int_width

# 清理每行尾随空白
def _strip_line_trailing_space(text: str) -> str:
    """
    去掉每一行末尾的空白字符并补一个结尾换行。

    :param text: 任意多行文本。
    :return: 行尾无多余空白且以换行结束的文本。
    """

    # 统一规范输出文本结尾，避免后续拼接时丢失末尾换行。
    return "\n".join(line.rstrip() for line in text.splitlines()) + "\n"

# 渲染 module 端口块和输出保持辅助片段
def _mock_port_block(ordered_ports: list[MockPortSpec]) -> str:
    """
    把排序后的端口列表渲染为模块端口声明块。

    :param ordered_ports: 已按显示顺序排序的端口列表。
    :return: 可直接嵌入模块声明的端口块文本。
    """

    # 初始化模块端口块的文本行。
    list_port_lines: list[str] = []  # 模块端口块中的输出行

    # 全局时钟复位存在时先写分组标题。
    if any(item.get("role") in {"clock", "reset"} for item in ordered_ports):

        # 写入全局端口组标题。
        list_port_lines.append("\t//-----------------全局信号-----------------//")

    # 记录用户接口分组是否已经打开。
    bool_user_group_started = False  # 避免重复写入用户端口标题

    # 逐项渲染排序后的端口声明。
    for index, port in enumerate(ordered_ports):

        # 首次遇到普通业务端口时切换到用户接口分组。
        if port.get("role") not in {"clock", "reset"} and not bool_user_group_started:

            # 已有全局端口时先插入分组空行。
            if list_port_lines:

                # 用一个空行隔开端口分组。
                list_port_lines.append("")

            # 写入用户接口组标题。
            list_port_lines.append("\t//-----------------用户接口-----------------//")

            # 标记用户端口分组已经开始。
            bool_user_group_started = True  # 后续普通端口不再重复写组标题

        # 计算当前端口的位宽前缀。
        str_width_text = _width_text(port)  # 端口声明位宽

        # 计算当前端口行的尾随逗号。
        str_trailing_comma = "," if index < len(ordered_ports) - 1 else ""  # 端口尾随逗号

        # 追加当前端口声明行。
        list_port_lines.append(
            f"\t{port['direction']} {str_width_text}{port['name']}{str_trailing_comma}\t//{_mock_port_comment(port)}"
        )

    # 返回拼接后的端口块文本。
    return "\n".join(list_port_lines)

# 构造输出声明块文本
def _mock_output_decl_block(
    layout: MockPortLayout,
    data_register_width: str,
    valid_register_width: str,
) -> str:
    """
    生成 mock DUT 输出寄存器声明块。

    :param layout: 端口布局对象。
    :param data_register_width: 数据输出寄存器位宽前缀。
    :param valid_register_width: valid 输出寄存器位宽前缀。
    :return: 输出寄存器声明块文本。
    """

    # dict_output_decl_lines 按外部输出口名缓存内部寄存器声明。
    dict_output_decl_lines: dict[str, str] = {  # 输出口到内部寄存器声明的映射
        layout.str_data_output_name: (  # 以主数据输出口名作为默认寄存器声明的索引键
            f"\treg {data_register_width}{layout.data_output_internal} = DATA_RESET_VALUE;\t//"
            f"{_mock_internal_output_comment(layout.str_data_output_name)}"
        )
    }

    # 独立 valid 输出要沿外部接口名单独缓存一条寄存器声明。
    if layout.bool_has_distinct_valid_output:

        # 记录独立 valid 输出寄存器声明，后续按接口顺序取出。
        dict_output_decl_lines[layout.str_valid_output_name] = (
            f"\treg {valid_register_width}{layout.valid_output_internal} = 1'b0;\t//"
            f"{_mock_internal_output_comment(layout.str_valid_output_name)}"
        )

    # list_output_lines 负责按接口顺序和分组标签拼接声明区。
    list_output_lines: list[str] = []  # 输出寄存器声明文本行

    # str_current_group_label 防止相同 group/section 标签重复写入。
    str_current_group_label = ""  # 当前声明区已经输出的 group/section 标签

    # 按外部接口输出顺序回放内部寄存器声明。
    for output_port in layout.outputs:

        # 取出当前输出口名，作为声明映射的稳定键。
        str_output_name = str(output_port.get("name") or "")  # 处理区查表使用的输出键

        # 当前输出口没有内部寄存器声明时跳过。
        if str_output_name not in dict_output_decl_lines:

            # 未落内部寄存器的输出口不会出现在输出声明区。
            continue

        # 从当前声明输出口推导输出声明区的镜像标签。
        str_group_label = _mock_output_group_label(output_port)  # 输出声明区分组标签

        # group/section 标签变化时补齐新的分组注释。
        if str_group_label != str_current_group_label:

            # 分组切换前插入空行，保持输出区块可读。
            if list_output_lines:

                # 声明区内分组切换时保留视觉间隔。
                list_output_lines.append("")

            # 写入当前输出寄存器所属的 group/section 标签。
            list_output_lines.append(f"\t//{str_group_label}")

            # 记住声明区最新的小节标签，避免下一条重复落同标题。
            str_current_group_label = str_group_label  # 声明区最新分组标签

        # 追加当前输出口对应的内部寄存器声明。
        list_output_lines.append(dict_output_decl_lines[str_output_name])

    # 返回按接口顺序整理后的输出声明块文本。
    return "\n".join(list_output_lines)

# 构造输出 assign 块文本
def _mock_output_assign_block(layout: MockPortLayout) -> str:
    """
    生成 mock DUT 输出桥接 assign 区域。

    :param layout: 端口布局对象。
    :return: 输出 assign 文本块。
    """

    # list_assign_lines 负责按接口顺序和分组标签拼接输出桥接区。
    list_assign_lines: list[str] = []  # 输出桥接语句文本行

    # str_current_group_label 防止同一分组标题重复输出。
    str_current_group_label = ""  # 当前桥接区的 group/section 标签

    # 按外部输出顺序生成桥接语句。
    for output_port in layout.outputs:

        # 先抽取输出口名字，便于判断该端口的桥接来源。
        str_output_name = str(output_port.get("name") or "")  # 当前输出口名

        # 根据外部输出口归类 assign 区所在的小节。
        str_group_label = _mock_output_group_label(output_port)  # 输出桥接区分组标签

        # 分组标签变化时补一行新的 group/section 注释。
        if str_group_label != str_current_group_label:

            # 不同输出分组之间保留一个空行。
            if list_assign_lines:

                # 分组切换时增加视觉间隔。
                list_assign_lines.append("")

            # 写入当前 assign 所属的 group/section 标签。
            list_assign_lines.append(f"\t//{str_group_label}")

            # 把 assign 区游标推进到当前输出所属的小节。
            str_current_group_label = str_group_label  # assign 区最新分组标签

        # 数据输出口使用内部保持寄存器桥接。
        if str_output_name == layout.str_data_output_name:

            # 生成数据输出口的桥接语句。
            str_assign_line = (
                f"\tassign {layout.str_data_output_name} = {layout.data_output_internal};\t//"
                f"{_mock_output_bridge_comment(layout.str_data_output_name)}"
            )

        # 独立 valid 输出口使用各自的保持寄存器桥接。
        elif layout.bool_has_distinct_valid_output and str_output_name == layout.str_valid_output_name:

            # 生成独立 valid 输出的桥接语句。
            str_assign_line = (
                f"\tassign {layout.str_valid_output_name} = {layout.valid_output_internal};\t//"
                f"{_mock_output_bridge_comment(layout.str_valid_output_name)}"
            )

        # 其余输出统一拉到固定零值，保持 mock 交付闭合。
        else:

            # 计算当前未直接建模输出对应的零值字面量。
            str_zero_literal = _zero_literal(int(output_port.get("width", 1) or 1))  # 未使用输出复位值

            # 缓存当前输出位宽，供补零注释复用。
            int_output_width = int(output_port.get("width", 1) or 1)  # 当前输出位宽

            # 生成包含端口职责的补零说明，避免多路输出复用模板注释。
            str_unused_output_comment = _mock_unused_output_comment(str_output_name, int_output_width)  # 当前补零输出的行尾说明

            # 生成当前输出的补零桥接语句。
            str_assign_line = (  # 当前补零输出的桥接语句
                f"\tassign {str_output_name} = {str_zero_literal};\t//{str_unused_output_comment}"  # 当前输出的固定桥接源码行
            )

        # 追加当前输出口对应的桥接语句。
        list_assign_lines.append(str_assign_line)

    # 返回按接口顺序整理后的输出桥接块文本。
    return "\n".join(list_assign_lines)

# 生成输出口在输出区内应使用的 group/section 标签。
def _mock_output_group_label(output_port: MockPortSpec) -> str:
    """
    从外部输出口提取输出区复用的 group/section 标签。

    :param output_port: 当前外部输出口定义。
    :return: `group--section` 或仅 `group` 的输出区标签。
    """

    # group 为空时回退到统一的用户接口标签，避免生成未知标题。
    str_group = str(output_port.get("group") or "用户接口")  # 当前输出口的一级分组

    # section 为空时只保留一级 group 标签。
    str_section = str(output_port.get("section") or "")  # 当前输出口的小节标签

    # 同时存在 group 和 section 时拼接成稳定的镜像标签。
    if str_section:

        # 使用质量门要求的 group--section 标签格式。
        return f"{str_group}--{str_section}"

    # 缺少 section 时仅返回 group 标签。
    return str_group

# 构造按接口顺序回放的输出处理区。
def _mock_output_processing_block(layout: MockPortLayout, data_hold_assignment: str) -> str:
    """
    按接口输出顺序生成输出处理区的 always 块。

    :param layout: 端口布局对象。
    :param data_hold_assignment: 数据输出保持分支赋值语句。
    :return: 完整的输出处理区域文本。
    """

    # str_data_output_block 固定描述数据输出的寄存器更新逻辑。
    str_data_output_block = f"""\t//输出数据寄存器更新逻辑
\talways@(posedge {layout.clock_name} or negedge {layout.reset_name})begin
\t\tif({layout.reset_name} == 1'b0)begin
\t\t\t{layout.data_output_internal} <= DATA_RESET_VALUE;\t//复位时输出数据清零
\t\tend else if({layout.str_valid_register_name} == 1'b1)begin
\t\t\t{layout.data_output_internal} <= {layout.str_data_register_name};\t//缓存有效时更新输出数据
\t\tend else begin
{data_hold_assignment}
\t\tend
\tend"""

    # dict_output_blocks_by_name 让 always 块顺序跟外部接口输出顺序对齐。
    dict_output_blocks_by_name = {  # 输出口到处理区文本的映射
        layout.str_data_output_name: str_data_output_block,  # 先登记主数据输出对应的 always 文本
    }

    # 独立 valid 输出时再补一条对应的输出处理逻辑。
    if layout.bool_has_distinct_valid_output:

        # 按外部 valid 输出口名注册对应的时序更新逻辑。
        dict_output_blocks_by_name[layout.str_valid_output_name] = f"""\t//输出有效标志寄存器更新逻辑
\talways@(posedge {layout.clock_name} or negedge {layout.reset_name})begin
\t\tif({layout.reset_name} == 1'b0)begin
\t\t\t{layout.valid_output_internal} <= 1'b0;\t//复位时清除输出有效标志
\t\tend else if({layout.str_valid_register_name} == 1'b1)begin
\t\t\t{layout.valid_output_internal} <= 1'b1;\t//输入缓存有效时拉高输出有效
\t\tend else begin
\t\t\t{layout.valid_output_internal} <= 1'b0;\t//无有效输入时拉低输出有效
\t\tend
\tend"""

    # list_processing_lines 负责按接口顺序和分组标签拼接输出处理区。
    list_processing_lines = [  # 输出处理区域文本行
        "\t//-------------输出信号处理区域-------------//",  # 输出处理区域横幅
    ]

    # str_current_group_label 防止连续 always 块重复写同一 group/section 标签。
    str_current_group_label = ""  # 当前输出处理区分组标签

    # bool_rendered_block 标记是否已经输出过至少一个 always 块。
    bool_rendered_block = False  # 输出处理区是否已有业务 always

    # 按外部接口输出顺序回放内部输出 always 块。
    for output_port in layout.outputs:

        # 当前循环专门匹配 data/valid 输出的 always 文本表，这里先抽出查表键。
        str_output_name = str(output_port.get("name") or "")  # 当前外部输出口名

        # 不是内部寄存器驱动的输出口无需输出 always 块。
        if str_output_name not in dict_output_blocks_by_name:

            # 固定拉零或纯桥接输出不进入输出处理区域。
            continue

        # 第二个及之后的 always 块前补一个空行，避免块间粘连。
        if bool_rendered_block:

            # 输出处理区多个 always 块之间保留可读间隔。
            list_processing_lines.append("")

        # 计算当前 always 块所属的 group/section 标签。
        str_group_label = _mock_output_group_label(output_port)  # 输出处理区分组标签

        # 把当前 always 块归到对应的输出镜像分组。
        if str_group_label != str_current_group_label:

            # 写入当前 always 块所属的 group/section 标签。
            list_processing_lines.append(f"\t//{str_group_label}")

            # 记录处理区刚刚输出的小节标签，供后续 always 复用。
            str_current_group_label = str_group_label  # 输出处理区最新分组标签

        # 追加当前输出口对应的 always 块正文。
        list_processing_lines.extend(dict_output_blocks_by_name[str_output_name].splitlines())

        # 标记输出处理区已经写入业务 always。
        bool_rendered_block = True  # 输出处理区已写入至少一个 always

    # 返回输出处理区域的完整文本。
    return "\n".join(list_processing_lines)
