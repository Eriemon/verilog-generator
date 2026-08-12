"""工作流 mock provider 的 testbench 生成辅助逻辑。"""

# future annotations 让 testbench helper 的类型注解延迟解析
from __future__ import annotations

# testbench helper 只保留最小类型与共享布局依赖
from typing import Any

# 端口契约类型
from .model_provider import MockPortSpec

# 端口布局与时序 testbench 骨架 helper
from .model_provider_mock_rtl import (
    MockPortLayout,
    _build_mock_port_layout,
    _layout_has_sequential_controls,
    _mock_rtl_parts,
)

# 组合路径选择 helper
from .model_provider_mock_rtl import (
    _select_comb_input_paths,
    _select_comb_parity_output,
    _select_comb_primary_output,
)

# 行尾语义注释与标量位宽工具
from .model_provider_mock_comments import (
    _add_mock_line_comments,
    _width_text,
    _zero_literal,
)

# 向量标签用于 testbench 里的契约摘要
from .vectors import VECTOR_HASH_TAG

# testbench 主渲染入口从这里开始
def _mock_erie_rtl_testbench_text(
    spec: dict[str, Any],
    vectors: list[dict[str, Any]],
    vector_hash: str,
) -> str:
    """
    根据 spec 与向量生成自检式 testbench。

    :param spec: 含模块名与端口定义的规范化规格。
    :param vectors: 用于构造期望值的测试向量。
    :param vector_hash: 向量契约哈希，可写入注释供回归核对。
    :return: 带行尾语义注释的 Verilog testbench 文本。
    """

    # 复用与 DUT 一致的端口语义布局。
    mock_port_layout_snapshot = _build_mock_port_layout(spec)  # testbench 生成阶段使用的端口布局快照

    # 纯组合规范使用无时钟复位的专用 testbench 模板。
    if not _layout_has_sequential_controls(mock_port_layout_snapshot):

        # 组合型 mock testbench 直接验证 mux 与 parity 的组合传播。
        return _mock_erie_comb_testbench_text(mock_port_layout_snapshot, vectors, vector_hash)

    # 计算 testbench 中使用的整数期望值。
    int_expected_value = _mock_expected_value(vectors)  # 自检比较使用的期望值

    # 计算统一的数据位宽。
    dict_width_source = mock_port_layout_snapshot.data_output or mock_port_layout_snapshot.data_input or {"width": 8}  # EXPECTED_VALUE 与 value 共用位宽的来源端口

    # 把位宽来源对象折算成 EXPECTED_VALUE 和 value 共用的数据总线宽度。
    int_data_width = int(dict_width_source.get("width", 8) or 8)  # EXPECTED_VALUE 与 value 两条总线共用的数据位宽

    # 选择自检时优先观察的数据输出、valid 输出或兜底常量端口。
    dict_primary_observed_port = mock_port_layout_snapshot.data_output or mock_port_layout_snapshot.valid_output  # 自检优先观察的数据或 valid 端口候选

    # 在候选缺失时回退到 EXPECTED_VALUE 常量，保证观测信号名始终存在。
    dict_observed_port = dict_primary_observed_port or {"name": "EXPECTED_VALUE"}  # 自检最终使用的观测端口

    # 选择自检时实际观测的信号名。
    str_observed_signal = str(dict_observed_port["name"])  # 自检 value 导线实际观测到的 DUT 信号名

    # 时序 testbench 从声明段开始积累源码行。
    list_lines: list[str] = []  # 时序 testbench 完整源码行缓存

    # 声明段包含驱动寄存器、观测导线和 DUT 例化头。
    list_lines.extend(
        _mock_sequential_tb_declaration_lines(
            mock_port_layout_snapshot,  # 时序 testbench 端口布局
            int_expected_value,  # EXPECTED_VALUE 初始化值
            int_data_width,  # value 与 EXPECTED_VALUE 共用位宽
            str_observed_signal,  # value 导线观测目标
        )
    )

    # 组合 DUT 映射保持用户端口声明顺序，便于 testbench 同名连接。
    list_lines.extend(_mock_instance_port_mapping_lines(mock_port_layout_snapshot.ports))  # DUT 端口映射行

    # initial 激励段负责复位、输入驱动和自检退出路径。
    list_initial_lines = _mock_sequential_tb_initial_lines(  # initial 块源码行集合
        mock_port_layout_snapshot,  # initial 阶段使用的 DUT 端口快照
        vectors,  # 写入测试意图说明的向量清单
        vector_hash,  # 回归日志中的向量契约摘要
        int_data_width,  # 数据激励总线宽度
        int_expected_value,  # 最终比较的整数期望值
    )

    # 把 initial 块拼到 DUT 例化之后。
    list_lines.extend(list_initial_lines)

    # 返回带行尾语义注释的 testbench 文本。
    return _add_mock_line_comments("\n".join(list_lines) + "\n")

