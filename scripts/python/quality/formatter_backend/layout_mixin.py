"""为 VerilogFormatterEngine 提供端口分组、协议识别和布局推断辅助。"""

# 延迟注解求值，保证 mixin 拆分后类型引用不会影响运行期导入。
from __future__ import annotations

# 标准库用于端口名称模式、时间型兼容注解和路径型兼容注解。
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable

# banner 工具用于识别和生成既有分组注释样式。
from .banners import display_width, extract_banner_title, is_banner_line, make_banner

# formatter 错误与端口布局模型，供端口整理主链直接复用。
from .models import (
    VerilogFormatterError,
    ParamDecl,
    ParamRenderCluster,
    PortDecl,
    PortLayoutInfo,

    # 下面这些模型服务于运行期其它版式节点，不在本文件内新增语义。
    OutputSignalLayout,
    AssignSourceLayout,
    InstanceSignalLayout,
    SignalDecl,
    AssignStmt,
    BodyBlock,
    LValueRef,

    # 下面这些模型主要服务于控制流、实例化和头部版式渲染。
    CaseItem,
    ControlNode,
    AlwaysBlock,
    InstanceBlock,

    # 这些模型更多承载块级结构与预处理边界。
    GenerateBlock,
    InitialBlock,
    FunctionBlock,
    RawBlock,
    PreprocessorConditional,
    HeaderMetadata,
)

# 文本读取工具保留给旧继承入口，避免拆分后改变可导入符号集合。
from .textio import read_verilog_text

