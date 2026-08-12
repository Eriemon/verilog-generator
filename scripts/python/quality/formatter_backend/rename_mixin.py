"""为 Verilog formatter 提供重命名映射、body tree 改名和模块接口查找辅助。"""

# future annotations 避免运行期求值复杂 formatter 模型类型。
from __future__ import annotations

# 正则、时间和路径工具支撑模块接口扫描与头部元数据读取。
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable

# 横幅工具用于识别和重建 Verilog 区域标题。
from .banners import display_width, extract_banner_title, is_banner_line, make_banner

# 错误类型和参数、端口模型描述重命名阶段的输入边界。
from .models import VerilogFormatterError, ParamDecl, ParamRenderCluster, PortDecl, PortLayoutInfo

# 输出、赋值和实例布局模型用于桥接端口与内部信号。
from .models import OutputSignalLayout, AssignSourceLayout, InstanceSignalLayout

# 声明、赋值和控制流模型承载 body tree 的可改名节点。
from .models import SignalDecl, AssignStmt, BodyBlock, LValueRef, CaseItem, ControlNode

# always、实例和 generate 模型保留 parser 结构化块边界。
from .models import AlwaysBlock, InstanceBlock, GenerateBlock, InitialBlock

# function、task、原始块和条件编译模型避免改名阶段丢失源码结构。
from .models import FunctionBlock, TaskBlock, RawBlock, PreprocessorConditional, HeaderMetadata

# 文本读取工具服务模块接口提取路径。
from .textio import read_verilog_text