# 生成时序 testbench 的声明区行。
def _mock_sequential_tb_declaration_lines(
    layout: MockPortLayout,
    expected_value: int,
    data_width: int,
    observed_signal: str,
) -> list[str]:
    """
    生成时序 testbench 的寄存器、导线和 DUT 例化头。

    :param layout: mock 端口语义布局。
    :param expected_value: EXPECTED_VALUE 初始化值。
    :param data_width: EXPECTED_VALUE 和 value 共用的数据位宽。
    :param observed_signal: value 导线观测的 DUT 输出信号名。
    :return: testbench 声明区源码行。
    """

    # 组合 TB 的第一行只声明无时钟模块壳。
    list_lines: list[str] = []  # 时序 testbench 声明区缓存

    # 写入 testbench module 头。
    list_lines.append(f"module {layout.top}_tb;")

    # 时钟寄存器负责驱动 DUT 的 posedge 采样。
    list_lines.append(f"\treg {layout.clock_name} = 1'b0;")

    # 复位寄存器负责驱动 DUT 的低有效复位流程。
    list_lines.append(f"\treg {layout.reset_name} = 1'b0;")

    # 普通输入端口在 testbench 中生成为可驱动 reg。
    list_lines.extend(_mock_input_register_lines(layout.inputs))  # 输入驱动寄存器声明行

    # 输出端口在 testbench 中生成为观测 wire。
    list_lines.extend(_mock_output_wire_lines(layout.outputs))  # 输出观测导线声明行

    # EXPECTED_VALUE 保存最终比较用的期望数据。
    list_lines.append(f"\treg [{data_width - 1}:0] EXPECTED_VALUE = {data_width}'d{expected_value};")

    # value 统一绑定到当前场景最主要的 DUT 观测信号。
    list_lines.append(f"\twire [{data_width - 1}:0] value = {observed_signal};")

    # 固定周期时钟让时序 DUT 能完成采样。
    list_lines.append(f"\talways #5 {layout.clock_name} = ~{layout.clock_name};")

    # DUT 例化头等待后续端口映射行补齐。
    list_lines.append(f"\t{layout.top} dut (")

    # 返回完整声明区。
    return list_lines

# 生成 testbench 输入驱动寄存器声明行。
def _mock_input_register_lines(input_ports: list[MockPortSpec]) -> list[str]:
    """
    将输入端口列表渲染为 testbench 驱动寄存器声明。

    :param input_ports: 普通输入端口列表。
    :return: 输入驱动寄存器声明行。
    """

    # list_lines 保存输入端口对应的 reg 声明。
    list_lines: list[str] = []  # 输入寄存器声明行

    # 为每个普通输入生成驱动寄存器声明。
    for input_port in input_ports:

        # 计算当前输入的位宽前缀。
        str_width_text = _width_text(input_port)  # reg 声明位宽

        # 生成当前输入的上电初始值。
        str_init_value = _mock_zero_init_literal(int(input_port.get("width", 1) or 1))  # 输入寄存器上电初值

        # 追加当前输入寄存器声明。
        list_lines.append(f"\treg {str_width_text}{input_port['name']} = {str_init_value};")

    # 返回所有输入驱动寄存器声明。
    return list_lines

# 生成 testbench 输出观测导线声明行。
def _mock_output_wire_lines(output_ports: list[MockPortSpec]) -> list[str]:
    """
    将输出端口列表渲染为 testbench 观测导线声明。

    :param output_ports: 普通输出端口列表。
    :return: 输出观测导线声明行。
    """

    # list_lines 保存输出端口对应的 wire 声明。
    list_lines: list[str] = []  # 待返回的输出观测声明缓存

    # 为每个输出生成观测导线声明。
    for output_port in output_ports:

        # 计算当前输出的位宽前缀。
        str_width_text = _width_text(output_port)  # 当前观测导线沿用的输出位宽前缀

        # 追加当前输出导线声明。
        list_lines.append(f"\twire {str_width_text}{output_port['name']};")

    # 返回所有输出观测导线声明。
    return list_lines