# 端口布局 mixin 负责端口命名规范化、协议识别和布局分段。
class LayoutMixin:
    """维护端口命名、协议分组和时钟复位布局推断逻辑。"""

    # 判断端口名是否需要保持原样。
    def _should_preserve_port_name(self, name: str) -> bool:

        """
        判断端口名是否需要保持原样。
        
        参数:
            self: 当前 LayoutMixin 实例。
            name: 名称。
        返回:
            bool: True 表示当前端口名必须保持原样。
        """

        # 读取命名配置中需要原样保留的端口清单。
        dict_naming_config = self.config.get("naming", {})  # 端口命名配置段

        # 将显式端口名转成集合，避免每个端口反复线性查找。
        set_preserve_names = set(dict_naming_config.get("preserve_port_names", []))  # 原样保留端口名集合

        # 显式清单命中时直接保留，优先级高于模式匹配。
        if name in set_preserve_names:

            # 告诉调用方该端口名不能参与规范化重命名。
            return True

            # 按配置的正则模式判断 Vitis wrapper 等固定端口。

        # 这里把any、re、fullmatch作为当前 helper 的最终结果一并交回调用方。
        return any(re.fullmatch(pattern, name) for pattern in dict_naming_config.get("preserve_port_patterns", []))

    # 判断端口名是否命中 Vitis wrapper 保留规则。
    def _has_vitis_wrapper_port_preserve(self, lowered_name: str) -> bool:

        """
        判断端口名是否命中 Vitis wrapper 保留规则。
        
        参数:
            self: 当前 LayoutMixin 实例。
            lowered_name: 小写后的端口名。
        返回:
            bool: True 表示命中 wrapper 端口保留规则。
        """

        # 复用端口保留规则，保持 wrapper 特判和普通端口一致。
        return self._should_preserve_port_name(lowered_name)

    # 规范化端口名并生成输出端口的内部信号映射。
    def _normalize_ports(
        self,
        ports: list[PortDecl],
        direct_output_ports: set[str] | None = None,
    ) -> tuple[list[PortDecl], dict]:

        """
        规范化端口名并生成输出端口的内部信号映射。
        
        参数:
            self: 当前 LayoutMixin 实例。
            ports: 待处理的端口声明列表。
            direct_output_ports: 允许保持外部名称的直连输出端口集合。
        返回:
            tuple[list[PortDecl], dict]: 规范化后的端口列表和端口重命名映射。
        """

        # 缺省时没有被强制直连输出的端口。
        set_direct_output_ports = direct_output_ports or set()  # 可保持外部端口名的输出端口集合

        # 按输入顺序收集重命名后的端口声明。
        list_normalized_ports: list[PortDecl] = []  # 归一化后的端口声明

        # 记录旧端口名到渲染阶段实际信号名的映射。
        dict_rename_map: dict[str, str] = {}  # 外部端口到内部信号的重命名表

        # 保存输出端口需要额外生成的内部信号名。
        dict_internal_names: dict[str, str] = {}  # 输出端口对应的内部信号名

        # 逐个端口应用保留名、方向前缀和内部输出名规则。
        for port in ports:

            # 原始文本端口不参与结构化重命名，避免破坏手写声明。
            if port.raw_text:

                # 复制 raw port，保留 formatter 不能安全理解的声明片段。
                list_normalized_ports.append(self._copy_port_decl(port))

                # raw_text 端口已经被原样保留，本轮进入下一个端口。
                continue

                # 去掉已知方向前缀后再决定新的端口基础名。

            # 去掉原端口名里自带的方向前缀后，只保留统一命名阶段会复用的基础部分。
            str_base_name = self._normalize_port_base_name(port.name)  # 不含既有方向前缀的端口名

            # 保留名端口必须绕过输入/输出统一前缀。
            bool_preserve_port_name = self._should_preserve_port_name(port.name)  # 当前端口是否要求原样命名

            # 依据端口方向和保留名策略选出外部端口名。
            str_new_name = self._normalized_external_port_name(  # 新名称文本
                port,  # 当前待规范化的端口声明
                str_base_name,  # 去掉方向前缀后的基础端口名
                bool_preserve_port_name,  # 原样保留命名的判定结果
            )  # 渲染到模块接口中的端口名

            # 输出端口在非直连模式下需要使用内部信号承接 assign。
            if port.direction == "output" and port.name not in set_direct_output_ports:

                # 输出内部名沿用当前外部名作为 internal_names 的键。
                str_internal_name = self._build_internal_output_name(str_base_name)  # assign 改写阶段使用的内部输出信号名

                # 保存外部输出名到内部输出信号的关联。
                dict_internal_names[str_new_name] = str_internal_name  # 外部输出名对应的内部承接信号

                # 原端口名在表达式重写阶段指向内部信号。
                dict_rename_map[port.name] = str_internal_name  # 原始输出名在表达式替换时映射到的内部信号

            # output 端口不需要额外内部承接信号时，重命名表直接指向最终对外暴露的端口名。
            else:

                # 输入、inout 和直连输出都映射到最终外部端口名。
                dict_rename_map[port.name] = str_new_name  # 输入端口、inout 端口或直连输出最终映射到的外部名称

                # 将归一化后的名称写回结构化端口声明。

            # 端口名称策略已经确定后，这里把新名字写回端口副本，继续累积最终输出顺序。
            list_normalized_ports.append(self._copy_port_decl(port, name=str_new_name))

            # 返回保持旧 API 字段名的端口和重命名元数据。

        # 这里连同重命名表和内部承接信号表一起返回，供后续表达式改写和端口渲染阶段复用。
        return list_normalized_ports, {
            "rename_map": dict_rename_map,
            "internal_names": dict_internal_names,
        }

    # 复制端口声明，并按需要覆写少量字段。
    def _copy_port_decl(self, port: PortDecl, *, name: str | None = None) -> PortDecl:

        """
        复制端口声明，并按需要覆写少量字段。
        
        参数:
            self: 当前 LayoutMixin 实例。
            port: 当前端口声明。
            name: 名称。
        返回:
            PortDecl: 按当前字段复制得到的新端口声明。
        """

        # 保留调用方未覆盖的原端口名。
        str_port_name = port.name if name is None else name  # 克隆端口使用的名称

        # 构造新的 PortDecl，避免修改解析阶段共享的端口对象。
        return PortDecl(
            port.direction,
            port.width,
            str_port_name,
            port.comment,
            port.group,
            port.section,
            port.signed,

            # 保留声明文本和属性字段，避免克隆后丢失位宽、属性与原始文本信息。
            port.unpacked,
            port.attributes,
            port.raw_text,
            port.synthetic,

            # 子分组相关元数据也需要一起沿用，后续 banner 渲染会继续依赖它们。
            port.subgroup,
            port.subgroup_mode,
            port.allow_generic_subgroup,
        )

    # 生成端口渲染到模块接口时使用的名称。
    def _normalized_external_port_name(
        self,
        port: PortDecl,
        str_base_name: str,
        bool_preserve_port_name: bool,
    ) -> str:

        """
        生成端口渲染到模块接口时使用的名称。
        
        参数:
            self: 当前 LayoutMixin 实例。
            port: 当前端口声明。
            str_base_name: 去掉方向前缀后的端口基础名。
            bool_preserve_port_name: 与 `bool_preserve_port_name` 对应的当前输入参数。
        返回:
            str: 当前 helper 的返回结果。
        """

        # 保留名端口的外部接口名不能被方向前缀改写。
        if bool_preserve_port_name:

            # 返回原始端口名，满足 wrapper 或用户配置的命名约束。
            return port.name

            # 输入端口使用项目配置里的输入前缀规范。

        # 只有在 port.direction 等于 "input" 时，当前分支里的布局处理才有意义。
        if port.direction == "input":

            # 返回输入端口规范化后的外部接口名。
            return self._normalize_input_port_name(str_base_name)

            # 输出端口先生成模块接口名，内部信号另由调用方决定。

        # 只有在 当前端口方向是 output 时，当前分支里的布局处理才有意义。
        if port.direction == "output":

            # 返回输出端口带方向前缀的接口名。
            return self._apply_prefix(str_base_name, self.config["naming"]["output_prefix"])

            # inout 端口使用双向端口前缀，保持旧配置语义。

        # 这里把applyprefix、去掉既有方向前缀后的基础端口名、config作为当前 helper 的最终结果一并交回调用方。
        return self._apply_prefix(str_base_name, self.config["naming"]["inout_prefix"])

    # 构造输出端口对应的内部信号名。
    def _build_internal_output_name(self, str_base_name: str) -> str:

        """
        构造输出端口对应的内部信号名。
        
        参数:
            self: 当前 LayoutMixin 实例。
            str_base_name: 去掉方向前缀后的端口基础名。
        返回:
            str: 当前 helper 的返回结果。
        """

        # 内部输出基础名去掉外部方向前缀，避免出现双重 out 前缀。
        str_internal_base = self._normalize_internal_output_base_name(str_base_name)  # 内部输出基础名

        # 读取配置里的内部输出后缀，保持与旧渲染协议一致。
        str_internal_suffix = self.config["naming"]["internal_output_suffix"]  # 内部输出信号后缀

        # 返回 assign 源侧和声明侧共同使用的内部输出信号名。
        return f"{str_internal_base}{str_internal_suffix}"

    # 按照重命名映射批量更新端口声明。
    def _rename_ports(self, ports: list[PortDecl], rename_map: dict[str, str]) -> list[PortDecl]:

        """
        按照重命名映射批量更新端口声明。
        
        参数:
            self: 当前 LayoutMixin 实例。
            ports: 待处理的端口声明列表。
            rename_map: 旧端口名到新端口名的映射表。
        返回:
            list[PortDecl]: 按映射更新后的端口声明列表。
        """

        # 收集字段文本完成替换后的端口声明。
        list_renamed_ports: list[PortDecl] = []  # 已替换宽度、属性和 raw_text 的端口

        # 每个端口只改写可包含标识符的文本字段。
        for port in ports:

            # 追加字段重写后的端口对象，端口名本身由 normalize 阶段负责。
            list_renamed_ports.append(
                PortDecl(
                    # 这些字段不参与文本替换，只保留当前端口的结构定义。
                    direction=port.direction,
                    width=self._rename_text(port.width, rename_map),
                    name=port.name,
                    comment=port.comment,
                    group=port.group,
                    section=port.section,
                    signed=port.signed,

                    # 这些文本字段可能嵌入旧标识符，需要一起按映射表重写。
                    unpacked=self._rename_text(port.unpacked, rename_map),
                    attributes=self._rename_text(port.attributes, rename_map),
                    raw_text=self._rename_text(port.raw_text, rename_map) if port.raw_text else "",
                    synthetic=port.synthetic,

                    # 其余布局字段保持原样，避免影响前面已推断出的分组信息。
                    subgroup=port.subgroup,
                    subgroup_mode=port.subgroup_mode,
                    allow_generic_subgroup=port.allow_generic_subgroup,
                )
            )

            # 返回给后续声明渲染流程继续使用。

        # 这里把renamedports列表作为当前 helper 的最终结果一并交回调用方。
        return list_renamed_ports

    # 补齐端口布局字段，供后续渲染直接使用。
    def _prepare_ports_for_render(self, ports: list[PortDecl]) -> list[PortDecl]:

        """
        补齐端口布局字段，供后续渲染直接使用。
        
        参数:
            self: 当前 LayoutMixin 实例。
            ports: 待处理的端口声明列表。
        返回:
            list[PortDecl]: 已补齐布局字段的端口声明列表。
        """

        # 没有端口时保持空列表，避免后续分段逻辑访问首项。
        if not ports:

            # 返回空端口序列，兼容旧调用方的真值判断。
            return []

            # raw_text 端口保留手写顺序，formatter 不重新分组。

        # 只有在 any(端口仍保留原始文本声明 for port 属于 ports) 时，当前分支里的布局处理才有意义。
        if any(port.raw_text for port in ports):

            # 返回原始端口列表，避免破坏不可解析声明。
            return ports

            # 清理从旧注释推断出的非法 section，防止错误分组扩散。

        # 先复制并清理每个端口自带的布局提示，避免旧的 group/section 标记干扰这轮重新分段。
        list_sanitized_ports = [self._sanitize_port_layout_hints(port) for port in ports]  # 可安全重新布局的端口

        # 分段处理显式分组和无分组端口，再统一推导 subgroup。
        list_prepared_ports = self._prepare_port_layout_segments(list_sanitized_ports)  # 已完成协议分组排序的端口

        # 为连续同类接口补充二级 subgroup，供 banner 渲染使用。
        return self._apply_port_subgroups(list_prepared_ports)

    # 按显式分组和协议分组整理端口布局片段。
    def _prepare_port_layout_segments(self, ports: list[PortDecl]) -> list[PortDecl]:

        """
        按显式分组和协议分组整理端口布局片段。
        
        参数:
            self: 当前 LayoutMixin 实例。
            ports: 待处理的端口声明列表。
        返回:
            list[PortDecl]: 带 section 与 subgroup 信息的端口声明列表。
        """

        # 按原始顺序累积显式分组段和无分组段的处理结果。
        list_prepared_ports: list[PortDecl] = []  # 分段布局后的端口列表

        # 当前扫描位置指向尚未处理的第一个端口。
        int_start = 0  # 分段扫描起点

        # 按连续分组扫描，避免跨越用户已有 banner 边界。
        while int_start < len(ports):

            # 当前端口带有显式 group 时，整段交给协议组处理。
            if ports[int_start].group:

                # 记录当前显式分组名，后续只合并同名连续端口。
                str_group = ports[int_start].group  # 当前连续显式分组名

                # 找到同一显式分组的右边界。
                int_end = self._find_port_segment_end(  # 结束序号
                    ports,  # 当前端口列表
                    int_start,  # 本段扫描起点
                    str_group,  # 当前显式分组名
                    bool_grouped=True,  # 按显式 group 规则收束当前连续段
                )  # 显式分组段右边界

                # 保留显式分组语义，只在组内做协议顺序整理。
                list_prepared_ports.extend(self._prepare_explicit_group_segment(ports[int_start:int_end]))

                # 移动到下一段继续扫描。
                int_start = int_end  # 起始序号

                # 当前显式分组段已经完成，继续处理后续端口。
                continue

                # 无显式 group 的连续端口需要由名称推断协议分组。

            # 这一段连续端口到底在哪一项结束，需要先把右边界定位出来。
            int_end = self._find_port_segment_end(  # 当前无分组段的结束序号
                ports,  # 当前待整理的完整端口列表
                int_start,  # 当前无分组段的扫描起点
                "",  # 未显式分组时使用的空 group 名
                bool_grouped=False,  # 明确声明当前片段来自未手工分组的裸端口区
            )  # 当前无显式分组段的右边界

            # 这一段完全没有人工 group 标记，所以要整段送进自动协议归类链处理。
            list_prepared_ports.extend(self._prepare_ungrouped_port_segment(ports[int_start:int_end]))

            # 切换到下一段端口。
            int_start = int_end  # 下一段无分组端口的扫描起点

            # 返回已完成一层布局整理的端口列表。

        # 这里返回的是已经按显式分组、协议分组和 subgroup 规则重新整理过的整段端口列表。
        return list_prepared_ports

    # 查找当前端口布局片段的结束位置。
    def _find_port_segment_end(
        self,
        ports: list[PortDecl],
        start: int,
        str_group: str,
        *,
        bool_grouped: bool,
    ) -> int:

        """
        查找当前端口布局片段的结束位置。
        
        参数:
            self: 当前 LayoutMixin 实例。
            ports: 待处理的端口声明列表。
            start: 当前扫描的起始索引。
            str_group: 与 `str_group` 对应的当前输入参数。
            bool_grouped: 与 `bool_grouped` 对应的当前输入参数。
        返回:
            int: 当前端口片段结束后一位的索引。
        """

        # 扫描右边界时从当前端口之后开始。
        int_end = start + 1  # 当前连续段的候选右边界

        # 显式分组段按相同 group 合并，无分组段按 group 为空合并。
        while int_end < len(ports):

            # 显式分组要求 group 名完全一致。
            if bool_grouped and ports[int_end].group == str_group:

                # 右边界后移，继续吸收同名显式分组端口。
                int_end += 1  # 同名显式分组后的右边界

                # 当前端口仍属于这段未分组片段，右边界继续向后扩展即可。
                continue

                # 无分组段只吸收 group 为空的连续端口。

            # 只有在 当前处于未显式分组模式且后面的端口也还没有 group 时，当前分支里的布局处理才有意义。
            if not bool_grouped and not ports[int_end].group:

                # 右边界后移，继续吸收未分组端口。
                int_end += 1  # 未分组连续段后的右边界

                # 当前端口仍属于本段，继续检查下一项。
                continue

                # 遇到不同分组边界时结束本段扫描。

            # 右边界或目标位置已经确认后，继续扫描只会重复工作，所以这里立刻结束循环。
            break

            # 返回半开区间右边界，供调用方切片。

        # 这里把当前片段扫描到的右边界索引作为当前 helper 的最终结果一并交回调用方。
        return int_end

    # 清理单个端口上携带的布局提示字段。
    def _sanitize_port_layout_hints(self, port: PortDecl) -> PortDecl:
        """
        清理单个端口上携带的布局提示字段。
        
        参数:
            self: 当前 LayoutMixin 实例。
            port: 当前端口声明。
        返回:
            PortDecl: 清理过布局提示字段的端口声明。
        """

        # 只有在 port.group 或 不 port.section 或 _is_port_section_label(port.section) 时，当前分支里的布局处理才有意义。
        if port.group or not port.section or self._is_port_section_label(port.section):

            # 这里把端口作为当前 helper 的最终结果一并交回调用方。
            return port

        # section 被清空后，其它字段仍保持原声明，供后续 banner 与排序流程继续复用。
        return PortDecl(
            direction=port.direction,
            width=port.width,
            name=port.name,
            comment=port.comment,
            group=port.group,
            section="",
            signed=port.signed,

            # section 虽然被清空，但声明文本和 synthetic 标记仍需保留。
            unpacked=port.unpacked,
            attributes=port.attributes,
            raw_text=port.raw_text,
            synthetic=port.synthetic,

            # 子分组相关字段保持不变，避免影响后续 subgroup banner 行为。
            subgroup=port.subgroup,
            subgroup_mode=port.subgroup_mode,
            allow_generic_subgroup=port.allow_generic_subgroup,
        )

    # 判断文本是否表示端口 section 标签。
    def _is_port_section_label(self, text: str) -> bool:
        """
        判断文本是否表示端口 section 标签。
        
        参数:
            self: 当前 LayoutMixin 实例。
            text: 待识别的文本。
        返回:
            bool: 当前 helper 的返回结果。
        """

        # 先去掉候选 group 标签两端的空白，避免 section 判定被注释里的排版差异干扰。
        normalized = text.strip()  # 去掉首尾空白后的 group 标签文本

        # 只有在 规范化后的标签为空 时，当前分支里的布局处理才有意义。
        if not normalized:

            # 一旦确认 规范化后的标签为空，这里就立即返回假值，不再继续走后面的判定。
            return False

        # 只有在 规范化后的标签命中了 section 白名单 时，当前分支里的布局处理才有意义。
        if normalized in self.PORT_SECTION_LABELS:

            # 一旦确认 规范化后的标签命中了 section 白名单，这里就立即返回真值，不再继续走后面的判定。
            return True

        # 这里把bool、匹配端口section别名、去掉首尾空白后的规范化标签文本作为当前 helper 的最终结果一并交回调用方。
        return bool(self._match_port_section_alias(normalized))

    # 判断文本是否表示当前分组下的子分组标签。
    def _is_port_subgroup_label(self, text: str, current_group: str) -> bool:
        """
        判断文本是否表示当前分组下的子分组标签。
        
        参数:
            self: 当前 LayoutMixin 实例。
            text: 待识别的文本。
            current_group: 当前所在的端口分组名。
        返回:
            bool: 当前 helper 的返回结果。
        """

        # 先把标签文本去掉多余空白，后面所有 section 判定都基于这份规范化结果。
        normalized = text.strip()  # 规范化结果

        # 只有在 规范化后的标签为空 或 不 current_group 时，当前分支里的布局处理才有意义。
        if not normalized or not current_group:

            # 一旦确认 规范化后的标签为空 或 不 current_group，这里就立即返回假值，不再继续走后面的判定。
            return False

        # 只有在 normalized 属于 {"用户接口", current_group} 时，当前分支里的布局处理才有意义。
        if normalized in {"用户接口", current_group}:

            # 一旦确认 normalized 属于 {"用户接口", current_group}，这里就立即返回假值，不再继续走后面的判定。
            return False

        # 只有在 规范化后的标签本身是 section 标签 时，当前分支里的布局处理才有意义。
        if self._is_port_section_label(normalized):

            # 一旦确认 规范化后的标签本身是 section 标签，这里就立即返回假值，不再继续走后面的判定。
            return False

        # 只有看起来像“某某接口”而不是“总线”或 section 标签时，才把它视为新的 group 标题。
        return "总线" not in normalized and normalized.endswith("接口")

    # 匹配端口 section 标签的别名。
    def _match_port_section_alias(self, text: str) -> str:
        """
        匹配端口 section 标签的别名。
        
        参数:
            self: 当前 LayoutMixin 实例。
            text: 待识别的文本。
        返回:
            str: 当前 helper 的返回结果。
        """

        # 先把 section 标签规范化成别名表使用的统一文本，后面才能稳定做字典查找。
        normalized = self._normalize_port_section_alias(text)  # 归一化后的 section 别名键

        # 规范化后仍为空时，不可能命中任何 section 别名。
        if not normalized:

            # 空白标签没有可映射的小节别名，这里直接返回空串表示未命中。
            return ""

        # 归一化后的标签如果存在别名映射，就返回对应的标准 section 名。
        return self.PORT_SECTION_ALIASES.get(normalized, "")

    # 把端口 section 别名统一成规范名称。
    def _normalize_port_section_alias(self, text: str) -> str:
        """
        把端口 section 别名统一成规范名称。
        
        参数:
            self: 当前 LayoutMixin 实例。
            text: 待识别的文本。
        返回:
            str: 当前 helper 的返回结果。
        """

        # 先把原始标签折成去空白的小写文本，保证后续规则不受大小写影响。
        lowered = text.strip().lower()  # 去空白后的基础小写文本

        # 清洗后为空时，不需要继续做任何命名规则识别。
        if not lowered:

            # 空标签没有可匹配的语义，直接返回空串结束本轮标准化。
            return ""

        # 把常见分隔符统一换成空格，让后面的词元匹配只面对一种分词形式。
        lowered = re.sub(r"[-_/&]+", " ", lowered)  # 分隔符折叠后的命名文本

        # 独立出现的 and 只是连词，不参与协议成员语义判断，这里提前剔除。
        lowered = re.sub(r"\band\b", " ", lowered)  # 去掉连接词后的命名文本

        # 最后把多余空格压成单空格，生成下游规则统一消费的标准文本。
        lowered = re.sub(r"\s+", " ", lowered).strip()  # 折叠空白后的标准命名文本

        # 返回这份稳定的小写命名文本，供时钟、复位和协议成员规则共用。
        return lowered

    # 准备显式声明了分组信息的端口片段。
    def _prepare_explicit_group_segment(self, ports: list[PortDecl]) -> list[PortDecl]:
        """
        准备显式声明了分组信息的端口片段。
        
        参数:
            self: 当前 LayoutMixin 实例。
            ports: 待处理的端口声明列表。
        返回:
            list[PortDecl]: 已补齐显式分组信息的端口列表。
        """

        # 先给显式 group 段里的每个端口附上推断布局，后面补 section 时直接复用。
        list_annotated_ports = [(index, port, self._infer_port_layout(port)) for index, port in enumerate(ports)]  # 带布局标注的端口声明

        # 关闭排序时，这个 helper 只负责补齐 layout 字段，不重新调整端口顺序。
        if not self.config["formatter"].get("sort_ports", True):

            # 关闭端口排序时，这里只补齐 layout 字段，不改变原始端口的先后顺序。
            return [
                self._with_port_layout(port, port.group, port.section, port.subgroup)
                for _, port, _ in list_annotated_ports
            ]

        # 只有在 not any(info.kind 属于 KNOWN_PROTOCOL_KINDS for _, _, info 属于 annotated) 时，当前分支里的布局处理才有意义。
        if not any(info.kind in self.KNOWN_PROTOCOL_KINDS for _, _, info in list_annotated_ports):

            # 这一组端口没有命中任何已知协议时，直接沿用原有 group/section 信息返回即可。
            return [
                self._with_port_layout(port, port.group, port.section, port.subgroup)
                for _, port, _ in list_annotated_ports
            ]

        # 先确认这一段协议端口是否全部自带 section，决定后面要不要按推断规则补缺失 section。
        all_have_explicit_section = all(port.section for _, port, _ in list_annotated_ports)  # 当前协议段是否全部显式写了 section

        # 只要发现有端口缺少 section，后面的协议分段逻辑就需要允许自动补全 section。
        fill_missing_sections = not all_have_explicit_section  # 当前协议段是否需要自动补齐缺失 section

        # 这张映射表记录每个端口索引在当前协议段里最终应该落到哪个 section。
        dict_effective_sections = {  # 当前协议段端口索引到生效 section 的映射表
            index: self._resolve_explicit_group_section(port, info, fill_missing_sections)  # 当前端口索引对应的生效 section
            for index, port, info in list_annotated_ports  # 当前协议段全部待判定的端口项
        }

        # 协议段的 section 归属算清楚以后，这里统一交给协议分组整理器生成最终布局顺序。
        return self._prepare_protocol_group_segment(
            list_annotated_ports,
            group_name=ports[0].group,
            effective_sections=dict_effective_sections,
            preserve_existing_sections=all_have_explicit_section,
        )

    # 为未显式分组的端口推断布局片段。
    def _prepare_ungrouped_port_segment(self, ports: list[PortDecl]) -> list[PortDecl]:
        """
        为未显式分组的端口推断布局片段。
        
        参数:
            self: 当前 LayoutMixin 实例。
            ports: 待处理的端口声明列表。
        返回:
            list[PortDecl]: 经过自动分组推断后的端口列表。
        """

        # 先为未显式分组段生成初始布局候选，后面未知前缀聚类会在这份候选表上继续整理。
        list_annotated_ports = [(index, port, self._infer_port_layout(port)) for index, port in enumerate(ports)]  # 未分组端口的初始布局候选列表

        # 先给未显式分组的端口附上推断布局，后面再继续做未知簇和协议排序。
        list_annotated_ports = self._apply_unknown_prefix_clusters(list_annotated_ports)  # 叠加未知前缀聚类后的布局候选列表

        # 这个列表会暂存端口索引、端口声明和布局信息三元组，后面排序阶段直接消费它。
        list_finalized: list[tuple[int, PortDecl, PortLayoutInfo]] = []  # 后续排序阶段消费的端口布局三元组列表

        # 这一轮遍历会把每个端口和它的布局信息重新打包，形成后续排序阶段使用的稳定候选集。
        for index, port, layout_state in list_annotated_ports:

            # unknown 类型的端口还没有命中任何协议规则，所以先把它们显式归到“用户接口”分组。
            if layout_state.kind == "unknown":

                # 当前端口推断出的布局信息会决定 group、section 和 member 排序。
                layout_state = PortLayoutInfo(  # 当前端口重写后的布局信息
                    group="用户接口",  # 未知协议端口统一归入的用户接口分组
                    section="",  # 未知协议端口默认不预设 section
                    section_rank=0,  # 未知协议端口不参与 section 细分排序
                    direction_rank=layout_state.direction_rank,  # 方向顺序沿用原始推断结果
                    member_rank=0,  # 未知协议端口在用户接口分组内不细分成员顺序
                    kind="user",  # 统一标记为用户接口成员
                )

            # 当前端口的 layout 信息已经确定后，这里把它追加到待排序候选列表里。
            list_finalized.append((index, port, layout_state))

        # 用户关闭排序时，只保留刚推断出的布局字段，不再额外重排端口顺序。
        if not self.config["formatter"].get("sort_ports", True):

            # 关闭排序时只需把布局信息写回端口副本，并保持原始出现顺序。
            list_preserved_order: list[PortDecl] = []  # 保留原顺序写回布局后的端口列表

            # 逐个写回布局字段，避免列表推导式把“保序写回”语义压成一长行。
            for _, port, info in list_finalized:

                # 把当前端口的 group 和 section 写回到副本列表，保持输入顺序不变。
                list_preserved_order.append(self._with_port_layout(port, info.group, info.section, port.subgroup))

            # 用户显式关闭排序时，直接返回按原顺序写回布局后的结果。
            return list_preserved_order

        # 这张表记录每个分组第一次出现的位置，后面会用它保持分组的大致输入顺序。
        dict_group_order: dict[str, int] = {}  # 分组顺序映射表

        # 这里遍历整理后的候选集，只为每个 group 记住第一次出现的位置。
        for index, _, layout_info in list_finalized:

            # 每个 group 只记录第一次出现的位置，后面跨组排序就按这个顺序保持稳定。
            dict_group_order.setdefault(layout_info.group, index)

        # 这个列表会顺序累积每个 group 最终整理好的端口结果。
        list_prepared: list[PortDecl] = []  # 整理后列表

        # 下面按分组首次出现的位置遍历各个 group，确保跨组顺序和输入文本保持一致。
        for group_name, _ in sorted(dict_group_order.items(), key=lambda item: item[1]):

            # 当前分组下真实参与排序的端口项，会先聚成这一小段再统一处理。
            list_group_items = [item for item in list_finalized if item[2].group == group_name]  # 当前分组里的端口项列表

            # 如果某个分组里已经出现已知协议成员，就把它交给协议分组流程做更细的 section 排序。
            if any(info.kind in self.KNOWN_PROTOCOL_KINDS for _, _, info in list_group_items):

                # 这里先判断当前 group 里的协议端口是否已经全部带 section，再决定是否允许自动回填。
                all_have_explicit_section = all(port.section for _, port, _ in list_group_items)  # 当前 group 是否全部显式写了 section

                # 当前 group 里只要存在缺失 section 的端口，就开启自动补齐 section 的整理分支。
                fill_missing_sections = not all_have_explicit_section  # 当前 group 是否需要自动补齐缺失 section

                # 先为这个 group 里的每个端口算出本轮排序真正采用的 section。
                dict_effective_sections = {  # 当前 group 端口索引到生效 section 的映射表
                    index: self._resolve_explicit_group_section(port, info, fill_missing_sections)  # 当前端口最终参与排序的生效 section
                    for index, port, info in list_group_items  # 当前 group 内全部待判定的端口项
                }

                # 协议分组整理出的完整结果在这里整体并回主列表，保持 group 内排序一次性完成。
                list_prepared.extend(
                    self._prepare_protocol_group_segment(
                        list_group_items,
                        group_name=group_name,
                        effective_sections=dict_effective_sections,
                        preserve_existing_sections=all_have_explicit_section,
                    )
                )

                # 未显式分组端口整理流程在当前元素处理完成后，直接跳到下一轮循环继续看后面的端口。
                continue

            # 不属于已知协议的分组走普通排序路径，这里先按 section 和 member 优先级排好候选。
            list_ranked_items = sorted(  # 当前分组普通端口的排序结果
                list_group_items,  # 当前分组里的全部候选端口项
                key=lambda item: (  # section、方向和成员优先级组成的排序键
                    item[2].section_rank,  # 当前端口在本分组内的 section 排序号
                    item[2].direction_rank if item[2].kind != "cluster" else 0,  # cluster 标题优先排在普通端口前
                    item[2].member_rank if item[2].kind not in {"cluster", "user"} else 0,  # 普通成员才继续比较协议内部顺序
                    item[0],  # 完全同优先级时退回原始出现顺序
                ),
            )

            # 排序完成后，把这一组普通端口按新顺序整体并回最终输出列表。
            list_prepared.extend(
                self._with_port_layout(port, info.group, info.section, port.subgroup)
                for _, port, info in list_ranked_items
            )

        # 这里把整理后列表作为当前 helper 的最终结果一并交回调用方。
        return list_prepared

    # 复制端口声明并写入新的布局信息。
    def _with_port_layout(
        self,
        port: PortDecl,
        group: str,
        section: str,
        subgroup: str = "",
        *,
        subgroup_mode: str | None = None,
        allow_generic_subgroup: bool | None = None,
    ) -> PortDecl:
        """
        复制端口声明并写入新的布局信息。
        
        参数:
            self: 当前 LayoutMixin 实例。
            port: 当前端口声明。
            group: 端口分组名。
            section: 与 `section` 对应的当前输入参数。
            subgroup: 端口子分组名。
            subgroup_mode: 与 `subgroup_mode` 对应的当前输入参数。
            allow_generic_subgroup: 与 `allow_generic_subgroup` 对应的当前输入参数。
        返回:
            PortDecl: 附带指定布局信息的新端口声明。
        """

        # 这里直接返回写入新布局后的端口副本，供后续 banner 与排序流程继续消费。
        return PortDecl(
            direction=port.direction,
            width=port.width,
            name=port.name,
            comment=port.comment,
            group=group,
            section=section,
            signed=port.signed,

            # 声明结构字段沿用原值，只覆盖布局整理阶段关心的 group/section/subgroup。
            unpacked=port.unpacked,
            attributes=port.attributes,
            subgroup=subgroup,
            subgroup_mode=port.subgroup_mode if subgroup_mode is None else subgroup_mode,

            # 调用方显式指定通用 subgroup 策略时，使用新值覆盖旧值。
            allow_generic_subgroup=port.allow_generic_subgroup
            if allow_generic_subgroup is None
            else allow_generic_subgroup,
        )

    # 复制端口声明并写入新的子分组标签。
    def _with_port_subgroup(self, port: PortDecl, subgroup: str) -> PortDecl:
        """
        复制端口声明并写入新的子分组标签。
        
        参数:
            self: 当前 LayoutMixin 实例。
            port: 当前端口声明。
            subgroup: 端口子分组名。
        返回:
            PortDecl: 附带指定子分组的新端口声明。
        """

        # 这里只改 subgroup，其它字段继续沿用原声明，避免破坏前面推断好的布局信息。
        return PortDecl(
            direction=port.direction,
            width=port.width,
            name=port.name,
            comment=port.comment,
            group=port.group,
            section=port.section,
            signed=port.signed,

            # 端口正文和生成标记仍沿用原值，避免影响非 subgroup 相关行为。
            unpacked=port.unpacked,
            attributes=port.attributes,
            raw_text=port.raw_text,
            synthetic=port.synthetic,

            # 这里只更新 subgroup 名，其它 subgroup 控制位保持原样。
            subgroup=subgroup,
            subgroup_mode=port.subgroup_mode,
            allow_generic_subgroup=port.allow_generic_subgroup,
        )

    # 推导单个端口所属的子分组键。
    def _derive_port_subgroup_key(self, port: PortDecl) -> str:
        """
        推导单个端口所属的子分组键。
        
        参数:
            self: 当前 LayoutMixin 实例。
            port: 当前端口声明。
        返回:
            str: 当前 helper 的返回结果。
        """

        # 这里先拿到去掉方向前缀后的基础端口名，方便后面的协议识别统一基于同一命名形态判断。
        base_name = self._strip_known_prefixes(port.name).lower()  # 基础名称结果

        # 这里先整理词元结果，方便后续步骤直接复用。
        list_name_tokens = [token for token in base_name.split("_") if token]  # 词元结果

        # 协议名至少要有“槽位前缀 + 成员名”两段，缺一段就无法派生 group 标题。
        if len(list_name_tokens) < 2:

            # 词元不足时说明端口名还不像协议成员，这里返回空串表示不生成 group 标题。
            return ""

        # 去掉最后一个成员词元后，剩余前缀就是当前协议 group 的标题主体。
        return f"{'_'.join(list_name_tokens[:-1]).upper()}接口"

    # 提取协议端口所属的槽位标签。
    def _extract_protocol_slot_label(self, port: PortDecl, info: PortLayoutInfo) -> str:
        """
        提取协议端口所属的槽位标签。
        
        参数:
            self: 当前 LayoutMixin 实例。
            port: 当前端口声明。
            info: 当前端口对应的布局信息。
        返回:
            str: 当前 helper 的返回结果。
        """

        # 只有已知协议成员才有可提取的槽位标签，其它端口直接跳过。
        if info.kind not in self.KNOWN_PROTOCOL_KINDS:

            # 非协议端口没有槽位概念，这里返回空串让上游沿用原 subgroup。
            return ""

        # 规范化名称。
        normalized_name = self._strip_known_prefixes(port.name).lower()  # 规范化名称结果

        # 拆出的词元会同时参与 AXI/AXIS 槽位识别和 Vitis wrapper 特例判断。
        list_name_tokens = [token for token in normalized_name.split("_") if token]  # 协议端口名拆出的词元列表

        # 协议槽位识别至少需要槽位和成员两段词元，否则无法继续派生。
        if len(list_name_tokens) < 2:

            # 词元不足时无法形成稳定槽位标签，返回空串表示本端口没有 subgroup。
            return ""

        # 第二个词元是 axis 时，当前端口命中 AXIS 命名规则。
        if list_name_tokens[1] == "axis":

            # AXIS 槽位标签沿用首个词元，并固定追加 `_AXIS` 后缀。
            return f"{list_name_tokens[0].upper()}_AXIS"

        # 第二个词元是 axi 时，继续检查普通 AXI 和 Vitis wrapper 的细分写法。
        if list_name_tokens[1] == "axi":

            # Vitis wrapper 某些端口会把 control 子通道编码进第三个词元，这里单独保留那部分信息。
            if (
                self._has_vitis_wrapper_port_preserve(normalized_name)
                and len(list_name_tokens) > 2
                and list_name_tokens[0] in {"s", "m"}
                and (list_name_tokens[2] == "control" or list_name_tokens[0] == "m")
            ):

                # 命中 Vitis wrapper 特例时，把第三个词元并进 AXI 槽位标签。
                return f"{list_name_tokens[0].upper()}_AXI_{list_name_tokens[2].upper()}"

            # 普通 AXI 命名只保留主从前缀，生成标准的 `_AXI` 槽位标签。
            return f"{list_name_tokens[0].upper()}_AXI"

        # 既不是 AXIS 也不是 AXI 命名时，这个协议端口不派生槽位标签。
        return ""

    # 判断协议分组是否只对应单个槽位。
    def _protocol_group_matches_single_slot(self, group: str, slot_label: str) -> bool:
        """
        判断协议分组是否只对应单个槽位。
        
        参数:
            self: 当前 LayoutMixin 实例。
            group: 端口分组名。
            slot_label: 与 `slot_label` 对应的当前输入参数。
        返回:
            bool: 当前 helper 的返回结果。
        """

        # 规范化分组。
        normalized_group = group.strip().upper()  # 规范化分组结果

        # 分组名或槽位标签任一为空时，不可能建立“group 对应单槽位”的关系。
        if not normalized_group or not slot_label:

            # 缺少任一输入时，直接判定“不匹配单槽位 group”。
            return False

        # 只有在 slot_label.endswith("_AXIS") 时，当前分支里的布局处理才有意义。
        if slot_label.endswith("_AXIS"):

            # AXIS 槽位标签去掉 `_AXIS` 后缀后，只要 group 以该前缀结尾就视为匹配。
            return normalized_group.endswith(slot_label[:-5])

        # 普通 AXI 槽位保留 `_AXI` 后缀，直接用它和 group 尾部做对应。
        if slot_label.endswith("_AXI"):

            # AXI 槽位标签本身就是 group 尾缀要求，直接判断是否以它结尾即可。
            return normalized_group.endswith(slot_label)

        # 一旦确认 slot_label.endswith("_AXI")，这里就立即返回假值，不再继续走后面的判定。
        return False

    # 生成协议槽位标签的排序键。
    def _sort_slot_key(self, slot_label: str) -> tuple[int, str]:
        """
        生成协议槽位标签的排序键。
        
        参数:
            self: 当前 LayoutMixin 实例。
            slot_label: 与 `slot_label` 对应的当前输入参数。
        返回:
            tuple[int, str]: 槽位标签对应的稳定排序键。
        """

        # 简单总线槽位标题遵循 `S0_AXI` / `M1_AXIS` 这类格式，先按这个模式拆字段。
        match = re.match(r"^(?P<prefix>[A-Z]+)(?P<index>\d*)_(?P<kind>AXIS|AXI)$", slot_label)  # 匹配结果

        # 不符合简单总线槽位格式时，只能退回到“未知顺序但保持原标签”的兜底键。
        if not match:

            # 槽位标签拆不出主从前缀时，返回一个很靠后的排序键并保留原标签文本。
            return (999, slot_label)

        # 正则命中的前缀决定当前简单总线成员属于哪一侧接口。
        str_prefix = match.group("prefix")  # 简单总线命名里的主从前缀文本

        # 先把总线编号的原始文本单独取出来，后面才能安全转成整数参与排序。
        index_text = match.group("index")  # index文本结果

        # 这里把总线编号转成整数，供槽位标签和排序键继续复用。
        index = int(index_text) if index_text else -1  # 简单总线槽位编号

        # master/slave 前缀会影响槽位排序，所以这里先映射成稳定的数值优先级。
        int_prefix_rank = {"S": 0, "M": 1}.get(str_prefix, 9)  # prefix优先级结果

        # 主从前缀优先级和编号组合后，就得到简单总线槽位的稳定排序键。
        return (int_prefix_rank * 1000 + index + 1, slot_label)

    # 生成协议槽位横幅标题。
    def _derive_protocol_slot_banner_title(self, group: str, subgroup: str) -> str:
        """
        生成协议槽位横幅标题。
        
        参数:
            self: 当前 LayoutMixin 实例。
            group: 端口分组名。
            subgroup: 端口子分组名。
        返回:
            re.Match[str] | None: 命中协议槽位横幅格式时返回匹配对象，否则返回空值。
        """

        # 这里只接受协议槽位风格的 subgroup，避免把普通中文 subgroup 误解释成槽位横幅。
        match = re.match(r"^(?P<slot>[A-Z]+\d*)_(?:AXIS|AXI)接口$", subgroup.strip(), re.IGNORECASE)  # 协议槽位横幅格式匹配结果

        # 不符合协议槽位标题格式时，让调用方继续沿用原始 subgroup 文本。
        if not match:

            # subgroup 不符合协议槽位格式时，不生成专门的横幅标题。
            return ""

        # 槽位标题采用“分组名 + 小写槽位号”的旧格式，保持现有横幅渲染兼容。
        return f"{group}{match.group('slot').lower()}"

    # 渲染端口子分组的头部横幅文本。
    def _render_port_subgroup_header(self, port: PortDecl, group: str) -> str:
        """
        渲染端口子分组的头部横幅文本。
        
        参数:
            self: 当前 LayoutMixin 实例。
            port: 当前端口声明。
            group: 端口分组名。
        返回:
            str: 需要输出的槽位横幅文本；当前端口无需独立横幅时返回空串。
        """

        # 只有 subgroup 先于 section 展示时，当前端口才需要独立生成槽位横幅。
        if port.subgroup_mode == "subgroup_first":

            # 只有需要 subgroup 横幅时，才提前生成当前槽位对应的 banner 标题。
            str_slot_banner_title = self._derive_protocol_slot_banner_title(group, port.subgroup)  # 槽位bannertitle结果

            # 只有槽位标题成功派生出来时，才生成对应的 banner 文本。
            if str_slot_banner_title:

                # subgroup-first 场景下优先输出协议槽位横幅，沿用 bus 横幅样式。
                return make_banner(str_slot_banner_title, "bus")

        # 没有专门的槽位横幅时，退回到普通的 `//subgroup` 注释行。
        return f"//{port.subgroup}"

    # 判断当前分组是否应当省略外层分组横幅。
    def _should_suppress_port_group_banner(self, ports: list[PortDecl], start_index: int) -> bool:
        """
        判断当前分组是否应当省略外层分组横幅。
        
        参数:
            self: 当前 LayoutMixin 实例。
            ports: 待处理的端口声明列表。
            start_index: 当前分组的起始端口索引。
        返回:
            bool: 当前 helper 的返回结果。
        """

        # 这里算出来的分组名会直接决定端口最终落在哪个 group 横幅下面。
        str_group_name = ports[start_index].group  # 分组结果

        # 起点端口本身没有 group 时，不可能生成“省略外层 group 横幅”的版式。
        if not str_group_name:

            # 没有 group 横幅可省略时，直接返回假值。
            return False

        # 这里先取出当前片段的起始端口，后面的 synthetic 和 raw_text 过滤都围绕它判断。
        target_port = ports[start_index]  # 端口结果

        # 只有在 port.synthetic 或 端口仍保留原始文本声明 时，当前分支里的布局处理才有意义。
        if target_port.synthetic or target_port.raw_text:

            # 一旦确认 port.synthetic 或 端口仍保留原始文本声明，这里就立即返回假值，不再继续走后面的判定。
            return False

        # 只有 subgroup-first 且后面存在稳定 subgroup 簇时，才值得省略外层 group 横幅。
        return bool(
            target_port.subgroup_mode == "subgroup_first"
            and target_port.subgroup
            and self._derive_protocol_slot_banner_title(str_group_name, target_port.subgroup)
        )

    # 准备协议类端口分组的布局片段。
    def _prepare_protocol_group_segment(
        self,
        annotated: list[tuple[int, PortDecl, PortLayoutInfo]],
        *,
        group_name: str,
        effective_sections: dict[int, str],
        preserve_existing_sections: bool,
    ) -> list[PortDecl]:
        """
        准备协议类端口分组的布局片段。
        
        参数:
            self: 当前 LayoutMixin 实例。
            annotated: 与 `annotated` 对应的当前输入参数。
            group_name: 与 `group_name` 对应的当前输入参数。
            effective_sections: 与 `effective_sections` 对应的当前输入参数。
            preserve_existing_sections: 与 `preserve_existing_sections` 对应的当前输入参数。
        返回:
            list[PortDecl]: 已按协议槽位切分好的端口列表。
        """

        # 先记录每个端口对应的协议槽位，后面才能判断这一段要不要拆成多路接口。
        dict_slot_labels = {index: self._extract_protocol_slot_label(port, info) for index, port, info in annotated}  # 端口索引到槽位标签的映射

        # 这里保留当前协议段里真正出现过的非空槽位标签集合。
        list_distinct_slots = [slot for slot in dict.fromkeys(dict_slot_labels.values()) if slot]  # 当前协议段里实际出现过的非空槽位标签列表

        # 这张映射表控制协议段内各个 section 的最终排序先后。
        section_order = self._build_section_order_map(  # section顺序结果
            annotated, effective_sections, preserve_existing=preserve_existing_sections  # 待排序的协议端口项和 section 配置
        )

        # 一个协议段里出现多个槽位时，要先按槽位再按 section 和 member 重新排端口顺序。
        if len(list_distinct_slots) > 1:

            # 多槽位协议段需要先给每个槽位标签分配稳定排序号。
            dict_slot_order = {
                slot: order  # 当前槽位对应的稳定顺序号
                for order, slot in enumerate(sorted(list_distinct_slots, key=self._sort_slot_key))  # 依槽位排序键枚举顺序号
            }  # 槽位顺序结果

            # 多槽位协议段走完整排序路径，这里先把所有候选按槽位和 section 规则排好。
            list_ranked_items = sorted(  # 多槽位协议段的排序结果
                annotated,  # 当前协议分组里的全部候选端口项
                key=lambda item: (  # 槽位、section、方向和成员联合排序键
                    dict_slot_order.get(dict_slot_labels[item[0]], 999),  # 先按槽位聚拢同一接口成员
                    section_order.get(effective_sections[item[0]], 999),  # 同一槽位内再按 section 顺序展开
                    item[2].direction_rank, item[2].member_rank, item[0],  # 方向、成员和原始次序共同收束最终顺序
                ),
            )

            # 多槽位排序完成后，逐个写回 section 和对应的槽位 subgroup。
            list_ranked_ports: list[PortDecl] = []  # 多槽位协议段写回布局后的端口列表

            # 这里逐个回写端口，确保每个槽位标签都准确落到对应 subgroup 上。
            for index, port, _ in list_ranked_items:

                # 当前端口会写回所属槽位 subgroup，确保多槽位接口在输出中清晰分段。
                list_ranked_ports.append(
                    self._with_port_layout(
                        port,
                        group_name,
                        effective_sections[index],
                        f"{dict_slot_labels[index]}接口" if dict_slot_labels[index] else port.subgroup,
                        subgroup_mode="subgroup_first" if dict_slot_labels[index] else port.subgroup_mode,
                        allow_generic_subgroup=False,
                    )
                )

            # 多槽位协议段在这里返回已经写回 subgroup 的结果。
            return list_ranked_ports

        # 只有单槽位但 group 名还没吸收槽位信息时，这里再补一轮带槽位 subgroup 的输出。
        if list_distinct_slots and not self._protocol_group_matches_single_slot(group_name, list_distinct_slots[0]):

            # 单槽位但仍需保留 subgroup 时，这里只按 section 和 member 规则排序当前候选。
            list_ranked_items = sorted(  # 单槽位但保留 subgroup 时的排序结果
                annotated,  # 当前单槽位协议段的端口候选
                key=lambda item: (  # section、方向和成员三层排序键
                    section_order.get(effective_sections[item[0]], 999),  # 单槽位内部仍先按 section 展开
                    item[2].direction_rank, item[2].member_rank,  # section 内继续按方向优先级和成员表顺序排布
                    item[0],  # 最后用原始索引兜底稳定顺序
                ),
            )

            # 单槽位场景下，先把唯一的槽位标签取出来供 subgroup 命名使用。
            str_slot_label = list_distinct_slots[0]  # 槽位标签结果

            # 这一支需要显式保留唯一槽位横幅，因此整段端口都要回写同一个 subgroup。
            list_single_slot_ports: list[PortDecl] = []  # 单槽位 subgroup 写回后的端口列表

            # 这里逐个写回统一的槽位 subgroup，保持单槽位接口在输出中的分组一致性。
            for index, port, _ in list_ranked_items:

                # 当前端口沿用同一个槽位标签，保证这一段最终收束到统一的 subgroup 横幅下。
                list_single_slot_ports.append(
                    self._with_port_layout(
                        port,
                        group_name,
                        effective_sections[index],
                        f"{str_slot_label}接口",
                        subgroup_mode="subgroup_first",
                        allow_generic_subgroup=False,
                    )
                )

            # 单槽位但保留 subgroup 的路径在这里返回写回后的结果。
            return list_single_slot_ports

        # 协议 group 已经天然对应单槽位时，只按 section 和 member 排序即可。
        list_ranked_items = sorted(  # 单槽位协议段的排序结果
            annotated,  # group 标题已吸收槽位信息后的端口候选
            key=lambda item: (  # section、方向和原始次序联合排序键
                section_order.get(effective_sections[item[0]], 999),  # 单槽位协议段先按 section 排开
                item[2].direction_rank, item[2].member_rank,  # 同一 section 内沿用方向优先级和成员表顺序
                item[0],  # 最后保留原始声明先后次序
            ),
        )

        # 这一支的 group 标题本身已经含有槽位语义，因此只补 section，不再改 subgroup。
        list_group_aligned_ports: list[PortDecl] = []  # group 已吸收槽位信息后的端口列表

        # 逐个写回 section，避免误覆盖已经与 group 保持一致的 subgroup 信息。
        for index, port, _ in list_ranked_items:

            # 当前端口原有 subgroup 已经可用，这里只把最终 section 写回到端口副本。
            list_group_aligned_ports.append(
                self._with_port_layout(  # group 已吸收槽位语义时只更新 section 的端口副本
                    port, group_name, effective_sections[index], port.subgroup, allow_generic_subgroup=False
                )
            )

        # 单槽位 group 已自带槽位语义时，返回仅更新 section 的端口列表。
        return list_group_aligned_ports

    # 把子分组标签应用到端口列表。
    def _apply_port_subgroups(self, ports: list[PortDecl]) -> list[PortDecl]:

        """
        把子分组标签应用到端口列表。
        
        参数:
            self: 当前 LayoutMixin 实例。
            ports: 待处理的端口声明列表。
        返回:
            list[PortDecl]: 写回子分组标签后的端口列表。
        """

        # 空端口不需要补充 subgroup。
        if not ports:

            # 返回空列表，保持旧调用方的空输入行为。
            return []

            # 克隆端口列表，避免在调用方持有的列表对象上原地改写。

        # 这里先复制一份端口列表，后面的 subgroup 写回都在副本上完成。
        list_updated_ports = list(ports)  # 带 subgroup 推断结果的端口列表

        # `int_start` 始终指向下一个尚未补 subgroup 的 group/section 连续段开头。
        int_start = 0  # 下一段待补 subgroup 的 group/section 片段起点

        # 逐段处理同一 group 和 section 内的端口。
        while int_start < len(list_updated_ports):

            # 先找出当前 group/section 连续片段的右边界，再把这整段交给 subgroup 整理流程。
            int_end = self._find_group_section_segment_end(  # 当前 group/section 连续段的结束序号
                list_updated_ports,  # 待更新的端口片段列表
                int_start,  # 当前 group/section 连续段的起点
            )  # 当前 group/section 段右边界

            # 对当前段内可自动分组的连续端口补充 subgroup。
            list_segment = self._apply_subgroups_to_segment(list_updated_ports[int_start:int_end])  # 已补充 subgroup 的当前端口段

            # 当前片段整理完成后，这里把更新后的端口段整体写回主列表。
            list_updated_ports[int_start:int_end] = list_segment  # 当前 group/section 片段整理后的端口序列

            # 当前片段写回完成后，扫描起点直接跳到该片段右边界。
            int_start = int_end  # 下一段待补 subgroup 片段的起始索引

            # 返回补充分组信息后的端口声明。

        # 全部 group/section 片段都补完 subgroup 后，返回整份更新后的端口列表。
        return list_updated_ports

    # 查找同一分组 section 片段的结束位置。
    def _find_group_section_segment_end(self, ports: list[PortDecl], start: int) -> int:

        """
        查找同一分组 section 片段的结束位置。
        
        参数:
            self: 当前 LayoutMixin 实例。
            ports: 待处理的端口声明列表。
            start: 当前扫描的起始索引。
        返回:
            int: 同一 group/section 片段结束后一位的索引。
        """

        # 当前段必须同时保持 group 和 section 一致。
        str_group = ports[start].group  # 当前段的端口组名

        # section 也是 subgroup 推断边界的一部分。
        str_section = ports[start].section  # 当前段的小节名

        # 从当前端口后一项开始寻找边界。
        int_end = start + 1  # group/section 段候选右边界

        # 连续端口仍属于同一 group/section 时继续吸收。
        while int_end < len(ports) and self._same_group_section(
            ports[int_end],
            str_group,
            str_section,
        ):

            # 右边界向后移动一项。
            int_end += 1  # 同组同小节连续段右边界

            # 返回供切片使用的半开右边界。

        # 扫描结束后拿到的这个右边界，正好就是当前 group/section 连续片段的切片终点。
        return int_end

    # 判断端口是否仍属于同一分组 section。
    def _same_group_section(self, port: PortDecl, str_group: str, str_section: str) -> bool:

        """
        判断端口是否仍属于同一分组 section。
        
        参数:
            self: 当前 LayoutMixin 实例。
            port: 当前端口声明。
            str_group: 与 `str_group` 对应的当前输入参数。
            str_section: 与 `str_section` 对应的当前输入参数。
        返回:
            bool: 当前 helper 的返回结果。
        """

        # group 和 section 都一致时才允许同段 subgroup 聚类。
        return port.group == str_group and port.section == str_section

    # 给单个端口片段补齐子分组标签。
    def _apply_subgroups_to_segment(self, ports: list[PortDecl]) -> list[PortDecl]:

        """
        给单个端口片段补齐子分组标签。
        
        参数:
            self: 当前 LayoutMixin 实例。
            ports: 待处理的端口声明列表。
        返回:
            list[PortDecl]: 当前片段写回子分组后的端口列表。
        """

        # 当前段复制后再局部替换 PortDecl，避免改写切片外对象。
        list_segment = list(ports)  # 当前 group/section 段端口

        # cursor 指向本段中尚未检查的第一个端口。
        int_cursor = 0  # subgroup 聚类扫描位置

        # 在当前 group/section 内寻找连续同前缀端口簇。
        while int_cursor < len(list_segment):

            # 已显式标注 subgroup 或禁止通用 subgroup 的端口不能被自动覆盖。
            if list_segment[int_cursor].subgroup or not list_segment[int_cursor].allow_generic_subgroup:

                # 跳过不可自动补充分组的端口。
                int_cursor += 1  # 下一个 subgroup 候选位置

                # 当前端口不参与自动 subgroup，继续检查后续端口。
                continue

                # 根据端口名派生 subgroup 键，例如 DATA接口。

            # 当前 cluster 的 subgroup 名先从起点端口推导出来，再决定这段端口是否能并为一组。
            str_subgroup = self._derive_port_subgroup_key(list_segment[int_cursor])  # 当前 cluster 推导出的 subgroup 名称

            # 不能派生 subgroup 的端口保持原状。
            if not str_subgroup:

                # 跳过无法形成二级分组的端口。
                int_cursor += 1  # 下一个可检查端口位置

                # 当前端口没有稳定 subgroup 键，继续扫描。
                continue

                # 找到与当前 subgroup 键连续一致的端口簇边界。

            # 聚类结束索引。
            int_cluster_end = self._find_subgroup_cluster_end(  # 当前 subgroup 簇的结束序号
                list_segment,  # 当前 group/section 片段里的端口列表
                int_cursor,  # 当前候选 subgroup 簇的起点
                str_subgroup,  # 当前连续簇共享的 subgroup 名称
            )  # 当前 subgroup 簇右边界

            # 只有达到最小成员数时才自动生成 subgroup。
            if int_cluster_end - int_cursor >= self.PORT_SUBGROUP_MIN_MEMBERS:

                # 给当前连续簇内的端口写入同一个 subgroup。
                self._mark_port_subgroup_range(
                    list_segment,
                    int_cursor,
                    int_cluster_end,
                    str_subgroup,
                )

                # 当前簇已经检查完成，跳到簇后继续。

            # 处理完一个 subgroup cluster 后，游标直接跳到它的末尾继续扫描。
            int_cursor = int_cluster_end  # 当前 cluster 处理完成后的游标位置

            # 返回当前段内补充后的端口列表。

        # 当前片段里所有满足条件的 subgroup 都已回写完毕，这里交回更新后的端口切片副本。
        return list_segment

    # 查找连续子分组簇的结束位置。
    def _find_subgroup_cluster_end(
        self,
        ports: list[PortDecl],
        int_cursor: int,
        str_subgroup: str,
    ) -> int:

        """
        查找连续子分组簇的结束位置。
        
        参数:
            self: 当前 LayoutMixin 实例。
            ports: 待处理的端口声明列表。
            int_cursor: 与 `int_cursor` 对应的当前输入参数。
            str_subgroup: 与 `str_subgroup` 对应的当前输入参数。
        返回:
            int: 连续子分组簇结束后一位的索引。
        """

        # 从当前候选端口后一项开始扩展连续簇。
        int_cluster_end = int_cursor + 1  # subgroup 连续簇候选右边界

        # 只吸收仍可自动分组且派生键相同的端口。
        while int_cluster_end < len(ports) and self._port_matches_subgroup_cluster(
            ports[int_cluster_end],
            str_subgroup,
        ):

            # 当前端口属于同一 subgroup 簇，右边界后移。
            int_cluster_end += 1  # subgroup 连续簇右边界

            # 返回当前 subgroup 连续簇的半开右边界。

        # 这里把聚类结束索引作为当前 helper 的最终结果一并交回调用方。
        return int_cluster_end

    # 判断端口是否属于当前子分组簇。
    def _port_matches_subgroup_cluster(self, port: PortDecl, str_subgroup: str) -> bool:

        """
        判断端口是否属于当前子分组簇。
        
        参数:
            self: 当前 LayoutMixin 实例。
            port: 当前端口声明。
            str_subgroup: 与 `str_subgroup` 对应的当前输入参数。
        返回:
            bool: 当前 helper 的返回结果。
        """

        # 已有 subgroup 或禁止自动 subgroup 的端口会切断当前簇。
        if port.subgroup or not port.allow_generic_subgroup:

            # 返回 False 表示当前端口不能并入自动 subgroup 簇。
            return False

            # 派生键一致时，当前端口可并入连续 subgroup 簇。

        # 这里把derive端口子分组key、端口、子分组文本作为当前 helper 的最终结果一并交回调用方。
        return self._derive_port_subgroup_key(port) == str_subgroup

    # 批量写回连续端口范围的子分组标签。
    def _mark_port_subgroup_range(
        self,
        ports: list[PortDecl],
        start: int,
        int_end: int,
        str_subgroup: str,
    ) -> None:

        """
        批量写回连续端口范围的子分组标签。
        
        参数:
            self: 当前 LayoutMixin 实例。
            ports: 待处理的端口声明列表。
            start: 当前扫描的起始索引。
            int_end: 与 `int_end` 对应的当前输入参数。
            str_subgroup: 与 `str_subgroup` 对应的当前输入参数。
        返回:
            None: 当前 helper 的返回结果。
        """

        # 这一段循环把连续 cluster 范围内的每个端口都替换成带 subgroup 的新副本。
        for int_index in range(start, int_end):

            # 只替换当前簇范围内的端口对象。
            ports[int_index] = self._with_port_subgroup(  # 写回 subgroup 后的新端口声明
                ports[int_index],  # 当前簇内待写回 subgroup 的端口
                str_subgroup,  # 要写回当前簇端口的 subgroup 名称
            )

    # 解析端口显式声明的分组 section。
    def _resolve_explicit_group_section(self, port: PortDecl, info: PortLayoutInfo, fill_missing_sections: bool) -> str:
        """
        解析端口显式声明的分组 section。
        
        参数:
            self: 当前 LayoutMixin 实例。
            port: 当前端口声明。
            info: 当前端口对应的布局信息。
            fill_missing_sections: 是否为缺失 section 的端口补默认 section。
        返回:
            str: 当前 helper 的返回结果。
        """

        # 端口已经显式声明 section 时，优先保留用户写下来的 section。
        if port.section:

            # 显式 section 的优先级最高，这里直接返回它作为生效 section。
            return port.section

        # 只有允许补 section 且当前端口属于已知协议时，才回退到推断得到的 section。
        if fill_missing_sections and info.kind in self.KNOWN_PROTOCOL_KINDS:

            # 协议端口缺少显式 section 时，沿用布局推断阶段给出的 section 值。
            return info.section

        # 既没有显式 section，也不允许自动补齐时，返回空串让上游保持“无 section”状态。
        return ""

    # 构造 section 名称到排序序号的映射。
    def _build_section_order_map(
        self,
        annotated: list[tuple[int, PortDecl, PortLayoutInfo]],
        effective_sections: dict[int, str],
        *,
        preserve_existing: bool,
    ) -> dict[str, int]:
        """
        构造 section 名称到排序序号的映射。
        
        参数:
            self: 当前 LayoutMixin 实例。
            annotated: 与 `annotated` 对应的当前输入参数。
            effective_sections: 与 `effective_sections` 对应的当前输入参数。
            preserve_existing: 与 `preserve_existing` 对应的当前输入参数。
        返回:
            dict[str, int]: section 到排序号的映射表。
        """

        # 保留原始 section 顺序时，优先按输入里第一次出现的位置生成顺序表。
        if preserve_existing:

            # 这张表记录每个显式 section 首次出现的顺序号。
            dict_order: dict[str, int] = {}  # 按输入出现顺序登记的 section 排序表

            # 逐项遍历 `index, port, _ in annotated`，把当前循环体的布局处理做完整。
            for index, port, _ in annotated:

                # 只在第一次遇到某个显式 section 时登记顺序，避免重复覆盖。
                if port.section and port.section not in dict_order:

                    # 当前 section 首次出现时，把它登记到保序映射表里。
                    dict_order[port.section] = len(dict_order)  # 首次出现的 section 对应顺序号

            # 保序模式下直接返回这张 section 顺序映射表给调用方使用。
            return dict_order

        # 这张表记录每个 section 目前看到的最佳排序候选。
        dict_section_meta: dict[str, tuple[int, int]] = {}  # 每个 section 当前最佳排序候选的映射表

        # 遍历整段候选端口，为每个 section 找到“排序最靠前”的代表样本。
        for index, port, info in annotated:

            # 这里取出当前端口实际采用的 section，后面会据此登记 section 顺序候选。
            section = effective_sections[index]  # 当前端口实际采用的 section 名称

            # 没有生效 section 的端口不会参与 section 顺序推导，直接跳过即可。
            if not section:

                # section 顺序映射构建流程在当前元素处理完成后，直接跳到下一轮循环继续看后面的端口。
                continue

            # 这个候选元组同时承载 section 排序优先级和首次出现位置。
            tuple_candidate = (info.section_rank, index if port.section else index + len(annotated))  # 当前 section 的排序候选元组

            # 如果这个 section 之前已经登记过候选，就拿旧候选出来和当前值比较。
            tuple_previous_candidate = dict_section_meta.get(section)  # 这个 section 之前登记过的最佳候选

            # 只有当前候选比已登记候选更靠前时，才更新这个 section 的排序依据。
            if tuple_previous_candidate is None or tuple_candidate < tuple_previous_candidate:

                # 用当前更优的候选覆盖该 section 之前登记的排序依据。
                dict_section_meta[section] = tuple_candidate  # 当前 section 的最佳排序候选元组

        # section 最佳候选选定以后，这里把它们按优先级和位置整理成稳定顺序。
        list_ordered_sections = sorted(dict_section_meta.items(), key=lambda item: (item[1][0], item[1][1], item[0]))  # 按优先级整理后的 section 候选列表

        # section 排序列表准备好后，这里再反向生成真正给调用方使用的 section 顺序映射表。
        return {section: dict_order for dict_order, (section, _) in enumerate(list_ordered_sections)}

    # 对未知前缀端口应用聚类分段。
    def _apply_unknown_prefix_clusters(
        self,
        annotated: list[tuple[int, PortDecl, PortLayoutInfo]],
    ) -> list[tuple[int, PortDecl, PortLayoutInfo]]:
        """
        对未知前缀端口应用聚类分段。
        
        参数:
            self: 当前 LayoutMixin 实例。
            annotated: 与 `annotated` 对应的当前输入参数。
        返回:
            list[tuple[int, PortDecl, PortLayoutInfo]]: 已按未知前缀聚类处理的端口列表。
        """

        # 这里复制一份候选列表，后续未知前缀聚类提升只修改副本。
        list_updated = list(annotated)  # 待聚类重写的端口候选副本列表

        # `int_start` 始终指向下一个尚未判断是否可成簇的未知前缀端口。
        int_start = 0  # 未知前缀聚类扫描的起始索引

        # 这里从左到右扫描未知前缀端口，尝试把足够长的连续簇提升成独立接口分组。
        while int_start < len(list_updated):

            # 每轮扫描都先取出当前起点的索引、端口和布局信息，后面据此判断是否能形成聚类。
            int_index, target_port, layout_state = list_updated[int_start]  # 当前聚类扫描起点处的索引、端口和布局信息

            # 先为当前端口提取未知前缀聚类键，只有键稳定存在才值得继续向右扩展。
            cluster_key = self._extract_unknown_cluster_key(target_port, layout_state)  # 聚类键结果

            # 当前端口提不出聚类键时，它不适合参与未知前缀成簇提升，直接跳过即可。
            if not cluster_key:

                # 当前端口不形成聚类时，扫描起点直接后移一格去看下一项。
                int_start += 1  # 跳到下一项端口后的扫描位置

                # 当前端口既然不能作为聚类起点，就把扫描控制权直接交给下一项。
                continue

            # 右边界从起点后一位开始试探，后面会持续向右扩展聚类范围。
            int_end = int_start + 1  # 结束结果

            # 这里继续向右检查连续端口，看看同一个未知前缀聚类还能延伸多远。
            while int_end < len(list_updated):

                # 继续扩展右边界前，先取出候选位置上的端口和布局信息做聚类键比较。
                _, next_target_port, next_layout_state = list_updated[int_end]  # 右边界候选位置上的端口和布局信息

                # 一旦后一个端口的聚类键变了，就说明当前未知前缀簇已经在这里结束。
                if self._extract_unknown_cluster_key(next_target_port, next_layout_state) != cluster_key:

                    # 右侧端口改换前缀族时，当前这一簇的连续边界就已经确定。
                    break

                # 只要后一个端口仍属于同一聚类键，就把右边界继续向后推一格。
                int_end += 1  # 继续向右扩展聚类后的边界位置

            # 只有连续出现至少三个端口时，才把未知前缀提升成一个独立接口分组。
            if int_end - int_start >= 3:

                # 连续长度满足阈值后，把当前前缀整体提升成独立接口标题。
                str_group_name = f"{cluster_key}接口"  # 未知前缀簇提升后的 group 标题

                # 下面逐个回写这个未知前缀簇里的端口，把它们统一提升到新的 group 名下面。
                for cluster_index in range(int_start, int_end):

                    # 每次回写前都先拿到原始索引、端口和旧布局信息，避免丢失原有排序依据。
                    original_index, original_target_port, original_layout_state = list_updated[cluster_index]  # 聚类内原始端口的索引、声明和布局信息

                    # 这里把聚类后的 group 名写进当前端口的新布局对象，并整体替换回候选列表。
                    list_updated[cluster_index] = (
                        original_index,  # 聚类重写后的原始索引
                        original_target_port,  # 聚类重写后沿用的原始端口声明
                        PortLayoutInfo(  # 聚类提升后替换回列表的新布局对象
                            group=str_group_name,  # 聚类提升后写回的新 group 名称
                            section="",  # 聚类标题本身不再保留细分 section
                            section_rank=0,  # 聚类标题在新 group 内不参与 section 细排
                            direction_rank=original_layout_state.direction_rank,  # 方向顺序沿用聚类前的推断结果
                            member_rank=original_index,  # 聚类成员继续按原始出现顺序稳定输出
                            kind="cluster",  # 标记为未知前缀聚类提升出的 cluster 成员
                        ),
                    )

            # 本轮检查过的连续簇已经完整消费，扫描游标直接跳到它的右边界。
            int_start = int_end  # 下一轮未知前缀聚类扫描的起点

        # 未知前缀聚类全部处理完后，返回这份已经重写 group 的候选集。
        return list_updated

    # 提取未知前缀端口的聚类键。
    def _extract_unknown_cluster_key(self, port: PortDecl, info: PortLayoutInfo) -> str:
        """
        提取未知前缀端口的聚类键。
        
        参数:
            self: 当前 LayoutMixin 实例。
            port: 当前端口声明。
            info: 当前端口对应的布局信息。
        返回:
            str: 当前 helper 的返回结果。
        """

        # 只有 unknown 端口才需要继续尝试未知前缀聚类，已识别协议端口直接跳过。
        if info.kind != "unknown":

            # 非 unknown 端口不参与未知前缀聚类，这里返回空串表示没有 cluster key。
            return ""

        # 去掉方向前缀后的基础名，才是未知前缀聚类真正比较的命名主体。
        base_name = self._strip_known_prefixes(port.name).lower()  # 去掉方向前缀后用于聚类比较的基础端口名

        # 未知前缀聚类只关心下划线分词后的前缀是否稳定复现，所以先拆成词元序列。
        list_name_tokens = base_name.split("_")  # 按下划线切开的基础命名词元

        # 词元太少时连“前缀 + 成员”都拆不出来，不值得继续做未知前缀聚类。
        if len(list_name_tokens) < 2:

            # 命名过短时无法形成稳定 cluster key，这里返回空串表示不聚类。
            return ""

        # 常见单词元后缀命名可以直接去掉成员尾词，剩余前缀就是 cluster key。
        if list_name_tokens[-1] in self.UNKNOWN_CLUSTER_SUFFIXES:

            # 去掉尾部成员词元后，剩余前缀就是未知接口聚类使用的 cluster key。
            return "_".join(list_name_tokens[:-1]).upper()

        # compound 后缀能帮助识别像 reset_n 这样的双词元复位名称。
        compound_suffix = "_".join(list_name_tokens[-2:]) if len(list_name_tokens) >= 3 else ""  # 名称末尾两个词元组成的复合后缀

        # 双词元收尾模式命中时，同样去掉末尾两个成员词元作为 cluster key。
        if compound_suffix in {
            "tx_data",
            "tx_valid",
            "tx_ready",
            "rx_data",
            "rx_valid",
            "rx_ready",
        }:

            # 命中双词元尾缀时，去掉这两个词元后剩余前缀就是 cluster key。
            return "_".join(list_name_tokens[:-2]).upper()

        # 既不命中单词元后缀也不命中双词元后缀时，这个 unknown 端口不参与聚类提升。
        return ""

    # 推断单个端口的布局元数据。
    def _infer_port_layout(self, port: PortDecl) -> PortLayoutInfo:
        """
        推断单个端口的布局元数据。
        
        参数:
            self: 当前 LayoutMixin 实例。
            port: 当前端口声明。
        返回:
            PortLayoutInfo: 当前端口推断出的布局元数据。
        """

        # 端口方向优先级会直接参与后续布局排序，所以先在入口就算出来。
        int_direction_rank = self.PORT_DIRECTION_ORDER.get(port.direction, len(self.PORT_DIRECTION_ORDER))  # 方向优先级结果

        # 去掉方向前缀后的基础名称，才是协议识别真正比较的命名主体。
        base_name = self._strip_known_prefixes(port.name)  # 去掉方向前缀后参与协议识别的基础名称

        # 协议匹配和时钟复位识别都基于小写命名，先统一转换减少分支差异。
        lowered = base_name.lower()  # 用于协议识别的小写端口名

        # 先尝试所有已知协议的专门匹配器，命中后就不再落回 unknown 分支。
        layout_match = self._match_known_port_layout(lowered, int_direction_rank)  # known布局结果

        # 只要命中任意已知协议布局，就直接返回，不再落回 unknown 路径。
        if layout_match:

            # 已知协议匹配器已经给出完整布局信息，这里直接沿用它。
            return layout_match

        # 未命中协议规则后，再退一步检查它是否更像独立的时钟或复位端口。
        if self._looks_like_clock_reset_name(lowered):

            # 当前布局处理流程已经把 group、section 和 member 优先级都算清楚了，这里直接封装成 PortLayoutInfo 返回。
            return PortLayoutInfo(
                group="全局信号",
                section="",
                section_rank=0,
                direction_rank=int_direction_rank,
                member_rank=self._clock_reset_member_rank(lowered),
                kind="global",
            )

        # 既不属于已知协议也不像时钟复位时，保留空 group 让后面的未知前缀聚类继续接管。
        return PortLayoutInfo(
            group="",
            section="",
            section_rank=0,
            direction_rank=int_direction_rank,
            member_rank=0,
            kind="unknown",
        )

    # 尝试用已知协议规则匹配端口布局。
    def _match_known_port_layout(self, lowered_name: str, direction_rank: int) -> PortLayoutInfo | None:
        """
        尝试用已知协议规则匹配端口布局。
        
        参数:
            self: 当前 LayoutMixin 实例。
            lowered_name: 小写后的端口名。
            direction_rank: 端口方向优先级。
        返回:
            PortLayoutInfo | None: 命中规则时返回对应布局，否则返回空值。
        """

        # 已知协议按更具体到更宽泛的顺序依次尝试，命中后立即停下。
        for matcher in (
            self._match_axis_port_layout,
            self._match_axi_port_layout,
            self._match_apb_port_layout,
            self._match_wishbone_port_layout,

            # 串行外设和以太网接口放在共享总线规则之后继续尝试。
            self._match_uart_port_layout,
            self._match_spi_port_layout,
            self._match_i2c_port_layout,
            self._match_rgmii_port_layout,
            self._match_gmii_port_layout,
        ):

            # 每条匹配规则命中后都会先生成一个候选布局对象，再统一决定是否返回。
            layout_match = matcher(lowered_name, direction_rank)  # 当前匹配规则生成的布局候选

            # 只有在当前匹配器确实命中布局时，才直接结束整个匹配链。
            if layout_match:

                # 一旦某条具体协议规则命中，就立即返回这份布局信息。
                return layout_match

        # 当前布局处理流程在这里确认当前端口不符合这条规则，所以返回空值把处理机会留给后续匹配链。
        return None

    # 匹配 AXIS 端口的布局元数据。
    def _match_axis_port_layout(self, lowered_name: str, direction_rank: int) -> PortLayoutInfo | None:
        """
        匹配 AXIS 端口的布局元数据。
        
        参数:
            self: 当前 LayoutMixin 实例。
            lowered_name: 小写后的端口名。
            direction_rank: 端口方向优先级。
        返回:
            PortLayoutInfo | None: 命中 AXIS 规则时返回对应布局，否则返回空值。
        """

        # 先拆出 AXIS 槽位前缀和成员载荷，后面的 group 与 section 判断都依赖这个结果。
        list_prefix_tokens, list_payload_tokens = self._split_protocol_tokens(lowered_name, ("axis",))  # 拆出的 AXIS 槽位前缀词元和成员载荷词元

        # 没有槽位前缀或成员载荷时，当前端口就不像 AXIS 协议成员。
        if list_payload_tokens is None or not list_prefix_tokens:

            # AXIS 布局识别流程在这里确认当前端口不符合这条规则，所以返回空值把处理机会留给后续匹配链。
            return None

        # 协议槽位标签会直接影响 group 标题和 section 的组织方式。
        str_slot = "_".join(list_prefix_tokens).upper()  # 协议槽位标签

        # 去掉槽位前缀后剩下的成员载荷，才是后面继续分类的真正输入。
        payload = "_".join(list_payload_tokens)  # 协议成员载荷

        # AXIS 成员如果本质上是时钟或复位，就直接归入 clock_reset 小节。
        if self._looks_like_clock_reset_name(payload):

            # AXIS 布局识别流程已经把 group、section 和 member 优先级都算清楚了，这里直接封装成 PortLayoutInfo 返回。
            return PortLayoutInfo(
                group=self._format_group_label("AXI-Stream总线", str_slot),
                section=self.AXIS_SECTION_LABELS["clock_reset"],
                section_rank=self.AXIS_SECTION_ORDER["clock_reset"],
                direction_rank=direction_rank,
                member_rank=self._clock_reset_member_rank(payload),
                kind="axis",
            )

        # 成员名会继续参与数据段、控制段和其他段的细分排序。
        str_member = self._normalize_axis_member(payload)  # 协议成员名

        # 命中 AXIS 数据通道成员表时，优先把它归到 data 小节。
        if str_member in self.AXIS_DATA_ORDER:

            # 这里固定当前成员所属的 section 键，后面的 section 标签和排序都依赖它。
            str_section_key = "data"  # AXI 地址或数据成员归属的 data section 键

            # data section 内的成员顺序来自 AXIS_DATA_ORDER，后面会直接拿它排序。
            int_member_rank = self.AXIS_DATA_ORDER[str_member]  # 成员优先级序号

        # 当前面的条件未命中且 `member in self.AXIS_CONTROL_ORDER` 成立时走这里。
        elif str_member in self.AXIS_CONTROL_ORDER:

            # AXIS 握手线和响应线统一落到 control 小节，避免和 data 通道成员混排。
            str_section_key = "control"  # AXIS 控制握手成员归属的小节键

            # control section 内的成员顺序来自 AXIS_CONTROL_ORDER。
            int_member_rank = self.AXIS_CONTROL_ORDER[str_member]  # control 小节内的成员顺序号

        # 上面的特判都没有命中后，最后在这里走默认收口路径。
        else:

            # 其余 AXIS 成员统一落到 other section，后面作为兜底小节处理。
            str_section_key = "other"  # 未落入 data/control 时使用的兜底小节键

            # 没有命中 data/control 顺序表的成员统一给兜底优先级。
            int_member_rank = 99  # 兜底小节里未建表成员的默认顺序号

        # AXIS section 和成员优先级确定后，在这里一次性封装成统一布局对象。
        return PortLayoutInfo(
            group=self._format_group_label("AXI-Stream总线", str_slot),
            section=self.AXIS_SECTION_LABELS[str_section_key],
            section_rank=self.AXIS_SECTION_ORDER[str_section_key],
            direction_rank=direction_rank,
            member_rank=int_member_rank,
            kind="axis",
        )

    # 按 AXI / AXI-Lite 命名拆槽位、通道成员和时钟复位小节。
    def _match_axi_port_layout(self, lowered_name: str, direction_rank: int) -> PortLayoutInfo | None:
        """
        匹配 AXI 端口的布局元数据。
        
        参数:
            self: 当前 LayoutMixin 实例。
            lowered_name: 小写后的端口名。
            direction_rank: 端口方向优先级。
        返回:
            PortLayoutInfo | None: 命中 AXI 规则时返回对应布局，否则返回空值。
        """

        # 先拆出 AXI 槽位前缀和成员载荷，后面要据此判断 Vitis wrapper 特例和 section 归属。
        list_prefix_tokens, list_payload_tokens = self._split_protocol_tokens(  # AXI 槽位前缀与成员载荷的拆分结果
            lowered_name,  # 当前待识别的 AXI 端口名
            ("axi",),  # 标准 AXI 别名词元
            ("axil",),  # AXI-Lite 连写别名词元
            ("axi", "lite"),  # AXI-Lite 分词别名词元
        )  # 拆出的 AXI 通道前缀词元和成员载荷词元

        # 没拆出成员载荷时，当前端口就不像 AXI 协议成员，直接跳过。
        if list_payload_tokens is None:

            # 当前命名拆不出 AXI 成员载荷时，把处理机会留给后面的协议匹配器。
            return None

        # Vitis wrapper 会把 control 通道编码进载荷前缀，这里单独保留那段槽位信息。
        if (
            self._has_vitis_wrapper_port_preserve(lowered_name)
            and list_prefix_tokens in (["s"], ["m"])
            and len(list_payload_tokens) > 1
            and (list_payload_tokens[0] == "control" or list_prefix_tokens == ["m"])
        ):

            # Vitis wrapper 的 control 前缀要并入槽位名，否则 group 会丢掉这层语义。
            str_slot = f"{list_prefix_tokens[0].upper()}_AXI_{list_payload_tokens[0].upper()}"  # 带 control 通道标识的 wrapper 槽位标题

            # 槽位后面的载荷词元会继续决定成员名和 section 归属。
            list_payload_tokens = list_payload_tokens[1:]  # 协议载荷词元

        # 非 Vitis wrapper 特例时，走普通 AXI 槽位命名路径。
        else:

            # 默认路径只保留普通 AXI 槽位前缀，不额外吸收 control 词元。
            str_slot_prefix = "_".join(list_prefix_tokens).upper()  # 普通 AXI 命名里的槽位前缀文本

            # 普通 AXI 命名只需要 `S0_AXI` / `M1_AXI` 这一层槽位标签。
            str_slot = f"{str_slot_prefix}_AXI" if str_slot_prefix else ""  # 由主从前缀拼回的 AXI 槽位标题

        # 这里开始只保留 AXI 成员主体，后面会继续拆成通道名和最终字段。
        payload = "_".join(list_payload_tokens)  # 送去拆通道与成员名的 AXI 载荷片段

        # AXI 成员如果本质上是时钟或复位，就直接归到 clock_reset 小节。
        if self._looks_like_clock_reset_name(payload):

            # AXI 时钟复位成员在这里直接落到 clock_reset 小节，不再走通道拆分。
            return PortLayoutInfo(
                group=self._format_group_label("AXI总线", str_slot),
                section=self.AXI_SECTION_LABELS["clock_reset"],
                section_rank=self.AXI_SECTION_ORDER["clock_reset"],
                direction_rank=direction_rank,
                member_rank=self._clock_reset_member_rank(payload),
                kind="axi",
            )

        # 先拆出 AXI 通道和成员名，后面的 section 与 member 排序都依赖这两个结果。
        str_channel, str_member = self._split_axi_channel(payload)  # AXI 载荷里的通道名和成员名

        # 这里根据通道名确定成员所在的 section，没有通道名时统一落到 other。
        section_key = str_channel or "other"  # 当前 AXI 成员归属的 section 键

        # 当前成员在各自 section 内的排序优先级会在这里先计算出来。
        int_member_rank = self.AXI_MEMBER_ORDER.get(section_key, {}).get(str_member, 99)  # 成员优先级结果

        # AXI 通道与成员排序都确定后，在这里统一封装最终布局对象。
        return PortLayoutInfo(
            group=self._format_group_label("AXI总线", str_slot),
            section=self.AXI_SECTION_LABELS[section_key],
            section_rank=self.AXI_SECTION_ORDER[section_key],
            direction_rank=direction_rank,
            member_rank=int_member_rank,
            kind="axi",
        )

    # 按 APB 旧命名和标准命名把端口归类到请求、响应及时钟复位布局。
    def _match_apb_port_layout(self, lowered_name: str, direction_rank: int) -> PortLayoutInfo | None:

        """
        匹配 APB 端口的布局元数据。
        
        参数:
            self: 当前 LayoutMixin 实例。
            lowered_name: 小写后的端口名。
            direction_rank: 端口方向优先级。
        返回:
            PortLayoutInfo | None: 命中 APB 规则时返回对应布局，否则返回空值。
        """

        # APB 兼容 apb_* 和 apb0_* 两类历史端口名。
        return self._match_simple_bus_port_layout(
            lowered_name,
            direction_rank,
            {
                "protocol_variants": (("apb",),),
                "fallback_pattern": r"^apb(\d*)_(.+)$",
                "group_prefix": "APB总线",
                "section_labels": self.APB_SECTION_LABELS,
                "section_order": self.APB_SECTION_ORDER,
                "member_order": self.APB_MEMBER_ORDER,
                "section_candidates": ("request", "response"),
                "kind": "apb",
                "extra_clock_payloads": {"pclk", "presetn"},
                "clock_rank_overrides": {"pclk": 0, "presetn": 1},
            },
        )

    # 识别 Wishbone 与 wb 缩写端口，并补出对应的总线布局标签。
    def _match_wishbone_port_layout(self, lowered_name: str, direction_rank: int) -> PortLayoutInfo | None:

        """
        匹配 Wishbone 端口的布局元数据。
        
        参数:
            self: 当前 LayoutMixin 实例。
            lowered_name: 小写后的端口名。
            direction_rank: 端口方向优先级。
        返回:
            PortLayoutInfo | None: 命中 Wishbone 规则时返回对应布局，否则返回空值。
        """

        # Wishbone 同时接受完整 wishbone_* 和缩写 wb_* 命名。
        return self._match_simple_bus_port_layout(
            lowered_name,
            direction_rank,
            {
                "protocol_variants": (("wishbone",), ("wb",)),
                "fallback_pattern": r"^wb(\d*)_(.+)$",
                "group_prefix": "Wishbone总线",
                "section_labels": self.WISHBONE_SECTION_LABELS,
                "section_order": self.WISHBONE_SECTION_ORDER,
                "member_order": self.WISHBONE_MEMBER_ORDER,
                "section_candidates": ("request", "response"),
                "kind": "wishbone",
            },
        )

    # 按 UART 历史端口命名拆槽位，并区分发送、接收和状态小节。
    def _match_uart_port_layout(self, lowered_name: str, direction_rank: int) -> PortLayoutInfo | None:

        """
        匹配 UART 端口的布局元数据。
        
        参数:
            self: 当前 LayoutMixin 实例。
            lowered_name: 小写后的端口名。
            direction_rank: 端口方向优先级。
        返回:
            PortLayoutInfo | None: 命中 UART 规则时返回对应布局，否则返回空值。
        """

        # UART 只有历史命名回退形式，没有 token 式协议前缀。
        return self._match_simple_bus_port_layout(
            lowered_name,
            direction_rank,
            {
                "protocol_variants": (),
                "fallback_pattern": r"^uart(\d*)_(.+)$",
                "group_prefix": "UART接口",
                "section_labels": self.UART_SECTION_LABELS,
                "section_order": self.UART_SECTION_ORDER,
                "member_order": self.UART_MEMBER_ORDER,
                "section_candidates": ("tx", "rx", "status"),
                "kind": "uart",
            },
        )

    # 识别 SPI 命名端口，并优先拦截时钟与片选这类关键成员。
    def _match_spi_port_layout(self, lowered_name: str, direction_rank: int) -> PortLayoutInfo | None:

        """
        匹配 SPI 端口的布局元数据。
        
        参数:
            self: 当前 LayoutMixin 实例。
            lowered_name: 小写后的端口名。
            direction_rank: 端口方向优先级。
        返回:
            PortLayoutInfo | None: 命中 SPI 规则时返回对应布局，否则返回空值。
        """

        # SPI 要先识别 `clock_cs` 小节，避免片选和串行时钟被通用 clk/rst 规则抢走。
        return self._match_simple_bus_port_layout(
            lowered_name,
            direction_rank,
            {
                "protocol_variants": (),
                "fallback_pattern": r"^spi(\d*)_(.+)$",
                "group_prefix": "SPI接口",
                "section_labels": self.SPI_SECTION_LABELS,
                "section_order": self.SPI_SECTION_ORDER,
                "member_order": self.SPI_MEMBER_ORDER,
                "section_candidates": ("clock_cs", "tx", "rx", "extended"),
                "kind": "spi",
                "priority_member_sections": ("clock_cs",),
            },
        )

    # 按 I2C 命名把端口分配到总线信号和控制信号两组布局。
    def _match_i2c_port_layout(self, lowered_name: str, direction_rank: int) -> PortLayoutInfo | None:

        """
        匹配 I2C 端口的布局元数据。
        
        参数:
            self: 当前 LayoutMixin 实例。
            lowered_name: 小写后的端口名。
            direction_rank: 端口方向优先级。
        返回:
            PortLayoutInfo | None: 命中 I2C 规则时返回对应布局，否则返回空值。
        """

        # I2C 端口只分 bus/control 两类，直接复用简单总线布局 matcher。
        return self._match_simple_bus_port_layout(
            lowered_name,
            direction_rank,
            {
                "protocol_variants": (),
                "fallback_pattern": r"^i2c(\d*)_(.+)$",
                "group_prefix": "I2C接口",
                "section_labels": self.I2C_SECTION_LABELS,
                "section_order": self.I2C_SECTION_ORDER,
                "member_order": self.I2C_MEMBER_ORDER,
                "section_candidates": ("bus", "control"),
                "kind": "i2c",
            },
        )

    # 识别 GMII token 命名端口，并按监视、发送和接收小节排序。
    def _match_gmii_port_layout(self, lowered_name: str, direction_rank: int) -> PortLayoutInfo | None:

        """
        匹配 GMII 端口的布局元数据。
        
        参数:
            self: 当前 LayoutMixin 实例。
            lowered_name: 小写后的端口名。
            direction_rank: 端口方向优先级。
        返回:
            PortLayoutInfo | None: 命中 GMII 规则时返回对应布局，否则返回空值。
        """

        # GMII 只接受 token 式命名，槽位前缀可以出现在 `gmii` 之前。
        return self._match_simple_bus_port_layout(
            lowered_name,
            direction_rank,
            {
                "protocol_variants": (("gmii",),),
                "fallback_pattern": "",
                "group_prefix": "GMII总线",
                "section_labels": self.GMII_SECTION_LABELS,
                "section_order": self.GMII_SECTION_ORDER,
                "member_order": self.GMII_MEMBER_ORDER,
                "section_candidates": ("monitor", "write", "read"),
                "kind": "gmii",
            },
        )

    # 识别 RGMII token 命名端口，并按收发方向推断对应的小节布局。
    def _match_rgmii_port_layout(self, lowered_name: str, direction_rank: int) -> PortLayoutInfo | None:

        """
        匹配 RGMII 端口的布局元数据。
        
        参数:
            self: 当前 LayoutMixin 实例。
            lowered_name: 小写后的端口名。
            direction_rank: 端口方向优先级。
        返回:
            PortLayoutInfo | None: 命中 RGMII 规则时返回对应布局，否则返回空值。
        """

        # RGMII 与 GMII 同样使用协议 token 位置推导槽位。
        return self._match_simple_bus_port_layout(
            lowered_name,
            direction_rank,
            {
                "protocol_variants": (("rgmii",),),
                "fallback_pattern": "",
                "group_prefix": "RGMII总线",
                "section_labels": self.RGMII_SECTION_LABELS,
                "section_order": self.RGMII_SECTION_ORDER,
                "member_order": self.RGMII_MEMBER_ORDER,
                "section_candidates": ("write", "read"),
                "kind": "rgmii",
            },
        )

    # 匹配简单总线前缀端口的布局元数据。
    def _match_simple_bus_port_layout(
        self,
        lowered_name: str,
        direction_rank: int,
        protocol_config: dict[str, object],
    ) -> PortLayoutInfo | None:

        """
        匹配简单总线前缀端口的布局元数据。
        
        参数:
            self: 当前 LayoutMixin 实例。
            lowered_name: 小写后的端口名。
            direction_rank: 端口方向优先级。
            protocol_config: 当前协议布局识别所需的配置字典。
        返回:
            PortLayoutInfo | None: 命中简单总线规则时返回对应布局，否则返回空值。
        """

        # 先尝试把端口名拆成槽位和成员载荷，只有拆成功才说明它像当前协议的成员。
        tuple_payload = self._parse_protocol_slot_payload(  # 协议槽位和成员载荷的拆分结果
            lowered_name,  # 待识别的端口名、协议别名和回退模式
            protocol_config["protocol_variants"],  # 当前协议允许的前缀词元变体
            protocol_config["fallback_pattern"],  # 协议历史命名使用的回退正则
        )  # 协议槽位和成员载荷

        # 协议槽位都拆不出来时，这个 helper 不应接管当前端口，直接把机会留给后续匹配器。
        if tuple_payload is None:

            # 返回空结果，让协议匹配链继续尝试其它协议。
            return None

        # 槽位和载荷拆分成功后，后面的优先成员规则与时钟复位规则都会复用这两个结果。
        str_slot, str_payload = tuple_payload  # 协议槽位标签和成员载荷名

        # 优先成员规则要先于时钟复位规则执行，避免某些专用成员被 reset/clock 逻辑错误抢占。
        priority_layout_match = self._match_priority_member_layout(  # 优先成员规则命中的布局结果
            str_slot,  # 当前槽位标签
            str_payload,  # 当前成员载荷名
            direction_rank,  # 端口方向优先级
            protocol_config,  # 当前协议布局识别配置
        )  # 优先成员命中时的布局结果

        # 一旦命中协议特定的优先成员布局，这里直接返回，避免再被更宽泛的规则覆盖。
        if priority_layout_match is not None:

            # 返回协议特定成员的布局信息。
            return priority_layout_match

        # 如果优先成员没有命中，这里再尝试把当前载荷识别成协议里的时钟或复位成员。
        clock_layout_match = self._match_protocol_clock_layout(  # 时钟复位规则命中的布局结果
            str_slot,  # 当前协议实例槽位
            str_payload,  # 当前协议成员载荷
            direction_rank,  # 当前端口方向顺序号
            protocol_config,  # 当前协议的小节与排序配置
        )

        # 识别成协议内部时钟复位成员后，就不再继续尝试普通业务 section。
        if clock_layout_match is not None:

            # 返回协议时钟复位成员的布局信息。
            return clock_layout_match

        # 当前成员如果不是优先成员或时钟复位，就按普通 section 候选表继续归类。
        tuple_member_section = self._find_protocol_member_section(  # 普通成员所属小节和组内排序
            str_payload,  # 当前待归类的协议成员载荷
            protocol_config["member_order"],  # 用于优先成员查表的 section->member 顺序映射
            protocol_config["section_candidates"],  # 当前协议允许归属的小节顺序
        )

        # 普通成员 section 也匹配不到时，这个协议 helper 就不接管当前端口。
        if tuple_member_section is None:

            # 返回空结果表示当前协议没有接管该端口。
            return None

        # 普通成员命中 section 后，这里拆出 section 键和该 section 内的成员排序号。
        section_key, int_member_rank = tuple_member_section  # 成员小节键和小节内序号

        # 构造协议普通成员的布局描述。
        return self._make_protocol_layout(
            str_slot,
            direction_rank,
            protocol_config,
            section_key=section_key,
            member_rank=int_member_rank,
        )

    # 解析协议端口名中的槽位与成员载荷。
    def _parse_protocol_slot_payload(
        self,
        lowered_name: str,
        protocol_variants: tuple[tuple[str, ...], ...],
        fallback_pattern: str,
    ) -> tuple[str, str] | None:

        """
        解析协议端口名中的槽位与成员载荷。
        
        参数:
            self: 当前 LayoutMixin 实例。
            lowered_name: 小写后的端口名。
            protocol_variants: 与 `protocol_variants` 对应的当前输入参数。
            fallback_pattern: 与 `fallback_pattern` 对应的当前输入参数。
        返回:
            tuple[str, str] | None: 协议槽位前缀、槽位标签和成员载荷的拆分结果。
        """

        # token 形式协议允许槽位名前置，例如 s0_gmii_txd。
        if protocol_variants:

            # 有些协议需要先从端口词元里识别出协议前缀和剩余载荷，再决定后续回退匹配怎么走。
            list_prefix_tokens, list_payload_tokens = self._split_protocol_tokens(  # 待识别的端口名
                lowered_name,  # 当前待拆分的协议端口名
                *protocol_variants,  # 当前协议允许匹配的全部别名词元序列
            )  # 协议关键字前缀和关键字后的载荷 token

            # token 命名命中时直接返回槽位和载荷。
            if list_payload_tokens is not None:

                # 拼出协议槽位和成员载荷，保持旧大小写处理。
                str_slot = "_".join(list_prefix_tokens).upper()  # token 命名解析出的协议槽位标签

                # 载荷 token 重新合并成旧 matcher 使用的成员名。
                str_payload = "_".join(list_payload_tokens)  # token 命名解析出的协议成员载荷名

                # 返回解析出的槽位和成员名。
                return str_slot, str_payload

                # 没有 fallback 正则时，该协议只接受 token 形式。

        # 没有回退正则时，这个协议只接受 token 形式命名，不再尝试历史命名兼容。
        if not fallback_pattern:

            # 返回空结果，避免对其它命名形式误识别。
            return None

        # 回退正则专门兼容历史命名，这里先试着把槽位和载荷按旧格式拆开。
        obj_match = re.match(fallback_pattern, lowered_name)  # fallback 命名匹配结果

        # 正则未命中时该协议不接管当前端口。
        if not obj_match:

            # 返回空结果给协议匹配链。
            return None

        # 回退正则命中后，先把槽位标签规范成大写文本，后面的 group 标题直接使用它。
        str_slot = obj_match.group(1).upper()  # 旧式 fallback 命名中的槽位标签

        # 提取正则匹配出的成员载荷。
        str_payload = obj_match.group(2)  # 旧式 fallback 命名中的成员载荷名

        # 返回 fallback 解析出的槽位和载荷。
        return str_slot, str_payload

    # 按照优先级成员规则匹配端口布局。
    def _match_priority_member_layout(
        self,
        str_slot: str,
        str_payload: str,
        direction_rank: int,
        protocol_config: dict[str, object],
    ) -> PortLayoutInfo | None:

        """
        按照优先级成员规则匹配端口布局。
        
        参数:
            self: 当前 LayoutMixin 实例。
            str_slot: 与 `str_slot` 对应的当前输入参数。
            str_payload: 与 `str_payload` 对应的当前输入参数。
            direction_rank: 端口方向优先级。
            protocol_config: 当前协议布局识别所需的配置字典。
        返回:
            PortLayoutInfo | None: 命中优先级成员规则时返回布局，否则返回空值。
        """

        # 没有优先成员时直接跳过该分支。
        if not protocol_config.get("priority_member_sections", ()):

            # 返回空结果，调用方继续检查 clock/reset。
            return None

        # 优先成员只在指定小节名单里查一次，命中后就直接走这条专用布局路径。
        tuple_member_section = self._find_protocol_member_section(  # 优先成员所属小节和排序
            str_payload,  # 当前待匹配的优先成员载荷
            protocol_config["member_order"],  # 当前协议的成员顺序表
            protocol_config["priority_member_sections"],  # 当前协议声明的优先成员小节
        )

        # 优先成员未命中时继续交给 clock/reset 判断。
        if tuple_member_section is None:

            # 返回空结果，调用方继续普通识别流程。
            return None

        # 优先成员命中 section 后，这里拆出 section 键和该 section 内的成员排序号。
        section_key, int_member_rank = tuple_member_section  # 优先成员小节键和序号

        # 构造优先成员的协议布局信息。
        return self._make_protocol_layout(
            str_slot,
            direction_rank,
            protocol_config,
            section_key=section_key,
            member_rank=int_member_rank,
        )

    # 匹配协议时钟与复位端口的布局信息。
    def _match_protocol_clock_layout(
        self,
        str_slot: str,
        str_payload: str,
        direction_rank: int,
        protocol_config: dict[str, object],
    ) -> PortLayoutInfo | None:

        """
        匹配协议时钟与复位端口的布局信息。
        
        参数:
            self: 当前 LayoutMixin 实例。
            str_slot: 与 `str_slot` 对应的当前输入参数。
            str_payload: 与 `str_payload` 对应的当前输入参数。
            direction_rank: 端口方向优先级。
            protocol_config: 当前协议布局识别所需的配置字典。
        返回:
            PortLayoutInfo | None: 命中时钟或复位规则时返回布局，否则返回空值。
        """

        # 协议内部的时钟复位成员既可能长得像通用 clk/rst，也可能来自协议自定义白名单。
        bool_clock_payload = (
            self._looks_like_clock_reset_name(str_payload)  # 通用时钟复位命名是否命中
            or str_payload in protocol_config.get("extra_clock_payloads", set())  # 协议成员是否应归入时钟复位小节
        )  # 当前载荷是否属于时钟复位小节

        # 非时钟复位成员不在这里接管。
        if not bool_clock_payload:

            # 返回空结果，调用方继续普通成员分区。
            return None

        # 生成 clock_reset 布局前，先确定该成员在本小节内部应该落到哪个顺序号。
        int_member_rank = protocol_config.get("clock_rank_overrides", {}).get(  # clock_reset 小节采用的最终排序号
            str_payload,  # 协议显式声明覆盖顺序的成员键
            self._clock_reset_member_rank(str_payload),  # 未声明覆盖时按通用 clk/rst 命名推断
        )

        # 构造 clock_reset 小节的布局信息。
        return self._make_protocol_layout(  # 时钟复位成员对应的协议布局结果
            str_slot, direction_rank, protocol_config, section_key="clock_reset", member_rank=int_member_rank
        )

    # 查找协议成员应归属的 section。
    def _find_protocol_member_section(
        self,
        str_payload: str,
        member_order: dict[str, dict[str, int]],
        section_candidates: tuple[str, ...],
    ) -> tuple[str, int] | None:

        """
        查找协议成员应归属的 section。
        
        参数:
            self: 当前 LayoutMixin 实例。
            str_payload: 与 `str_payload` 对应的当前输入参数。
            member_order: 与 `member_order` 对应的当前输入参数。
            section_candidates: 与 `section_candidates` 对应的当前输入参数。
        返回:
            tuple[str, int] | None: 当前协议成员应归属的 section 名称。
        """

        # 按协议配置顺序查找成员所属小节，保持旧排序稳定。
        for str_candidate_section in section_candidates:

            # 当前小节包含该 payload 时即可确定归属。
            if str_payload in member_order[str_candidate_section]:

                # 读取小节内 rank，供排序阶段稳定输出。
                int_member_rank = member_order[str_candidate_section][str_payload]  # 协议成员小节内排序

                # 返回成员小节和 rank。
                return str_candidate_section, int_member_rank

        # 协议成员 section 归类流程在这里确认当前端口不符合这条规则，所以返回空值把处理机会留给后续匹配链。
        return None

    # 构造协议端口布局对象。
    def _make_protocol_layout(
        self, str_slot: str, direction_rank: int, protocol_config: dict[str, object], *,
        section_key: str, member_rank: int,
    ) -> PortLayoutInfo:

        """
        构造协议端口布局对象。
        
        参数:
            self: 当前 LayoutMixin 实例。
            str_slot: 与 `str_slot` 对应的当前输入参数。
            direction_rank: 端口方向优先级。
            protocol_config: 当前协议布局识别所需的配置字典。
            section_key: 与 `section_key` 对应的当前输入参数。
            member_rank: 与 `member_rank` 对应的当前输入参数。
        返回:
            PortLayoutInfo: 构造完成的协议布局对象。
        """

        # 协议组名由中文前缀和槽位共同组成。
        str_group = self._format_group_label(protocol_config["group_prefix"], str_slot)  # 协议组展示名称

        # 这里统一构造协议成员的布局对象，供排序和 banner 渲染共用。
        return PortLayoutInfo(
            group=str_group,
            section=protocol_config["section_labels"][section_key],
            section_rank=protocol_config["section_order"][section_key],
            direction_rank=direction_rank,
            member_rank=member_rank,
            kind=protocol_config["kind"],
        )

    # 规范化 AXIS 成员名称。
    def _normalize_axis_member(self, payload: str) -> str:
        """
        规范化 AXIS 成员名称。
        
        参数:
            self: 当前 LayoutMixin 实例。
            payload: 协议端口名中拆出的成员载荷。
        返回:
            str: 规范化后的 AXIS 成员名称。
        """

        # 先把原始 payload 当作候选成员名，后面再按总线命名习惯剥掉通道前缀。
        str_member = payload  # 尚未去掉通道前缀的协议成员名

        # AXIS `tdata/tvalid/...` 这类成员要去掉前导 `t`，还原成统一成员名。
        if payload.startswith("t") and len(payload) > 1:

            # 去掉前导 `t` 后，得到与排序表一致的 AXIS 成员名。
            str_member = payload[1:]  # 去掉 `t` 前缀后的 AXIS 成员名

        # 兼容少量把 AXI `wdata/rdata` 风格成员误送进 AXIS 归一化链的旧命名。
        elif payload[0] in {"w", "r"} and len(payload) > 1:

            # AXI `wdata/rdata/...` 这类成员去掉通道首字母后再参与成员排序。
            str_member = payload[1:]  # 去掉通道首字母后的 AXI 成员名

        # `usr` 在排序表里统一写成 `user`，这里顺手做一次名称归一化。
        return {"usr": "user"}.get(str_member, str_member)

    # 拆分 AXI 通道名前缀和成员载荷。
    def _split_axi_channel(self, payload: str) -> tuple[str | None, str]:
        """
        拆分 AXI 通道名前缀和成员载荷。
        
        参数:
            self: 当前 LayoutMixin 实例。
            payload: 协议端口名中拆出的成员载荷。
        返回:
            tuple[str | None, str]: AXI 通道前缀和剩余成员载荷。
        """

        # 依次尝试 AXI 常见通道前缀，找到后就把前缀和成员名拆开返回。
        for prefix in ("aw", "ar", "w", "b", "r"):

            # 当前载荷以某个通道前缀开头时，说明它属于对应 AXI 小节。
            if payload.startswith(prefix):

                # 去掉通道前缀后，剩下的部分才是 AXI 成员排序真正使用的名称。
                str_member = payload[len(prefix) :]  # 去掉 AXI 通道前缀后的成员名

                # 返回命中的通道前缀，以及归一化后的成员名给上游 section 逻辑使用。
                return prefix, {"usr": "user"}.get(str_member, str_member)

        # 没有命中任何 AXI 通道前缀时，返回空通道并保留原始成员载荷。
        return None, payload

    # 拆分协议端口名中的词元序列。
    def _split_protocol_tokens(
        self,
        lowered_name: str,
        *protocol_variants: tuple[str, ...],
    ) -> tuple[list[str], list[str] | None]:
        """
        拆分协议端口名中的词元序列。
        
        参数:
            self: 当前 LayoutMixin 实例。
            lowered_name: 小写后的端口名。
            *protocol_variants: 额外的位置参数。
        返回:
            tuple[list[str], list[str] | None]: 清理后的协议词元列表。
        """

        # 端口名拆成词元后，才能在其中滑动匹配不同协议别名。
        list_name_tokens = lowered_name.split("_")  # 端口名拆出的全部词元列表

        # 每个协议别名都要尝试一次窗口匹配，找到后才能拆出槽位前缀和载荷。
        for variant in protocol_variants:

            # 当前协议别名先拆成词元列表，后面才能在端口名词元里做窗口匹配。
            list_variant_tokens = list(variant)  # variant词元列表

            # 在端口名词元里滑动窗口，寻找当前协议别名第一次出现的位置。
            for start in range(len(list_name_tokens) - len(list_variant_tokens) + 1):

                # 当前窗口不等于协议别名时，继续向后滑动寻找下一处命中。
                if list_name_tokens[start : start + len(list_variant_tokens)] != list_variant_tokens:

                    # 协议词元拆分流程在当前元素处理完成后，直接跳到下一轮循环继续看后面的端口。
                    continue

                # 命中协议别名后，返回别名前面的槽位前缀词元和别名后面的成员载荷词元。
                return list_name_tokens[:start], list_name_tokens[start + len(list_variant_tokens) :]

        # 所有协议别名窗口都没命中时，返回空槽位和空载荷表示当前协议不接管。
        return [], None

    # 格式化分组标签文本。
    def _format_group_label(self, prefix: str, slot: str) -> str:
        """
        格式化分组标签文本。
        
        参数:
            self: 当前 LayoutMixin 实例。
            prefix: 分组标签前缀。
            slot: 协议槽位标签。
        返回:
            str: 组合后的分组标签文本。
        """

        # 分组标题由协议前缀和槽位标签直接拼接，保持既有显示格式。
        return f"{prefix}{slot}" if slot else prefix

    # 判断名称是否像时钟或复位信号。
    def _looks_like_clock_reset_name(self, lowered_name: str) -> bool:
        """
        判断名称是否像时钟或复位信号。
        
        参数:
            self: 当前 LayoutMixin 实例。
            lowered_name: 小写后的端口名。
        返回:
            bool: True 表示名称像时钟或复位信号。
        """

        # 先把连字符统一折成下划线，方便后面的时钟复位词元匹配用一套规则处理。
        normalized = lowered_name.replace("-", "_")  # 连字符统一为下划线后的命名文本

        # 这组词元覆盖了常见的 clk/reset 命名，完全命中时可以直接判断为时钟或复位。
        set_exact_tokens = {
            "clk",  # 常见时钟与复位端口的精确词元
            "clock",  # 常见时钟名称的完整词元
            "aclk",  # AXI 风格时钟名称词元
            "rst",  # 下面这些词元覆盖同步/异步、高有效/低有效以及 AXIS 风格复位别名
            "rstn",  # 低有效复位的连写词元
            "rst_n",  # 低有效复位的下划线写法
            "reset",  # 高有效复位的完整单词写法
            "resetn",  # reset 全词低有效的连写别名
            "reset_n",  # reset 全词低有效的下划线别名
            "areset",  # 不带极性后缀的异步复位全词名称
            "aresetn",  # 低有效异步复位的全词连写名称
            "areset_n",  # 低有效异步复位的全词下划线名称
            "arst",  # 不带极性后缀的异步复位简写
            "arstn",  # 低有效异步复位的简写连写名称
            "arst_n",  # 低有效异步复位的简写下划线名称
            "axis_aclk",  # AXIS 风格时钟全词名称
            "axis_rst",  # AXIS 风格高有效复位简写
            "axis_rstn",  # AXIS 风格低有效复位简写
            "axis_reset",  # AXIS 风格高有效复位全词名称
            "axis_resetn",  # AXIS 风格低有效复位全词连写
            "axis_aresetn",  # AXIS 风格低有效异步复位全词名称
            "axis_arstn",  # AXIS 风格低有效异步复位简写
        }

        # 端口名恰好等于常见时钟复位词元时，不需要再走后缀匹配分支。
        if normalized in set_exact_tokens:

            # 名称整体就等于常见词元时，可以直接判定为时钟或复位信号。
            return True

        # 如果不是精确命中，就再检查名称是否以这些时钟复位词元结尾。
        return any(normalized.endswith(f"_{token}") for token in set_exact_tokens)

    # 判断信号名是否命中给定的词元集合。
    def _matches_signal_name_token(self, lowered_name: str, tokens: set[str]) -> bool:
        """
        判断信号名是否命中给定的词元集合。
        
        参数:
            self: 当前 LayoutMixin 实例。
            lowered_name: 小写后的端口名。
            tokens: 需要匹配的词元集合。
        返回:
            bool: True 表示信号名命中了目标词元集合。
        """

        # 这里同样先统一端口名里的分隔符，确保词元集合匹配不受 '-' 和 '_' 写法差异影响。
        normalized = lowered_name.replace("-", "_")  # 连字符统一为下划线后的信号名

        # 端口名完全命中目标词元集合时，可以直接给出肯定结论。
        if normalized in tokens:

            # 精确命中目标词元集合时，当前名称已经满足这条命名规则。
            return True

        # 精确不命中时，再退一步检查名称是否以目标词元结尾。
        return any(normalized.endswith(f"_{token}") for token in tokens)

    # 推断复位信号名称表达的有效极性。
    def _infer_reset_name_polarity(self, lowered_name: str) -> str | None:
        """
        推断复位信号名称表达的有效极性。
        
        参数:
            self: 当前 LayoutMixin 实例。
            lowered_name: 小写后的端口名。
        返回:
            str | None: 推断出的复位极性，无法确定时返回空值。
        """

        # 这组词元专门覆盖低电平有效复位的常见别名。
        set_low_active_tokens = {
            "rstn",  # 覆盖低电平有效复位的常见连写、下划线和 AXIS/AXI 变体
            "rst_n",  # rst 缩写的低有效下划线形式
            "resetn",  # reset 全词后直接接低有效尾字母的写法
            "reset_n",  # reset 全词保留下划线尾缀的低有效写法
            "aresetn",  # async reset 全词直连低有效尾字母
            "areset_n",  # async reset 全词保留下划线尾缀
            "arstn",  # a/rst 简写拼成的低有效连写
            "arst_n",  # a/rst 简写保留下划线尾缀
            "axis_rstn",  # AXIS 端口常见的 rstn 低有效缩写
            "axis_resetn",  # AXIS 端口常见的 resetn 全词写法
            "axis_aresetn",  # AXIS 命名里带 aresetn 的异步复位字段
            "axis_arstn",  # AXIS 命名里带 arstn 的异步复位简写
        }

        # 这组词元表示高电平有效复位的常见命名形式。
        set_high_active_tokens = {
            "rst",  # 覆盖高电平有效复位的常见普通写法与 AXIS/AXI 变体
            "reset",  # 直接写成 reset 的高有效名称
            "areset",  # 很多 IP 把异步高有效复位直接写成 areset
            "arst",  # 部分旧命名把异步高有效复位缩写成 arst
            "axis_rst",  # 带 axis 前缀且只保留 rst 缩写时，也按高有效默认复位处理
            "axis_reset",  # 带 axis 前缀并写成完整 reset 时，同样归到高有效默认集合
        }

        # 先看名称是否符合低电平有效复位的常见命名，命中后就不必再看高电平分支。
        if self._matches_signal_name_token(lowered_name, set_low_active_tokens):

            # 命中低有效复位命名时，返回 low 供上游生成低有效默认复位语义。
            return "low"

        # 低电平命名不成立时，再检查它是否属于高电平有效复位的命名集合。
        if self._matches_signal_name_token(lowered_name, set_high_active_tokens):

            # 命中高有效复位命名时，返回 high 供上游生成同步或异步高有效默认复位语义。
            return "high"

        # 复位极性推断流程在这里确认当前端口不符合这条规则，所以返回空值把处理机会留给后续匹配链。
        return None

    # 生成默认的高电平有效复位名。
    def _default_high_reset_name(self) -> str:
        """
        生成默认的高电平有效复位名。
        
        参数:
            self: 当前 LayoutMixin 实例。
        返回:
            str: 默认的高电平有效复位名称。
        """

        # 用户配置里的默认复位名会影响默认高电平复位的兜底返回值。
        str_default_reset = self.config["reset_clock"]["default_reset"]  # 默认复位结果

        # 先把默认复位名统一成可比对的小写形式，后面再判断它本身是不是低有效命名。
        normalized = str_default_reset.replace("-", "_")  # 用于判定极性的标准化默认复位名

        # `_n` 结尾通常表示低有效复位，这里优先处理这种最明确的命名形式。
        if normalized.endswith("_n"):

            # 默认复位名若以 `_n` 结尾，这里去掉低有效后缀，得到对应的高有效名称。
            return str_default_reset[:-2]

        # 某些工程会把低有效复位写成 `resetn/rstn`，这里兼容这种无下划线写法。
        if normalized.endswith("n"):

            # 默认复位名若以单个 `n` 结尾，这里同样去掉低有效尾缀生成高有效名称。
            return str_default_reset[:-1]

        # 默认复位名本身已经不是低有效写法时，直接沿用配置值作为高有效名称。
        return str_default_reset

    # 计算时钟或复位成员的排序优先级。
    def _clock_reset_member_rank(self, lowered_name: str) -> int:
        """
        计算时钟或复位成员的排序优先级。
        
        参数:
            self: 当前 LayoutMixin 实例。
            lowered_name: 小写后的端口名。
        返回:
            int: 时钟或复位成员的排序优先级。
        """

        # 这里取出名称最后一个词元，后面的时钟/复位排序主要靠这个尾词判断优先级。
        str_tail_token = lowered_name.split("_")[-1]  # 当前名称最后一个词元

        # 纯时钟名称优先排在复位前面，所以这些尾词在这里先返回最高优先级。
        if str_tail_token in {"clk", "clock", "aclk"}:

            # 时钟类尾词优先级最高，这里直接返回 0 让它排在复位前面。
            return 0

        # 低电平有效复位通常排在普通 reset 之前，这里给它单独的排序档位。
        if "reset" in str_tail_token or "rst" in str_tail_token:

            # reset/rst 类尾词统一排在时钟之后，返回次级优先级 1。
            return 1

        # 其余 reset 相关尾词继续走普通复位优先级档位。
        return 2