# RenameMixin 封装所有不会直接渲染文本的命名整理逻辑。
class RenameMixin:
    """封装 formatter 命名归一、body tree 改写和模块接口查找逻辑。"""

    # rename 阶段严格错误直接抛出，避免调用点拼接不稳定的错误前缀。
    def _raise_rename_error(self, category: str, statement: str, suggestion: str) -> None:
        """
        抛出 rename 阶段 strict mode formatter 异常。

        :param category: strict 检查类别，保持 formatter 诊断分类。
        :param statement: 触发错误的 Verilog 语句或源码片段。
        :param suggestion: 面向调用方的修复建议。
        :return: 无业务返回值，本函数始终抛出异常。
        :raises VerilogFormatterError: 始终抛出带分类、摘要和建议的 formatter 错误。
        """

        # str_summary 沿用语法工具的源码摘要口径，避免错误信息过宽。
        str_summary = self._summarize_statement(statement)  # strict 错误源码摘要

        # 异常文本带项目标准错误前缀，便于脚本日志归类。
        raise VerilogFormatterError(
            f"> ERR: [Python] Strict mode [{category}]: {str_summary}. Suggestion: {suggestion}"
        )

    # 参数改名表只比较 parser 原始参数和 normalize 后参数的一一对应关系。
    def _build_param_rename_map(
        self, raw_params: list[ParamDecl], normalized_params: list[ParamDecl]
    ) -> dict[str, str]:
        """
        构建 module header 参数的原名到规范名映射。

        :param raw_params: parser 读取到的原始参数声明列表。
        :param normalized_params: 已按 formatter 命名规则整理过的参数列表。
        :return: 原始参数名到规范参数名的映射。
        """

        # dict_mapping 记录同位置参数在 normalize 前后的名称差异。
        dict_mapping: dict[str, str] = {}  # 参数原名到规范名的映射

        # 按位置配对参数，避免同名参数重复时误跨项匹配。
        for raw_param, normalized_param in zip(raw_params, normalized_params):

            # 当前参数对写入后续表达式改名需要的映射表。
            dict_mapping[raw_param.name] = normalized_param.name  # 单个参数的重命名结果

        # 返回供 body 参数表达式同步改名的映射。
        return dict_mapping

    # 顶层 body 参数需要分成公开 parameter、状态编码和 localparam 三类。
    def _partition_top_level_body_params(
        self,
        params: list[ParamDecl],
        state_param_names: set[str] | None = None,
    ) -> tuple[list[ParamDecl], list[ParamDecl], list[ParamDecl]]:
        """
        将 body 参数分配到普通配置、状态编码和 localparam 区域。

        :param params: module body 中解析出的参数声明。
        :param state_param_names: 外部已确认的状态参数名集合；为空时由命名规则推断。
        :return: 普通 parameter、状态 parameter、localparam 三组声明。
        """

        # set_state_names 提供显式状态参数判定集合。
        set_state_names = state_param_names or set()  # 显式状态参数名称集合

        # list_nonstate_parameters 收集普通配置参数。
        list_nonstate_parameters: list[ParamDecl] = []  # 普通配置参数声明

        # list_state_parameters 收集状态机编码参数。
        list_state_parameters: list[ParamDecl] = []  # 状态编码参数声明

        # list_localparams 保留不应进入 module header 的局部参数。
        list_localparams: list[ParamDecl] = []  # localparam 声明列表

        # 按原始顺序遍历参数，保证渲染时不打乱用户分组。
        for param in params:

            # localparam 直接进入局部参数区域，不参与状态编码判断。
            if param.keyword != "parameter":

                # 保留 localparam 原始顺序供后续区域渲染。
                list_localparams.append(param)

                # 当前参数已归类，继续处理下一个声明。
                continue

            # 显式集合或命名规则命中时，参数进入状态编码区域。
            if param.name in set_state_names or (state_param_names is None and self._is_state_param(param.name)):

                # 状态参数单独输出，便于状态机区域保持可读。
                list_state_parameters.append(param)

            # 未命中状态规则的 parameter 保持普通配置语义。
            else:

                # 普通 parameter 进入配置参数区域。
                list_nonstate_parameters.append(param)

        # 返回三类参数列表，调用方负责区域渲染。
        return list_nonstate_parameters, list_state_parameters, list_localparams

    # 简单信号名提取只接受裸标识符，避免把表达式误当作可改名对象。
    def _extract_exact_simple_signal_name(self, text: str) -> str | None:
        """
        从声明或赋值片段中提取严格裸信号名。

        :param text: 可能带尾注释或分号的 Verilog 片段。
        :return: 裸信号名；无法确认时返回 None。
        """

        # str_candidate 去掉尾注释后保留可判定的代码片段。
        str_candidate, _ = self._split_comment(text)  # 去除注释后的候选信号片段

        # str_candidate 再移除空白和结尾分号。
        str_candidate = str_candidate.strip().rstrip(";")  # 规范化后的候选裸名

        # 只有完整匹配 Verilog 标识符时才允许进入改名映射。
        if not str_candidate or not re.fullmatch(r"[A-Za-z_]\w*", str_candidate):

            # 表达式、空串或复杂选择都不能作为简单信号名返回。
            return None

        # 返回确认安全的裸信号名。
        return str_candidate

    # 输出内部信号改名表把 assign 桥接中的代理线网改成标准后缀。
    def _build_output_internal_rename_map(
        self,
        ports: list[PortDecl],
        decls: list[SignalDecl],
        assigns: list[AssignStmt],
        rename_map: dict[str, str],
        output_internal_names: dict[str, str],
    ) -> dict[str, str]:
        """
        推断输出端口桥接信号需要采用的内部代理名。

        :param ports: module 端口声明列表。
        :param decls: body 中解析出的信号声明列表。
        :param assigns: body 中解析出的连续赋值列表。
        :param rename_map: 已有声明改名映射。
        :param output_internal_names: 输出端口到内部代理名的目标映射。
        :return: 可安全应用到声明名的输出代理改名表。
        """

        # set_output_ports 提供输出端口快速判定集合。
        set_output_ports = {port.name for port in ports if port.direction == "output"}  # 输出端口名称集合

        # set_used_names 汇总端口名和已分配代理名，避免新名冲突。
        set_used_names = set(set_output_ports) | set(output_internal_names.values())  # 已占用信号名集合

        # dict_decl_by_name 按规范化后的声明名查找原始声明。
        dict_decl_by_name: dict[str, SignalDecl | None] = {}  # 规范名到声明对象的映射

        # 先扫描声明，建立可用于右值代理识别的名称索引。
        for decl in decls:

            # 虚拟 assign 声明不代表真实可改名线网。
            if decl.kind == "__assign__":

                # 跳过 parser 为统一处理生成的占位声明。
                continue

            # str_new_name 是已有改名规则作用后的声明名。
            str_new_name = rename_map.get(decl.name, decl.name)  # 声明当前可见名称

            # 规范化后的名称也需要视为已占用。
            set_used_names.add(str_new_name)

            # 同一规范名出现多次时不能再安全反查到唯一声明。
            if str_new_name in dict_decl_by_name:

                # None 标记该规范名存在歧义，后续不用于输出代理改名。
                dict_decl_by_name[str_new_name] = None  # 歧义声明名称

                # 当前声明已登记为歧义，不再覆盖。
                continue

            # 唯一规范名可以反查到原始声明。
            dict_decl_by_name[str_new_name] = decl  # 唯一声明名称

        # dict_output_to_rhs 记录每个输出端口由哪个裸信号驱动。
        dict_output_to_rhs: dict[str, str] = {}  # 输出端口到驱动信号的映射

        # dict_rhs_to_outputs 反向识别一个右值是否驱动多个输出。
        dict_rhs_to_outputs: dict[str, set[str]] = {}  # 驱动信号到输出端口集合的映射

        # set_ambiguous_outputs 收集同一输出被多个右值赋值的情况。
        set_ambiguous_outputs: set[str] = set()  # 右值不唯一的输出端口集合

        # 遍历连续赋值，筛选形如 output = internal_signal 的桥接关系。
        for assign in assigns:

            # str_lhs_name 只在赋值左侧是裸信号时有值。
            str_lhs_name = self._extract_exact_simple_signal_name(assign.lhs)  # 赋值左侧裸信号名

            # 右侧裸名用于定位可被输出端口代理化的内部信号。
            str_rhs_name = self._extract_exact_simple_signal_name(assign.rhs)  # 赋值右侧裸信号名

            # 只处理输出端口由单个内部信号驱动的桥接。
            if str_lhs_name not in set_output_ports or not str_rhs_name:

                # 非输出桥接或复杂表达式不参与内部代理改名。
                continue

            # str_previous_rhs 用于发现同一输出端口的多驱动歧义。
            str_previous_rhs = dict_output_to_rhs.get(str_lhs_name)  # 当前输出已记录的右值名

            # 一个输出端口对应多个不同右值时不能安全推断代理名。
            if str_previous_rhs is not None and str_previous_rhs != str_rhs_name:

                # 记录歧义输出，后续跳过该端口。
                set_ambiguous_outputs.add(str_lhs_name)

                # 当前赋值不再进入一对一映射。
                continue

            # 输出端口与右值的单向关系用于后续生成目标名。
            dict_output_to_rhs[str_lhs_name] = str_rhs_name  # 输出端口到右值裸名的映射

            # 反向集合用于检测右值被多个输出复用。
            dict_rhs_to_outputs.setdefault(str_rhs_name, set()).add(str_lhs_name)

        # dict_mapping 是最终可合并到声明改名表的安全子集。
        dict_mapping: dict[str, str] = {}  # 原始声明名到输出代理名的映射

        # 逐个输出桥接关系确认是否具备唯一声明和无冲突目标名。
        for str_output_name, str_rhs_name in dict_output_to_rhs.items():

            # 歧义输出端口不生成内部代理改名。
            if str_output_name in set_ambiguous_outputs:

                # 保留原始声明，避免错误折叠多驱动关系。
                continue

            # 同一个右值驱动多个输出时，不能归属到单个输出代理名。
            if len(dict_rhs_to_outputs.get(str_rhs_name, set())) != 1:

                # 多输出复用右值保持原名。
                continue

            # 已经是目标代理名的右值不需要再次改名。
            if str_rhs_name in output_internal_names.values():

                # 现有名称已满足输出代理约定。
                continue

            # 缺少声明或存在重复声明时不做自动改名。
            if str_rhs_name not in dict_decl_by_name:

                # 无法定位唯一声明时放弃该桥接关系。
                continue

            # signal_decl_rhs_signal_decl 是右值信号对应的唯一声明。
            signal_decl_rhs_signal_decl: SignalDecl = dict_decl_by_name[str_rhs_name]  # 右值桥接信号声明

            # 只允许普通线网或寄存器代理改名，数组等复杂声明保持原状。
            if signal_decl_rhs_signal_decl.kind not in {"wire", "reg", "logic"} or signal_decl_rhs_signal_decl.unpacked:

                # 复杂声明可能存在维度语义，避免改名误导渲染。
                continue

            # 状态信号前缀不应被输出代理规则覆盖。
            if rename_map.get(signal_decl_rhs_signal_decl.name, signal_decl_rhs_signal_decl.name).startswith(
                self.config["naming"]["state_signal_prefix"]
            ):

                # 状态相关声明保留状态机命名职责。
                continue

            # str_internal_output_suffix 是项目配置的输出代理后缀。
            str_internal_output_suffix = self.config["naming"]["internal_output_suffix"]  # 输出代理后缀

            # str_output_base_name 是输出端口转换后的内部代理基础名。
            str_output_base_name = self._normalize_internal_output_base_name(str_output_name)  # 输出代理基础名

            # str_default_target_name 是未显式配置时推断出的内部代理名。
            str_default_target_name = f"{str_output_base_name}{str_internal_output_suffix}"  # 默认输出代理目标名

            # str_target_name 是输出端口对应的内部代理目标名。
            str_target_name = output_internal_names.get(str_output_name, str_default_target_name)  # 输出代理目标名

            # 右值已经等于目标名时无需写入映射。
            if str_target_name == str_rhs_name:

                # 避免生成无意义的自映射。
                continue

            # 目标名被其它信号占用时不能覆盖。
            if str_target_name in set_used_names and str_target_name != str_rhs_name:

                # 名称冲突时保持原始声明。
                continue

            # 既有重命名已经得到目标名时不重复记录。
            if rename_map.get(signal_decl_rhs_signal_decl.name, signal_decl_rhs_signal_decl.name) == str_target_name:

                # 旧映射已经覆盖此桥接关系。
                continue

            # 安全确认后的声明进入输出代理改名表。
            dict_mapping[signal_decl_rhs_signal_decl.name] = str_target_name  # 原声明名到代理目标名

            # 新目标名加入占用集合，防止同轮重复分配。
            set_used_names.add(str_target_name)

        # 返回只包含安全改名项的输出代理映射。
        return dict_mapping

    # 链式改名需要压平，避免 a->b、b->c 这类中间名泄露到渲染输出。
    def _resolve_rename_map_chains(
        self, rename_map: dict[str, str], terminal_names: set[str] | None = None
    ) -> dict[str, str]:
        """
        将多段重命名链压平成最终目标名。

        :param rename_map: 原名到中间名或最终名的映射。
        :param terminal_names: 必须停止追踪的外部保留名集合。
        :return: 原名到最终可见名的映射。
        """

        # set_terminal_names 标记不应继续追踪的外部名称。
        set_terminal_names = terminal_names or set()  # 重命名链停止名称集合

        # dict_resolved 保存压平后的最终映射。
        dict_resolved: dict[str, str] = {}  # 链路压平映射

        # 遍历每个原始名称并沿映射链追踪到最终名称。
        for str_name in rename_map:

            # str_target 保存当前追踪到的目标名称。
            str_target = rename_map[str_name]  # 当前链路目标名

            # set_visited 防止循环映射导致无限追踪。
            set_visited = {str_name}  # 当前重命名链已访问名称

            # 仅当目标仍在映射内且未命中终止条件时继续压平。
            while str_target in rename_map and str_target not in set_visited and str_target not in set_terminal_names:

                # 记录已访问目标以便检测环。
                set_visited.add(str_target)

                # 沿链路跳到下一段目标。
                str_target = rename_map[str_target]  # 压平过程中的下一目标名

            # 当前原名对应最终目标名。
            dict_resolved[str_name] = str_target  # 原名到最终名的压平结果

        # 返回压平后的映射供后续统一替换。
        return dict_resolved

    # 声明改名表负责将状态、寄存器和后缀驱动信号整理到项目命名约定。
    def _build_decl_rename_map(
        self, decls: list[SignalDecl], always_blocks: list[AlwaysBlock] | None = None
    ) -> dict[str, str]:
        """
        为 body 中的信号声明构建基础改名表。

        :param decls: parser 提取的信号声明列表。
        :param always_blocks: 可选 always 块列表，用于识别真实状态寄存器。
        :return: 原始声明名到规范声明名的映射。
        """

        # dict_mapping 汇总状态机和普通声明的改名结果。
        dict_mapping: dict[str, str] = {}  # 声明原名到规范名的映射

        # always 信息存在时，优先识别真实状态寄存器。
        if always_blocks:

            # 状态机专用映射优先级高于普通寄存器前缀规则。
            dict_mapping.update(self._build_true_fsm_state_rename_map(decls, always_blocks))

        # 遍历声明列表，补充非状态声明的命名规范。
        for decl in decls:

            # str_original_name 是 parser 捕获到的声明原名。
            str_original_name = decl.name  # 原始声明名称

            # 内部输出代理后缀由输出桥接逻辑单独管理。
            if str_original_name.endswith(self.config["naming"]["internal_output_suffix"]):

                # 输出代理信号保持桥接阶段确定的名称。
                continue

            # 已由状态机识别阶段确认的声明不再重复处理。
            if str_original_name in dict_mapping:

                # 保留更高优先级的状态机映射。
                continue

            # str_lowered_name 用于大小写无关的状态关键词判定。
            str_lowered_name = str_original_name.lower()  # 小写声明名称

            # str_base_name 去除已有已知前缀，避免叠加重复前缀。
            str_base_name = self._strip_known_prefixes(str_original_name)  # 去前缀后的声明基础名

            # str_normalized_category_name 由 counter/flag 等后缀规则推导。
            str_normalized_category_name = self._normalize_suffix_driven_signal_name(str_original_name)  # 后缀规则规范名

            # 后缀语义明确时优先使用对应类别前缀。
            if str_normalized_category_name is not None:

                # 后缀驱动的命名结果写入声明映射。
                dict_mapping[str_original_name] = str_normalized_category_name  # 后缀语义规范名

            # 名称中含 state 的声明按状态信号前缀处理。
            elif "state" in str_lowered_name:

                # str_state_prefix 是项目配置的状态信号前缀。
                str_state_prefix = self.config["naming"]["state_signal_prefix"]  # 状态信号前缀

                # str_state_name 是按状态前缀规范化后的声明名。
                str_state_name = self._apply_prefix(str_base_name, str_state_prefix)  # 状态信号规范名

                # 状态类声明映射到统一前缀，便于 renderer 汇总状态机区域。
                dict_mapping[str_original_name] = str_state_name  # 状态类声明目标名

            # 已有受控类别前缀的声明不再重写。
            elif self._has_managed_signal_category_prefix(str_original_name):

                # 保留用户已经采用的 counter/flag 等规范前缀。
                continue

            # 普通 reg/logic 声明默认补寄存器前缀。
            elif decl.kind in {"reg", "logic"}:

                # str_register_prefix 是项目配置的寄存器信号前缀。
                str_register_prefix = self.config["naming"]["register_prefix"]  # 寄存器信号前缀

                # str_register_name 是按寄存器前缀规范化后的声明名。
                str_register_name = self._apply_prefix(str_base_name, str_register_prefix)  # 寄存器规范名

                # 普通时序寄存器使用配置前缀，避免与 wire 代理命名混在一起。
                dict_mapping[str_original_name] = str_register_name  # 时序寄存器目标名

        # 返回基础声明改名表。
        return dict_mapping

    # 声明归一化仅把 logic 渲染为 reg，保持其它字段原样。
    def _normalize_decl(self, decl: SignalDecl) -> SignalDecl:
        """
        归一化单条信号声明的类型关键字。

        :param decl: parser 提取的信号声明。
        :return: kind 已转换为渲染兼容形式的声明对象。
        """

        # 声明类型转换为 renderer 兼容关键字。
        str_render_kind = decl.kind.replace("logic", "reg")  # renderer 使用的声明类型

        # 返回新的声明对象，避免修改 parser 原始模型。
        return SignalDecl(
            kind=str_render_kind,
            width=decl.width,
            name=decl.name,

            # 原声明的表达式和注释元数据需要原样透传。
            init=decl.init,
            comment=decl.comment,
            leading_comments=list(decl.leading_comments),
            signed=decl.signed,
            unpacked=decl.unpacked,

            # 属性和后缀属于 renderer 回贴边界，不能在类型归一化时丢失。
            attributes=decl.attributes,
            suffix=decl.suffix,
        )

    # 连续赋值当前不改写，保留扩展钩子以匹配 normalize 阶段接口。
    def _normalize_assign(self, assign: AssignStmt) -> AssignStmt:
        """
        保留连续赋值对象的 normalize 扩展点。

        :param assign: parser 提取的连续赋值语句。
        :return: 原始赋值对象。
        """

        # 当前阶段不改变 assign 结构。
        return assign

    # always 块归一化当前交由分析函数处理，本钩子保持对象身份。
    def _normalize_always(self, always: AlwaysBlock) -> AlwaysBlock:
        """
        保留 always 块对象的 normalize 扩展点。

        :param always: parser 提取的 always 块。
        :return: 原始 always 块对象。
        """

        # always 块在分析阶段已经携带区域信息，这里保持对象身份。
        return always

    # always 块分析用于给 renderer 标注组合、时序、状态机等区域类别。
    def _analyze_always_block(
        self, header: str, lines: list[str], targets: list[str],
        raw_block: str, *, lvalues: list[LValueRef] | None = None
    ) -> AlwaysBlock:
        """
        分析 always 块触发类型和语义区域。

        :param header: always 语句头。
        :param lines: always 块内部代码行。
        :param targets: always 块内被赋值的目标信号名。
        :param raw_block: always 块原始文本。
        :param lvalues: 可选的左值分析结果，缺省时从原始文本提取。
        :return: 带 block_kind、触发信息和左值风险标记的 always 模型。
        :raises VerilogFormatterError: 控制流无法被内置后端完整解析时抛出。
        """

        # bool_is_combinational 标记 always@(*) 或 always@* 组合逻辑。
        bool_is_combinational = "@(*)" in header.replace(" ", "") or "@*" in header.replace(" ", "")  # 是否组合逻辑

        # list_event_names 提取 posedge/negedge 后的时钟和复位候选。
        list_event_names = re.findall(r"(?:posedge|negedge)\s+([A-Za-z_]\w*)", header)  # 事件控制信号名列表

        # str_clock_name 取第一个事件控制信号作为时钟候选。
        str_clock_name = list_event_names[0] if list_event_names else ""  # always 时钟信号名

        # str_reset_name 取第二个事件控制信号作为复位候选。
        str_reset_name = list_event_names[1] if len(list_event_names) > 1 else ""  # always 复位信号名

        # str_trigger_kind 默认标记为未知触发形式。
        str_trigger_kind = "unknown"  # always 触发类型

        # 组合逻辑触发优先归类为 comb。
        if bool_is_combinational:

            # always@* 或 always@(*) 归为组合逻辑。
            str_trigger_kind = "comb"  # 组合逻辑触发类型

        # 存在边沿事件但非组合逻辑时归类为时序逻辑。
        elif list_event_names:

            # posedge/negedge 事件归为时序触发。
            str_trigger_kind = "seq"  # 时序逻辑触发类型

        # bool_contains_case 标记 always 内是否存在 case 结构。
        bool_contains_case = "case" in raw_block  # 是否包含 case 语句

        # bool_contains_if 仅做文本层快速标记，结构化分支由后续 parser 确认。
        bool_contains_if = bool(re.search(r"\bif\b", raw_block))  # 文本层条件分支标记

        # str_state_reference_pattern 覆盖项目兼容的状态信号命名形式。
        str_state_reference_pattern = r"\b(state_(?:cur|current|next)|cur_state|next_state|state_current|state_next)\b"  # 状态引用匹配表达式

        # bool_references_state 检查 always 文本是否引用常见状态信号名。
        bool_references_state = bool(re.search(str_state_reference_pattern, raw_block))  # 是否引用状态信号

        # list_lvalue_refs 默认复用调用方传入的左值分析。
        list_lvalue_refs = lvalues  # 调用方传入的左值引用列表

        # 缺省左值分析时从原始 always 文本现场提取。
        if list_lvalue_refs is None:

            # str_lvalue_violation_code 标识左值规范化失败的严格错误类别。
            str_lvalue_violation_code = "lvalue_normalization_violation"  # 左值规范化错误类别

            # 现场提取 always 块左值引用。
            list_lvalue_refs = self._extract_lvalues_from_text(raw_block, str_lvalue_violation_code)  # 解析得到的左值引用列表

        # bool_has_complex_lvalues 标记数组或位选等复杂左值。
        bool_has_complex_lvalues = any(lvalue.is_complex for lvalue in list_lvalue_refs)  # 是否包含复杂左值

        # 状态目标赋值通常表示状态转移 always。
        if any(t.startswith(self.config["naming"]["state_signal_prefix"]) for t in targets):

            # str_block_kind 默认按状态转移时序块处理。
            str_block_kind = "state_transition_always"  # 状态转移 always 区域

            # 组合逻辑直接赋值状态目标时归入组合 always。
            if bool_is_combinational:

                # 状态目标组合块仍由组合逻辑区域渲染。
                str_block_kind = "always_comb"  # 状态目标组合 always 区域

        # 引用状态但不直接赋值状态目标时，视为状态相关任务逻辑。
        elif bool_references_state:

            # str_block_kind 标记状态相关任务逻辑。
            str_block_kind = "state_task_always"  # 状态依赖任务 always 区域

        # 普通块按组合或主任务时序逻辑分类。
        else:

            # str_block_kind 默认归入主任务时序逻辑区域。
            str_block_kind = "main_task_always"  # 主任务时序逻辑区域

            # 普通组合逻辑归入 always_comb 区域。
            if bool_is_combinational:

                # 组合块使用组合逻辑区域名称。
                str_block_kind = "always_comb"  # 普通组合 always 区域

        # 含预处理宏的 always 块不能安全拆成控制节点。
        if any(self._normalize_statement_line(line.strip()).startswith("`") for line in lines):

            # 宏保护块按原始行保守保留，不进入结构化控制节点。
            list_empty_nodes: list[ControlNode] = []  # 宏块禁用的控制节点列表

            # 返回保守模型，避免控制流 parser 跨宏误解析。
            return AlwaysBlock(
                header=header,
                lines=lines,
                targets=targets,

                # 触发信息仍来自 always 头部初步分析结果。
                clock=str_clock_name,
                reset=str_reset_name,
                trigger_kind=str_trigger_kind,
                is_combinational=bool_is_combinational,

                # 语义标志保留给 renderer 做区域选择。
                contains_case=bool_contains_case,
                contains_if=bool_contains_if,
                references_state=bool_references_state,
                block_kind=str_block_kind,

                # 宏场景禁用结构化节点，但保留左值风险信息。
                nodes=list_empty_nodes,
                lvalues=list_lvalue_refs,
                has_complex_lvalues=bool_has_complex_lvalues,
            )

        # tuple_control_parse 接收结构化控制节点和已消费行数。
        tuple_control_parse = self._parse_control_nodes(lines, 0, set(), "always")  # 控制节点解析结果

        # list_nodes 展开 parser 返回的控制节点。
        list_nodes = tuple_control_parse[0]  # always 控制节点列表

        # int_consumed 记录控制流 parser 成功消费的源码行数。
        int_consumed = tuple_control_parse[1]  # 控制流 parser 消费行数

        # parser 未消费完整 always 块时，内置后端不能安全归一化。
        if int_consumed != len(lines):

            # 抛出严格模式错误，提示调用方简化 always 控制流形状。
            self._raise_rename_error(
                "unsupported_shape",
                raw_block,
                "simplify unsupported always control flow.",
            )

        # 完整解析后的节点列表交给后续重命名和 renderer 复用。
        list_validated_nodes = list_nodes  # 已完整消费的 always 控制节点

        # 返回完整控制节点模型，供 rename 和 renderer 复用。
        return AlwaysBlock(
            header=header,
            lines=lines,
            targets=targets,

            # 触发信息由头部 sensitivity list 和组合逻辑判定共同决定。
            clock=str_clock_name,
            reset=str_reset_name,
            trigger_kind=str_trigger_kind,
            is_combinational=bool_is_combinational,

            # 结构特征帮助 renderer 选择状态、组合或主任务区域。
            contains_case=bool_contains_case,
            contains_if=bool_contains_if,
            references_state=bool_references_state,
            block_kind=str_block_kind,

            # 完整控制树和左值信息支撑后续 rename 与重排。
            nodes=list_validated_nodes,
            lvalues=list_lvalue_refs,
            has_complex_lvalues=bool_has_complex_lvalues,
        )

    # 参数改名会同步名称、默认值、声明修饰和保留 raw_text。
    def _rename_params(self, params: list[ParamDecl], rename_map: dict[str, str]) -> list[ParamDecl]:
        """
        按统一重命名映射改写参数声明列表。

        :param params: 待改名的参数声明列表。
        :param rename_map: 原始名称到规范名称的映射。
        :return: 已同步名称、表达式和原始文本的参数声明列表。
        """

        # list_result 按原始顺序收集改名后的参数对象。
        list_result: list[ParamDecl] = []  # 改名后的参数声明列表

        # 逐个参数同步名称和所有可能引用参数名的文本字段。
        for param in params:

            # 当前参数改名后作为新模型加入结果列表。
            list_result.append(
                ParamDecl(
                    keyword=param.keyword,
                    name=rename_map.get(param.name, param.name),
                    value=self._rename_text(param.value, rename_map),

                    # 声明修饰和注释跟随参数整体移动。
                    decl_spec=self._rename_text(param.decl_spec, rename_map),
                    comment=param.comment,
                    leading_comments=list(param.leading_comments),

                    # raw_text 只在 parser 提供原文时同步改名。
                    raw_text=self._rename_text(param.raw_text, rename_map) if param.raw_text else "",
                    synthetic=param.synthetic,
                )
            )

        # 返回供渲染阶段直接使用的参数列表。
        return list_result

    # 信号声明改名需要兼顾 inline wire assign 拆分和缺失输出代理补齐。
    def _rename_decls(
        self, decls: list[SignalDecl], rename_map: dict[str, str], output_internal_names: dict[str, str],
        output_signal_layouts: dict[str, OutputSignalLayout], *, add_missing_output_internals: bool = True,
    ) -> list[SignalDecl]:
        """
        改写信号声明并按配置拆分禁止内联的 wire 初始化。

        :param decls: parser 提取的信号声明列表。
        :param rename_map: 原始名称到规范名称的映射。
        :param output_internal_names: 输出端口到内部代理名的映射。
        :param output_signal_layouts: 输出代理信号的布局信息。
        :param add_missing_output_internals: 是否补齐未显式声明的输出代理信号。
        :return: 可直接渲染的声明列表。
        """

        # list_result 按渲染顺序收集改名后的声明和拆分出的 assign 占位。
        list_result: list[SignalDecl] = []  # 改名后的声明列表

        # set_declared_output_internals 先假设所有输出代理都缺失，遍历时逐个剔除。
        set_declared_output_internals = set(output_internal_names.values())  # 尚未声明的输出代理名集合

        # 遍历声明并同步所有可能包含标识符引用的字段。
        for decl in decls:

            # str_new_name 是当前声明改名后的可见名称。
            str_new_name = rename_map.get(decl.name, decl.name)  # 声明规范化名称

            # str_init 是改名后的初始化表达式。
            str_init = self._rename_text(decl.init, rename_map)  # 初始化表达式

            # str_width 是改名后的位宽表达式。
            str_width = self._rename_text(decl.width, rename_map)  # 位宽表达式

            # str_unpacked 是改名后的 unpacked 维度表达式。
            str_unpacked = self._rename_text(decl.unpacked, rename_map)  # unpacked 维度表达式

            # str_attributes 是改名后的属性文本。
            str_attributes = self._rename_text(decl.attributes, rename_map)  # 属性文本

            # str_suffix 是改名后的声明后缀。
            str_suffix = self._rename_text(decl.suffix, rename_map)  # 声明后缀文本

            # 禁止 inline wire assign 时，把声明和 assign 占位拆成两项。
            if decl.init and decl.kind == "wire" and self.config["wire_assign_rules"]["forbid_inline_wire_assign"]:

                # 先加入无初始化的 wire 声明，保留原始注释和属性。
                list_result.append(
                    SignalDecl(
                        kind="wire",
                        width=str_width,
                        name=str_new_name,

                        # inline 初始化被拆走后，wire 声明自身不再携带 init。
                        init="",
                        comment=decl.comment,
                        signed=decl.signed,
                        unpacked=str_unpacked,

                        # 属性、后缀和前导注释仍属于声明节点。
                        attributes=str_attributes,
                        suffix=str_suffix,
                        leading_comments=list(decl.leading_comments),
                    )
                )

                # wire 初始化表达式移到 assign 占位节点中，由 renderer 输出连续赋值。
                list_assign_leading_comments = list(decl.leading_comments)  # inline assign 继承的前导注释

                # 再加入内部 assign 占位，交给 renderer 输出连续赋值。
                list_result.append(
                    SignalDecl(
                        kind="__assign__",
                        width=str_width,
                        name=str_new_name,

                        # assign 占位保留原 inline 初始化表达式。
                        init=str_init,
                        comment=decl.comment,
                        signed=decl.signed,
                        unpacked=str_unpacked,

                        # renderer 需要这些字段恢复完整连续赋值上下文。
                        attributes=str_attributes,
                        suffix=str_suffix,
                        leading_comments=list_assign_leading_comments,
                    )
                )

                # 当前声明已拆分完成，继续处理下一条声明。
                continue

            # 普通声明保留前导注释并按改名后的字段重新构造。
            list_decl_leading_comments = list(decl.leading_comments)  # 普通声明前导注释副本

            # 普通声明按改名后的字段重新构造。
            list_result.append(
                SignalDecl(
                    kind=decl.kind,
                    width=str_width,
                    name=str_new_name,

                    # init 需要按声明类型和数组维度决定是否保留。
                    init=self._default_decl_init(decl.kind, str_init, str_unpacked),
                    comment=decl.comment,
                    signed=decl.signed,
                    unpacked=str_unpacked,

                    # 保留 parser 无法进一步结构化的声明附加片段。
                    attributes=str_attributes,
                    suffix=str_suffix,
                    leading_comments=list_decl_leading_comments,
                )
            )

            # 已经显式声明的输出代理不再由补齐逻辑追加。
            set_declared_output_internals.discard(str_new_name)

        # 配置允许时，为 bridge assign 需要但源码缺失的输出代理补声明。
        if add_missing_output_internals:

            # 稳定排序让补齐声明在不同平台上保持确定性。
            for internal_name in sorted(set_declared_output_internals):

                # output_signal_layout_output_signal_layout 提供代理声明的位宽、有符号和属性信息。
                output_signal_layout_output_signal_layout: OutputSignalLayout = OutputSignalLayout()  # 默认补齐代理声明布局

                # 已记录布局时优先沿用原输出端口的声明属性。
                if internal_name in output_signal_layouts:

                    # 输出代理声明继承已有端口布局。
                    output_signal_layout_output_signal_layout: OutputSignalLayout = output_signal_layouts[internal_name]  # 已识别代理声明布局

                # 缺失代理声明没有源码前导注释，使用空列表保持模型字段完整。
                list_missing_leading_comments: list[str] = []  # 补齐代理前导注释

                # str_output_unpacked 是补齐代理声明需要继承的 unpacked 维度。
                str_output_unpacked = output_signal_layout_output_signal_layout.unpacked  # 输出代理 unpacked 维度

                # str_default_init 是补齐代理声明的 renderer 兼容初始化文本。
                str_default_init = self._default_decl_init("reg", "", str_output_unpacked)  # 补齐代理默认初始化

                # 缺失代理默认按 reg 声明，便于时序块驱动。
                list_result.append(
                    SignalDecl(
                        "reg",
                        output_signal_layout_output_signal_layout.width,
                        internal_name,
                        str_default_init,
                        "internal output signal",
                        output_signal_layout_output_signal_layout.signed,
                        output_signal_layout_output_signal_layout.unpacked,
                        output_signal_layout_output_signal_layout.attributes,
                        leading_comments=list_missing_leading_comments,
                    )
                )

        # 返回声明改名和补齐后的完整列表。
        return list_result

    # 默认初始化只在渲染兼容性需要时补入，避免改动显式用户表达式。
    def _default_decl_init(self, kind: str, init: str, unpacked: str) -> str:
        """
        为未显式初始化的标量 reg 生成安全默认值。

        :param kind: 声明类型关键字。
        :param init: 原始或已改名的初始化表达式。
        :param unpacked: unpacked 维度文本。
        :return: 应写入声明模型的初始化表达式。
        """

        # 已有初始化、非 reg 或数组声明都保持原样。
        if init or kind != "reg" or unpacked:

            # 返回调用方传入的初始化表达式。
            return init

        # 标量 reg 缺省初始化为 0。
        return "0"

    # 连续赋值改名保持语句顺序，仅替换标识符引用。
    def _rename_assigns(self, assigns: list[AssignStmt], rename_map: dict[str, str]) -> list[AssignStmt]:
        """
        按统一映射改写连续赋值语句。

        :param assigns: parser 提取的连续赋值语句列表。
        :param rename_map: 原始名称到规范名称的映射。
        :return: 已改写左右值、延时和注释边界的赋值列表。
        """

        # list_result 按 parser 顺序收集改名后的连续赋值。
        list_result: list[AssignStmt] = []  # 连续赋值改名输出列表

        # 逐条替换 assign 左右值和可选延迟片段。
        for assign in assigns:

            # assign_stmt_renamed 保存当前连续赋值的文本替换结果。
            assign_stmt_renamed: AssignStmt = AssignStmt(  # 当前 assign 替换模型
                lhs=self._rename_text(assign.lhs, rename_map),  # assign 左侧替换结果
                rhs=self._rename_text(assign.rhs, rename_map),  # assign 右侧替换结果

                # 注释边界直接继承原 assign 节点。
                comment=assign.comment,  # 原 assign 行尾说明
                leading_comments=list(assign.leading_comments),  # 原 assign 前导说明
                delay=self._rename_text(assign.delay, rename_map) if assign.delay else "",  # assign 延迟替换结果
            )

            # 当前 assign 节点加入保序输出列表。
            list_result.append(assign_stmt_renamed)

        # 返回完成文本替换的连续赋值列表。
        return list_result

    # always 改名后必须重新分析，确保目标集合和 block_kind 与新名称一致。
    def _rename_always(self, always_blocks: list[AlwaysBlock], rename_map: dict[str, str]) -> list[AlwaysBlock]:
        """
        改写 always 块文本并重新分析其结构化语义。

        :param always_blocks: parser 提取的 always 块列表。
        :param rename_map: 原始名称到规范名称的映射。
        :return: 已改名并重新分析的 always 块列表。
        """

        # list_result 按原始顺序收集改名后的 always 模型。
        list_result: list[AlwaysBlock] = []  # always 重新分析输出队列

        # 每个 always 改名后都需要重新分析触发和目标语义。
        for block in always_blocks:

            # str_header 是替换信号名后的 always 头部。
            str_header = self._rename_text(block.header, rename_map)  # sensitivity 头部替换结果

            # list_lines 是逐行替换信号名后的 always 主体。
            list_lines = [self._rename_text(line, rename_map) for line in block.lines]  # always 主体替换行

            # list_targets 是改名后的赋值目标集合输入。
            list_targets = [rename_map.get(target, target) for target in block.targets]  # 目标信号替换集合

            # str_renamed_always_source 汇总改名后的 always 源码，供重新分析和错误摘要复用。
            str_renamed_always_source = "\n".join([str_header, *list_lines])  # 重新分析用源码

            # list_unique_targets 是重新分析时使用的稳定目标集合。
            list_unique_targets = sorted(set(list_targets))  # always 去重目标列表

            # always_block_renamed_block 重新承载改名后的控制流分析结果。
            always_block_renamed_block: AlwaysBlock = self._analyze_always_block(  # rename 后 always 语义模型
                str_header,  # 已替换 sensitivity 头部
                list_lines,  # 已替换 always 主体
                list_unique_targets,  # 重新分析目标集合
                str_renamed_always_source,  # 控制流复核源码
            )

            # 保留 always 块前导注释，避免 rename 过程丢失分区说明。
            always_block_renamed_block.leading_comments = list(block.leading_comments)  # 原 always 说明行

            # 当前 always 块加入输出列表。
            list_result.append(always_block_renamed_block)

        # 返回全部改名后的 always 块。
        return list_result

    # initial 块改名需要保护 parameter_check 的严格形态。
    def _rename_initial_blocks(
        self, initial_blocks: list[InitialBlock], rename_map: dict[str, str]
    ) -> list[InitialBlock]:

        """
        改写 initial 块并维护 parameter_check 的结构约束。

        :param initial_blocks: parser 提取的 initial 块列表。
        :param rename_map: 原始名称到规范名称的映射。
        :return: 已改名并重新验证控制节点的 initial 块列表。
        :raises VerilogFormatterError: 非 parameter_check initial 无法重新解析时抛出。
        """

        # list_result 按源码顺序收集已经完成结构复核的 initial 块。
        list_result: list[InitialBlock] = []  # initial 改名输出队列

        # 逐块处理，优先复用 parser 已提供的结构化节点。
        for block in initial_blocks:

            # initial 头部只包含块入口关键字，仍需同步可能出现的标识符。
            str_header = self._rename_text(block.header, rename_map)  # initial 入口关键字文本

            # list_lines 是替换语句文本后的 initial 主体。
            list_lines = [self._rename_statement_text(line, rename_map) for line in block.lines]  # initial 可执行语句

            # 已有控制节点时直接改写节点树，避免重新解析造成结构差异。
            if block.nodes:

                # list_renamed_nodes 是改名后的 initial 控制节点树。
                list_renamed_nodes = self._rename_control_nodes(block.nodes, rename_map)  # initial 结构化语句树

                # parameter_check 块必须保持受支持的检查形态。
                if block.block_kind == "parameter_check":

                    # 验证重命名后的参数检查节点仍然符合约束。
                    self._validate_parameter_check_nodes(list_renamed_nodes)

                # list_initial_leading_comments 保留 initial 块前导说明。
                list_initial_leading_comments = list(block.leading_comments)  # initial 前导注释副本

                # 改名后的结构化 initial 块加入输出列表。
                list_result.append(
                    InitialBlock(
                        header=str_header,
                        lines=list_lines,

                        # 结构化节点和块分类必须保持同步。
                        nodes=list_renamed_nodes,
                        leading_comments=list_initial_leading_comments,
                        block_kind=block.block_kind,  # initial 语义分类
                    )
                )

                # 当前 initial 块处理完毕。
                continue

            # 缺少节点时尝试从改名后的文本重新解析控制结构。
            try:

                # tuple_control_parse 接收节点列表和已消费行数。
                tuple_control_parse = self._parse_control_nodes(list_lines, 0, set(), "initial")  # initial 复核解析结果

                # list_nodes 是 initial 块重新解析出的控制节点。
                list_nodes = tuple_control_parse[0]  # initial 复核节点列表

                # int_consumed 是 parser 成功消费的主体行数。
                int_consumed = tuple_control_parse[1]  # initial 主体消费行数

                # parser 必须完整消费 parameter_check，才能保证改名无歧义。
                if int_consumed != len(list_lines):

                    # 严格错误提示调用方简化 parameter_check initial 块。
                    self._raise_rename_error(
                        "unsupported_shape",
                        "\n".join([str_header, *list_lines]),
                        "simplify unsupported parameter-check initial flow.",
                    )

                # parameter_check 块需要额外验证检查语句形态。
                if block.block_kind == "parameter_check":

                    # 验证重新解析出的参数检查节点。
                    self._validate_parameter_check_nodes(list_nodes)

                # list_initial_leading_comments 保留重新解析成功块的前导说明。
                list_initial_leading_comments = list(block.leading_comments)  # 复核成功块说明副本

                # 解析成功的 initial 块加入输出列表。
                list_result.append(
                    InitialBlock(
                        header=str_header,
                        lines=list_lines,

                        # 重新解析出的节点需要连同原块分类一起输出。
                        nodes=list_nodes,
                        leading_comments=list_initial_leading_comments,
                        block_kind=block.block_kind,  # 复核路径语义分类
                    )
                )

            # 非参数检查块必须继续暴露解析错误，参数检查块可保守保留文本。
            except VerilogFormatterError:

                # 只有 parameter_check 允许降级为空节点，避免误吞普通 initial 错误。
                if block.block_kind != "parameter_check":

                    # 普通 initial 解析错误交由上层严格处理。
                    raise

                # list_empty_nodes 表示 parameter_check 降级为文本保留模式。
                list_empty_nodes: list[ControlNode] = []  # parameter_check 降级节点列表

                # list_initial_leading_comments 保留降级块的原始说明。
                list_initial_leading_comments = list(block.leading_comments)  # 降级块说明副本

                # parameter_check 降级时仍保留改名后的文本和前导注释。
                list_result.append(
                    InitialBlock(
                        header=str_header,  # 降级保留的 initial 头部
                        lines=list_lines,  # 降级保留的 initial 主体

                        # 降级模式明确输出空控制节点，renderer 只使用文本行。
                        nodes=list_empty_nodes,  # 降级时不输出结构化节点
                        leading_comments=list_initial_leading_comments,  # 降级路径保留说明
                        block_kind=block.block_kind,  # parameter_check 分类标记
                    )
                )

        # 返回经过 parameter_check 保护处理的 initial 块。
        return list_result

    # 原始文本块只做语句级重命名，不尝试理解其内部结构。
    def _rename_raw_blocks(self, raw_blocks: list[RawBlock], rename_map: dict[str, str]) -> list[RawBlock]:

        """
        改写无法结构化解析的原始文本块。

        :param raw_blocks: parser 保留下来的原始块列表。
        :param rename_map: 原始名称到规范名称的映射。
        :return: 已完成语句文本替换的原始块列表。
        """

        # list_result 按原始顺序收集改名后的 raw block。
        list_result: list[RawBlock] = []  # 改名后的原始块列表

        # 逐个 raw block 替换内部语句文本。
        for block in raw_blocks:

            # 当前 raw block 重建时保留前导注释。
            list_result.append(
                RawBlock(
                    lines=[self._rename_statement_text(line, rename_map) for line in block.lines],
                    leading_comments=list(block.leading_comments),
                )
            )

        # 返回改名后的 raw block 列表。
        return list_result

    # function 块内部语句可重命名，但边界和前导注释保持原样。
    def _rename_function_blocks(
        self, function_blocks: list[FunctionBlock], rename_map: dict[str, str]
    ) -> list[FunctionBlock]:

        """
        改写 Verilog function 块内部语句。

        :param function_blocks: parser 提取的 function 块列表。
        :param rename_map: 原始名称到规范名称的映射。
        :return: 已改写语句文本的 function 块列表。
        """

        # list_result 保持 function 块原始排列，避免改名阶段改变声明顺序。
        list_result: list[FunctionBlock] = []  # 保序 function 块输出列表

        # 逐个 function block 替换其内部语句。
        for block in function_blocks:

            # function 内部语句替换标识符，函数声明边界继续由 parser 原块承载。
            list_result.append(
                FunctionBlock(
                    lines=[self._rename_statement_text(line, rename_map) for line in block.lines],
                    leading_comments=list(block.leading_comments),
                )
            )

        # 返回只改写内部语句文本的 function 块。
        return list_result

    # task 块内部语句可重命名，任务边界不在这里重排。
    def _rename_task_blocks(self, task_blocks: list[TaskBlock], rename_map: dict[str, str]) -> list[TaskBlock]:

        """
        改写 Verilog task 块内部语句。

        :param task_blocks: parser 提取的 task 块列表。
        :param rename_map: 原始名称到规范名称的映射。
        :return: 已改写语句文本的 task 块列表。
        """

        # list_result 保持 task 块原始排列，避免任务声明跨区域移动。
        list_result: list[TaskBlock] = []  # task 语句替换输出队列

        # task 逐块改写，保持声明区域中的任务相对顺序。
        for block in task_blocks:

            # task 内部语句替换标识符，任务边界和前导说明保持原样。
            list_result.append(
                TaskBlock(
                    lines=[self._rename_statement_text(line, rename_map) for line in block.lines],  # task 主体替换结果
                    leading_comments=list(block.leading_comments),  # task 前导说明副本
                )
            )

        # 返回已经保序改写的 task 块集合。
        return list_result

    # body tree 归一化阶段只改写类型和参数形态，不做跨节点重命名。
    def _normalize_body_tree(self, items: dict[str, list], state_param_names: set[str]) -> dict[str, list]:

        """
        递归归一化 body tree 中可直接规范化的节点。

        :param items: parser 生成的 body tree 字典。
        :param state_param_names: 已识别的状态参数名集合。
        :return: 归一化后的 body tree 字典。
        """

        # dict_normalized 是与原 body tree 结构相同的新容器。
        dict_normalized = self._new_body_items()  # 局部归一化输出容器

        # function 块包含用户过程逻辑，此阶段只保留 parser 顺序。
        dict_normalized["functions"] = list(items["functions"])  # 过程函数原样保留

        # task 块同样保留原结构，避免归一化阶段改变过程接口。
        dict_normalized["tasks"] = list(items["tasks"])  # 过程任务原样保留

        # list_normalized_localparams 按状态参数集合选择命名规则。
        list_normalized_localparams: list[ParamDecl] = []  # body localparam 规范化结果

        # localparam 逐项归一化，便于注释和类型检查定位。
        for item in items["localparams"]:

            # param_decl_normalized 是单个 body 参数的规范化结果。
            param_decl_normalized: ParamDecl = self._normalize_param(item, state=item.name in state_param_names)  # body 参数规范声明

            # 当前参数写入归一化 body tree 队列。
            list_normalized_localparams.append(param_decl_normalized)

        # localparam 结果写回 body tree，供后续 rename 阶段按规范名继续替换引用。
        dict_normalized["localparams"] = list_normalized_localparams  # 归一化后 body 参数源

        # 声明节点执行类型关键字归一化。
        dict_normalized["decls"] = [self._normalize_decl(item) for item in items["decls"]]  # renderer 兼容声明节点

        # 连续赋值保持接口一致的归一化钩子。
        dict_normalized["assigns"] = [self._normalize_assign(item) for item in items["assigns"]]  # 连续赋值占位节点

        # always 块保持接口一致的归一化钩子。
        dict_normalized["always"] = [self._normalize_always(item) for item in items["always"]]  # 已分析 always 节点

        # initial 块的 parameter_check 约束留到 rename 阶段统一验证。
        dict_normalized["initials"] = list(items["initials"])  # 留待 rename 复核的 initial 块

        # 实例块在此阶段保持 parser 原结构。
        dict_normalized["instances"] = list(items["instances"])  # 未改写的实例块队列

        # 原始块在此阶段保持 parser 原结构。
        dict_normalized["raw_blocks"] = list(items["raw_blocks"])  # 不可结构化源码片段

        # 预处理 prologue 在此阶段保持 parser 原结构。
        dict_normalized["preprocessor_prologue"] = list(items["preprocessor_prologue"])  # 文件头宏片段

        # generate 块可能含层级控制节点，当前阶段不重排生成结构。
        dict_normalized["generates"] = list(items["generates"])  # 待 rename 复核的 generate 块

        # 通用 block 在此阶段保持 parser 原结构。
        dict_normalized["blocks"] = list(items["blocks"])  # parser 保底通用片段

        # list_normalized_conditionals 收集递归归一化后的条件编译分支。
        list_normalized_conditionals: list[PreprocessorConditional] = []  # 条件编译归一化结果

        # 条件编译分支需要递归归一化两侧 body tree。
        for conditional in items["conditionals"]:

            # dict_true_items 是 true 分支的归一化 body tree。
            dict_true_items = self._normalize_body_tree(conditional.true_items, state_param_names)  # 归一化宏命中分支

            # dict_false_items 承载未命中宏条件时的归一化 body tree。
            dict_false_items = self._normalize_body_tree(conditional.false_items, state_param_names)  # else 分支归一化结果

            # preprocessor_conditional_normalized 保留条件编译边界并替换内部 body tree。
            preprocessor_conditional_normalized = PreprocessorConditional(  # body tree 归一化条件分支
                directive=conditional.directive,  # 保留原始宏指令
                symbol=conditional.symbol,  # 保留原始宏名
                true_items=dict_true_items,  # 归一化后的宏命中 body tree
                false_items=dict_false_items,  # 归一化后的宏备用 body tree
                leading_comments=list(conditional.leading_comments),  # 条件编译前导说明
                has_else=conditional.has_else,  # 原始 else 分支标记
            )

            # 当前条件编译分支写入归一化结果。
            list_normalized_conditionals.append(preprocessor_conditional_normalized)

        # 条件编译分支列表写回归一化 body tree。
        dict_normalized["conditionals"] = list_normalized_conditionals  # 条件编译归一化队列

        # 返回只完成局部节点归一化的 body tree。
        return dict_normalized

    # body tree 改名阶段统一协调不同节点类型的递归替换。
    def _rename_body_tree(
        self, items: dict[str, list], rename_map: dict[str, str], output_internal_names: dict[str, str],
        output_signal_layouts: dict[str, OutputSignalLayout], *, add_missing_output_internals: bool = False,
    ) -> dict[str, list]:

        """
        递归改写 body tree 中所有可引用标识符的节点。

        :param items: 待改名的 body tree 字典。
        :param rename_map: 本层递归 body tree 使用的名称替换表。
        :param output_internal_names: 输出端口到内部代理名的映射。
        :param output_signal_layouts: 输出代理信号的布局信息。
        :param add_missing_output_internals: 是否在当前层级补齐输出代理声明。
        :return: 改名后的 body tree 字典。
        """

        # dict_renamed 是 rename 阶段逐类写入的新 body tree 容器。
        dict_renamed = self._new_body_items()  # rename 阶段输出容器

        # function 块只替换内部语句引用，函数声明自身不参与模块级排序。
        dict_renamed["functions"] = self._rename_function_blocks(items["functions"], rename_map)  # 过程函数改名队列

        # task 块按同样文本规则替换内部引用，保护任务前导注释。
        dict_renamed["tasks"] = self._rename_task_blocks(items["tasks"], rename_map)  # 过程任务改名队列

        # localparam 值表达式可能引用已改名信号，需要同步替换。
        dict_renamed["localparams"] = self._rename_params(items["localparams"], rename_map)  # localparam 引用替换结果

        # 声明节点承担输出代理补齐和 inline wire assign 拆分。
        dict_renamed["decls"] = self._rename_decls(  # 声明改名与代理补齐
            items["decls"],  # 原始声明节点队列
            rename_map,  # 本层标识符映射
            output_internal_names,  # 输出端口代理目标
            output_signal_layouts,  # 输出代理布局表
            add_missing_output_internals=add_missing_output_internals,  # 当前层是否补齐输出代理
        )

        # 连续赋值的左右两侧都按同一映射替换。
        dict_renamed["assigns"] = self._rename_assigns(items["assigns"], rename_map)  # 连续赋值替换结果

        # always 块需要重新分析触发和控制结构，保证分区信息仍准确。
        dict_renamed["always"] = self._rename_always(items["always"], rename_map)  # always 分析重建结果

        # initial 块在替换后复核 parameter_check 的严格形态。
        dict_renamed["initials"] = self._rename_initial_blocks(items["initials"], rename_map)  # initial 约束复核结果

        # list_renamed_instances 收集重新解析后的实例块。
        list_renamed_instances: list[InstanceBlock] = []  # 接口同步后的实例块

        # 实例块文本改名后需要重新解析端口连接结构。
        for instance in items["instances"]:

            # str_renamed_text 是实例块完成标识符替换后的文本。
            str_renamed_text = self._rename_statement_text(instance.text, rename_map)  # 实例源码替换结果

            # instance_block_renamed_instance 重新解析端口连接以便后续渲染。
            instance_block_renamed_instance: InstanceBlock = self._parse_instance_block(  # 端口连接复核结果
                str_renamed_text  # 实例源码改名文本
            )

            # 保留实例块前导注释。
            instance_block_renamed_instance.leading_comments = list(instance.leading_comments)  # 原实例说明行

            # 当前实例块加入输出列表。
            list_renamed_instances.append(instance_block_renamed_instance)

        # 实例块列表写回 body tree。
        dict_renamed["instances"] = list_renamed_instances  # 实例接口同步结果

        # 原始块只做安全文本替换，不改变其不可结构化的渲染边界。
        dict_renamed["raw_blocks"] = self._rename_raw_blocks(items["raw_blocks"], rename_map)  # raw block 文本替换结果

        # 预处理 prologue 保留原结构，只改写语句文本。
        dict_renamed["preprocessor_prologue"] = self._rename_raw_blocks(  # 文件头宏片段改名队列
            items["preprocessor_prologue"],  # prologue 原始文本块
            rename_map,  # 当前 body tree 映射
        )

        # generate 块需要改名后重新解析控制节点。
        dict_renamed["generates"] = self._rename_generate_blocks(items["generates"], rename_map)  # generate 控制流复核结果

        # 通用 block 缺少结构化语义，保持 parser 原始片段。
        dict_renamed["blocks"] = list(items["blocks"])  # 原始通用块片段

        # list_renamed_conditionals 收集递归改名后的条件编译分支。
        list_renamed_conditionals: list[PreprocessorConditional] = []  # 条件编译递归替换队列

        # 条件编译分支递归改名，但子层不重复补齐输出代理声明。
        for conditional in items["conditionals"]:

            # dict_true_items 是 true 分支改名后的 body tree。
            dict_true_items = self._rename_body_tree(  # 宏命中分支递归改名
                conditional.true_items,  # 命中分支源节点
                rename_map,  # 外层共享改名表
                output_internal_names,  # 输出端口代理字典
                output_signal_layouts,  # 输出代理布局字典
                add_missing_output_internals=False,  # 子分支不重复补代理
            )

            # dict_false_items 承载宏未命中路径的改名 body tree。
            dict_false_items = self._rename_body_tree(  # 宏备用分支改名结果
                conditional.false_items,  # else 侧原始节点
                rename_map,  # 备用分支共享改名表
                output_internal_names,  # 备用分支代理字典
                output_signal_layouts,  # 备用分支布局字典
                add_missing_output_internals=False,  # 备用分支不补代理
            )

            # preprocessor_conditional_renamed 保留条件编译指令，只替换分支内容。
            preprocessor_conditional_renamed: PreprocessorConditional = PreprocessorConditional(  # 条件分支改名模型
                directive=conditional.directive,  # 改名后保留的预处理指令
                symbol=conditional.symbol,  # 改名后保留的宏条件
                true_items=dict_true_items,  # 改名后的命中分支
                false_items=dict_false_items,  # 改名后的备用分支
                leading_comments=list(conditional.leading_comments),  # 改名分支说明副本
                has_else=conditional.has_else,  # 改名分支 else 标记
            )

            # 当前条件编译分支写入改名结果。
            list_renamed_conditionals.append(preprocessor_conditional_renamed)

        # 条件编译分支列表写回改名 body tree。
        dict_renamed["conditionals"] = list_renamed_conditionals  # 条件编译改名队列

        # 返回递归替换后的 body tree。
        return dict_renamed

    # always 分裂只作用于 always 列表，其它 body tree 节点保持浅复制。
    def _split_body_tree_always(self, items: dict[str, list]) -> dict[str, list]:

        """
        递归拆分 body tree 中可独立渲染的 always 块。

        :param items: parser 或前序阶段生成的 body tree 字典。
        :return: always 块已拆分后的 body tree 字典。
        """

        # dict_split_items 是拆分 always 后的新 body tree 容器。
        dict_split_items = self._new_body_items()  # always 拆分输出容器

        # 普通节点只需要保持原有顺序和对象边界。
        for key in (
            "functions",
            "tasks",
            "localparams",
            "decls",
            "assigns",
            "initials",
            "instances",
            "raw_blocks",
            "preprocessor_prologue",
            "generates",
            "blocks",
        ):

            # 当前类型节点浅复制到新容器。
            dict_split_items[key] = list(items[key])  # 拆分阶段保序浅复制

        # always 列表执行真实拆分逻辑。
        dict_split_items["always"] = self._split_always_blocks(items["always"])  # 拆分后的 always 单元

        # list_split_conditionals 收集 always 拆分后的条件编译分支。
        list_split_conditionals: list[PreprocessorConditional] = []  # always 拆分条件队列

        # 条件编译分支需要递归拆分两侧 always。
        for conditional in items["conditionals"]:

            # dict_true_items 是 true 分支拆分 always 后的 body tree。
            dict_true_items = self._split_body_tree_always(conditional.true_items)  # 命中分支 always 结果

            # dict_false_items 承载宏未命中路径的 always 拆分结果。
            dict_false_items = self._split_body_tree_always(conditional.false_items)  # 备用分支 always 结果

            # preprocessor_conditional_split 保留条件指令，只替换已拆分的分支内容。
            preprocessor_conditional_split: PreprocessorConditional = PreprocessorConditional(  # always 拆分条件模型
                directive=conditional.directive,  # 拆分后保留的预处理指令
                symbol=conditional.symbol,  # 拆分后保留的宏条件
                true_items=dict_true_items,  # 拆分后的命中分支
                false_items=dict_false_items,  # 拆分后的备用分支
                leading_comments=list(conditional.leading_comments),  # 拆分分支说明副本
                has_else=conditional.has_else,  # 拆分分支 else 标记
            )

            # 当前条件编译分支写入拆分结果。
            list_split_conditionals.append(preprocessor_conditional_split)

        # 条件编译分支列表写回拆分后的 body tree。
        dict_split_items["conditionals"] = list_split_conditionals  # 条件编译拆分队列

        # 返回已处理 always 拆分的 body tree。
        return dict_split_items

    # 控制节点改名递归覆盖 header、文本、标签、分支和 case 项。
    def _rename_control_nodes(self, nodes: list[ControlNode], rename_map: dict[str, str]) -> list[ControlNode]:

        """
        递归改写结构化控制节点中的标识符引用。

        :param nodes: 待改名的控制节点列表。
        :param rename_map: 原始名称到规范名称的映射。
        :return: 已改写文本和子节点的控制节点列表。
        """

        # list_renamed_nodes 按原始顺序收集控制节点副本。
        list_renamed_nodes: list[ControlNode] = []  # 控制节点改名输出队列

        # 遍历当前层级控制节点并递归改写子结构。
        for node in nodes:

            # list_case_items 先改写 case 分支，避免构造节点时嵌套过深。
            list_case_items: list[CaseItem] = []  # case 分支递归改名队列

            # case 分支逐项构造，便于每个字段保留明确语义。
            for item in node.items:

                # case_item_renamed 是单个 case 分支的改名副本。
                case_item_renamed: CaseItem = CaseItem(  # 单项 case 分支模型
                    label=self._rename_text(item.label, rename_map),  # case 标签表达式
                    children=self._rename_control_nodes(item.children, rename_map),  # case 分支语句树
                    block_label=self._rename_text(item.block_label, rename_map),  # 分支命名块标签
                )

                # 当前 case 分支写入控制节点副本。
                list_case_items.append(case_item_renamed)

            # list_child_nodes 是当前控制节点主分支的递归改名结果。
            list_child_nodes = self._rename_control_nodes(node.children, rename_map)  # 主分支节点替换结果

            # list_alternate_nodes 是 else/默认分支的递归改名结果。
            list_alternate_nodes = self._rename_control_nodes(node.alternate, rename_map)  # 备用分支节点替换结果

            # control_node_clone 是当前节点的改名副本。
            control_node_clone: ControlNode = ControlNode(  # 单个控制节点改名模型
                kind=node.kind,  # 控制节点类型
                header=self._rename_text(node.header, rename_map),  # 控制语句头部替换结果
                text=self._rename_statement_text(node.text, rename_map),  # 原始语句文本替换结果
                label=self._rename_text(node.label, rename_map),  # 控制节点标签替换结果

                # 子节点字段保持主分支、备用分支和 case 分支的边界。
                children=list_child_nodes,  # 主分支子节点
                alternate=list_alternate_nodes,  # else 分支子节点
                items=list_case_items,  # case 分支替换结果
            )

            # 当前节点副本加入输出列表。
            list_renamed_nodes.append(control_node_clone)

        # 返回当前层级改名后的控制节点列表。
        return list_renamed_nodes

    # generate 块改名后重新渲染并解析，确保结构仍可由内置后端理解。
    def _rename_generate_blocks(
        self, generate_blocks: list[GenerateBlock], rename_map: dict[str, str]
    ) -> list[GenerateBlock]:

        """
        改写 generate 块中的控制节点并重新验证结构。

        :param generate_blocks: parser 提取的 generate 块列表。
        :param rename_map: 原始名称到规范名称的映射。
        :return: 已改名且重新解析成功的 generate 块列表。
        :raises VerilogFormatterError: 改名后的 generate 结构无法完整解析时抛出。
        """

        # list_result 按原始顺序收集通过复核的 generate 块。
        list_result: list[GenerateBlock] = []  # generate 结构复核输出队列

        # 逐个 generate 块重命名控制节点并验证渲染结果。
        for block in generate_blocks:

            # list_renamed_nodes 是替换标识符后的 generate 控制节点。
            list_renamed_nodes = self._rename_control_nodes(block.nodes, rename_map)  # generate 节点引用替换结果

            # list_renamed_lines 是由控制节点重新渲染出的 generate 主体。
            list_renamed_lines = self._render_control_nodes(list_renamed_nodes, 0)  # 重建后的 generate 文本

            # tuple_control_parse 接收重新解析的节点和消费行数。
            tuple_control_parse = self._parse_control_nodes(list_renamed_lines, 0, set(), "generate")  # generate 结构复核结果

            # list_nodes 是重新解析出的 generate 控制节点。
            list_nodes = tuple_control_parse[0]  # generate 复核后的节点树

            # int_consumed 是 parser 成功消费的 generate 行数。
            int_consumed = tuple_control_parse[1]  # generate 复核消费行数

            # generate 改名后必须仍能被完整解析。
            if int_consumed != len(list_renamed_lines):

                # 抛出严格错误，要求调用方简化 generate 形态。
                self._raise_rename_error(
                    "unsupported_generate_shape",
                    "\n".join(list_renamed_lines),
                    "simplify unsupported generate control flow.",
                )

            # 当前 generate 块保留头部和前导注释，只替换主体节点。
            list_result.append(
                GenerateBlock(
                    header=block.header,  # 原 generate 头部文本
                    lines=list_renamed_lines,  # 重渲染 generate 主体
                    nodes=list_nodes,  # 复核后的 generate 节点
                    leading_comments=list(block.leading_comments),  # generate 块说明副本
                )
            )

        # 返回已通过控制流复核的 generate 块。
        return list_result

    # 普通文本改名按长名称优先替换，避免短名先替换破坏长标识符。
    def _rename_text(self, text: str, rename_map: dict[str, str]) -> str:

        """
        在普通表达式文本中替换受控标识符名称。

        :param text: 待替换的 Verilog 文本片段。
        :param rename_map: 原始名称到规范名称的映射。
        :return: 完成标识符替换后的文本。
        """

        # str_renamed_text 保存当前替换进度。
        str_renamed_text = text  # 标识符逐步替换文本

        # 长名称优先替换，降低前缀相同标识符的误替换风险。
        for str_old_name, str_new_name in sorted(rename_map.items(), key=lambda item: -len(item[0])):

            # 当前映射只匹配非层级访问形式的完整标识符。
            str_identifier_pattern = rf"(?<!\.)\b{re.escape(str_old_name)}\b"  # 当前标识符匹配模式

            # 当前映射只替换完整标识符，不触碰层级访问 formal。
            str_renamed_text = re.sub(  # 单轮映射后的表达式文本
                str_identifier_pattern,  # 完整标识符正则
                str_new_name,  # 本轮替换目标标识符
                str_renamed_text,  # 当前累计替换文本
            )

        # 返回所有映射应用后的文本。
        return str_renamed_text

    # 语句文本改名额外处理实例化 named association 的 formal 名称。
    def _rename_statement_text(self, text: str, rename_map: dict[str, str]) -> str:

        """
        改写语句文本并同步实例端口 formal 名称。

        :param text: 待改写的语句文本。
        :param rename_map: 原始名称到规范名称的映射。
        :return: 已完成表达式和实例关联改名的语句文本。
        """

        # str_renamed_text 先完成普通标识符替换。
        str_renamed_text = self._rename_text(text, rename_map)  # 语句标识符替换文本

        # 返回同步处理实例 named association 后的文本。
        return self._rename_instance_associations(str_renamed_text)

    # 实例 association 改名依赖被例化模块接口，只处理可解析实例文本。
    def _rename_instance_associations(self, text: str) -> str:

        """
        根据被例化模块接口改写 named association 的 formal 名称。

        :param text: 可能包含 Verilog 实例化语句的文本。
        :return: 已同步 formal 名称的文本；无法确认接口时返回原文本。
        """

        # 非实例文本不做接口解析，避免普通语句被误处理。
        if not self._looks_like_instance_text(text):

            # 返回原文本表示无实例改名需求。
            return text

        # instance_block_parsed_instance 是当前文本解析出的实例结构。
        instance_block_parsed_instance: InstanceBlock = self._parse_instance_block(text)  # 待查接口的实例块

        # 缺少模块名或实例名时不能安全查找接口。
        if not instance_block_parsed_instance.module_name or not instance_block_parsed_instance.instance_name:

            # 返回原文本避免误改无法确认的语句。
            return text

        # dict_interface 保存被例化模块的参数和端口映射。
        dict_interface = self._resolve_module_interface(instance_block_parsed_instance.module_name)  # 被例化模块接口

        # 找不到唯一接口时保守返回原文本。
        if dict_interface is None:

            # 返回原文本，避免跨模块接口误判。
            return text

        # dict_param_map 提供参数 formal 的规范名称映射。
        dict_param_map = dict_interface["params"]  # 参数 formal 映射

        # dict_port_map 提供端口 formal 的规范名称映射。
        dict_port_map = dict_interface["ports"]  # 端口 formal 映射

        # list_lines 逐行收集完成 formal 改写的实例文本。
        list_lines: list[str] = []  # formal 同步后的实例行

        # str_phase 标记当前处于参数 association 还是端口 association。
        str_phase = "root"  # 实例关联扫描阶段

        # 逐行扫描实例文本，依据括号阶段改写 named association。
        for raw_line in text.splitlines():

            # tuple_split_line 保留代码和注释两部分。
            tuple_split_line = self._split_comment(raw_line)  # 代码与注释拆分结果

            # str_raw_code 是去掉注释后的代码部分。
            str_raw_code = tuple_split_line[0]  # 无注释代码文本

            # str_comment 是原行注释文本。
            str_comment = tuple_split_line[1]  # 原行注释文本

            # str_stripped_code 用于判断当前行结构。
            str_stripped_code = str_raw_code.strip()  # 去空白代码文本

            # str_rewritten_line 保存当前行改写结果。
            str_rewritten_line = str_raw_code.rstrip()  # 当前行改写文本

            # 非纯注释行才需要尝试改写 formal 名称。
            if str_stripped_code and not str_stripped_code.startswith("//"):

                # 参数 association 阶段使用 header/localparam 的 formal 映射。
                if str_phase == "params":

                    # 当前行按参数 formal 映射改写。
                    str_rewritten_line = self._rename_named_association_formal(  # 参数 formal 同步行
                        str_rewritten_line,  # 当前实例参数行
                        dict_param_map,  # 参数 formal 对照表
                    )

                # 端口 association 阶段使用端口声明的 formal 映射。
                elif str_phase == "ports":

                    # 当前行按端口 formal 映射改写。
                    str_rewritten_line = self._rename_named_association_formal(  # 端口 formal 同步行
                        str_rewritten_line,  # 当前实例端口行
                        dict_port_map,  # 端口 formal 对照表
                    )

            # 有注释时把原注释重新拼回改写后的代码行。
            if str_comment:

                # str_rewritten_line 恢复原行注释。
                str_rewritten_line = f"{str_rewritten_line} // {str_comment}"  # 带注释改写行

            # 当前行写入输出列表。
            list_lines.append(str_rewritten_line.rstrip())

            # str_normalized_line 用于识别实例参数和端口列表阶段。
            str_normalized_line = self._normalize_statement_line(str_stripped_code)  # 规范化结构行

            # `# (` 表示进入参数 association。
            if re.search(r"#\s*\($", str_normalized_line):

                # 后续 named association 按参数映射处理。
                str_phase = "params"  # 参数关联扫描阶段

            # 模块实例端口列表起始表示进入端口 association。
            elif (
                re.match(r"^\)\s*\w+\s*\($", str_normalized_line)
                or re.match(r"^[A-Za-z_]\w+\s+\w+\s*\($", str_normalized_line)
                or re.match(r"^[A-Za-z_]\w+\s*\($", str_normalized_line)
            ):

                # 后续 named association 按端口映射处理。
                str_phase = "ports"  # 端口关联扫描阶段

            # 实例结束后停止 formal 改名阶段。
            elif str_normalized_line == ");":

                # 标记实例 association 已结束。
                str_phase = "done"  # 实例关联扫描结束

        # 返回重新拼接后的实例文本。
        return "\n".join(list_lines)

    # 实例文本快速判定只看规范化后的起始行，避免不必要的接口扫描。
    def _looks_like_instance_text(self, text: str) -> bool:

        """
        判断文本片段是否可能是 Verilog 模块实例化。

        :param text: 待检查的语句文本。
        :return: 文本起始形态符合实例化语句时返回 True。
        """

        # list_normalized_lines 保存去空白后的有效语句行。
        list_normalized_lines: list[str] = []  # 实例判定使用的非空规范行

        # 逐行规范化后再判断起始形态，避免列表推导掩盖空行过滤规则。
        for raw_line in text.splitlines():

            # str_stripped_line 是实例判定前的空白裁剪结果。
            str_stripped_line = raw_line.strip()  # 原始行去空白文本

            # 空行不参与实例起始判定。
            if not str_stripped_line:

                # 继续处理下一行有效文本。
                continue

            # 当前有效行写入规范化判定队列。
            list_normalized_lines.append(self._normalize_statement_line(str_stripped_line))

        # 没有有效行时不可能是实例化。
        if not list_normalized_lines:

            # 空文本直接返回否定结果。
            return False

        # str_first_line 是实例判定使用的首行。
        str_first_line = list_normalized_lines[0]  # 规范化首行

        # str_next_line 辅助判断多行实例起始。
        str_next_line = list_normalized_lines[1] if len(list_normalized_lines) > 1 else ""  # 规范化次行

        # 返回实例起始行判定结果。
        return self._is_instance_start_line(str_first_line, str_next_line)

    # named association formal 改名只替换 `.formal(` 的 formal 部分。
    def _rename_named_association_formal(self, text: str, formal_map: dict[str, str]) -> str:

        """
        按接口映射改写单行 named association 的 formal 名称。

        :param text: 单行 named association 文本。
        :param formal_map: 原 formal 名到规范 formal 名的映射。
        :return: formal 名已改写的文本；无需改写时返回原文本。
        """

        # str_association_pattern 捕获 `.formal(` 形式的 named association。
        str_association_pattern = r"^(?P<prefix>\s*)\.(?P<formal>\w+)(?P<suffix>\s*\(.*)$"  # formal 捕获正则

        # match_association 是当前行的 named association 匹配对象。
        match_association = re.match(str_association_pattern, text)  # named association 匹配结果

        # 非 named association 行保持原样。
        if not match_association:

            # 非 association 语句无需接口 formal 修正。
            return text

        # str_formal_name 是当前行使用的 formal 名称。
        str_formal_name = match_association.group("formal")  # 当前 formal 名称

        # 全大写 formal 通常表示宏式名称，按保守规则保留。
        if self._should_preserve_instance_formal(str_formal_name):

            # 宏式 formal 保留用户原始写法。
            return text

        # str_renamed_formal 是接口映射给出的目标 formal 名。
        str_renamed_formal = formal_map.get(str_formal_name, str_formal_name)  # 改名后的 formal 名

        # 未命中映射时无需重写。
        if str_renamed_formal == str_formal_name:

            # formal 映射未命中时保持原 association。
            return text

        # 返回仅替换 formal 名称后的 association 行。
        return f"{match_association.group('prefix')}.{str_renamed_formal}{match_association.group('suffix')}"

    # 全大写 named association formal 由用户语义保留规则处理。
    def _should_preserve_instance_formal(self, formal: str) -> bool:

        """
        判断实例 formal 名称是否应按全大写约定保留。

        :param formal: 实例 association 中的 formal 名称。
        :return: 包含字母且完全大写时返回 True。
        """

        # 返回全大写 formal 的保留判定结果。
        return any(char.isalpha() for char in formal) and formal.upper() == formal

    # workspace root 解析优先使用源码路径向上查找项目标记。
    def _resolve_workspace_root(self, source_path: Path | None) -> Path | None:

        """
        根据当前源码路径推断可用于模块接口扫描的工作区根目录。

        :param source_path: 当前正在格式化的源码路径。
        :return: 推断出的工作区根目录；缺少源码路径时返回当前目录。
        """

        # 无源码路径时以当前工作目录作为扫描根。
        if source_path is None:

            # 返回当前进程工作目录。
            return Path.cwd().resolve()

        # 从源码所在目录逐级向上查找仓库或工程标记。
        for candidate in [source_path.parent, *source_path.parents]:

            # git 仓库根优先作为 workspace root。
            if (candidate / ".git").exists():

                # 返回命中的 git 仓库根。
                return candidate

            # config 与 scripts 同时存在时视为本项目工程根。
            if (candidate / "config").exists() and (candidate / "scripts").exists():

                # 返回命中的工程根。
                return candidate

        # 未命中标记时退回源码所在目录。
        return source_path.parent

    # 模块接口解析先查相邻文件，再在 workspace 内唯一匹配。
    def _resolve_module_interface(self, module_name: str) -> dict[str, object] | None:

        """
        解析被例化模块的参数、端口和布局接口。

        :param module_name: 待解析的 Verilog 模块名。
        :return: 唯一解析出的模块接口；未找到或存在歧义时返回 None。
        """

        # path_source_dir 是当前源码所在目录或当前工作目录。
        path_source_dir = (
            self._current_source_path.parent.resolve()  # 当前源码目录
            if self._current_source_path is not None  # 已记录当前源码路径
            else Path.cwd().resolve()  # 缺少源码路径时使用工作目录
        )  # 模块接口起始扫描目录

        # path_workspace_root 是模块接口扫描的工作区根。
        path_workspace_root = self._current_workspace_root or Path.cwd().resolve()  # workspace 扫描根目录

        # tuple_cache_key 区分不同源码目录、workspace 和目标模块名。
        tuple_cache_key = (str(path_source_dir), str(path_workspace_root), module_name)  # 模块解析缓存键

        # 命中解析缓存时直接返回，避免重复扫描工作区。
        if tuple_cache_key in self._module_resolution_cache:

            # 返回缓存中的接口或歧义 None。
            return self._module_resolution_cache[tuple_cache_key]

        # 优先检查源码同目录下的同名 Verilog 文件。
        for extension in (".v", ".vh"):

            # path_candidate 是同目录候选模块文件。
            path_candidate = path_source_dir / f"{module_name}{extension}"  # 同目录候选文件

            # dict_interface 是候选文件解析出的模块接口。
            dict_interface = self._load_module_interface(path_candidate, module_name)  # 同目录模块接口

            # 同目录候选命中时立即缓存并返回。
            if dict_interface is not None:

                # 写入模块解析缓存。
                self._module_resolution_cache[tuple_cache_key] = dict_interface  # 同目录命中接口缓存

                # 返回唯一接口信息。
                return dict_interface

        # list_matches 收集 workspace 扫描得到的接口候选。
        list_matches: list[dict[str, object]] = []  # workspace 模块接口候选列表

        # 扫描 workspace 内所有允许的 Verilog 文件。
        for path_candidate in self._iter_workspace_verilog_files(path_workspace_root):

            # 已经检查过的同目录同名文件不重复解析。
            if path_candidate.parent.resolve() == path_source_dir and path_candidate.stem == module_name:

                # 跳过同目录候选文件。
                continue

            # dict_interface 承载当前 workspace 文件中目标模块的接口抽取结果。
            dict_interface = self._load_module_interface(path_candidate, module_name)  # 目标模块接口候选

            # 未命中目标模块时继续扫描。
            if dict_interface is None:

                # 当前文件不包含目标模块。
                continue

            # 记录命中的模块接口候选。
            list_matches.append(dict_interface)

            # 多个候选会导致接口歧义，缓存 None 后返回。
            if len(list_matches) > 1:

                # 歧义结果写入缓存。
                self._module_resolution_cache[tuple_cache_key] = None  # 多候选歧义接口缓存

                # 返回 None 表示无法安全选择接口。
                return None

        # dict_resolved_interface 是唯一候选或未命中 None。
        dict_resolved_interface = list_matches[0] if len(list_matches) == 1 else None  # workspace 唯一接口或未命中

        # 缓存最终解析结果。
        self._module_resolution_cache[tuple_cache_key] = dict_resolved_interface  # workspace 扫描最终缓存

        # 返回最终模块接口解析结果。
        return dict_resolved_interface

    # workspace 扫描扩展名来自配置，运行期不内置业务枚举。
    def _configured_workspace_scan_extensions(self) -> tuple[str, ...]:

        """
        读取并规范化 workspace 扫描允许的文件扩展名。

        :param 无业务参数: 扩展名来源于 formatter 配置对象。
        :return: 去重且带点号前缀的扩展名元组。
        """

        # raw_extensions 是配置中的原始扩展名集合。
        raw_extensions = self.config["execution"]["include_extensions"]  # 原始扩展名配置

        # list_extensions 按配置顺序收集规范化扩展名。
        list_extensions: list[str] = []  # 去重前的扫描扩展名列表

        # 逐个扩展名做类型和空值过滤。
        for extension in raw_extensions:

            # 非字符串配置项直接忽略。
            if not isinstance(extension, str):

                # 跳过非法扩展名配置。
                continue

            # str_normalized_extension 去掉配置项首尾空白。
            str_normalized_extension = extension.strip()  # 规范化扩展名文本

            # 空扩展名不参与扫描。
            if not str_normalized_extension:

                # 跳过空扩展名配置。
                continue

            # 扫描扩展名统一带点号前缀。
            list_extensions.append(
                str_normalized_extension
                if str_normalized_extension.startswith(".")
                else f".{str_normalized_extension}"
            )

        # dict.fromkeys 保留顺序并去重。
        return tuple(dict.fromkeys(list_extensions))

    # 排除目录名来自配置，用集合支持快速命中。
    def _configured_workspace_scan_exclude_dir_names(self) -> set[str]:

        """
        读取 workspace 扫描需要排除的目录名。

        :param 无业务参数: 排除目录来源于 formatter 配置对象。
        :return: 配置中有效的目录名集合。
        """

        # raw_names 是配置中的原始排除目录名列表。
        raw_names = self.config["execution"]["workspace_scan_exclude_dir_names"]  # 原始排除目录名配置

        # 返回过滤后的目录名集合。
        return {name for name in raw_names if isinstance(name, str) and name}

    # 排除子路径按路径片段元组保存，便于跨平台比较。
    def _configured_workspace_scan_exclude_subpaths(self) -> tuple[tuple[str, ...], ...]:

        """
        读取 workspace 扫描需要排除的子路径片段序列。

        :param 无业务参数: 排除子路径来源于 formatter 配置对象。
        :return: 每条排除子路径拆分后的路径片段元组。
        """

        # raw_subpaths 是配置中的原始排除子路径列表。
        raw_subpaths = self.config["execution"]["workspace_scan_exclude_subpaths"]  # workspace 子路径排除配置

        # list_subpaths 收集有效的路径片段序列。
        list_subpaths: list[tuple[str, ...]] = []  # 有效排除子路径片段

        # 逐条配置解析为跨平台片段元组。
        for subpath in raw_subpaths:

            # 非字符串子路径配置不参与路径片段比较。
            if not isinstance(subpath, str):

                # 跳过非法子路径配置。
                continue

            # tuple_parts 是去空片段后的子路径。
            tuple_parts = tuple(part for part in subpath.replace("\\", "/").split("/") if part)  # 排除子路径片段

            # 非空子路径才加入排除规则。
            if tuple_parts:

                # 记录有效排除子路径。
                list_subpaths.append(tuple_parts)

        # 返回配置中的所有有效子路径规则。
        return tuple(list_subpaths)

    # 路径片段序列匹配用于实现跨平台的子路径排除规则。
    def _path_contains_part_sequence(self, parts: tuple[str, ...], sequence: tuple[str, ...]) -> bool:

        """
        判断完整路径片段中是否包含指定连续片段序列。

        :param parts: 待检查路径的完整片段元组。
        :param sequence: 需要匹配的连续片段序列。
        :return: 命中连续片段序列时返回 True。
        """

        # 空规则或比路径更长的规则不可能命中。
        if not sequence or len(sequence) > len(parts):

            # 返回否定匹配结果。
            return False

        # 滑动窗口检查连续片段序列是否出现。
        return any(parts[index : index + len(sequence)] == sequence for index in range(len(parts) - len(sequence) + 1))

    # workspace Verilog 扫描排除规则合并目录名和子路径两类配置。
    def _is_workspace_verilog_scan_excluded(self, path: Path) -> bool:

        """
        判断给定路径是否应被 workspace Verilog 扫描排除。

        :param path: 待判断的文件路径。
        :return: 命中排除目录或排除子路径时返回 True。
        """

        # tuple_parts 是待检查路径的片段序列。
        tuple_parts = tuple(path.parts)  # 待匹配文件路径片段

        # 任一路径片段命中排除目录名即排除。
        if self._configured_workspace_scan_exclude_dir_names() & set(tuple_parts):

            # 返回目录名排除结果。
            return True

        # 子路径规则命中时同样排除。
        return any(
            self._path_contains_part_sequence(tuple_parts, subpath)
            for subpath in self._configured_workspace_scan_exclude_subpaths()
        )

    # workspace Verilog 文件枚举遵守配置的扩展名和排除规则。
    def _iter_workspace_verilog_files(self, root: Path) -> list[Path]:

        """
        枚举 workspace 中可用于模块接口解析的 Verilog 源文件。

        :param root: workspace 扫描根目录。
        :return: 已过滤当前源文件和排除路径的 Verilog 文件列表。
        """

        # list_files 按扫描顺序收集候选 Verilog 文件。
        list_files: list[Path] = []  # workspace 接口候选文件

        # 按配置扩展名逐类扫描 workspace。
        for extension in self._configured_workspace_scan_extensions():

            # 当前扩展名下递归枚举候选文件。
            for path_candidate in root.rglob(f"*{extension}"):

                # 命中排除规则的路径不参与接口解析。
                if self._is_workspace_verilog_scan_excluded(path_candidate):

                    # 跳过排除路径。
                    continue

                # 当前正在格式化的源文件不作为外部模块接口候选。
                if self._current_source_path is not None and path_candidate.resolve() == self._current_source_path:

                    # 跳过当前源文件。
                    continue

                # 记录规范化后的候选路径。
                list_files.append(path_candidate.resolve())

        # 返回可用于模块接口解析的文件列表。
        return list_files

    # 单文件模块接口加载负责解析、归一化并缓存模块参数和端口映射。
    def _load_module_interface(self, path: Path, module_name: str) -> dict[str, object] | None:

        """
        从指定 Verilog 文件加载目标模块的规范化接口信息。

        :param path: 候选 Verilog 源文件路径。
        :param module_name: 待解析的模块名。
        :return: 目标模块接口信息；文件无效、不可读或模块不匹配时返回 None。
        """

        # path_resolved 是候选文件的绝对规范路径。
        path_resolved = path.resolve()  # 规范化候选文件路径

        # tuple_cache_key 区分候选文件和目标模块。
        tuple_cache_key = (str(path_resolved), module_name)  # 模块接口缓存键

        # 命中单文件接口缓存时直接返回。
        if tuple_cache_key in self._module_interface_cache:

            # dict_interface 是缓存的接口或 None。
            dict_interface = self._module_interface_cache[tuple_cache_key]  # 单文件接口缓存值

            # None 表示该文件已确认不可用。
            if dict_interface is None:

                # 返回未命中结果。
                return None

            # 返回缓存的接口信息。
            return dict_interface

        # 文件不存在时缓存未命中，避免重复访问磁盘。
        if not path_resolved.exists():

            # 记录不可用候选文件。
            self._module_interface_cache[tuple_cache_key] = None  # 文件缺失接口缓存

            # 缓存确认该文件没有可用接口。
            return None

        # 读取并解析候选 Verilog 文件。
        try:

            # str_source 是候选文件的原始 Verilog 文本。
            str_source = read_verilog_text(path_resolved)  # 原始 Verilog 源码

            # str_clean_source 去掉 formatter 已有头部，避免影响模块解析。
            str_clean_source = self._strip_existing_header(str_source)  # 去头部后的源码

            # tuple_module_parse 承载模块名、参数、端口和 body 文本。
            tuple_module_parse = self._parse_module(str_clean_source)  # 模块解析结果

            # str_parsed_module_name 是候选文件中解析出的模块名。
            str_parsed_module_name = tuple_module_parse[0]  # 解析出的模块名

            # list_raw_params 是候选模块 header 中的原始参数。
            list_raw_params = tuple_module_parse[1]  # 原始 header 参数列表

            # list_ports 是候选模块端口列表。
            list_ports = tuple_module_parse[2]  # 原始端口列表

            # str_body 是候选模块 body 文本。
            str_body = tuple_module_parse[3]  # 模块 body 文本

        # 读取或解析失败时缓存未命中结果。
        except (OSError, UnicodeDecodeError, VerilogFormatterError):

            # 记录不可解析候选文件。
            self._module_interface_cache[tuple_cache_key] = None  # 文件解析失败接口缓存

            # 解析异常路径不继续参与接口推断。
            return None

        # 文件模块名不匹配目标时缓存未命中。
        if str_parsed_module_name != module_name:

            # 记录模块名不匹配结果。
            self._module_interface_cache[tuple_cache_key] = None  # 模块名不匹配缓存

            # 模块名不匹配时不能作为目标接口。
            return None

        # dict_body_items 是 parser 提取出的模块 body tree。
        dict_body_items = self._parse_body(str_body)  # 候选模块 body tree

        # list_raw_local_params 收集接口映射需要暴露的 body localparam 原名。
        list_raw_local_params = self._collect_body_items_recursive(dict_body_items, "localparams")  # 接口 body 参数原名

        # list_always_blocks 收集 body 内所有 always 块。
        list_always_blocks = self._collect_body_items_recursive(dict_body_items, "always")  # 接口推断 always 块

        # list_assigns 收集 body 内所有连续赋值。
        list_assigns = self._collect_body_items_recursive(dict_body_items, "assigns")  # 接口推断连续赋值

        # list_generate_blocks 收集可能包含输出桥接赋值的 generate 块。
        list_generate_blocks = self._collect_body_items_recursive(dict_body_items, "generates")  # 输出桥接 generate 来源

        # set_state_param_names 是由参数和 always 结构推断出的状态参数集合。
        set_state_param_names = self._infer_state_param_names(  # 接口映射使用的状态参数集合
            list_raw_params + list_raw_local_params,  # header 与 body 参数合集
            list_always_blocks,  # 状态机推断 always 来源
        )

        # list_normalized_params 是按状态规则归一化后的 header 参数。
        list_normalized_params: list[ParamDecl] = []  # 接口 header 参数规范序列

        # header 参数逐项规范化，后面需要与原始参数按位置配对。
        for param in list_raw_params:

            # bool_header_param_is_state 标记当前 header 参数是否参与状态命名。
            bool_header_param_is_state = param.name in set_state_param_names  # header 状态参数判定

            # param_decl_header_normalized 是单个 header 参数的规范声明。
            param_decl_header_normalized = self._normalize_param(param, state=bool_header_param_is_state)  # header 参数规范模型

            # 当前 header 参数写入接口映射候选序列。
            list_normalized_params.append(param_decl_header_normalized)

        # 拆分 body 参数，保持普通参数和状态参数在接口映射中可见。
        raw_body_nonstate_params, raw_body_state_params, _ = self._partition_top_level_body_params(  # 原始 body 参数分组
            dict_body_items["localparams"],  # body localparam 输入列表
            set_state_param_names,  # 状态参数判定集合
        )

        # dict_normalized_body_items 用于获得 body 参数的规范名。
        dict_normalized_body_items = self._normalize_body_tree(dict_body_items, set_state_param_names)  # 参数对齐用规范 body tree

        # 拆分规范化后的 body 参数，与原始参数按位置配对。
        normalized_body_nonstate_params, normalized_body_state_params, _ = self._partition_top_level_body_params(  # 规范 body 参数分组
            dict_normalized_body_items["localparams"]  # 规范化后的 body localparam
        )

        # list_combined_raw_params 汇总 header 和 body 中需要暴露的参数原名。
        list_combined_raw_params = [  # 接口映射使用的原始参数序列
            *list_raw_params,  # header 原始参数
            *raw_body_nonstate_params,  # body 普通参数
            *raw_body_state_params,  # body 状态参数
        ]

        # list_combined_normalized_params 汇总对应的规范参数名。
        list_combined_normalized_params = [  # 与原始参数按位置配对的规范序列
            *list_normalized_params,  # header 规范参数
            *normalized_body_nonstate_params,  # body 普通规范参数
            *normalized_body_state_params,  # body 状态规范参数
        ]

        # set_direct_output_ports 识别可直接输出的端口，供端口归一化使用。
        set_direct_output_ports = self._collect_direct_output_ports(  # 直接输出端口判定集合
            list_ports,  # 候选模块原始端口
            list_assigns,  # 输出桥接赋值来源
            list_generate_blocks,  # generate 内输出桥接来源
        )

        # tuple_normalized_ports 接收规范化端口列表和附加信息。
        tuple_normalized_ports = self._normalize_ports(list_ports, set_direct_output_ports)  # 接口端口归一化结果

        # list_normalized_ports 是模块接口中暴露的规范端口列表。
        list_normalized_ports = tuple_normalized_ports[0]  # 接口暴露端口列表

        # list_prepared_ports 是补充分组布局后的端口列表。
        list_prepared_ports = self._prepare_ports_for_render(list_normalized_ports)  # 端口布局元数据来源

        # dict_param_formal_map 建立原参数 formal 到规范参数 formal 的映射。
        dict_param_formal_map: dict[str, str] = {}  # 参数 formal 改名映射

        # 参数映射按原始参数和规范参数的位置关系配对。
        for raw_param, normalized_param in zip(list_combined_raw_params, list_combined_normalized_params):

            # 空参数名不能参与实例 association 改名。
            if raw_param.name and normalized_param.name:

                # 当前参数 formal 映射写入接口信息。
                dict_param_formal_map[raw_param.name] = normalized_param.name  # 参数 formal 目标名

        # dict_port_formal_map 建立原端口 formal 到规范端口 formal 的映射。
        dict_port_formal_map: dict[str, str] = {}  # 端口 formal 改名映射

        # 端口映射按 parser 端口和规范端口的位置关系配对。
        for raw_port, normalized_port in zip(list_ports, list_normalized_ports):

            # 空端口名不能参与实例 association 改名。
            if raw_port.name and normalized_port.name:

                # 当前端口 formal 映射写入接口信息。
                dict_port_formal_map[raw_port.name] = normalized_port.name  # 端口 formal 目标名

        # dict_port_directions 记录规范端口名对应的端口方向。
        dict_port_directions: dict[str, str] = {}  # 规范端口方向映射

        # 只有标准 Verilog 端口方向会进入接口方向表。
        for raw_port, normalized_port in zip(list_ports, list_normalized_ports):

            # 方向合法且两侧端口名有效时记录方向。
            if raw_port.name and normalized_port.name and raw_port.direction in {"input", "output", "inout"}:

                # 当前规范端口方向写入接口信息。
                dict_port_directions[normalized_port.name] = raw_port.direction  # 规范端口方向

        # dict_port_layouts 保存规范端口渲染分组布局。
        dict_port_layouts: dict[str, PortLayoutInfo] = {}  # 端口布局映射

        # 布局信息来自 prepare 阶段补齐后的端口对象。
        for port in list_prepared_ports:

            # 空端口名不能作为布局索引。
            if port.name:

                # 当前端口布局写入接口信息。
                dict_port_layouts[port.name] = PortLayoutInfo(group=port.group, section=port.section)  # 端口布局信息

        # dict_interface 汇总实例 formal 改名所需的接口映射和布局。
        dict_interface = {  # 实例 formal 改名所需的完整接口映射
            "module_name": str_parsed_module_name,  # 被例化模块名称
            "params": dict_param_formal_map,  # 参数 formal 改名表
            "ports": dict_port_formal_map,  # 端口 formal 改名表
            "port_directions": dict_port_directions,  # 规范端口方向表
            "port_layouts": dict_port_layouts,  # 规范端口布局表
        }

        # 缓存解析成功的接口信息。
        self._module_interface_cache[tuple_cache_key] = dict_interface  # 模块接口缓存结果

        # 返回模块接口信息。
        return dict_interface