# 生成 testbench 端口映射行。
def _mock_instance_port_mapping_lines(ports: list[MockPortSpec]) -> list[str]:
    """
    生成 DUT 例化中的逐项端口映射行。

    :param ports: DUT 端口声明顺序。
    :return: 端口映射源码行。
    """

    # list_lines 保存例化端口映射。
    list_lines: list[str] = []  # DUT 连接表逐行缓存

    # 例化映射保持 DUT 端口声明顺序。
    for index, port in enumerate(ports):

        # 末尾端口映射不追加逗号。
        str_trailing_comma = "," if index < len(ports) - 1 else ""  # 当前映射行尾随逗号

        # 将 DUT 当前端口接到同名 testbench 信号。
        list_lines.append(f"\t\t.{port['name']}({port['name']}){str_trailing_comma}")

    # 返回全部端口映射行。
    return list_lines

# 生成时序 testbench initial 激励和自检行。
def _mock_sequential_tb_initial_lines(
    layout: MockPortLayout,
    vectors: list[dict[str, Any]],
    vector_hash: str,
    data_width: int,
    expected_value: int,
) -> list[str]:
    """
    生成时序 testbench 的 initial 块主体和收尾。

    :param layout: mock 端口语义布局。
    :param vectors: 用于写入可审查说明的测试向量。
    :param vector_hash: 向量契约哈希。
    :param data_width: 数据激励使用的位宽。
    :param expected_value: 数据输入激励使用的值。
    :return: DUT 例化闭合、initial 主体和 endmodule 行。
    """

    # list_lines 先闭合 DUT 例化。
    list_lines = ["\t);"]  # DUT 例化闭合行

    # initial 块承载复位、激励和自检流程。
    list_lines.append("\tinitial begin")

    # 向需要追踪向量版本的场景写入契约哈希。
    list_lines.extend(_mock_vector_comment_lines(vectors, vector_hash, "checkpoint value against EXPECTED_VALUE"))  # 向量说明注释行

    # 低有效复位先拉低，确保 DUT 进入确定状态。
    list_lines.append(f"\t\t{layout.reset_name} = 1'b0;")

    # 复位等待覆盖一个完整时钟周期。
    list_lines.append("\t\t#12;")

    # 释放复位后再写入业务激励。
    list_lines.append(f"\t\t{layout.reset_name} = 1'b1;")

    # 数据输入和 valid 输入根据端口是否存在分别驱动。
    list_lines.extend(_mock_sequential_input_stimulus_lines(layout, data_width, expected_value))  # 输入激励行

    # 追加传播等待、valid 检查、最终值比较和仿真收尾。
    list_lines.extend(_mock_sequential_check_lines(layout))  # 时序自检与收尾行

    # 返回完整 initial 区域。
    return list_lines

# 生成向量契约和样例说明注释行。
def _mock_vector_comment_lines(vectors: list[dict[str, Any]], vector_hash: str, comparison_text: str) -> list[str]:
    """
    生成 testbench initial 块中的向量哈希和样例说明注释。

    :param vectors: 用于写入可审查说明的测试向量。
    :param vector_hash: 向量契约哈希。
    :param comparison_text: 每条向量说明中的比较对象文本。
    :return: 可直接插入 initial 块的注释行。
    """

    # list_lines 保存 initial 块开头的可审查说明。
    list_lines: list[str] = []  # 向量契约和样例说明行

    # 向量契约哈希存在时写入固定标签。
    if vector_hash:

        # 追加向量契约哈希注释。
        list_lines.append(f"\t\t// {VECTOR_HASH_TAG} {vector_hash}")

    # 为每个向量写入一行自检说明。
    for case in vectors:

        # 追加当前向量的检查摘要。
        list_lines.append(f'\t\t// {case["id"]} compares {comparison_text} and reports PASS/FAIL')

    # 返回所有向量说明注释行。
    return list_lines

# 生成时序 testbench 输入激励行。
def _mock_sequential_input_stimulus_lines(layout: MockPortLayout, data_width: int, expected_value: int) -> list[str]:
    """
    根据端口是否存在生成 data 和 valid 输入激励行。

    :param layout: mock 端口语义布局。
    :param data_width: 数据激励使用的位宽。
    :param expected_value: 数据输入激励使用的值。
    :return: 输入激励源码行。
    """

    # list_lines 保存可选输入激励行。
    list_lines: list[str] = []  # 输入激励源码行

    # 数据输入存在时写入一次样例激励。
    if layout.data_input:

        # 追加数据输入激励语句。
        list_lines.append(f"\t\t{layout.data_input['name']} = {data_width}'d{expected_value};")

    # valid 输入存在时写入一次握手激励。
    if layout.valid_input:

        # 追加 valid 输入激励语句。
        list_lines.append(f"\t\t{layout.valid_input['name']} = 1'b1;")

    # 返回所有输入激励行。
    return list_lines

# 生成时序 testbench 自检和收尾行。
def _mock_sequential_check_lines(layout: MockPortLayout) -> list[str]:
    """
    生成时序 testbench 的传播等待、可选 valid 检查和值比较。

    :param layout: mock 端口语义布局。
    :return: 自检与仿真结束源码行。
    """

    # list_lines 先给 DUT 一段时间完成输出传播。
    list_lines = ["\t\t#20;"]  # 输出传播等待行

    # valid 输出存在时先检查握手是否拉高。
    if layout.valid_output:

        # valid 输出检查入口绑定到实际 valid 端口名。
        list_lines.append(f"\t\tif ({layout.valid_output['name']} !== 1'b1) begin")

        # valid 未拉高时直接终止仿真。
        list_lines.append('\t\t\t$fatal(1, "FAIL: valid output did not assert");')

        # 关闭 valid 输出检查分支。
        list_lines.append("\t\tend")

    # 追加最终值比较和仿真结束语句。
    list_lines.extend(_mock_common_tb_tail_lines())  # 最终自检和仿真收尾

    # 返回完整自检区域。
    return list_lines

# 生成 testbench 通用收尾行。
def _mock_common_tb_tail_lines() -> list[str]:
    """
    生成 PASS 日志、失败比较和仿真退出语句。

    :param: 无输入参数；收尾结构由 mock testbench 协议固定。
    :return: testbench initial 块末尾与 endmodule 行。
    """

    # list_lines 保存时序 testbench 共用的末尾自检结构。
    list_lines = ["\t\tif (value !== EXPECTED_VALUE) begin"]  # 最终值比较入口

    # 期望值不匹配时立即报错退出。
    list_lines.append('\t\t\t$fatal(1, "FAIL: value checkpoint mismatch");')

    # 关闭最终值比较分支。
    list_lines.append("\t\tend")

    # 自检全部通过时打印 PASS 摘要。
    list_lines.append('\t\t$display(" > INFO: [Verilog] self-checking mock testbench completed.");')

    # 主动结束仿真。
    list_lines.append("\t\t$finish;")

    # 关闭 initial 块。
    list_lines.append("\tend")

    # 关闭 testbench 模块。
    list_lines.append("endmodule")

    # 返回通用收尾源码行。
    return list_lines

# 生成 testbench 寄存器初始值字面量。
def _mock_zero_init_literal(width: int) -> str:
    """
    根据端口位宽返回 testbench 寄存器上电清零字面量。

    :param width: 目标端口位宽。
    :return: 单比特或多比特清零字面量。
    """

    # 多比特输入上电时清零整个总线。
    if width > 1:

        # 返回带位宽的十进制零值。
        return f"{width}'d0"

    # 单比特输入统一回到低电平。
    return "1'b0"

# 为组合型 mock 生成稳定的非零测试激励字面量。
def _mock_sample_literal(width: int, *, alternate: bool) -> str:
    """按位宽返回组合 testbench 使用的稳定字面量。

    :param width: 目标信号位宽。
    :param alternate: 是否选择第二组测试花纹。
    :return: 与位宽匹配的 Verilog 字面量文本。
    """

    # 单比特激励只需要两种互补电平即可。
    if width <= 1:

        # 第二组激励返回高电平，第一组返回低电平。
        return "1'b1" if alternate else "1'b0"

    # 多比特激励用固定十六进制花纹，确保两组样例明显可区分。
    str_seed = "A5" if alternate else "3C"  # 组合样例使用的固定十六进制花纹

    # 十六进制位数按目标位宽向上取整。
    int_hex_digits = max(1, (width + 3) // 4)  # 目标位宽所需的最小十六进制字符数

    # 把固定花纹重复到足够长度，再截断到目标位数。
    str_hex_value = (str_seed * ((int_hex_digits + len(str_seed) - 1) // len(str_seed)))[:int_hex_digits]  # 宽度对齐后的十六进制字面量正文

    # 返回与位宽匹配的十六进制字面量。
    return f"{width}'h{str_hex_value}"

# 生成组合型 mux 场景的自检语句。
def _mock_comb_mux_check_lines(
    dict_primary_output: MockPortSpec,
    dict_selector_port: MockPortSpec,
    dict_first_data_input: MockPortSpec,
    dict_second_data_input: MockPortSpec,
    dict_parity_output: MockPortSpec | None,
) -> list[str]:
    """生成 selector 驱动双路输入切换的组合检查语句。

    :param dict_primary_output: 主数据输出端口。
    :param dict_selector_port: selector 输入端口。
    :param dict_first_data_input: selector 为 0 时应命中的第一路输入。
    :param dict_second_data_input: selector 为 1 时应命中的第二路输入。
    :param dict_parity_output: 可选 parity 输出端口。
    :return: 可直接拼接进 initial 块的自检语句列表。
    """

    # 初始化 mux 场景的检查语句缓冲。
    list_check_lines: list[str] = []  # mux 场景检查语句缓冲

    # 生成 selector 低电平场景的第一组数据激励。
    str_first_value = _mock_sample_literal(int(dict_first_data_input.get("width", 1) or 1), alternate=False)  # selector 为 0 的数据激励值

    # 再准备一组互补花纹，确保 selector 翻转后主输出确实切到另一条数据通路。
    str_second_value = _mock_sample_literal(int(dict_second_data_input.get("width", 1) or 1), alternate=True)  # 第二路输入花纹值

    # 先写入第一路输入的样例值。
    list_check_lines.append(f"\t\t{dict_first_data_input['name']} = {str_first_value};")

    # 再写入第二路输入的对照样例值。
    list_check_lines.append(f"\t\t{dict_second_data_input['name']} = {str_second_value};")

    # 让 selector 先选择第一路输入。
    list_check_lines.append(f"\t\t{dict_selector_port['name']} = 1'b0;")

    # 为组合传播预留一个最小等待周期。
    list_check_lines.append("\t\t#1;")

    # 检查 selector 为 0 时主输出是否命中第一路输入。
    list_check_lines.append(
        f"\t\tif ({dict_primary_output['name']} !== {dict_first_data_input['name']}) begin"
    )

    # 当 selector 为 0 的主输出不匹配时立即终止仿真。
    list_check_lines.append(
        '\t\t\t$fatal(1, "FAIL: combinational primary output mismatch when selector is 0");'
    )

    # 关闭 selector 为 0 的失败分支。
    list_check_lines.append("\t\tend")

    # parity 输出存在时，需要确认第一条路径的奇偶校验同步成立。
    if dict_parity_output:

        # 检查 selector 为 0 时 parity 是否跟随第一条数据路径同步折叠。
        list_check_lines.append(
            f"\t\tif ({dict_parity_output['name']} !== ^{dict_primary_output['name']}) begin"
        )

        # 当 selector 为 0 的 parity 不匹配时立即终止仿真。
        list_check_lines.append('\t\t\t$fatal(1, "FAIL: parity output mismatch when selector is 0");')

        # 收束第一轮 parity 校验对应的 begin-end 失败块。
        list_check_lines.append("\t\tend")

    # selector 切到第二路后，应把主输出切换到第二个数据端口。
    list_check_lines.append(f"\t\t{dict_selector_port['name']} = 1'b1;")

    # 给 selector 翻转后的输出留一次新的组合传播窗口。
    list_check_lines.append("\t\t#1;")

    # 检查 selector 为 1 时主输出是否切到第二路输入。
    list_check_lines.append(
        f"\t\tif ({dict_primary_output['name']} !== {dict_second_data_input['name']}) begin"
    )

    # 用第二轮主输出专属的报错信息标记高电平 selector 失败。
    list_check_lines.append(
        '\t\t\t$fatal(1, "FAIL: combinational primary output mismatch when selector is 1");'
    )

    # 收束第二轮主输出校验对应的 begin-end 失败块。
    list_check_lines.append("\t\tend")

    # parity 输出存在时，也要覆盖 selector 为 1 的同步奇偶校验。
    if dict_parity_output:

        # 检查高电平 selector 场景下的 parity 是否切到第二条数据路径。
        list_check_lines.append(
            f"\t\tif ({dict_parity_output['name']} !== ^{dict_primary_output['name']}) begin"
        )

        # 这里保留 selector 为 1 的 parity 专属报错文本，便于定位第二轮切换失败。
        list_check_lines.append('\t\t\t$fatal(1, "FAIL: parity output mismatch when selector is 1");')

        # 用 end 收束这一次高电平 parity 校验对应的失败块。
        list_check_lines.append("\t\tend")

    # 返回双路 mux 场景的完整自检语句。
    return list_check_lines

# 生成组合型直通场景的自检语句。
def _mock_comb_passthrough_check_lines(
    dict_primary_output: MockPortSpec,
    dict_first_data_input: MockPortSpec,
    dict_parity_output: MockPortSpec | None,
) -> list[str]:
    """生成单输入直通型组合路径的检查语句。

    :param dict_primary_output: 主数据输出端口。
    :param dict_first_data_input: 唯一数据输入端口。
    :param dict_parity_output: 可选 parity 输出端口。
    :return: 可直接拼接进 initial 块的自检语句列表。
    """

    # 初始化直通场景的检查语句缓冲。
    list_check_lines: list[str] = []  # 直通场景检查语句缓冲

    # 生成直通场景唯一输入使用的激励值。
    str_first_value = _mock_sample_literal(int(dict_first_data_input.get("width", 1) or 1), alternate=False)  # 直通路径的数据激励值

    # 把样例值写入唯一数据输入。
    list_check_lines.append(f"\t\t{dict_first_data_input['name']} = {str_first_value};")

    # 给直通路径一次 delta 周期，让输出完成传播。
    list_check_lines.append("\t\t#1;")

    # 检查直通路径的主输出是否跟随唯一输入变化。
    list_check_lines.append(
        f"\t\tif ({dict_primary_output['name']} !== {dict_first_data_input['name']}) begin"
    )

    # 当直通路径的主输出不匹配时立即终止仿真。
    list_check_lines.append('\t\t\t$fatal(1, "FAIL: combinational primary output mismatch");')

    # 关闭直通路径的失败分支。
    list_check_lines.append("\t\tend")

    # parity 输出存在时，同步补齐组合折叠检查。
    if dict_parity_output:

        # 检查直通路径的 parity 是否与主输出保持同拍组合一致。
        list_check_lines.append(
            f"\t\tif ({dict_parity_output['name']} !== ^{dict_primary_output['name']}) begin"
        )

        # 当直通路径的 parity 不匹配时立即终止仿真。
        list_check_lines.append('\t\t\t$fatal(1, "FAIL: parity output mismatch");')

        # 收束直通场景 parity 校验对应的 begin-end 失败块。
        list_check_lines.append("\t\tend")

    # 返回直通场景的完整自检语句。
    return list_check_lines

# 生成组合型零值兜底场景的自检语句。
def _mock_comb_zero_check_lines(
    dict_primary_output: MockPortSpec | None,
    dict_parity_output: MockPortSpec | None,
) -> list[str]:
    """生成无输入场景下的常零检查语句。

    :param dict_primary_output: 可选主数据输出端口。
    :param dict_parity_output: 可选 parity 输出端口。
    :return: 可直接拼接进 initial 块的自检语句列表。
    """

    # 初始化零值兜底场景的检查语句缓冲。
    list_check_lines: list[str] = []  # 零值兜底场景检查语句缓冲

    # 无输入组合场景统一先等待一个组合传播周期。
    list_check_lines.append("\t\t#1;")

    # 主输出存在时，应保持为固定零值。
    if dict_primary_output:

        # 主数据口在无输入激励时只能输出该位宽下的零字面量。
        list_check_lines.append(
            f"\t\tif ({dict_primary_output['name']} !== "
            f"{_zero_literal(int(dict_primary_output.get('width', 1) or 1))}) begin"
        )

        # 主数据口偏离零值说明兜底组合路径不可用。
        list_check_lines.append(
            '\t\t\t$fatal(1, "FAIL: combinational primary output should stay zero");'
        )

        # 收束主数据口零值断言分支。
        list_check_lines.append("\t\tend")

    # parity 输出存在时，同样应保持为零值。
    if dict_parity_output:

        # parity 口无输入时也必须落在零值，避免孤立校验位漂移。
        list_check_lines.append(
            f"\t\tif ({dict_parity_output['name']} !== "
            f"{_zero_literal(int(dict_parity_output.get('width', 1) or 1))}) begin"
        )

        # parity 口非零表示组合兜底路径产生了额外状态。
        list_check_lines.append(
            '\t\t\t$fatal(1, "FAIL: combinational parity output should stay zero");'
        )

        # 收束 parity 零值断言分支。
        list_check_lines.append("\t\tend")

    # 返回零值兜底场景的完整自检语句。
    return list_check_lines

# 为组合型 mock 生成无时钟复位的自检 testbench。
def _mock_erie_comb_testbench_text(
    layout: MockPortLayout,
    vectors: list[dict[str, Any]],
    vector_hash: str,
) -> str:
    """根据组合型端口布局生成自检式 testbench。

    :param layout: mock 端口语义布局对象。
    :param vectors: 用于写入说明注释的测试向量。
    :param vector_hash: 向量契约哈希，可写入注释供回归核对。
    :return: 带行尾语义注释的组合 testbench 文本。
    """

    # 记录组合 testbench 需要观测的主数据输出端口。
    dict_primary_output = _select_comb_primary_output(layout)  # mux 或直通检查的主输出

    # parity 输出存在时会追加异或校验场景。
    dict_parity_output = _select_comb_parity_output(layout)  # parity 自检候选输出

    # 组合 testbench 声明区包含输入 reg、输出 wire 与 DUT 例化头。
    list_lines = _mock_comb_tb_declaration_lines(layout)  # 组合用声明和例化头行

    # 组合 DUT 例化保持接口声明顺序。
    list_lines.extend(_mock_instance_port_mapping_lines(layout.ports))  # 组合 DUT 同名连接行

    # 关闭 DUT 例化参数和端口映射列表。
    list_lines.append("\t);")

    # initial 块写入向量说明并执行组合路径检查。
    list_lines.append("\tinitial begin")

    # 向量说明注释保留组合检查的人读回归线索。
    list_lines.extend(_mock_vector_comment_lines(vectors, vector_hash, "combinational checkpoints"))  # 组合向量说明行

    # 根据端口布局选择 mux、直通或零值兜底检查。
    list_lines.extend(_mock_comb_selected_check_lines(layout, dict_primary_output, dict_parity_output))  # 组合场景自检行

    # 追加组合 testbench 原有的 PASS 日志和仿真结束语句。
    list_lines.extend(_mock_comb_tb_tail_lines())  # 组合 testbench 统一收尾

    # 返回带行尾语义注释的组合 testbench 文本。
    return _add_mock_line_comments("\n".join(list_lines) + "\n")

# 生成组合 testbench 的声明区行。
def _mock_comb_tb_declaration_lines(layout: MockPortLayout) -> list[str]:
    """
    生成组合 testbench 的输入寄存器、输出导线和 DUT 例化头。

    :param layout: mock 端口语义布局对象。
    :return: 组合 testbench 声明区源码行。
    """

    # list_lines 先保存组合 testbench module 头。
    list_lines: list[str] = []  # 组合声明区行缓存

    # 写入组合 testbench module 声明。
    list_lines.append(f"module {layout.top}_tb;")

    # 组合输入同样用 testbench reg 驱动。
    list_lines.extend(_mock_input_register_lines(layout.inputs))  # 组合输入激励寄存器

    # 组合输出统一生成为观测 wire。
    list_lines.extend(_mock_output_wire_lines(layout.outputs))  # 组合输出观测导线

    # 写入 DUT 例化头，后续逐项补齐端口映射。
    list_lines.append(f"\t{layout.top} dut (")

    # 返回声明区和 DUT 例化头。
    return list_lines

# 选择组合 testbench 的自检场景行。
def _mock_comb_selected_check_lines(
    layout: MockPortLayout,
    primary_output: MockPortSpec | None,
    parity_output: MockPortSpec | None,
) -> list[str]:
    """
    根据组合输入输出形态选择 mux、直通或零值兜底检查。

    :param layout: mock 端口语义布局对象。
    :param primary_output: 可选主数据输出端口。
    :param parity_output: 可选 parity 输出端口。
    :return: 组合场景自检源码行。
    """

    # tuple_input_paths 保存 selector、低电平输入和高电平输入候选。
    tuple_input_paths = _select_comb_input_paths(layout)  # selector/数据输入候选三元组

    # dict_selector_port 控制 mux 双输入检查路径。
    dict_selector_port = tuple_input_paths[0]  # 控制路径端口快照

    # dict_first_data_input 是直通场景的默认输入路径。
    dict_first_data_input = tuple_input_paths[1]  # 低电平路径端口快照

    # dict_second_data_input 是 mux 场景的备选输入路径。
    dict_second_data_input = tuple_input_paths[2]  # 高电平路径端口快照

    # 双路 mux 场景需要 selector 与两路不同的数据输入。
    if _mock_has_comb_mux_case(primary_output, dict_selector_port, dict_first_data_input, dict_second_data_input):

        # 双路 mux 检查需要同时驱动 selector 和两路数据样例。
        return _mock_comb_mux_check_lines(
            primary_output,  # mux 主数据输出
            dict_selector_port,  # mux 路径选择输入
            dict_first_data_input,  # selector 低电平路径输入
            dict_second_data_input,  # selector 高电平路径输入
            parity_output,  # 可选 parity 输出
        )

    # 只有一路数据输入时，退化成直通型组合检查。
    if primary_output and dict_first_data_input:

        # 单输入场景只需要检查主输出是否直通该输入。
        return _mock_comb_passthrough_check_lines(primary_output, dict_first_data_input, parity_output)

    # 没有输入端口时，退化成常零输出检查。
    return _mock_comb_zero_check_lines(primary_output, parity_output)

# 判断组合 testbench 是否具备双路 mux 检查条件。
def _mock_has_comb_mux_case(
    primary_output: MockPortSpec | None,
    selector_port: MockPortSpec | None,
    first_data_input: MockPortSpec | None,
    second_data_input: MockPortSpec | None,
) -> bool:
    """
    判断组合布局是否足够执行双路 mux 检查。

    :param primary_output: 可选主数据输出端口。
    :param selector_port: 可选 selector 控制端口。
    :param first_data_input: selector 拉低时的候选输入端口。
    :param second_data_input: selector 拉高时的候选输入端口。
    :return: 端口齐全且两路输入不同则返回 True。
    """

    # 端口缺失时不能执行双路 mux 检查。
    if not (primary_output and selector_port and first_data_input and second_data_input):

        # 返回 False，调用方继续尝试直通或零值兜底检查。
        return False

    # 两个数据输入必须是不同信号，否则 mux 检查没有区分度。
    return str(first_data_input.get("name")) != str(second_data_input.get("name"))

# 生成组合 testbench 原有收尾行。
def _mock_comb_tb_tail_lines() -> list[str]:
    """
    生成组合 testbench 的 PASS 日志和仿真退出语句。

    :param: 无输入参数；组合场景失败检查由上游 helper 先行生成。
    :return: initial 块末尾和 endmodule 行。
    """

    # list_lines 只保存组合路径全部断言通过后的退出脚本。
    list_lines = ['\t\t$display(" > INFO: [Verilog] self-checking mock testbench completed.");']  # 组合自检通过日志

    # 组合检查没有时钟收尾，PASS 后立即结束仿真。
    list_lines.append("\t\t$finish;")

    # initial 块到这里已经完成所有组合断言。
    list_lines.append("\tend")

    # 组合 testbench 模块在 PASS 路径后闭合。
    list_lines.append("endmodule")

    # 返回组合 testbench 通用收尾源码行。
    return list_lines

# 从向量中提取期望值
def _mock_expected_value(vectors: list[dict[str, Any]]) -> int:
    """
    从 mock 向量里抽取一个稳定的整数期望值。

    :param vectors: 供 testbench 使用的 mock 向量列表。
    :return: 非负整数期望值，缺失时回退 1。
    """

    # 空向量场景直接回退到最小默认值。
    if not vectors:

        # 用 1 保持 testbench 示例可运行。
        return 1

    # 只从首个向量中抽取示例期望值。
    first_case = vectors[0]  # 期望值的主来源向量

    # 依次扫描最常见的输出字段分组。
    for group_name in ("expected_outputs", "checkpoints", "outputs", "expected"):

        # 读取当前候选输出组。
        dict_group_values = first_case.get(group_name) if isinstance(first_case.get(group_name), dict) else None  # 当前输出候选集合

        # 从当前输出组里提取第一个可用标量。
        int_group_value = _mock_first_scalar_value(dict_group_values)  # 当前输出组提取到的标量值

        # 命中输出标量时直接返回。
        if int_group_value is not None:

            # 返回当前输出字段组里的稳定期望值。
            return int_group_value

    # 输入字典在输出缺失时承担兜底角色。
    dict_inputs = first_case.get("inputs") if isinstance(first_case.get("inputs"), dict) else None  # 兜底推断使用的输入集合

    # 从输入集合里提取第一个可用标量。
    int_input_value = _mock_first_scalar_value(dict_inputs)  # 输入兜底路径提取到的标量值

    # 命中输入标量时返回兜底期望值。
    if int_input_value is not None:

        # 返回从输入样例里推导出的稳定值。
        return int_input_value

    # 所有字段都未命中时回退默认值。
    return 1

# 从映射值里提取第一个可用于 testbench 的标量。
def _mock_first_scalar_value(values: dict[str, Any] | None) -> int | None:
    """
    从字典值中提取第一个布尔或整数标量。

    :param values: 候选输出或输入映射。
    :return: 命中标量时返回非负整数，否则返回 None。
    """

    # 空映射没有可用的标量候选。
    if not values:

        # 返回 None 让调用方继续尝试其他来源。
        return None

    # 在映射值里寻找可转换的布尔或整数。
    for candidate in values.values():

        # 布尔值转换成单比特整数。
        if isinstance(candidate, bool):

            # 布尔样例按高低电平折成 0 或 1。
            return int(candidate)

        # 整数值回收到非负范围。
        if isinstance(candidate, int):

            # 整数样例统一裁剪到非负范围。
            return max(candidate, 0)

    # 当前映射没有布尔或整数标量。
    return None

# 生成 review 阶段使用的 mock 摘要文本
