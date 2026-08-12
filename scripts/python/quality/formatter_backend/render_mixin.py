"""为 VerilogFormatterEngine 提供模块区域、声明区和赋值区渲染辅助。"""

# 延迟注解求值，避免 formatter mixin 之间出现运行期类型循环引用。
from __future__ import annotations

# 标准库用于局部文本识别和兼容继承链中的路径注解。
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

# banner 工具统一生成区域标题，保持 formatter 版式协议不变。
from .banners import display_width, extract_banner_title, is_banner_line, make_banner

# formatter 基础模型覆盖参数、端口和声明类渲染输入。
from .models import (
    VerilogFormatterError,
    ParamDecl,
    ParamRenderCluster,
    PortDecl,
    PortLayoutInfo,
    SignalDecl,
)

# 输出、实例和赋值布局模型用于保持区域排序协议稳定。
from .models import (
    OutputSignalLayout,
    AssignSourceLayout,
    InstanceSignalLayout,
    AssignStmt,
)

# 过程块和结构块模型承载 parser 已整理好的 Verilog 主体。
from .models import (
    BodyBlock,
    LValueRef,
    CaseItem,
    ControlNode,
    AlwaysBlock,
)

# 实例化、generate 和初始块模型用于恢复结构化 module body。
from .models import (
    InstanceBlock,
    GenerateBlock,
    InitialBlock,
)

# function/task、原始块和条件编译模型保持 parser 原始结构边界。
from .models import (
    FunctionBlock,
    TaskBlock,
    RawBlock,
    PreprocessorConditional,
    HeaderMetadata,
)

# 文本读取工具保留给旧继承入口，避免拆分后改变可导入符号集合。
from .textio import read_verilog_text

# Body 区域渲染上下文把高频共享参数收束到单一内部对象。
@dataclass(frozen=True)
class BodyRegionRenderContext:
    """承载单个 module body 区域渲染所需的共享输入。

    :param items: parser 输出的 module body 分组字典。
    :param ports: module 端口声明列表。
    :param output_internal_names: 输出端口名到内部代理信号名的映射。
    :param output_internal_layouts: 输出内部信号布局信息。
    :param output_target_layouts: 输出目标端口布局信息。
    :param include_output_bridges: 是否在 assign 区域补充输出桥接。
    :param include_region_banners: 是否保留区域横幅。
    """

    # items 保存 parser 已分组的 module body 输入。
    items: dict[str, list]  # body 分组输入

    # ports 提供输出桥接和 assign 来源排序所需的端口上下文。
    ports: list[PortDecl]  # module 端口列表

    # output_internal_names 记录输出端口到内部代理信号的映射。
    output_internal_names: dict[str, str]  # 输出代理名称映射

    # output_internal_layouts 描述内部输出代理信号的渲染位置。
    output_internal_layouts: dict[str, OutputSignalLayout]  # 输出代理布局

    # output_target_layouts 描述目标输出端口的区域排序布局。
    output_target_layouts: dict[str, OutputSignalLayout]  # 输出端口布局

    # include_output_bridges 控制当前 body 是否补充端口桥接 assign。
    include_output_bridges: bool  # 是否补输出桥接

    # include_region_banners 控制区域 helper 返回完整区块还是内嵌正文。
    include_region_banners: bool  # 是否保留区域横幅

# Module 渲染上下文收束 header、body 和 output 布局输入。
@dataclass(frozen=True)
class ModuleRenderContext:
    """承载单个 Verilog module 渲染所需的最终结构输入。

    :param module_name: module 声明名。
    :param version: 文件头版本号。
    :param params: module header 参数列表。
    :param ports: module 端口列表。
    :param body_items: parser 和 normalize 阶段整理后的 body 分组。
    :param file_preamble_blocks: module 前需要保留的原始前导块。
    :param include_header: 是否输出标准文件头。
    :param leading_comments: 不输出文件头时需要保留的 module 前注释。
    :param header_metadata: 可选的文件头元数据。
    :param output_internal_names: 输出端口到内部代理信号名的映射。
    :param output_internal_layouts: 输出代理信号布局。
    :param output_target_layouts: 输出端口目标布局。
    """

    # module_name 直接用于 Verilog module 声明首行。
    module_name: str  # module 声明名称

    # version 用于标准文件头版本字段。
    version: str  # 文件头版本号

    # params 保存 module header 中需要输出的参数。
    params: list[ParamDecl]  # header 中可公开渲染的参数声明

    # ports 保存最终端口声明顺序。
    ports: list[PortDecl]  # module 端口声明

    # body_items 保存 renderer 消费的最终 body tree。
    body_items: dict[str, list]  # normalize 后的 body 区域分组

    # file_preamble_blocks 保留 module 前的原始注释和宏块。
    file_preamble_blocks: list[RawBlock]  # 文件前导原始块

    # include_header 决定是否渲染标准文件头。
    include_header: bool  # 是否输出文件头

    # leading_comments 保存无标准头时的 module 前注释。
    leading_comments: list[str]  # module 前导注释

    # header_metadata 提供 Vivado 风格文件头字段。
    header_metadata: HeaderMetadata | None  # 文件头元数据

    # output_internal_names 记录输出端口对应的内部代理信号。
    output_internal_names: dict[str, str]  # 输出端口到内部线网的映射

    # output_internal_layouts 描述输出代理信号声明位置。
    output_internal_layouts: dict[str, OutputSignalLayout]  # 内部代理声明布局表

    # output_target_layouts 描述输出端口目标区域布局。
    output_target_layouts: dict[str, OutputSignalLayout]  # 输出目标布局表

# RenderMixin 只负责把 parser 产物拼回既有 formatter 输出协议。
class RenderMixin:
    """渲染 module 外壳、参数声明、主体区域和连续赋值区域。"""

    # module 外壳渲染只负责拼接最终文本，输入由上下文对象承载。
    def _render_module(self, context: ModuleRenderContext) -> str:
        """
        渲染单个 Verilog module 的完整源码文本。

        :param context: 单 module 渲染所需的 header、body 和 output 布局输入。
        :return: 带尾随换行的完整 Verilog module 文本。
        """

        # 模块渲染按输出顺序累积文本片段，最后一次性拼接成源码。
        list_lines = []  # module 级渲染输出行

        # 文件头只在调用方要求时写入，避免条件分支里的注释被误放进 module 内部。
        if context.include_header:

            # preamble 先于标准头部输出，保留宏和版权注释的原始位置。
            list_preamble_lines = self._render_file_preamble_lines(context.file_preamble_blocks)  # 文件前导注释与宏行

            # 前导块必须完整追加，后续头部再决定是否插入空行。
            list_lines.extend(list_preamble_lines)

            # 前导块存在时用空行隔开自动生成头部，维持旧 formatter 视觉结构。
            if list_preamble_lines:

                # 空行隔开用户前导内容和工具生成头部。
                list_lines.append("")

            # 标准文件头由公共 helper 生成，保持版本和时间字段兼容。
            list_lines.extend(self._render_header(context.module_name, context.version, context.header_metadata))

            # 定版 header 结束后只保留一个空行，不再输出额外模块摘要注释。
            list_lines.append("")

        # 不输出标准头时，保留 parser 识别到的 module 前注释。
        elif context.leading_comments:

            # module 前注释直接沿用输入内容，避免丢失用户手写说明。
            list_lines.extend(context.leading_comments)

            # module 关键字前保留一行分隔，匹配既有输出版式。
            list_lines.append("")

        # module 声明首行必须紧跟可选头部或注释之后。
        list_lines.append(f"module {context.module_name}")

        # 仅当存在参数时才输出 Verilog `#(...)` 参数列表。
        if context.params:

            # 参数列表开头沿用旧输出协议，不把空参数列表写入源码。
            list_lines.append("#(")

            # synthetic 参数只参与内部推导，不应暴露在 module 端口头部。
            list_visible_params = [param for param in context.params if not param.synthetic]  # module 头部可见参数

            # 逐个参数渲染并给除最后一项外的条目补逗号。
            for index, param in enumerate(list_visible_params):

                # raw_text 表示 parser 已保留原始声明，优先避免二次格式化破坏表达式。
                if param.raw_text:

                    # 原始参数声明只补缩进，内容不再重排。
                    list_lines.append(f"{self._indent(1)}{param.raw_text}")

                    # 原始声明已经包含完整参数语义，跳过重排路径。
                    continue

                # 非末尾参数需要逗号，保证 Verilog 参数列表语法完整。
                str_suffix = "," if index < len(list_visible_params) - 1 else ""  # 当前参数后的分隔符

                # decl_spec 保留 parameter/localparam 的类型、位宽和 signed 信息。
                str_decl_spec = self._format_param_decl_spec(param.decl_spec)  # 参数声明修饰片段

                # 参数表达式只做空白规范化，不改变宏、拼接或函数调用含义。
                str_param_value = self._normalize_expression_spacing(param.value)  # 参数默认值表达式

                # module 头部参数行保持旧 formatter 的缩进和尾注释策略。
                str_param_code = (
                    f"{self._indent(1)}{param.keyword} "  # 参数关键字和缩进前缀
                    f"{str_decl_spec}{param.name} = {str_param_value}{str_suffix}"  # 参数名和值片段
                )  # 单行 parameter 声明文本

                # 参数说明通过统一尾注释 helper 追加，保持 comment policy 一致。
                list_lines.append(self._append_trailing_comment(str_param_code, param.comment, "parameter"))

            # 参数列表闭合行必须单独输出，避免影响端口列表起始位置。
            list_lines.append(")")

        # 端口列表开始行固定输出，即使端口由后续 helper 决定是否为空。
        list_lines.append("(")

        # 端口渲染由 layout mixin 负责，本函数只拼接 module 外壳。
        list_lines.extend(self._render_ports(context.ports))

        # module 头部闭合后进入主体区域。
        list_lines.append(");")

        # 主体和头部之间保留一个空行，匹配 formatter 输出快照。
        list_lines.append("")

        # list_body_lines 是 module 主体树的完整渲染结果。
        list_body_lines = self._render_body_tree(  # module 主体区域源码行
            context.body_items,  # parser 归一化后的 body 分组
            context.ports,  # module header 中的端口声明
            context.output_internal_names,  # 输出端口代理信号映射

            # 输出布局参数和渲染开关分开排列，避免长调用块过密。
            context.output_internal_layouts,  # 输出代理声明布局
            context.output_target_layouts,  # 输出端口目标布局
            include_output_bridges=True,  # 顶层 body 需要补输出桥接
            include_region_banners=True,  # 顶层 body 保留区域横幅
        )

        # 主体树渲染包含区域横幅、声明、assign、always 和条件编译块。
        list_lines.extend(list_body_lines)

        # endmodule 是 module 输出的最后一行，避免主体 helper 误持有外壳责任。
        list_lines.append("endmodule")

        # 返回带尾随换行的完整 Verilog 文本，兼容现有 formatter API。
        return "\n".join(list_lines) + "\n"

    # 参数区域渲染负责 config/localparam 和 state 参数的横幅、分组与声明行。
    def _render_param_region(self, region: str, params: list[ParamDecl]) -> list[str]:
        """
        渲染指定参数区域。

        :param region: 参数区域名，例如 `config_param` 或 `state_param`。
        :param params: parser 提供的参数声明列表。
        :return: 带区域横幅和尾随空行的参数区域行；无可见参数时返回空列表。
        """

        # list_visible_params 过滤掉只供内部推导使用的 synthetic 参数。
        list_visible_params = [param for param in params if not param.synthetic]  # 区域可见参数

        # 没有真实参数时不输出空横幅，避免制造空区域。
        if not list_visible_params:

            # 空参数区域直接交还调用方跳过。
            return []

        # list_lines 以区域横幅开头，后续追加参数声明。
        list_lines = [f"{self._indent(1)}{self.REGION_TITLES[region]}"]  # 参数区域输出行

        # bool_rendered_param 记录是否已输出参数，用于 cluster 之间补单个空行。
        bool_rendered_param = False  # 参数区域是否已有内容

        # list_clusters 对 config_param 做语义族聚类，对其它参数保持输入顺序。
        list_clusters = self._build_param_render_clusters(region, list_visible_params)  # 参数渲染分组

        # 每个 cluster 对应一个连续的参数说明或推断标签段。
        for cluster in list_clusters:

            # 空 cluster 没有可见输出，通常来自防御性调用。
            if not cluster.params:

                # 跳过无参数分组。
                continue

            # 已有参数时，下一组之前保留一个空行便于阅读。
            if bool_rendered_param:

                # cluster 分隔只追加单个空行，避免输出漂移。
                self._ensure_single_blank_line_before_cluster(list_lines, bool_rendered_param)

            # 推断标签只用于 config_param 的高置信分组。
            if cluster.source == "inferred" and cluster.label:

                # 标签用 Verilog 注释输出，不改变参数语义。
                list_lines.append(f"{self._indent(1)}//{cluster.label}")

            # cluster 内参数保留原始顺序，避免改变依赖前序参数的表达式。
            for int_param_index, param in enumerate(cluster.params):

                # 用户已有前导注释时，在非首项前用空行隔开语义段。
                if param.leading_comments:

                    # 同一 cluster 的后续注释块前补空行，避免注释贴到上一项。
                    if int_param_index > 0:

                        # 前导注释块需要与上一条参数声明清晰分隔。
                        self._ensure_single_blank_line_before_cluster(list_lines, bool_rendered_param)

                    # 前导注释由公共 helper 统一处理缩进和注释符。
                    list_lines.extend(self._render_leading_comments(param.leading_comments, 1))

                # 原始参数文本由 parser 判定为不可安全重排。
                if param.raw_text:

                    # 原样输出时只补区域缩进。
                    list_lines.append(f"{self._indent(1)}{param.raw_text}")

                    # 标记已有参数，后续 cluster 可据此插入分隔空行。
                    bool_rendered_param = True  # 当前区域已输出参数声明

                    # 原始声明已经完整输出，不再走格式化路径。
                    continue

                # str_decl_spec 保留 localparam/parameter 的类型、位宽和 signed 组合。
                str_decl_spec = self._format_param_decl_spec(param.decl_spec)  # 区域参数类型修饰文本

                # str_param_value 只规范空白，不修改表达式结构。
                str_param_value = self._normalize_expression_spacing(param.value)  # 参数赋值表达式

                # str_param_code 是区域内以分号结束的参数声明。
                str_param_code = (
                    f"{self._indent(1)}{param.keyword} "  # 区域参数缩进和关键字
                    f"{str_decl_spec}{param.name} = {str_param_value};"  # 区域参数赋值片段
                )  # 区域参数声明文本

                # 尾注释 helper 负责补齐默认 parameter 注释。
                list_lines.append(self._append_trailing_comment(str_param_code, param.comment, "parameter"))

                # 当前参数行已经写入区域，下一组参数据此补分隔空行。
                bool_rendered_param = True  # 参数区域已出现真实声明行

        # 区域末尾保留空行，供后续区域自然分隔。
        list_lines.append("")

        # 返回带横幅和尾随空行的完整参数区域。
        return list_lines

    # 参数 cluster 构建保留人工注释边界，只对无注释 config 参数做推断。
    def _build_param_render_clusters(self, region: str, params: list[ParamDecl]) -> list[ParamRenderCluster]:
        """
        为参数区域构建渲染 cluster。

        :param region: 参数区域名。
        :param params: 已过滤 synthetic 的参数声明列表。
        :return: 按人工注释和自动推断规则划分的参数 cluster。
        """

        # 非配置参数不做语义推断，避免误拆状态编码顺序。
        if region != "config_param":

            # 单 cluster 保留输入顺序。
            return [ParamRenderCluster(params=list(params))]

        # list_clusters 汇总显式注释段和自动推断段。
        list_clusters: list[ParamRenderCluster] = []  # 配置参数渲染分组

        # list_segment 暂存相邻参数，直到显式注释或语义族边界出现。
        list_segment: list[ParamDecl] = []  # 当前连续参数片段

        # bool_segment_has_explicit_comments 表示当前片段是否由人工注释开启。
        bool_segment_has_explicit_comments = False  # 当前片段是否带显式注释

        # 扫描参数列表时只在边界处落盘 segment。
        for param in params:

            # 前导注释代表新的人工分组起点。
            if param.leading_comments:

                # 先把上一段按其来源写入 cluster 列表。
                if list_segment:

                    # 显式注释段保持原状，未注释段交给推断聚类。
                    if bool_segment_has_explicit_comments:

                        # 人工注释段直接作为 explicit cluster 输出。
                        list_clusters.append(ParamRenderCluster(params=list(list_segment), source="explicit"))

                    # 上一段没有人工注释时交给自动聚类。
                    else:

                        # 自动分组只处理没有人工注释的连续参数。
                        list_clusters.extend(self._build_inferred_config_param_clusters(list_segment))

                # 新片段从当前带注释参数开始。
                list_segment = [param]  # 当前显式注释片段

                # 当前片段带人工注释，后续边界按 explicit 处理。
                bool_segment_has_explicit_comments = True  # 当前片段来自显式注释

                # 带注释参数已进入新片段。
                continue

            # 无注释参数如果位于空片段，开启一个可自动推断的片段。
            if not list_segment:

                # 新片段暂存当前参数，等待后续同族成员。
                list_segment = [param]  # 当前自动推断片段

                # 无前导注释表示该片段可进入名称族推断。
                bool_segment_has_explicit_comments = False  # 当前片段没有显式注释

                # 当前参数已经进入片段，继续扫描下一项。
                continue

            # 当前无注释参数延续上一段。
            list_segment.append(param)

        # 文件末尾的暂存片段需要按来源补入 cluster 列表。
        if list_segment:

            # 显式注释片段保持用户书写边界。
            if bool_segment_has_explicit_comments:

                # 最后一段人工注释参数直接输出。
                list_clusters.append(ParamRenderCluster(params=list(list_segment), source="explicit"))

            # 末尾片段没有人工注释时继续做自动聚类。
            else:

                # 最后一段无注释参数继续做名称族推断。
                list_clusters.extend(self._build_inferred_config_param_clusters(list_segment))

        # 返回供参数区域渲染消费的 cluster 序列。
        return list_clusters

    # 无人工注释的 config 参数段会按已知前缀和高置信族名自动聚类。
    def _build_inferred_config_param_clusters(self, params: list[ParamDecl]) -> list[ParamRenderCluster]:
        """
        为连续无注释配置参数推断渲染 cluster。

        :param params: 没有显式注释边界的连续配置参数段。
        :return: 推断标签 cluster 与无标签兜底 cluster 的列表。
        """

        # list_clusters 保存推断得到的参数分组。
        list_clusters: list[ParamRenderCluster] = []  # 推断得到的参数分组

        # int_index 指向当前尚未归类的参数。
        int_index = 0  # 当前扫描位置

        # 顺序扫描参数，优先匹配已知前缀，再匹配高置信族名。
        while int_index < len(params):

            # str_known_label 是当前参数命中的内置配置标签。
            str_known_label = self._match_known_config_param_label(params[int_index].name)  # 已知配置标签

            # 连续同标签参数合并成一个 cluster。
            if str_known_label:

                # int_end 指向同标签连续区间的右边界。
                int_end = int_index + 1  # 已知标签段右边界初值

                # 扩展到标签变化或参数列表结束。
                while (
                    int_end < len(params)
                    and self._match_known_config_param_label(params[int_end].name) == str_known_label
                ):

                    # 同一配置前缀继续合并，直到下一个参数族边界出现。
                    int_end += 1  # 已知标签段扩展后的右边界

                # 当前连续已知标签段可直接输出推断标签。
                list_clusters.append(
                    ParamRenderCluster(
                        params=params[int_index:int_end],  # 已知标签覆盖的参数切片
                        label=str_known_label,  # 内置前缀推断出的配置标签
                        source="inferred",  # 来源标记为前缀推断
                    )
                )

                # 下一轮从区间右边界继续。
                int_index = int_end  # 已知标签段后的扫描起点

                # 已知标签段已经完成归类。
                continue

            # tuple_family_match 保存高置信参数族名和右边界。
            tuple_family_match = self._match_high_confidence_param_family(params, int_index)  # 高置信参数族匹配

            # str_family_key 是可作为标签前缀的参数族名。
            str_family_key = tuple_family_match[0]  # 高置信参数族名

            # int_family_end 是高置信族名覆盖的右边界。
            int_family_end = tuple_family_match[1]  # 高置信参数族右边界

            # 成功识别参数族时输出一个推断 cluster。
            if str_family_key:

                # family_key 作为标签前缀，保持已有中文“参数”后缀。
                list_clusters.append(
                    ParamRenderCluster(
                        params=params[int_index:int_family_end],  # 高置信族名覆盖的参数切片
                        label=f"{str_family_key}参数",  # 族名派生出的中文参数标签
                        source="inferred",  # 来源标记为族名推断
                    )
                )

                # 已识别的同族参数不再进入无标签兜底段。
                int_index = int_family_end  # 高置信族段后的扫描起点

                # 当前高置信族段已经完成归类。
                continue

            # int_none_end 是无标签兜底段的右边界。
            int_none_end = int_index + 1  # 无标签参数区间右边界

            # 兜底段延伸到下一处可识别边界之前。
            while int_none_end < len(params):

                # 已知标签出现时结束当前兜底段。
                if self._match_known_config_param_label(params[int_none_end].name):

                    # 下一个参数可形成独立标签段。
                    break

                # tuple_next_family_match 检查下一位置是否可独立成族。
                tuple_next_family_match = self._match_high_confidence_param_family(  # 下一位置族匹配结果
                    params,  # 当前无注释参数片段
                    int_none_end,  # 下一处候选起点
                )

                # 下一段可独立成族时保留边界。
                if tuple_next_family_match[0]:

                    # 兜底段到当前参数之前结束。
                    break

                # 普通配置项仍未形成独立族名，继续并入兜底段。
                int_none_end += 1  # 兜底参数段右边界

            # 未归类参数按原顺序输出，不额外插入推断标签。
            list_clusters.append(ParamRenderCluster(params=params[int_index:int_none_end], source="none"))

            # 扫描点跳到兜底段之后，重新尝试已知前缀和族名。
            int_index = int_none_end  # 兜底段后的扫描起点

        # 返回所有推断 cluster，供上层参数区域渲染。
        return list_clusters

    # 已知配置参数前缀提供最稳定的自动分组标签。
    def _match_known_config_param_label(self, name: str) -> str:
        """
        根据参数名前缀匹配内置配置分组标签。

        :param name: Verilog 参数名。
        :return: 匹配到的中文分组标签；未命中时返回空字符串。
        """

        # 参数名统一大写后匹配配置前缀，兼容不同命名大小写。
        str_normalized_name = name.strip().upper()  # 归一化后的参数名

        # 前缀表顺序由配置定义，优先级保持稳定。
        for prefix, label in self.KNOWN_CONFIG_PARAM_LABELS:

            # 命中已知前缀即可返回对应中文标签。
            if str_normalized_name.startswith(prefix):

                # 标签直接用于 Verilog 注释横幅下的局部分组。
                return label

        # 未命中时返回空字符串，调用方继续尝试参数族推断。
        return ""

    # 参数族候选从参数名 token 中提取，供高置信连续段判断使用。
    def _param_family_candidates(self, name: str) -> list[str]:
        """
        生成配置参数名的族名前缀候选。

        :param name: Verilog 参数名。
        :return: 从具体到宽泛排列的参数族候选列表。
        """

        # 下划线 token 用于识别 WIDTH/DATA_WIDTH 等参数族前缀。
        list_tokens = [token for token in name.strip().upper().split("_") if token]  # 参数名分词

        # 候选族名按更具体的双 token 优先。
        list_candidates: list[str] = []  # 参数族候选键

        # 双 token 候选需要避开 WIDTH/NUM 等泛化词，降低错误聚合概率。
        bool_has_specific_pair = (  # 前两个 token 是否适合作为具体族名
            len(list_tokens) >= 2  # 至少需要两个 token 才能形成具体族名
            and list_tokens[0] not in self.PARAM_FAMILY_GENERIC_TOKENS  # 首 token 不能是泛化词
            and list_tokens[1] not in self.PARAM_FAMILY_GENERIC_TOKENS  # 第二 token 也必须有区分度
        )

        # 具体双 token 可以表达更窄的参数族。
        if bool_has_specific_pair:

            # 双 token 候选保持小写，作为输出标签前缀。
            list_candidates.append("_".join(token.lower() for token in list_tokens[:2]))

        # 单 token 候选作为退路，只在首 token 不是泛化词时使用。
        if list_tokens and list_tokens[0] not in self.PARAM_FAMILY_GENERIC_TOKENS:

            # 单 token 族名用于 DATA_*、AXI_* 等常见参数簇。
            str_candidate = list_tokens[0].lower()  # 单 token 参数族候选

            # 避免双 token 与单 token 完全重复。
            if str_candidate not in list_candidates:

                # 追加低优先级候选。
                list_candidates.append(str_candidate)

        # 返回从具体到宽泛的候选族名。
        return list_candidates

    # 连续同族参数达到最小规模时才输出推断标签。
    def _match_high_confidence_param_family(self, params: list[ParamDecl], start_index: int) -> tuple[str, int]:
        """
        从指定位置识别高置信配置参数族。

        :param params: 当前无显式注释的连续配置参数段。
        :param start_index: 待尝试匹配的起始参数下标。
        :return: `(族名, 右边界)`；未识别时返回空族名和原起点。
        """

        # 候选族名按优先级尝试，命中三个以上才认为可信。
        for str_candidate in self._param_family_candidates(params[start_index].name):

            # end 表示当前候选族连续覆盖的右边界。
            int_end = start_index + 1  # 参数族右边界

            # 只有连续成员都包含同一族候选时才延伸。
            while int_end < len(params) and str_candidate in self._param_family_candidates(params[int_end].name):

                # 当前候选族继续向后覆盖。
                int_end += 1  # 当前候选族覆盖右边界

            # 三个以上同族参数才输出自动标签，避免偶然前缀造成误分组。
            if int_end - start_index >= 3:

                # 返回族名和右边界给上层切片。
                return str_candidate, int_end

        # 没有可信族名时，调用方继续按无标签段处理。
        return "", start_index

    # 声明区域 renderer 负责声明标签、前导注释和声明源码行。
    def _render_decl_region(
        self,
        region: str,
        decls: list[SignalDecl],
        signal_layouts: dict[str, InstanceSignalLayout] | None = None,
    ) -> list[str]:
        """
        渲染同一声明区域内的信号声明行。

        :param region: formatter 区域名，用于查找区域横幅标题。
        :param decls: 已按区域分组的信号声明列表。
        :param signal_layouts: 可选的实例信号布局，用于插入亲和性标签。
        :return: 带区域横幅、声明行和尾随空行的 Verilog 文本行；无声明时返回空列表。
        """

        # 空声明区域不输出横幅，避免生成只有标题的 formatter 区块。
        if not decls:

            # 调用方据此跳过当前声明区域。
            return []

        # list_lines 保存当前声明区域的横幅和声明源码行。
        list_lines = [f"{self._indent(1)}{self.REGION_TITLES[region]}"]  # 声明区域输出行

        # str_current_label 记录上一条已输出的布局标签，避免重复插入同一标签。
        str_current_label = "__start__"  # 当前声明标签状态

        # bool_rendered_decl 标记是否已经输出过真实声明。
        bool_rendered_decl = False  # 声明区域是否已有真实声明

        # dict_signal_layouts 统一处理无布局输入的区域。
        dict_signal_layouts = signal_layouts or {}  # 声明名到布局信息的映射

        # 按 parser 保留的顺序输出声明，布局标签只作为可读分隔。
        for decl in decls:

            # str_label 是当前声明所属的实例亲和性标签。
            str_label = self._format_signal_affinity_label(dict_signal_layouts.get(decl.name))  # 声明布局标签

            # 有布局信息时按标签边界插入 Verilog 注释标签。
            if dict_signal_layouts:

                # str_current_label 保存本次标签插入后的最新状态。
                str_current_label = self._begin_label_cluster(  # 更新后的声明标签状态
                    list_lines,  # 当前声明区域输出行缓存
                    str_label,  # 当前声明亲缘标签
                    decl.leading_comments,  # 当前声明原始前导注释
                    str_current_label,  # 上一条声明标签状态
                    bool_rendered_decl,  # 当前区域是否已有声明
                )

            # 声明前导注释保持 parser 捕获的相对位置。
            list_lines.extend(self._render_leading_comments(decl.leading_comments, 1))

            # str_code 是单条信号声明的 Verilog 代码文本。
            str_code = self._render_signal_decl_code(decl, 1)  # 单条声明源码

            # 声明尾注释通过统一 helper 追加，保留默认 signal 注释口径。
            list_lines.append(self._append_trailing_comment(str_code, decl.comment, "signal"))

            # 已输出声明后，后续标签和区域收尾都需要看到该状态。
            bool_rendered_decl = True  # 当前声明区域已有输出

        # 若输入声明全部被 helper 跳过，则调用方不应看到空区域。
        if not bool_rendered_decl:

            # 空区域直接返回，避免留下孤立横幅。
            return []

        # 区域末尾保留空行，维持和其它 region 的拼接协议。
        list_lines.append("")

        # 返回完整声明区域文本行。
        return list_lines

    # 去掉区域横幅外壳，供无横幅渲染路径复用主体行。
    def _trim_region_wrapper(self, lines: list[str]) -> list[str]:
        """
        移除区域渲染结果中的首行横幅和末尾空行。

        :param lines: 已渲染的区域行，首行通常是区域横幅。
        :return: 去掉横幅和尾随空行后的区域主体行。
        """

        # 空区域没有可裁剪内容。
        if not lines:

            # 调用方继续按空区域处理。
            return []

        # list_body 保留横幅之后的真实区域内容。
        list_body = lines[1:] if len(lines) > 1 else []  # 去掉首行横幅后的主体行

        # 删除主体末尾的拼接空行，避免无横幅模式出现多余空白。
        while list_body and list_body[-1] == "":

            # 每轮只移除一个尾随空行，保持其它内容顺序不变。
            list_body.pop()

        # 返回裁剪后的主体行。
        return list_body

    # 去掉任意行列表末尾空白，供条件块拼接前清理。
    def _trim_trailing_blank_lines(self, lines: list[str]) -> list[str]:
        """
        删除行列表末尾连续空行。

        :param lines: 待清理的源码行列表。
        :return: 保留原始顺序但不含尾随空行的新列表。
        """

        # list_trimmed 复制输入，避免调用方持有的列表被原地修改。
        list_trimmed = list(lines)  # 待裁剪的行副本

        # 末尾空行只影响区域拼接，不承载 Verilog 语义。
        while list_trimmed and list_trimmed[-1] == "":

            # 弹出当前末尾空行。
            list_trimmed.pop()

        # 返回去掉尾部空白的新列表。
        return list_trimmed

    # 预处理 prologue 保留 module body 前的 raw 宏块和注释。
    def _render_preprocessor_prologue(self, blocks: list[RawBlock]) -> list[str]:
        """
        渲染 body 区域前的预处理 raw block。

        :param blocks: parser 保留在 module body 起始位置的 raw/preprocessor 块。
        :return: 带统一缩进和尾随空行的 prologue 行；无块时返回空列表。
        """

        # 没有 prologue 时不制造空分隔行。
        if not blocks:

            # 调用方继续从常规 body 区域开始渲染。
            return []

        # list_lines 收集预处理 prologue 的注释和原始代码行。
        list_lines: list[str] = []  # 预处理前导输出行

        # 每个 raw block 都保留 parser 识别出的前导注释和原始行。
        for block in blocks:

            # 前导注释先于 raw 行输出，维持源码相对顺序。
            list_lines.extend(self._render_leading_comments(block.leading_comments, 1))

            # raw 行只补缩进，不做语义重排。
            list_lines.extend(self._render_raw_block_lines(block.lines, 1))

        # prologue 与后续结构化区域之间保留一个空行。
        list_lines.append("")

        # 返回完整的 body 前导行。
        return list_lines

    # body prologue 使用固定哨兵标签参与 region 拼接排序。
    def _body_prologue_anchor(self) -> str:
        """
        提供 body prologue 在 region 序列中的固定锚点。

        :param self: 使用当前 formatter 实例的固定锚点约定，无外部业务参数。
        :return: body prologue 的内部排序锚点字符串。
        """

        # 返回只在 formatter 内部使用的哨兵区域名。
        return "__body_prologue__"

    # body 区域顺序在配置基础上补齐 function、task 和 initial 的兼容位置。
    def _body_render_regions(self) -> list[str]:
        """
        生成 module body 的区域渲染顺序。

        :param self: 使用当前 formatter 配置中的结构区域顺序，无外部业务参数。
        :return: 按 formatter 配置和兼容规则整理后的区域名列表。
        """

        # list_regions 按输出顺序累积区域名并去重。
        list_regions: list[str] = []  # body 区域渲染顺序

        # function/task 固定优先于配置中的结构区域。
        for region in ("function_block", "task_block", *self.config["structure"]["region_order"]):

            # 配置中重复出现的区域只保留第一次位置。
            if region not in list_regions:

                # 新区域追加到输出顺序中。
                list_regions.append(region)

        # 初始块保持在实例区和参数检查区之前，确保参数检查区可以固定收敛到模块末尾。
        if "initial_block" not in list_regions:

            # 参数检查区存在时，initial 必须插在参数检查区之前。
            if "parameter_check" in list_regions:

                # 参数检查区需要保持模块内部最后一区。
                list_regions.insert(list_regions.index("parameter_check"), "initial_block")

            # instance 区域存在时，initial 应插入到实例化之前。
            elif "instance_block" in list_regions:

                # 保持旧 formatter 对 initial 和 instance 的相对顺序。
                list_regions.insert(list_regions.index("instance_block"), "initial_block")

            # 没有 instance 区域时，把 initial 放在末尾作为兜底。
            else:

                # 追加到末尾能保留配置中已有区域的相对顺序。
                list_regions.append("initial_block")

        # 返回可供 body tree 逐区渲染的顺序列表。
        return list_regions

    # 单一区域调度只选择渲染路径，具体数据整理交给专用 helper。
    def _render_body_region(self, region: str, context: BodyRegionRenderContext) -> list[str]:
        """
        渲染 module body 中的一个逻辑区域。

        :param region: formatter 区域名。
        :param context: 当前 body 渲染的共享上下文。
        :return: 当前区域的源码行；区域为空时返回空列表。
        """

        # function/task 区域直接复用已有块渲染器。
        if region == "function_block":

            # list_function_lines 保留函数区域输出，横幅裁剪由统一出口处理。
            list_function_lines = self._render_function_region(context.items["functions"])  # function 块区域源码行

            # 返回时按调用模式决定是否移除区域横幅。
            return self._finalize_body_region(list_function_lines, context)

        # task 区域和 function 区域保持同样的包装策略。
        if region == "task_block":

            # list_task_lines 保存 task 区域完整输出。
            list_task_lines = self._render_task_region(context.items["tasks"])  # task 声明区域源码行

            # 调用方可能在条件编译块内部要求无横幅文本。
            return self._finalize_body_region(list_task_lines, context)

        # 参数区域需要先按状态参数规则筛选 localparam。
        if region in {"config_param", "state_param"}:

            # 委托参数区域 helper 保持主调度函数短小。
            return self._render_param_body_region(region, context)

        # 声明类区域共享实例亲缘和输出代理布局。
        if region in self._body_decl_regions():

            # 声明区域 helper 负责分组、布局传播和 output_internal 特例。
            return self._render_signal_decl_body_region(region, context)

        # assign 类区域需要同时考虑左值亲缘和右值来源。
        if region in {"other_assign", "output_assign"}:

            # assign 区域 helper 汇总连续 assign 与声明初始化 assign。
            return self._render_assign_body_region(region, context)

        # always 类区域按输出端口布局或任务类型分组。
        if region in {"output_always", "state_machine", "state_task", "main_task"}:

            # always helper 封装输出布局选择和横幅裁剪。
            return self._render_always_body_region(region, context)

        # generate 块保持 parser 原始结构顺序。
        if region == "generate_block":

            # list_generate_lines 保存 generate 区域输出。
            list_generate_lines = self._render_generate_region(context.items["generates"])  # generate 结构区域源码行

            # generate 区域返回前统一处理横幅。
            return self._finalize_body_region(list_generate_lines, context)

        # 参数检查 initial 独立于普通 initial 输出。
        if region == "parameter_check":

            # list_parameter_blocks 只保留参数检查用途的 initial 块。
            list_parameter_blocks: list[InitialBlock] = []  # 参数检查 initial 块列表

            # 显式循环避免推导式把筛选语义藏进一行。
            for block in context.items["initials"]:

                # 参数检查块进入独立的 parameter_check 区域。
                if block.block_kind == "parameter_check":

                    # 保留 parser 中该 initial 块的原始顺序。
                    list_parameter_blocks.append(block)

            # list_rendered_lines 保存参数检查区域输出。
            list_rendered_lines = self._render_initial_region(list_parameter_blocks, region)  # 参数检查区域行

            # 参数检查区域按调用模式返回。
            return self._finalize_body_region(list_rendered_lines, context)

        # 普通 initial 区域排除已经单列的参数检查块。
        if region == "initial_block":

            # list_initial_blocks 保存常规 initial 块。
            list_initial_blocks: list[InitialBlock] = []  # 常规 initial 块列表

            # 常规 initial 区域排除已经单独渲染的参数检查块。
            for block in context.items["initials"]:

                # 非参数检查块保持在普通 initial 区域。
                if block.block_kind != "parameter_check":

                    # 追加时不改变同类 initial 的输入顺序。
                    list_initial_blocks.append(block)

            # list_rendered_lines 保存普通 initial 区域输出。
            list_rendered_lines = self._render_initial_region(list_initial_blocks, region)  # 普通 initial 区域行

            # 普通 initial 区域按调用模式返回。
            return self._finalize_body_region(list_rendered_lines, context)

        # 实例化区域需要 raw block 协同输出。
        if region == "instance_block":

            # list_rendered_lines 保存实例化区域和原始块混排输出。
            list_rendered_lines = self._render_instance_region(  # 实例化区域源码行
                context.items["instances"],  # parser 捕获的实例化块
                context.items["raw_blocks"],  # 需要与实例保持相对顺序的原始块
            )

            # 实例化区域按调用模式返回。
            return self._finalize_body_region(list_rendered_lines, context)

        # 未知区域不输出内容，维持旧 formatter 的宽容行为。
        return []

    # body 区域统一出口负责横幅保留或裁剪。
    def _finalize_body_region(self, rendered_lines: list[str], context: BodyRegionRenderContext) -> list[str]:
        """
        根据当前调用模式返回带横幅或无横幅区域行。

        :param rendered_lines: 已渲染的区域源码行。
        :param context: 当前 body 渲染共享上下文。
        :return: 原始区域行或裁剪横幅后的区域行。
        """

        # 带横幅模式直接返回完整区域。
        if context.include_region_banners:

            # 上层负责把多个区域按顺序拼接。
            return rendered_lines

        # 无横幅模式用于条件编译分支嵌入。
        return self._trim_region_wrapper(rendered_lines)

    # 参数 body 区域根据状态参数识别结果拆分为配置参数和状态参数。
    def _render_param_body_region(self, region: str, context: BodyRegionRenderContext) -> list[str]:
        """
        渲染 body 中的参数声明区域。

        :param region: `config_param` 或 `state_param`。
        :param context: 当前 body 渲染共享上下文。
        :return: 参数区域源码行；无参数时返回空列表。
        """

        # bool_keep_state_params 决定当前区域接收状态编码还是普通配置参数。
        bool_keep_state_params = region != "config_param"  # 当前区域是否保留状态参数

        # list_params 保存当前参数区域最终可渲染的 localparam。
        list_params: list[ParamDecl] = []  # 当前参数区域声明列表

        # 显式筛选便于注释说明 config/state 两类参数的分流规则。
        for param in context.items["localparams"]:

            # bool_is_state_param 标记当前 localparam 是否属于状态编码。
            bool_is_state_param = self._is_state_param(param.name)  # 当前参数是否为状态编码

            # 区域选择结果与参数类型一致时保留。
            if bool_is_state_param == bool_keep_state_params:

                # 保留输入顺序，避免参数声明重排。
                list_params.append(param)

        # list_rendered_lines 交给参数区域 renderer 输出横幅和声明。
        list_rendered_lines = self._render_param_region(region, list_params)  # 参数区域行

        # 参数区域返回前统一处理横幅。
        return self._finalize_body_region(list_rendered_lines, context)

    # 声明类 body 区域集合集中声明，供调度和门禁共用。
    def _body_decl_regions(self) -> set[str]:
        """
        返回所有声明类 body 区域名。

        :param self: 使用当前类的固定声明区域协议，无外部业务参数。
        :return: 可由声明区域 helper 处理的区域集合。
        """

        # list_decl_regions 避免调度函数内出现长字面量。
        list_decl_regions = [  # 声明类区域名列表
            "instance_signal",  # 实例端口亲缘声明区
            "counter_signal",  # 计数器声明区
            "state_signal",  # 状态编码声明区

            # 过程寄存器和控制类信号按语义分区。
            "register_signal",  # 过程寄存器声明区
            "flag_signal",  # 控制标志声明区
            "encoder_signal",  # 编码逻辑声明区

            # 编解码尾部和兜底声明区保持在声明类集合内。
            "decoder_signal",  # 解码逻辑声明区
            "other_signal",  # 普通内部信号声明区
            "output_internal",  # 输出代理声明区
        ]

        # 返回独立集合，调用方不会修改类级共享状态。
        return set(list_decl_regions)

    # 声明 body 区域负责按输出代理、实例亲缘和传播布局分组。
    def _render_signal_decl_body_region(self, region: str, context: BodyRegionRenderContext) -> list[str]:
        """
        渲染 body 中的信号声明类区域。

        :param region: 声明区域名。
        :param context: 当前 body 渲染共享上下文。
        :return: 声明区域源码行；区域为空时返回空列表。
        """

        # set_output_signal_names 记录输出代理信号，用于声明分组时排除桥接目标。
        set_output_signal_names = set(context.output_internal_layouts)  # 输出内部信号名集合

        # dict_instance_signal_layouts 从实例端口和 always/assign 传播中识别亲缘布局。
        dict_instance_signal_layouts = self._collect_instance_signal_layouts(  # 实例信号布局表
            context.items["decls"],  # body 中的信号声明
            context.items["assigns"],  # body 中的连续赋值
            context.items["always"],  # body 中的过程块
            context.items,  # 实例亲缘分析使用的完整 body 分组
        )

        # dict_register_signal_layouts 捕获被实例输出传播到寄存器区域的信号。
        dict_register_signal_layouts = self._collect_propagated_signal_layouts(  # 寄存器信号布局表
            context.items["decls"],  # 传播分析使用的声明
            context.items["assigns"],  # 传播分析使用的 assign
            context.items["always"],  # 传播分析使用的过程块
            dict_instance_signal_layouts,  # 已知实例亲缘布局
            set_output_signal_names,  # 需要排除的输出代理
            target_region="register_signal",  # 传播目标限定为寄存器区域
        )

        # dict_other_signal_layouts 捕获其它内部连线的实例亲缘。
        dict_other_signal_layouts = self._collect_propagated_signal_layouts(  # 其它信号布局表
            context.items["decls"],  # 普通信号传播声明输入
            context.items["assigns"],  # 普通信号传播赋值输入
            context.items["always"],  # 普通信号传播过程块输入
            dict_instance_signal_layouts,  # 传播起点布局表
            set_output_signal_names,  # 输出代理信号过滤集合
            target_region="other_signal",  # 传播目标限定为普通信号区域
        )

        # dict_grouped_decls 将声明按 formatter 区域和输出代理规则分类。
        dict_grouped_decls = self._group_declarations(  # 声明区域分组结果
            context.items["decls"],  # 待分组的全部声明
            set_output_signal_names,  # 输出代理信号名集合
            context.output_internal_layouts,  # 输出代理布局表
            dict_instance_signal_layouts,  # 普通 assign 继承的实例亲缘布局
        )

        # 输出内部信号区域使用专门 renderer 保持端口桥接布局。
        if region == "output_internal":

            # list_output_internal_lines 承接 output_internal 区域渲染结果。
            list_output_internal_lines = self._render_output_internal_region(  # 输出内部声明区域源码行
                dict_grouped_decls.get(region, []),  # 当前区域内的输出代理声明
                context.output_internal_layouts,  # 输出代理信号布局表
            )

            # output_internal 区域按调用模式返回。
            return self._finalize_body_region(list_output_internal_lines, context)

        # dict_region_layouts 只在部分声明区域需要标签分组。
        dict_region_layouts = self._select_signal_region_layouts(  # 当前声明区域布局表
            region,  # 当前待渲染的声明区域
            dict_instance_signal_layouts,  # 实例端口直接亲缘布局
            dict_register_signal_layouts,  # 寄存器传播后的亲缘布局
            dict_other_signal_layouts,  # 普通信号传播后的亲缘布局
        )

        # list_decl_region_lines 保存普通声明区域输出。
        list_decl_region_lines = self._render_decl_region(  # 普通信号声明区域源码行
            region,  # 当前普通声明区域名
            dict_grouped_decls.get(region, []),  # 当前区域内的声明列表
            dict_region_layouts,  # 当前区域可用的自动标签布局
        )

        # 声明区域按调用模式返回。
        return self._finalize_body_region(list_decl_region_lines, context)

    # 声明区域标签布局只对实例、寄存器和其它信号区域生效。
    def _select_signal_region_layouts(
        self,
        region: str,
        instance_signal_layouts: dict[str, InstanceSignalLayout],
        register_signal_layouts: dict[str, InstanceSignalLayout],
        other_signal_layouts: dict[str, InstanceSignalLayout],
    ) -> dict[str, InstanceSignalLayout] | None:
        """
        选择当前声明区域的实例亲缘布局表。

        :param region: 声明区域名。
        :param instance_signal_layouts: 实例端口直接关联的信号布局。
        :param register_signal_layouts: 传播到寄存器区域的信号布局。
        :param other_signal_layouts: 传播到其它信号区域的信号布局。
        :return: 当前区域可用的布局表；无需标签时返回 `None`。
        """

        # 实例信号直接使用实例布局。
        if region == "instance_signal":

            # 返回实例信号亲缘映射。
            return instance_signal_layouts

        # 寄存器信号使用传播后的寄存器布局。
        if region == "register_signal":

            # 返回寄存器信号亲缘映射。
            return register_signal_layouts

        # 其它信号使用传播后的普通连线布局。
        if region == "other_signal":

            # 返回其它信号亲缘映射。
            return other_signal_layouts

        # 其它声明区域不需要自动标签。
        return None

    # assign body 区域汇总连续 assign 和声明初始化形式的 assign。
    def _render_assign_body_region(self, region: str, context: BodyRegionRenderContext) -> list[str]:
        """
        渲染 body 中的连续赋值区域。

        :param region: `other_assign` 或 `output_assign`。
        :param context: 当前 body 渲染共享上下文。
        :return: assign 区域源码行；区域为空时返回空列表。
        """

        # dict_instance_signal_layouts 为 ordinary assign 提供左值亲缘标签。
        dict_instance_signal_layouts = self._collect_instance_signal_layouts(  # assign 左值实例布局表
            context.items["decls"],  # assign 布局使用的声明输入
            context.items["assigns"],  # assign 布局使用的赋值输入
            context.items["always"],  # assign 布局使用的过程块输入
            context.items,  # 完整 body 分组
        )

        # dict_other_signal_layouts 合并实例直接布局和传播布局，覆盖普通 assign 左值。
        dict_other_signal_layouts = self._collect_other_assign_signal_layouts(  # 普通 assign 左值布局
            context,  # 当前 body 渲染上下文
            dict_instance_signal_layouts,  # 实例信号亲缘布局
        )

        # dict_source_layouts 记录普通 assign 右值来自哪个端口区段。
        dict_source_layouts = self._collect_other_assign_source_layouts(context)  # 普通 assign 右值来源布局

        # tuple_partitioned_assigns 按输出端口桥接关系拆分 ordinary/output assign。
        tuple_partitioned_assigns = self._partition_assigns(  # 普通与输出 assign 拆分结果
            context.items["assigns"],  # 待拆分的连续 assign
            context.ports,  # 输出端口识别依据
            context.output_internal_names,  # 输出内部代理映射
        )

        # list_other_assigns 保存普通 assign 区域的候选语句。
        list_other_assigns = list(tuple_partitioned_assigns[0])  # 普通 assign 候选

        # list_output_assigns 保存 output_assign 区域的候选语句。
        list_output_assigns = list(tuple_partitioned_assigns[1])  # 输出 assign 候选

        # 条件编译分支内部不重复补顶层输出桥接 assign。
        if not context.include_output_bridges:

            # list_output_assigns 移除已由条件桥接规则覆盖的输出 assign。
            list_output_assigns = self._filter_output_bridge_assigns(list_output_assigns, context)  # 去桥接输出 assign

        # dict_grouped_decls 找出声明语句中携带初始化的 assign 形式。
        dict_grouped_decls = self._group_declarations(  # 声明初始化分组结果
            context.items["decls"],  # 可能携带初始化的声明
            set(context.output_internal_layouts),  # 输出代理信号集合
            context.output_internal_layouts,  # 声明初始化分组用的输出代理布局
            dict_instance_signal_layouts,  # 声明初始化分组用的实例亲缘布局
        )

        # list_other_assigns 合并声明初始化中的普通 assign。
        list_other_assigns = self._extend_assigns_from_group(  # 合并后的普通 assign
            list_other_assigns,  # 已有普通 assign
            dict_grouped_decls.get("other_assign", []),  # 普通初始化声明
        )

        # list_output_assigns 合并声明初始化中的输出 assign。
        list_output_assigns = self._extend_assigns_from_group(  # 合并后的输出 assign
            list_output_assigns,  # 已有输出 assign
            dict_grouped_decls.get("output_assign", []),  # 输出初始化声明
        )

        # list_selected_assigns 根据当前区域选择最终输出集合。
        list_selected_assigns = list_other_assigns if region == "other_assign" else list_output_assigns  # 当前区域 assign

        # list_assign_region_lines 承接当前 assign 区域渲染结果。
        list_assign_region_lines = self._render_assign_region(  # assign 区域源码行
            region,  # 当前 assign 区域名
            list_selected_assigns,  # 当前区域最终 assign 列表
            None if region == "other_assign" else context.output_target_layouts,  # output_assign 专用端口布局
            dict_other_signal_layouts if region == "other_assign" else None,  # other_assign 左值布局
            dict_source_layouts if region == "other_assign" else None,  # other_assign 右值来源布局
        )

        # 当前 assign 区域按调用模式返回。
        return self._finalize_body_region(list_assign_region_lines, context)

    # ordinary assign 左值布局来自实例亲缘和传播结果的合并。
    def _collect_other_assign_signal_layouts(
        self,
        context: BodyRegionRenderContext,
        instance_signal_layouts: dict[str, InstanceSignalLayout],
    ) -> dict[str, InstanceSignalLayout]:
        """
        收集普通 assign 左值侧可使用的布局信息。

        :param context: 当前 body 渲染共享上下文。
        :param instance_signal_layouts: 实例端口直接关联的信号布局。
        :return: 普通 assign 左值名到实例布局的映射。
        """

        # dict_signal_layouts 以实例直接布局为基础。
        dict_signal_layouts = dict(instance_signal_layouts)  # 普通 assign 左值布局表

        # 寄存器传播布局补充进入 ordinary assign 分组依据。
        dict_signal_layouts.update(
            self._collect_propagated_signal_layouts(
                context.items["decls"],
                context.items["assigns"],
                context.items["always"],
                instance_signal_layouts,
                set(context.output_internal_layouts),
                target_region="register_signal",
            )
        )

        # 其它信号传播布局覆盖剩余内部连线。
        dict_signal_layouts.update(
            self._collect_propagated_signal_layouts(
                context.items["decls"],
                context.items["assigns"],
                context.items["always"],
                instance_signal_layouts,
                set(context.output_internal_layouts),
                target_region="other_signal",
            )
        )

        # 返回完整左值布局表。
        return dict_signal_layouts

    # ordinary assign 右值来源布局从端口和传播关系中推导。
    def _collect_other_assign_source_layouts(self, context: BodyRegionRenderContext) -> dict[str, AssignSourceLayout]:
        """
        收集普通 assign 右值侧的端口来源布局。

        :param context: 当前 body 渲染共享上下文。
        :return: 信号名到来源端口布局的映射。
        """

        # dict_port_layouts 记录输入端口自身的来源区段。
        dict_port_layouts = self._collect_port_assign_source_layouts(context.ports)  # 端口来源布局表

        # dict_source_layouts 以端口布局为基础，再加入 assign/always 传播结果。
        dict_source_layouts = dict(dict_port_layouts)  # 普通 assign 右值来源表

        # 传播布局让内部信号也能继承输入端口区段。
        dict_source_layouts.update(
            self._collect_propagated_assign_source_layouts(
                context.items["decls"],
                context.items["assigns"],
                context.items["always"],
                dict_port_layouts,
            )
        )

        # 返回完整右值来源布局表。
        return dict_source_layouts

    # 条件分支渲染时过滤已经由输出桥接规则提供的 assign。
    def _filter_output_bridge_assigns(
        self,
        output_assigns: list[AssignStmt],
        context: BodyRegionRenderContext,
    ) -> list[AssignStmt]:
        """
        移除条件编译分支中重复的输出桥接 assign。

        :param output_assigns: 输出 assign 候选列表。
        :param context: 当前 body 渲染共享上下文。
        :return: 去除重复桥接后的输出 assign 列表。
        """

        # set_bridge_assigns 保存已知的输出端口桥接关系。
        set_bridge_assigns = self._conditional_output_bridge_assigns(  # 条件输出桥接集合
            context.ports,  # 输出端口列表
            context.output_internal_names,  # 条件桥接使用的输出代理映射
        )

        # list_filtered_assigns 只保留不属于桥接关系的 assign。
        list_filtered_assigns: list[AssignStmt] = []  # 去重后的输出 assign

        # 显式计算 lhs/rhs 基名，避免推导式掩盖桥接判定。
        for assign in output_assigns:

            # str_lhs_base 是 output assign 左值的规范化基名。
            str_lhs_base = self._extract_simple_signal_base(assign.lhs, "lvalue_normalization_violation")  # 输出 assign 左值基名

            # str_rhs_base 是 output assign 右值的规范化基名。
            str_rhs_base = self._extract_simple_signal_base(assign.rhs, "lvalue_normalization_violation")  # 输出 assign 右值基名

            # tuple_bridge_key 与条件输出桥接集合保持同一比较形态。
            tuple_bridge_key = (str_lhs_base, str_rhs_base)  # 当前 assign 桥接键

            # 不属于桥接集合的 assign 才继续渲染。
            if tuple_bridge_key not in set_bridge_assigns:

                # 保留原始 assign 对象，避免影响后续注释和 delay 信息。
                list_filtered_assigns.append(assign)

        # 返回可继续渲染的 output_assign 候选。
        return list_filtered_assigns

    # 声明初始化形式的 assign 需要转成统一 AssignStmt。
    def _extend_assigns_from_group(
        self,
        base_assigns: list[AssignStmt],
        decls: list[SignalDecl],
    ) -> list[AssignStmt]:
        """
        把声明初始化中的 assign 语义追加到已有 assign 列表。

        :param base_assigns: 已解析出的连续 assign 列表。
        :param decls: 当前区域中可能带初始化的声明列表。
        :return: 合并声明初始化 assign 后的新列表。
        """

        # list_decl_assigns 保存由声明初始化转换出的 assign。
        list_decl_assigns: list[AssignStmt] = []  # 声明初始化 assign 列表

        # 只有 kind=assign 的声明才具备连续赋值语义。
        for decl in decls:

            # assign 声明初始化需要转换成统一 AssignStmt。
            if decl.kind == "assign":

                # obj_decl_assign 保留声明名、初始化表达式和原有注释。
                assign_stmt_init_decl = AssignStmt(  # 声明初始化 assign 对象
                    decl.name,  # 声明名作为 assign 左值
                    decl.init,  # 初始化表达式作为 assign 右值
                    decl.comment,  # 声明尾注释迁移到 assign
                    list(decl.leading_comments),  # 声明前导注释副本
                )  # 声明初始化转换后的 assign

                # 追加到转换结果，后续与普通 assign 合并。
                list_decl_assigns.append(assign_stmt_init_decl)

        # 返回新列表，避免修改调用方传入的原列表对象。
        return [*base_assigns, *list_decl_assigns]

    # always body 区域根据输出 always 或普通过程块选择布局。
    def _render_always_body_region(self, region: str, context: BodyRegionRenderContext) -> list[str]:
        """
        渲染 body 中的 always/过程块类区域。

        :param region: always 类区域名。
        :param context: 当前 body 渲染共享上下文。
        :return: always 区域源码行；区域为空时返回空列表。
        """

        # dict_grouped_always 按输出端口目标和状态机任务规则分类 always 块。
        dict_grouped_always = self._group_always(  # always 分组结果
            context.items["always"],  # body 中的过程块列表
            set(context.output_target_layouts),  # always 输出目标端口集合
        )

        # dict_output_layouts 只对 output_always 区域生效。
        dict_output_layouts = context.output_target_layouts if region == "output_always" else None  # 输出 always 布局

        # list_always_region_lines 保存当前 always 区域输出。
        list_always_region_lines = self._render_always_region(  # 过程块区域源码行
            region,  # 待渲染的 always 区域名
            dict_grouped_always[region],  # 当前区域内的 always 块
            dict_output_layouts,  # output_always 区域专用布局
        )

        # 当前过程块区域按调用模式返回。
        return self._finalize_body_region(list_always_region_lines, context)

    # 输出端口桥接赋值集合用于条件编译分支中避免重复补桥接 assign。
    def _conditional_output_bridge_assigns(
        self,
        ports: list[PortDecl],
        output_internal_names: dict[str, str],
    ) -> set[tuple[str, str]]:
        """
        计算输出端口到内部信号的桥接赋值集合。

        :param ports: module 端口声明列表。
        :param output_internal_names: 输出端口名到内部代理信号名的映射。
        :return: 已归一化的 `(lhs, rhs)` 桥接赋值集合。
        """

        # set_bridges 记录已归一化的输出桥接关系，便于后续排除重复 assign。
        set_bridges: set[tuple[str, str]] = set()  # 输出桥接赋值集合

        # 输出桥接 assign 由端口和内部信号映射共同推导。
        for assign in self._build_output_assigns(ports, output_internal_names):

            # str_lhs_base 保留左值信号基名，忽略位选和简单包装。
            str_lhs_base = self._extract_simple_signal_base(assign.lhs, "lvalue_normalization_violation")  # 左侧信号基名

            # str_rhs_base 保留右值信号基名，和左值共同形成去重键。
            str_rhs_base = self._extract_simple_signal_base(assign.rhs, "lvalue_normalization_violation")  # 右侧信号基名

            # 只有两侧都能归一化时才参与桥接去重。
            if str_lhs_base and str_rhs_base:

                # 记录桥接方向，后续条件分支会用同一方向过滤。
                set_bridges.add((str_lhs_base, str_rhs_base))

        # 返回条件编译分支可复用的桥接关系集合。
        return set_bridges

    # 非空 chunk 才能进入最终扁平化队列。
    def _append_chunk(self, chunks: list[list[str]], lines: list[str]) -> None:
        """
        将非空行块追加到 chunk 队列。

        :param chunks: 调用方维护的区域行块列表。
        :param lines: 待过滤空行的候选行块。
        :return: 无业务返回值，函数会原地更新 `chunks`。
        """

        # list_chunk 删除纯空行，避免空区域在扁平化阶段制造分隔。
        list_chunk = [line for line in lines if line != ""]  # 已过滤空行的候选块

        # 非空块才追加，保持后续块间分隔只由真实内容触发。
        if list_chunk:

            # 调用方持有 chunks，因此这里原地追加。
            chunks.append(list_chunk)

    # 将若干区域块展开成带单空行分隔的源码行。
    def _flatten_chunks(self, chunks: list[list[str]]) -> list[str]:
        """
        合并多个非空区域块。

        :param chunks: 已按输出顺序收集的区域行块。
        :return: 块间用单个空行分隔后的源码行列表。
        """

        # list_lines 保存最终扁平化后的源码行。
        list_lines: list[str] = []  # 扁平化后的输出行

        # 块顺序由上游区域排序决定，这里只负责插入块间空行。
        for chunk in chunks:

            # 空块不应影响输出空行数量。
            if not chunk:

                # 继续检查下一个候选块。
                continue

            # 已有内容时，在新块前插入单个分隔空行。
            if list_lines:

                # 分隔空行保持不同 Verilog 区域的可读边界。
                list_lines.append("")

            # 当前 chunk 的行按原顺序追加。
            list_lines.extend(chunk)

        # 返回完整扁平化行列表。
        return list_lines

    # 预处理条件块内部复用 body tree 渲染，但不重复输出区域横幅。
    def _render_conditional_block(
        self,
        conditional: PreprocessorConditional,
        ports: list[PortDecl],
        output_internal_names: dict[str, str],
        output_internal_layouts: dict[str, OutputSignalLayout],
        output_target_layouts: dict[str, OutputSignalLayout],
    ) -> list[str]:
        """
        渲染单个预处理条件分支块。

        :param conditional: parser 识别出的 `ifdef` / `ifndef` 条件结构。
        :param ports: module 端口声明列表。
        :param output_internal_names: 输出端口名到内部代理信号名的映射。
        :param output_internal_layouts: 输出内部信号的布局信息。
        :param output_target_layouts: 输出目标端口的布局信息。
        :return: 完整条件编译块行；无有效内容时返回空列表。
        """

        # list_true_lines 渲染 true 分支内部 body，不携带区域横幅。
        list_true_lines = self._render_embedded_body_tree(  # 条件成立分支源码行
            conditional.true_items,  # 条件成立分支结构化 body
            ports,  # 条件块所在 module 的端口上下文
            output_internal_names,  # true 分支可复用的输出代理名
            output_internal_layouts,  # true 分支输出代理布局
            output_target_layouts,  # true 分支输出目标布局
        )

        # list_false_lines 渲染条件未成立分支内部 body，不携带区域横幅。
        list_false_lines = self._render_embedded_body_tree(  # 条件 else 分支源码行
            conditional.false_items,  # else 分支结构化 body
            ports,  # 条件块共享的端口上下文
            output_internal_names,  # else 分支继承的输出代理名
            output_internal_layouts,  # else 分支继承的代理布局
            output_target_layouts,  # else 分支继承的端口布局
        )

        # 两个分支都没有内容且没有显式 else 时，条件块无需输出。
        if not list_true_lines and not list_false_lines and not conditional.has_else:

            # 空条件块交由调用方跳过。
            return []

        # list_lines 从条件块前导注释开始累积。
        list_lines = list(self._render_leading_comments(conditional.leading_comments, 1))  # 条件块输出行

        # 条件编译指令行保持 parser 捕获的 directive 和 symbol。
        list_lines.append(f"{self._indent(1)}`{conditional.directive} {conditional.symbol}")

        # true 分支内容紧随条件指令输出。
        list_lines.extend(list_true_lines)

        # 显式 else 需要保留，即使 else 分支当前没有可渲染内容。
        if conditional.has_else:

            # else 指令作为 false 分支的边界。
            list_lines.append(f"{self._indent(1)}`else")

            # false 分支内容保持原有区域顺序。
            list_lines.extend(list_false_lines)

        # 条件块以 endif 闭合，和 Verilog 预处理语法对应。
        list_lines.append(f"{self._indent(1)}`endif")

        # 返回完整条件编译块。
        return list_lines

    # 嵌入条件编译块的 body 子树不输出区域横幅或顶层桥接。
    def _render_embedded_body_tree(
        self,
        items: dict[str, list],
        ports: list[PortDecl],
        output_internal_names: dict[str, str],
        output_internal_layouts: dict[str, OutputSignalLayout],
        output_target_layouts: dict[str, OutputSignalLayout],
    ) -> list[str]:
        """
        渲染条件编译分支中的嵌入式 body 子树。

        :param items: 条件分支中的结构化 body 分组。
        :param ports: module 端口声明列表。
        :param output_internal_names: 输出端口名到内部代理信号名的映射。
        :param output_internal_layouts: 输出内部信号布局信息。
        :param output_target_layouts: 输出目标端口布局信息。
        :return: 不含区域横幅和额外桥接的分支源码行。
        """

        # list_branch_lines 是嵌入式分支的主体渲染结果。
        list_branch_lines = self._render_body_tree(  # 嵌入式 body 子树源码行
            items,  # 条件分支 body 分组
            ports,  # 条件分支共享端口列表
            output_internal_names,  # 条件分支输出代理映射

            # 嵌入式分支的布局参数和关闭开关分开排列。
            output_internal_layouts,  # 条件分支内部代理布局
            output_target_layouts,  # 条件分支输出端口布局
            include_output_bridges=False,  # 分支内不重复补桥接
            include_region_banners=False,  # 分支内不重复输出横幅
        )

        # 返回不含顶层装饰的分支行。
        return list_branch_lines

    # 条件编译块挂靠到其内部最早出现的真实 body 区域。
    def _body_tree_anchor_region(
        self,
        items: dict[str, list],
        ports: list[PortDecl],
        output_internal_names: dict[str, str],
        output_internal_layouts: dict[str, OutputSignalLayout],
        output_target_layouts: dict[str, OutputSignalLayout],
    ) -> str | None:
        """
        为条件编译子树选择最早的 body 区域锚点。

        :param items: 当前 body 或条件分支中的结构化 Verilog 项。
        :param ports: module 端口声明列表。
        :param output_internal_names: 输出端口名到内部代理信号名的映射。
        :param output_internal_layouts: 输出内部信号布局信息。
        :param output_target_layouts: 输出目标端口布局信息。
        :return: 应挂靠的区域名；完全无内容时返回 `None`。
        """

        # list_region_order 包含 prologue 锚点和所有可渲染 body 区域。
        list_region_order = [self._body_prologue_anchor(), *self._body_render_regions()]  # 锚点候选顺序

        # dict_region_ranks 让嵌套分支可以按同一排序挑最早区域。
        dict_region_ranks = {region: index for index, region in enumerate(list_region_order)}  # 区域排序权重

        # context_anchor 使用无横幅、无额外桥接的方式探测当前子树区域内容。
        context_anchor = BodyRegionRenderContext(  # 锚点探测渲染上下文
            items=items,  # 待探测的 body 分组
            ports=ports,  # 锚点探测共享端口列表
            output_internal_names=output_internal_names,  # 锚点探测输出代理映射

            # 锚点探测只需要布局信息，不输出真实区域装饰。
            output_internal_layouts=output_internal_layouts,  # 锚点探测代理布局
            output_target_layouts=output_target_layouts,  # 锚点探测端口布局
            include_output_bridges=False,  # 锚点探测不生成桥接
            include_region_banners=False,  # 锚点探测不输出横幅
        )

        # 先检查当前分支自己的常规区域。
        for region in list_region_order[1:]:

            # 当前区域如果能渲染出内容，就作为条件块挂靠点。
            if self._render_body_region(region, context_anchor):

                # 返回最早有内容的区域名。
                return region

        # list_nested_regions 收集嵌套条件分支中可见的最早区域。
        list_nested_regions: list[str] = []  # 有效嵌套锚点列表

        # 嵌套条件的 true/false 分支都可能提供当前条件块的挂靠区域。
        for conditional in items["conditionals"]:

            # tuple_branch_items 保持 true 分支优先于 false 分支的扫描顺序。
            tuple_branch_items = (conditional.true_items, conditional.false_items)  # 嵌套分支 body 对

            # 分别探测两个分支，完全空的结果不加入候选。
            for branch_items in tuple_branch_items:

                # str_nested_region 是当前嵌套分支最早出现内容的区域。
                str_nested_region = self._body_tree_anchor_region(  # 嵌套分支锚点
                    branch_items,  # 当前嵌套分支 body
                    ports,  # 嵌套探测共享端口列表
                    output_internal_names,  # 嵌套探测输出代理映射
                    output_internal_layouts,  # 嵌套探测代理布局
                    output_target_layouts,  # 嵌套探测端口布局
                )

                # 非空锚点才参与全局顺序比较。
                if str_nested_region is not None:

                    # 保留候选区域名，后续用统一 rank 选最早者。
                    list_nested_regions.append(str_nested_region)

        # 嵌套分支有内容时，选择全局顺序最靠前的区域。
        if list_nested_regions:

            # min 使用区域权重保持和顶层区域顺序一致。
            return min(list_nested_regions, key=dict_region_ranks.__getitem__)

        # body prologue 单独存在时也需要挂靠到 prologue 锚点。
        if items["preprocessor_prologue"]:

            # 返回 prologue 哨兵，调用方会把条件块放到正文前导附近。
            return self._body_prologue_anchor()

        # 完全无可渲染内容时，不为条件块选择锚点。
        return None

    # body tree 渲染负责把普通区域和条件编译块重新交织成稳定顺序。
    def _render_body_tree(
        self,
        items: dict[str, list],
        ports: list[PortDecl],
        output_internal_names: dict[str, str],

        # 布局参数和开关参数分开，保持函数签名可扫描。
        output_internal_layouts: dict[str, OutputSignalLayout],
        output_target_layouts: dict[str, OutputSignalLayout],

        # include_* 只能用关键字传入，避免和多个布局参数错位。
        *,
        include_output_bridges: bool,
        include_region_banners: bool,
    ) -> list[str]:
        """
        渲染 module body 的完整区域树。

        :param items: parser 输出的 module body 分组字典。
        :param ports: module 端口声明列表。
        :param output_internal_names: 输出端口名到内部代理信号名的映射。
        :param output_internal_layouts: 输出内部信号布局信息。
        :param output_target_layouts: 输出目标端口布局信息。
        :param include_output_bridges: 是否在输出区域补充端口桥接 assign。
        :param include_region_banners: 是否输出区域横幅。
        :return: 按 formatter 区域顺序拼接好的 body 源码行。
        """

        # list_region_order 是常规 body 区域的输出顺序。
        list_region_order = self._body_render_regions()  # 常规 body 区域顺序

        # list_anchor_order 在常规区域前加入 preprocessor prologue 锚点。
        list_anchor_order = [self._body_prologue_anchor(), *list_region_order]  # 条件块挂靠顺序

        # dict_anchored_conditionals 按挂靠区域收集条件编译块。
        dict_anchored_conditionals: dict[str, list[list[str]]] = {  # 按区域挂靠的条件块
            region: []  # 当前区域暂存的条件块列表
            for region in list_anchor_order  # 可挂靠区域名
        }

        # 条件编译块先渲染成无横幅片段，再挂到最早相关区域。
        for conditional in items["conditionals"]:

            # str_true_anchor 是条件成立分支的挂靠区域。
            str_true_anchor = self._body_tree_anchor_region(  # true 分支锚点
                conditional.true_items,  # 条件成立分支 body
                ports,  # 顶层 body 端口上下文
                output_internal_names,  # 顶层输出代理映射
                output_internal_layouts,  # 顶层代理布局
                output_target_layouts,  # 顶层输出端口布局
            )

            # str_false_anchor 记录条件未成立分支的正文锚点。
            str_false_anchor = self._body_tree_anchor_region(  # 条件未成立分支锚点
                conditional.false_items,  # 条件 else 分支 body
                ports,  # else 分支共享端口上下文
                output_internal_names,  # else 分支输出代理映射
                output_internal_layouts,  # else 分支代理布局
                output_target_layouts,  # else 分支输出端口布局
            )

            # list_candidate_anchors 只保留实际有内容的分支锚点。
            list_candidate_anchors: list[str] = []  # 条件块候选挂靠区域

            # true 分支有内容时优先进入候选列表。
            if str_true_anchor is not None:

                # true 分支候选保持在 false 分支之前。
                list_candidate_anchors.append(str_true_anchor)

            # false 分支有内容时同样参与区域顺序比较。
            if str_false_anchor is not None:

                # false 分支候选用于覆盖只有 else 有内容的条件块。
                list_candidate_anchors.append(str_false_anchor)

            # str_anchor 选择 true/false 分支中最早出现内容的区域。
            str_anchor: str | None = None  # 条件块最终挂靠区域

            # 只有存在候选区域时才计算最小 rank。
            if list_candidate_anchors:

                # 按全局区域顺序挑选最靠前的分支锚点。
                str_anchor = min(list_candidate_anchors, key=list_anchor_order.index)  # 最早可见锚点

            # list_rendered_conditional 保存完整条件编译块行。
            list_rendered_conditional = self._render_conditional_block(  # 条件编译块源码行
                conditional,  # 当前条件编译结构
                ports,  # 条件块共享端口列表
                output_internal_names,  # 条件块输出代理映射
                output_internal_layouts,  # 条件块代理布局
                output_target_layouts,  # 条件块输出目标布局
            )

            # 有挂靠区域且条件块非空时才进入输出队列。
            if str_anchor is not None and list_rendered_conditional:

                # 条件块保持同区域内的原始扫描顺序。
                dict_anchored_conditionals[str_anchor].append(list_rendered_conditional)

        # 带横幅模式直接累积完整 body 行。
        if include_region_banners:

            # context_region 使用带横幅模式渲染顶层 body 区域。
            context_region = BodyRegionRenderContext(  # 带横幅 body 渲染上下文
                items=items,  # 顶层 body 分组
                ports=ports,  # 顶层端口列表
                output_internal_names=output_internal_names,  # 顶层 output 代理映射

                # 顶层布局参数用于输出代理和 output always 分组。
                output_internal_layouts=output_internal_layouts,  # 顶层输出代理声明布局
                output_target_layouts=output_target_layouts,  # 顶层 output 端口布局
                include_output_bridges=include_output_bridges,  # 顶层桥接开关
                include_region_banners=True,  # 顶层区域横幅开关
            )

            # list_lines 先接收 body 前导预处理块。
            list_lines = list(self._render_preprocessor_prologue(items["preprocessor_prologue"]))  # 带横幅 body 输出行

            # prologue 锚点下的条件块紧跟预处理前导输出。
            for list_conditional_lines in dict_anchored_conditionals[self._body_prologue_anchor()]:

                # 条件块按完整行片段追加。
                list_lines.extend(list_conditional_lines)

                # 条件块之后保留空行，和区域横幅前间隔一致。
                list_lines.append("")

            # 常规区域按 formatter 顺序输出。
            for region in list_region_order:

                # 当前区域渲染带横幅版本。
                list_lines.extend(self._render_body_region(region, context_region))

                # 挂靠到当前区域的条件编译块跟随该区域输出。
                for list_conditional_lines in dict_anchored_conditionals[region]:

                    # 条件块保持完整行片段。
                    list_lines.extend(list_conditional_lines)

                    # 条件块后保留空行，便于后续区域接续。
                    list_lines.append("")

            # 返回带区域横幅的完整 body 行。
            return list_lines

        # list_chunks 收集无横幅路径中的非空片段。
        list_chunks: list[list[str]] = []  # 无横幅 body 片段队列

        # context_region 使用无横幅模式渲染嵌入条件块的 body 区域。
        context_region = BodyRegionRenderContext(  # 无横幅 body 渲染上下文
            items=items,  # 嵌入式 body 分组
            ports=ports,  # 嵌入式端口列表
            output_internal_names=output_internal_names,  # 嵌入式输出代理映射

            # 嵌入式布局参数用于条件分支内部排序。
            output_internal_layouts=output_internal_layouts,  # 嵌入式代理声明布局
            output_target_layouts=output_target_layouts,  # 嵌入式输出端口布局
            include_output_bridges=include_output_bridges,  # 嵌入式桥接开关
            include_region_banners=False,  # 嵌入式区域横幅开关
        )

        # body prologue 先裁剪尾随空行后进入 chunk 队列。
        self._append_chunk(
            list_chunks,
            self._trim_trailing_blank_lines(
                self._render_preprocessor_prologue(items["preprocessor_prologue"])
            ),
        )

        # prologue 锚点下的条件块作为独立 chunk 输出。
        for list_conditional_lines in dict_anchored_conditionals[self._body_prologue_anchor()]:

            # 空条件块由 _append_chunk 过滤。
            self._append_chunk(list_chunks, list_conditional_lines)

        # 无横幅模式按区域收集主体 chunk。
        for region in list_region_order:

            # 当前区域使用无横幅渲染结果。
            self._append_chunk(
                list_chunks,
                self._render_body_region(region, context_region),
            )

            # 当前区域挂靠的条件块同样作为独立 chunk 插入。
            for list_conditional_lines in dict_anchored_conditionals[region]:

                # 保持条件块内部行顺序。
                self._append_chunk(list_chunks, list_conditional_lines)

        # 扁平化所有非空 chunk，并用单空行分隔。
        return self._flatten_chunks(list_chunks)

    # 输出内部信号区域需要按输出端口布局插入分组标签。
    def _render_output_internal_region(
        self,
        decls: list[SignalDecl],
        output_signal_layouts: dict[str, OutputSignalLayout],
    ) -> list[str]:
        """
        渲染输出端口内部代理信号声明区域。

        :param decls: 已分到 output_internal 区域的信号声明。
        :param output_signal_layouts: 内部信号名到输出端口布局的映射。
        :return: 带区域横幅和尾随空行的输出内部声明行。
        """

        # 没有输出内部声明时不输出区域横幅。
        if not decls:

            # 空区域交由调用方跳过。
            return []

        # list_lines 收集 output_internal 区域横幅和声明行。
        list_lines = [f"{self._indent(1)}{self.REGION_TITLES['output_internal']}"]  # 输出内部声明区域行

        # str_current_label 记录上一条输出布局标签。
        str_current_label = "__start__"  # 当前输出布局标签

        # bool_rendered_decl 标记是否已经写入真实声明。
        bool_rendered_decl = False  # 输出内部声明是否已有内容

        # 按声明顺序输出，并在布局变化时插入标签。
        for decl in decls:

            # str_label 表示当前内部信号对应的输出端口分组。
            str_label = self._format_output_layout_label(output_signal_layouts.get(decl.name))  # 输出布局标签

            # str_current_label 保存标签 cluster 处理后的最新标签。
            str_current_label = self._begin_output_label_cluster(  # 更新后的输出布局标签
                list_lines,  # output_internal 区域输出行缓存
                str_label,  # 当前内部输出分组标签
                decl.leading_comments,  # 当前声明的前导注释
                str_current_label,  # 上一条输出布局标签
                bool_rendered_decl,  # 输出代理标签前是否已有声明
            )

            # 用户原有前导注释跟随标签之后输出。
            list_lines.extend(self._render_leading_comments(decl.leading_comments, 1))

            # str_code 是 output_internal 的单条声明源码。
            str_code = self._render_signal_decl_code(decl, 1)  # 内部输出声明源码

            # 尾注释继续使用 signal 默认说明。
            list_lines.append(self._append_trailing_comment(str_code, decl.comment, "signal"))

            # 已输出声明后，后续标签需要用内容状态决定是否补空行。
            bool_rendered_decl = True  # 输出内部区域已有声明

        # 防御性处理：如果所有声明都没有产生内容，则返回空区域。
        if not bool_rendered_decl:

            # 避免留下孤立 output_internal 横幅。
            return []

        # 区域末尾留一个空行，和其它声明区域拼接策略一致。
        list_lines.append("")

        # 返回 output_internal 区域完整行。
        return list_lines

    # 信号声明行只做格式拼接，不改变 parser 已解析出的声明语义。
    def _render_signal_decl_code(self, decl: SignalDecl, indent_level: int) -> str:
        """
        组装单条信号声明源码。

        :param decl: parser 解析出的信号声明模型。
        :param indent_level: formatter 缩进层级。
        :return: 带缩进和分号的单条 Verilog 声明文本。
        """

        # str_attributes 保留 Verilog 属性前缀。
        str_attributes = f"{decl.attributes} " if decl.attributes else ""  # 声明属性前缀

        # str_signed 在 signed 声明中保留关键字和尾随空格。
        str_signed = "signed " if decl.signed else ""  # signed 修饰片段

        # str_width 规范化 packed 位宽的空白。
        str_width = self._normalize_decl_spec_spacing(decl.width)  # packed 位宽片段

        # str_packed_and_name 合并 packed 位宽和信号名。
        str_packed_and_name = f"{str_width}{decl.name}" if str_width else decl.name  # 位宽加信号名片段

        # str_unpacked 保留数组维度后缀。
        str_unpacked = decl.unpacked if decl.unpacked else ""  # unpacked 维度片段

        # str_suffix 保留声明尾部附加语法。
        str_suffix = decl.suffix if decl.suffix else ""  # 声明尾部片段

        # str_init_value 只规范初始化表达式空白。
        str_init_value = self._normalize_expression_spacing(decl.init) if decl.init else ""  # 初始化表达式

        # str_init 拼出可选初始化赋值片段。
        str_init = f" = {str_init_value}" if str_init_value else ""  # 初始化赋值片段

        # 返回符合 formatter 缩进协议的单行声明。
        return (
            f"{self._indent(indent_level)}{str_attributes}{decl.kind} {str_signed}"
            f"{str_packed_and_name}{str_unpacked}{str_suffix}{str_init};"
        )

    # 输出布局标签由 group 和 section 共同组成。
    def _format_output_layout_label(self, layout: OutputSignalLayout | None) -> str:
        """
        把输出端口布局转换为区域标签文本。

        :param layout: 输出端口布局信息；缺失时表示不输出标签。
        :return: 可写入 Verilog 注释的布局标签。
        """

        # 没有布局时调用方无需插入标签。
        if layout is None:

            # 空字符串表示不改变当前标签 cluster。
            return ""

        # group 与 subgroup 需要先合并成输出镜像真正使用的一级标签。
        str_group_text = layout.group  # 输出镜像标签的一级分组文本

        # subgroup-first 的协议槽位优先复用端口区同一套横幅标题推导逻辑。
        if layout.subgroup_mode == "subgroup_first" and layout.subgroup:

            # 例如把 `用户接口` + `M_AXIS接口` 还原为 `用户接口m`。
            str_slot_banner_title = self._derive_protocol_slot_banner_title(  # 协议槽位横幅标题
                layout.group,  # 当前输出标签的一级分组文本
                layout.subgroup,  # 当前输出标签的子分组文本
            )

            # 命中协议槽位横幅格式时直接使用端口区一致的一级标签。
            if str_slot_banner_title:

                # 输出镜像区沿用端口区已经验证过的槽位标题文本。
                str_group_text = str_slot_banner_title  # 槽位归一后的一级标签文本

        # 子分组存在且尚未并入 group 时，把二者拼成和端口区一致的槽位标签。
        elif layout.subgroup and not str_group_text.endswith(layout.subgroup):

            # 例如把 `用户接口` 与 `m` 组合成 `用户接口m`。
            str_group_text = f"{str_group_text}{layout.subgroup}"  # group 与 subgroup 拼接后的槽位标签

        # group 和 section 同时存在时使用双段结构标签。
        if str_group_text and layout.section:

            # 双段标签保持现有 `--` 分隔协议。
            return f"{str_group_text}--{layout.section}"

        # 只存在一个维度时直接使用该维度标签。
        return str_group_text or layout.section

    # 实例亲和性标签组合 module、group 和 section 三个可选层级。
    def _format_signal_affinity_label(self, layout: InstanceSignalLayout | None) -> str:
        """
        把实例信号布局转换为亲和性标签。

        :param layout: 实例信号布局信息；缺失时表示不输出标签。
        :return: 可写入 Verilog 注释的实例亲和性标签。
        """

        # 没有布局信息时不插入标签。
        if layout is None:

            # 空标签让上层保持当前 cluster 状态。
            return ""

        # list_parts 保留非空的层级字段。
        list_parts = [part for part in (layout.module_name, layout.group, layout.section) if part]  # 标签层级片段

        # 仅有模块名时补默认用户接口层级。
        if not list_parts and layout.module_name:

            # 该分支保留历史兼容，虽然当前过滤后通常不可达。
            list_parts = [layout.module_name, "用户接口"]  # 默认模块接口标签

        # 模块名存在且只有单段时，补充默认接口段。
        if layout.module_name and len(list_parts) == 1:

            # 默认段让标签保持两级结构，更容易和输出区域区分。
            list_parts.append("用户接口")

        # 返回 formatter 使用的结构化标签文本。
        return "--".join(list_parts)

    # cluster 前最多插入一个空行，避免标签贴紧上一段内容。
    def _ensure_single_blank_line_before_cluster(self, lines: list[str], rendered_content: bool) -> None:
        """
        在标签 cluster 前补一个分隔空行。

        :param lines: 当前区域已累积的输出行。
        :param rendered_content: 当前区域是否已经输出过真实内容。
        :return: 无业务返回值，函数会按需原地追加空行。
        """

        # 已有内容且末尾不是空行时才补分隔。
        if rendered_content and lines and lines[-1] != "":

            # 单空行作为标签 cluster 的视觉边界。
            lines.append("")

    # 普通声明或 assign 标签 cluster 的起始处理。
    def _begin_label_cluster(
        self,
        lines: list[str],
        label: str,
        leading_comments: list[str],
        current_label: str,
        rendered_content: bool,

        # indent_level 只允许关键字传入，避免调用处误把布尔状态错位。
        *,
        indent_level: int = 1,
    ) -> str:
        """
        根据标签变化插入 cluster 标签注释。

        :param lines: 当前区域已累积的输出行。
        :param label: 当前声明或 assign 对应的结构化标签。
        :param leading_comments: 当前语句已有的前导注释。
        :param current_label: 上一个已输出的标签。
        :param rendered_content: 当前区域是否已输出真实内容。
        :param indent_level: 标签注释使用的缩进层级。
        :return: 最新标签状态。
        """

        # 标签未变化时无需重复输出。
        if label == current_label:

            # 返回当前标签，供调用方继续记录状态。
            return label

        # 新标签前根据已有内容补分隔空行。
        if label:

            # cluster 分隔只在真实内容之后出现。
            self._ensure_single_blank_line_before_cluster(lines, rendered_content)

        # 用户前导注释已经包含匹配标签时，不再重复生成标签行。
        if label and not self._has_matching_label_comment(leading_comments, label):

            # 标签以 Verilog 行注释形式写入目标区域。
            lines.append(f"{self._indent(indent_level)}//{label}")

        # 返回新标签作为调用方状态。
        return label

    # 输出内部信号标签目前复用普通 cluster 策略，保留独立入口便于后续扩展。
    def _begin_output_label_cluster(
        self,
        lines: list[str],
        label: str,
        leading_comments: list[str],
        current_label: str,
        rendered_content: bool,

        # indent_level 保持关键字参数，和普通标签 helper 的调用方式一致。
        *,
        indent_level: int = 1,
    ) -> str:
        """
        处理输出内部信号的标签 cluster 起点。

        :param lines: 当前 output_internal 区域已累积的输出行。
        :param label: 当前输出布局标签。
        :param leading_comments: 当前声明已有的前导注释。
        :param current_label: 上一个已输出的输出布局标签。
        :param rendered_content: 当前区域是否已输出真实声明。
        :param indent_level: 标签注释使用的缩进层级。
        :return: 最新输出布局标签状态。
        """

        # 输出标签语义和普通标签一致，委托统一 helper 处理。
        return self._begin_label_cluster(
            lines,
            label,
            leading_comments,
            current_label,
            rendered_content,
            indent_level=indent_level,
        )

    # 结构化标签的后缀集合用于识别已被自动 cluster 覆盖的前导注释。
    def _label_suffixes(self, label: str) -> set[str]:
        """
        拆出结构化标签的所有后缀形式。

        :param label: 使用 `--` 分隔的结构化标签。
        :return: 从每个层级开始截取的标签后缀集合。
        """

        # list_parts 保存标签中非空的层级片段。
        list_parts: list[str] = []  # 结构化标签层级

        # 标签按层级分隔符拆开，空层级直接丢弃。
        for part in label.split("--"):

            # str_part 去除层级两侧空白。
            str_part = part.strip()  # 当前标签层级

            # 非空层级才参与后缀构建。
            if str_part:

                # 保留清理后的层级文本。
                list_parts.append(str_part)

        # set_suffixes 收集完整标签和各级后缀，便于去重旧注释。
        set_suffixes: set[str] = set()  # 标签后缀集合

        # 从每个层级向后拼接，覆盖人工注释中常见的短标签写法。
        for index in range(len(list_parts)):

            # 当前后缀保留原始层级分隔符。
            set_suffixes.add("--".join(list_parts[index:]))

        # 返回所有可匹配的标签后缀。
        return set_suffixes

    # 标签最后一段用于识别 `xxx连线` 和 `xxx信号` 这类旧 helper 注释。
    def _label_last_segment(self, label: str) -> str:
        """
        取得结构化标签的最后一个层级。

        :param label: 使用 `--` 分隔的结构化标签。
        :return: 标签最后一段；空标签返回空字符串。
        """

        # list_parts 保存可参与比较的非空层级。
        list_parts: list[str] = []  # 标签末段候选层级

        # 逐段清理标签层级，避免空段影响最后一段判断。
        for part in label.split("--"):

            # str_part 是当前层级去空白后的文本。
            str_part = part.strip()  # 当前标签层级文本

            # 空层级不应成为旧 helper 注释匹配依据。
            if str_part:

                # 最后一段判断只需要有效层级列表。
                list_parts.append(str_part)

        # 返回最末层级，供 assign 注释去重使用。
        return list_parts[-1] if list_parts else ""

    # 注释正文比较忽略 Verilog 行注释前缀。
    def _comment_body(self, comment: str) -> str:
        """
        提取 Verilog 注释的正文文本。

        :param comment: 原始注释行或注释片段。
        :return: 去掉 `//` 前缀和外侧空白后的正文。
        """

        # str_stripped 去除注释行两侧空白。
        str_stripped = comment.strip()  # 去空白后的注释文本

        # Verilog 行注释前缀不参与标签去重比较。
        if str_stripped.startswith("//"):

            # 返回去掉 `//` 后的注释正文。
            return str_stripped[2:].strip()

        # 非行注释文本直接用于比较。
        return str_stripped

    # 只有带结构层级分隔符的注释才视为人工主标签提示。
    def _comment_looks_like_structured_label(self, body: str) -> bool:
        """
        判断注释正文是否像结构化标签。

        :param body: 已去掉注释前缀的正文。
        :return: 正文包含结构化标签分隔符时为 `True`。
        """

        # 双横线是 formatter 当前结构化标签协议。
        return "--" in body

    # assign 前导注释过滤会去掉自动标签重复项，但保留用户语义说明。
    def _filter_assign_leading_comments(
        self,
        comments: list[str],
        *,
        primary_label: str = "",
        secondary_label: str = "",
    ) -> list[str]:
        """
        过滤 assign 前导注释中的重复标签说明。

        :param comments: parser 捕获的 assign 前导注释。
        :param primary_label: 当前 assign 的主分组标签。
        :param secondary_label: 当前 assign 的次级来源标签。
        :return: 去除自动标签重复项后的注释列表。
        """

        # 没有前导注释时直接返回空列表。
        if not comments:

            # 调用方无需再执行标签去重。
            return []

        # set_primary_suffixes 包含主标签的完整形式和短后缀。
        set_primary_suffixes = self._label_suffixes(primary_label) if primary_label else set()  # 主标签后缀集合

        # set_secondary_suffixes 包含次级标签的完整形式和短后缀。
        set_secondary_suffixes = self._label_suffixes(secondary_label) if secondary_label else set()  # 次级标签后缀集合

        # str_last_primary 用于匹配旧注释中的短主标签。
        str_last_primary = self._label_last_segment(primary_label)  # 主标签末级片段

        # str_last_secondary 用于匹配旧注释中的短次级标签。
        str_last_secondary = self._label_last_segment(secondary_label)  # 次级标签末级片段

        # list_kept 保存仍应随 assign 输出的用户注释。
        list_kept: list[str] = []  # 保留的 assign 前导注释

        # set_seen_bodies 防止同一注释正文重复输出。
        set_seen_bodies: set[str] = set()  # 已保留注释正文集合

        # 按原顺序检查注释，保持用户注释的相对位置。
        for comment in comments:

            # str_body 用于和自动标签集合做归一化比较。
            str_body = self._comment_body(comment)  # 当前注释正文

            # 空正文没有可保留的语义内容。
            if not str_body:

                # 跳过空注释行。
                continue

            # 已保留过的正文不重复输出。
            if str_body in set_seen_bodies:

                # 跳过重复注释正文。
                continue

            # 自动主/次标签已经由 cluster 行表达，不再重复保留。
            if str_body in set_primary_suffixes or str_body in set_secondary_suffixes:

                # 跳过标签后缀重复项。
                continue

            # 旧版主标签短注释也视为自动标签重复项。
            if str_last_primary and str_body in {str_last_primary, f"{str_last_primary}连线", f"{str_last_primary}信号"}:

                # 跳过主标签短形式。
                continue

            # 旧版次级标签短注释同样不重复输出。
            if str_last_secondary and str_body in {
                str_last_secondary,
                f"{str_last_secondary}连线",
                f"{str_last_secondary}信号",
            }:

                # 跳过次级标签短形式。
                continue

            # 当前注释不属于自动标签，保留给 assign 输出。
            list_kept.append(comment)

            # 记录正文，防止后续重复注释再次输出。
            set_seen_bodies.add(str_body)

        # 返回过滤后的前导注释。
        return list_kept

    # output assign 通过 lhs 信号名关联目标端口布局。
    def _resolve_output_assign_layout(
        self, assign: AssignStmt, output_target_layouts: dict[str, OutputSignalLayout] | None
    ) -> OutputSignalLayout | None:
        """
        为输出 assign 查找目标端口布局。

        :param assign: 待排序或渲染的连续赋值语句。
        :param output_target_layouts: 输出端口名到布局信息的映射。
        :return: 匹配到的输出布局；无法匹配时返回 `None`。
        """

        # 没有布局表时保持输入顺序。
        if not output_target_layouts:

            # 调用方按无布局处理。
            return None

        # str_lhs_base 是 assign 左侧的简单信号基名。
        str_lhs_base = self._extract_simple_signal_base(assign.lhs, "lvalue_normalization_violation")  # assign 左侧基名

        # 复杂左值不参与端口布局排序。
        if str_lhs_base is None:

            # 无法归一化时不强行猜测布局。
            return None

        # 返回左侧信号对应的输出目标布局。
        return output_target_layouts.get(str_lhs_base)

    # other assign 的来源布局由 rhs 中引用的端口或传播来源决定。
    def _resolve_assign_source_layout(
        self,
        assign: AssignStmt,
        source_layouts: dict[str, AssignSourceLayout] | None,
    ) -> AssignSourceLayout | None:
        """
        解析 assign 右侧引用对应的来源布局。

        :param assign: 待分析的连续赋值语句。
        :param source_layouts: 可用的来源信号布局表。
        :return: 唯一可判定的来源布局；无法唯一判定时返回 `None`。
        """

        # 没有来源布局表时不做来源分组。
        if not source_layouts:

            # 调用方继续使用兜底标签。
            return None

        # list_ref_names 收集 rhs 中出现且具备来源布局的信号名。
        list_ref_names = self._extract_layout_signal_names_from_text(assign.rhs, source_layouts)  # rhs 来源信号名

        # rhs 没有可识别来源信号时无法确定次级标签。
        if not list_ref_names:

            # 无来源引用时交给兜底分类。
            return None

        # set_unique_affinities 识别 rhs 是否混用了多个来源域。
        set_unique_affinities = {self._assign_source_layout_identity(source_layouts[name]) for name in list_ref_names}  # 来源域集合

        # 多个来源域混用时不输出单一来源标签。
        if len(set_unique_affinities) != 1:

            # 调用方保留原始顺序或使用兜底。
            return None

        # 同一来源域内选择端口 rank 最小的布局作为代表。
        return min((source_layouts[name] for name in list_ref_names), key=lambda layout: layout.port_rank)

    # 信号亲和性布局通过 lhs 归一化信号名解析。
    def _resolve_signal_affinity_assign_layout(
        self,
        assign: AssignStmt,
        signal_layouts: dict[str, InstanceSignalLayout] | None,
    ) -> InstanceSignalLayout | None:
        """
        为 assign 左侧信号查找实例亲和性布局。

        :param assign: 待分析的连续赋值语句。
        :param signal_layouts: 信号名到实例亲和性布局的映射。
        :return: 匹配到的实例布局；无法匹配时返回 `None`。
        """

        # 没有布局表时不做实例亲和性分组。
        if not signal_layouts:

            # 调用方继续按无标签处理。
            return None

        # str_lhs_base 是 assign 左值对应的简单信号基名。
        str_lhs_base = self._extract_simple_signal_base(assign.lhs, "signal_affinity_propagation")  # assign 左侧亲和性信号

        # 复杂左值无法可靠关联到单个声明布局。
        if str_lhs_base is None:

            # 不强行关联到实例布局。
            return None

        # 返回左侧信号对应的实例亲和性布局。
        return signal_layouts.get(str_lhs_base)

    # 普通 assign 的次级标签优先保留人工主标签，再使用来源布局。
    def _resolve_other_assign_secondary_label(
        self,
        assign: AssignStmt,
        signal_layouts: dict[str, InstanceSignalLayout] | None,
        source_layouts: dict[str, AssignSourceLayout] | None,
    ) -> tuple[int, str, int]:
        """
        为 ordinary assign 选择次级来源标签。

        :param assign: 待分析的普通连续赋值语句。
        :param signal_layouts: 左值信号到实例亲和性布局的映射。
        :param source_layouts: 右值来源信号到端口来源布局的映射。
        :return: `(类别, 标签, 排序 rank)`，类别越小越靠前。
        """

        # set_comment_bodies 保存 assign 前导注释正文，用于识别人工标签提示。
        set_comment_bodies: set[str] = set()  # assign 前导注释正文集合

        # 前导注释先清理成正文，空正文不参与标签判断。
        for comment in assign.leading_comments:

            # str_comment_body 是去掉 Verilog 注释前缀后的正文。
            str_comment_body = self._comment_body(comment)  # 当前前导注释正文

            # 非空正文才进入人工标签提示集合。
            if str_comment_body:

                # 集合去重可以避免重复注释影响后续 any 判断。
                set_comment_bodies.add(str_comment_body)

        # bool_manual_primary_hint 表示用户已经给出结构化主标签。
        bool_manual_primary_hint = False  # 是否存在人工结构化主标签

        # 手写结构化主标签存在时，自动次级来源标签需要让位。
        for str_comment_body in set_comment_bodies:

            # 普通连线兜底标签不算人工主分组。
            bool_is_manual_label = self._comment_looks_like_structured_label(str_comment_body)  # 是否为结构化注释

            # 命中人工主分组后即可停止扫描。
            if bool_is_manual_label and str_comment_body != "其他信号连线":

                # 标记人工主标签存在，后续不再额外叠加来源标签。
                bool_manual_primary_hint = True  # 已发现人工主分组

                # 已经确认存在人工主标签，无需继续检查。
                break

        # bool_helper_hint 标记旧 helper 已写入普通连线兜底标签。
        bool_helper_hint = "其他信号连线" in set_comment_bodies  # 是否已有普通连线兜底注释

        # obj_source_layout 代表 rhs 中唯一可判定的来源布局。
        obj_source_layout = self._resolve_assign_source_layout(assign, source_layouts)  # assign 右侧来源布局

        # 人工主标签存在但没有来源标签时，不再叠加自动次级标签。
        if bool_manual_primary_hint and obj_source_layout is None:

            # 类别 0 表示保持人工标签，不输出次级来源。
            return 0, "", 0

        # 旧 helper 兜底标签存在且没有来源布局时，保持普通连线标签。
        if bool_helper_hint and obj_source_layout is None:

            # 类别 1 是普通连线兜底分组。
            return 1, "其他信号连线", 0

        # obj_lhs_target_layout 表示 assign 左侧目标信号所属实例布局。
        obj_lhs_target_layout: InstanceSignalLayout | None = (  # assign 左侧目标实例布局
            self._resolve_signal_affinity_assign_layout(assign, signal_layouts)  # 查询 assign 左侧布局
        )

        # 左侧已有实例亲和性时，检查右侧是否仍属于同一实例域。
        if obj_lhs_target_layout is not None:

            # str_lhs_base 用于从 rhs 亲和性候选中排除自身。
            str_lhs_base = self._extract_simple_signal_base(assign.lhs, "signal_affinity_propagation")  # assign 左值基名

            # list_affinity_names 收集 rhs 中具备实例亲和性的信号。
            list_affinity_names = self._extract_affinity_signal_names_from_text(  # rhs 实例亲和性信号名
                assign.rhs,  # assign 右侧表达式文本
                signal_layouts or {},  # 可用于 rhs 解析的亲缘布局表
                exclude={str_lhs_base} if str_lhs_base else None,  # 排除左值自身
            )

            # rhs 存在同域引用时，不额外输出来源标签。
            if list_affinity_names:

                # set_unique_affinities 记录 rhs 涉及的实例域。
                set_unique_affinities: set[tuple[str, int]] = set()  # rhs 实例亲和性集合

                # rhs 中每个亲缘信号都折算成实例身份。
                for str_affinity_name in list_affinity_names:

                    # tuple_affinity_identity 是实例名和 rank 组成的稳定身份。
                    tuple_affinity_identity = self._signal_affinity_identity(signal_layouts[str_affinity_name])  # rhs 信号实例身份

                    # 去重后用于判断 rhs 是否只引用同一个实例域。
                    set_unique_affinities.add(tuple_affinity_identity)

                # rhs 唯一实例域与 lhs 一致时，主标签已经足够。
                if (
                    len(set_unique_affinities) == 1
                    and self._signal_affinity_identity(obj_lhs_target_layout) in set_unique_affinities
                ):

                    # 类别 0 表示不再插入次级标签。
                    return 0, "", 0

        # 来源布局存在时，使用 section 或 subgroup 作为次级标签。
        if obj_source_layout is not None:

            # str_label 优先使用来源 section，缺失时退回 subgroup。
            str_label = obj_source_layout.section or obj_source_layout.subgroup  # 来源分组标签

            # 类别 2 表示可排序的来源区段。
            return 2, str_label, obj_source_layout.port_rank

        # 其它情况统一放入普通信号连线兜底分组。
        return 1, "其他信号连线", 0

    # 输出 assign 按目标端口布局 rank 稳定排序。
    def _sort_assigns_by_output_layout(
        self,
        assigns: list[AssignStmt],
        output_target_layouts: dict[str, OutputSignalLayout] | None,
    ) -> list[AssignStmt]:
        """
        按输出端口布局排序连续赋值。

        :param assigns: 待排序的输出 assign 列表。
        :param output_target_layouts: 输出端口名到布局信息的映射。
        :return: 按端口 rank 和原始序号稳定排序后的 assign 列表。
        """

        # 没有 assign 或布局时保持原顺序。
        if not assigns or not output_target_layouts:

            # 返回副本，避免调用方误以为原列表会被排序。
            return list(assigns)

        # int_fallback_rank 把无法匹配布局的 assign 放到已知端口之后。
        int_fallback_rank = len(output_target_layouts) + len(assigns) + 1  # 未匹配输出端口的排序 rank

        # list_decorated 保存排序 key 和原 assign。
        list_decorated: list[tuple[tuple[int, int], AssignStmt]] = []  # 带排序键的输出 assign

        # 原始 index 作为稳定排序的第二关键字。
        for int_index, assign in enumerate(assigns):

            # obj_target_layout 是当前 assign 左值对应的输出端口布局。
            obj_target_layout: OutputSignalLayout | None = (  # 输出 assign 目标布局
                self._resolve_output_assign_layout(assign, output_target_layouts)  # 查询输出目标布局
            )

            # int_port_rank 代表输出端口声明中的排序位置。
            int_port_rank = obj_target_layout.port_rank if obj_target_layout is not None else int_fallback_rank  # 输出端口排序 rank

            # 排序键保留原始序号，确保同 rank assign 不重排。
            list_decorated.append(((int_port_rank, int_index), assign))

        # 就地排序只作用于局部装饰列表。
        list_decorated.sort(key=lambda item: item[0])

        # 去掉排序键后返回 assign 顺序。
        return [assign for _, assign in list_decorated]

    # 普通 assign 按实例亲和性和来源区段联合排序。
    def _sort_other_assigns_by_layout(
        self,
        assigns: list[AssignStmt],
        signal_layouts: dict[str, InstanceSignalLayout] | None,
        source_layouts: dict[str, AssignSourceLayout] | None,
    ) -> list[AssignStmt]:
        """
        按实例亲和性和来源布局排序普通 assign。

        :param assigns: 待排序的 ordinary assign 列表。
        :param signal_layouts: 左值信号到实例亲和性布局的映射。
        :param source_layouts: 右值来源信号到端口来源布局的映射。
        :return: 稳定排序后的 ordinary assign 列表。
        """

        # 缺少 assign 或主布局时保持输入顺序。
        if not assigns or not signal_layouts:

            # 返回副本，避免调用方误用原地排序假设。
            return list(assigns)

        # int_fallback_primary_rank 放置无法匹配实例布局的 assign。
        int_fallback_primary_rank = len(signal_layouts) + len(assigns) + 1  # 主实例布局兜底 rank

        # int_fallback_source_rank 放置无法匹配来源布局的 assign。
        int_fallback_source_rank = (len(source_layouts) if source_layouts else 0) + len(assigns) + 1  # 来源布局兜底 rank

        # list_decorated 保存六元排序键和原 assign。
        list_decorated: list[tuple[tuple[int, int, int, int, int, int], AssignStmt]] = []  # 带排序键的 ordinary assign

        # 遍历 assign 时保留原始序号作为最终稳定键。
        for int_index, assign in enumerate(assigns):

            # obj_lhs_target_layout 是当前 assign 左侧目标信号的实例亲和性布局。
            obj_lhs_target_layout: InstanceSignalLayout | None = (  # 普通 assign 左值目标布局
                self._resolve_signal_affinity_assign_layout(assign, signal_layouts)  # 查询普通 assign 左值布局
            )

            # tuple_secondary 保存次级分类、标签和来源 rank。
            tuple_secondary = self._resolve_other_assign_secondary_label(assign, signal_layouts, source_layouts)  # 次级标签三元组

            # int_secondary_category 决定无来源、兜底来源和真实来源的相对顺序。
            int_secondary_category = tuple_secondary[0]  # 次级标签类别

            # int_secondary_rank 是来源布局排序 rank。
            int_secondary_rank = tuple_secondary[2]  # 次级来源排序 rank

            # int_primary_instance_rank 默认把无实例布局的 assign 放在已知布局之后。
            int_primary_instance_rank = int_fallback_primary_rank  # 普通 assign 主实例排序 rank

            # int_primary_decl_rank 默认使用同一个兜底 rank。
            int_primary_decl_rank = int_fallback_primary_rank  # 普通 assign 声明排序 rank

            # int_primary_association_rank 默认也归入无布局分组。
            int_primary_association_rank = int_fallback_primary_rank  # 普通 assign 关联排序 rank

            # 有左值实例布局时，使用真实实例顺序覆盖兜底 rank。
            if obj_lhs_target_layout is not None:

                # 实例出现顺序是 ordinary assign 主分组的第一排序键。
                int_primary_instance_rank = obj_lhs_target_layout.instance_rank  # 真实实例排序 rank

                # 声明顺序帮助同实例内信号保持靠近原声明。
                int_primary_decl_rank = obj_lhs_target_layout.decl_index  # 真实声明排序 rank

                # 关联顺序用于同声明内的端口映射稳定排序。
                int_primary_association_rank = obj_lhs_target_layout.association_rank  # 真实关联排序 rank

            # tuple_primary_sort_key 只描述左值实例亲缘内的稳定顺序。
            tuple_primary_sort_key = (
                int_primary_instance_rank,  # 左值实例出现顺序
                int_primary_decl_rank,  # 左值声明出现顺序
                int_primary_association_rank,  # 左值端口关联顺序
            )  # 左值实例聚合排序字段

            # tuple_secondary_sort_key 描述 rhs 来源分组和输入稳定性。
            tuple_secondary_sort_key = (
                int_secondary_category,  # rhs 来源类别
                int_secondary_rank if int_secondary_category == 2 else int_fallback_source_rank,  # rhs 来源排序 rank
                int_index,  # assign 输入序号
            )  # rhs 来源和原始序号排序字段

            # 装饰列表只在本函数内排序。
            list_decorated.append(((*tuple_primary_sort_key, *tuple_secondary_sort_key), assign))

        # 按六元 key 稳定排序。
        list_decorated.sort(key=lambda item: item[0])

        # 返回去除排序键后的 assign 列表。
        return [assign for _, assign in list_decorated]

    # 连续出现足够成员的实例亲和性标签才显示，避免零散标签噪音。
    def _build_assign_signal_affinity_labels(
        self,
        assigns: list[AssignStmt],
        signal_layouts: dict[str, InstanceSignalLayout] | None,
        *,
        min_members: int,
    ) -> list[str]:
        """
        构建 ordinary assign 的主实例亲和性标签列表。

        :param assigns: 已排序的 ordinary assign 列表。
        :param signal_layouts: 左值信号到实例亲和性布局的映射。
        :param min_members: 连续同标签至少达到该数量才显示标签。
        :return: 与 `assigns` 等长的主标签列表。
        """

        # 没有 assign 或布局时返回等长空标签。
        if not assigns or not signal_layouts:

            # 空标签表示不插入主标签 cluster。
            return ["" for _ in assigns]

        # list_raw_labels 记录每条 assign 的直接实例亲和性标签。
        list_raw_labels: list[str] = []  # 每条 assign 的原始主标签

        # 逐条计算主标签，避免推导式隐藏布局解析过程。
        for assign in assigns:

            # obj_target_signal_layout 是当前 assign 左值目标的实例亲缘布局。
            obj_target_signal_layout: InstanceSignalLayout | None = (  # assign 目标主标签布局
                self._resolve_signal_affinity_assign_layout(assign, signal_layouts)  # 查询主标签布局
            )

            # str_raw_label 是实例亲缘布局格式化后的显示文本。
            str_raw_label = self._format_signal_affinity_label(obj_target_signal_layout)  # 原始主标签文本

            # 原始标签列表与 assign 输入顺序一一对应。
            list_raw_labels.append(str_raw_label)

        # list_display_labels 保存最终允许输出的标签。
        list_display_labels = ["" for _ in assigns]  # 可显示的主标签

        # int_index 指向当前待检查的连续标签段起点。
        int_index = 0  # 主标签扫描位置

        # 连续扫描相同标签，只对足够长的段落显示标签。
        while int_index < len(list_raw_labels):

            # str_label 是当前连续段的候选主标签。
            str_label = list_raw_labels[int_index]  # 当前候选主标签

            # 空标签不参与连续段统计。
            if not str_label:

                # 跳到下一条 assign。
                int_index += 1  # 空标签后的扫描位置

            # 非空标签才需要计算连续段长度。
            if str_label:

                # int_end 指向当前同标签连续段的右边界。
                int_end = int_index + 1  # 主标签连续段右边界

                # 扩展到标签变化或 assign 列表结束。
                while int_end < len(list_raw_labels) and list_raw_labels[int_end] == str_label:

                    # 同标签 assign 继续并入当前显示段候选。
                    int_end += 1  # 主标签段扩展后的右边界

                # 连续成员足够多时，才在该段显示主标签。
                if int_end - int_index >= min_members:

                    # 给当前连续段的每条 assign 标记同一个显示标签。
                    for int_label_index in range(int_index, int_end):

                        # 标签行只会在渲染阶段按 cluster 状态输出一次。
                        list_display_labels[int_label_index] = str_label  # 当前连续段显示标签

                # 下一轮从当前连续段之后开始。
                int_index = int_end  # 非空标签段后的扫描位置

        # 返回与 assign 列表对齐的显示标签。
        return list_display_labels

    # 来源区段标签和 ordinary assign 一一对齐。
    def _build_assign_source_section_labels(
        self,
        assigns: list[AssignStmt],
        signal_layouts: dict[str, InstanceSignalLayout] | None,
        source_layouts: dict[str, AssignSourceLayout] | None,
    ) -> list[str]:
        """
        构建 ordinary assign 的次级来源标签列表。

        :param assigns: 已排序的 ordinary assign 列表。
        :param signal_layouts: 左值信号到实例亲和性布局的映射。
        :param source_layouts: 右值来源信号到端口来源布局的映射。
        :return: 与 `assigns` 等长的次级来源标签列表。
        """

        # 没有 assign 时返回空标签列表。
        if not assigns:

            # 和输入长度保持一致。
            return ["" for _ in assigns]

        # 返回每条 assign 的次级标签文本。
        return [
            self._resolve_other_assign_secondary_label(assign, signal_layouts, source_layouts)[1] for assign in assigns
        ]

    # output always 块按其写入目标端口映射到输出布局。
    def _resolve_output_always_layout(
        self, block: AlwaysBlock, output_target_layouts: dict[str, OutputSignalLayout] | None
    ) -> OutputSignalLayout | None:
        """
        为 output always 块选择对应的输出端口布局。

        :param block: parser 解析出的 always 块。
        :param output_target_layouts: 输出目标信号名到布局信息的映射。
        :return: rank 最靠前的输出布局；无法匹配时返回 `None`。
        """

        # 没有输出布局表时不为 always 块生成布局标签。
        if not output_target_layouts:

            # output_always 后续按无布局路径处理。
            return None

        # list_resolved_layouts 收集 always 写入目标对应的输出布局。
        list_resolved_layouts: list[OutputSignalLayout] = []  # always 目标输出布局列表

        # 按 always 目标出现顺序查找可用的输出端口布局。
        for target in block.targets:

            # 只有具备布局的输出目标才参与排序。
            if target in output_target_layouts:

                # 当前目标的布局保留到候选列表中。
                list_resolved_layouts.append(output_target_layouts[target])

        # 没有任何目标匹配时不输出布局标签。
        if not list_resolved_layouts:

            # 调用方继续使用默认 always 分组。
            return None

        # 多个输出目标时使用端口 rank 最靠前的布局代表整个 always 块。
        return min(list_resolved_layouts, key=lambda layout: layout.port_rank)

    # 前导注释中已有匹配标签时，自动标签行不重复输出。
    def _has_matching_label_comment(self, comments: list[str], label: str) -> bool:
        """
        判断前导注释是否已经包含指定标签。

        :param comments: 当前声明或 assign 的前导注释列表。
        :param label: 准备自动输出的结构化标签。
        :return: 任一前导注释以该标签开头时返回 `True`。
        """

        # 空标签没有匹配意义。
        if not label:

            # 调用方应跳过自动标签输出。
            return False

        # 按原始顺序检查前导注释。
        for comment in comments:

            # str_stripped 去掉注释两侧空白后再匹配。
            str_stripped = comment.strip()  # 去空白后的前导注释

            # Verilog 行注释已经以目标标签开头时视为匹配。
            if str_stripped.startswith(f"//{label}"):

                # 匹配成功，调用方不需要再插入标签。
                return True

        # 未发现匹配的手写标签注释。
        return False

    # assign 区域渲染负责标签分组、前导注释去重和单行 assign 输出。
    def _render_assign_region(
        self,
        region: str,
        assigns: Iterable[AssignStmt],
        output_target_layouts: dict[str, OutputSignalLayout] | None = None,
        signal_layouts: dict[str, InstanceSignalLayout] | None = None,
        source_layouts: dict[str, AssignSourceLayout] | None = None,
    ) -> list[str]:
        """
        渲染连续赋值区域。

        :param region: assign 区域名。
        :param assigns: 待输出的连续赋值语句。
        :param output_target_layouts: 可选的输出端口排序布局。
        :param signal_layouts: 可选的普通 assign 左值亲缘布局。
        :param source_layouts: 可选的普通 assign 右值来源布局。
        :return: 带横幅和尾随空行的 assign 区域；无 assign 时返回空列表。
        """

        # Iterable 先固化为列表，后续排序和标签列表都依赖稳定索引。
        list_assigns = list(assigns)  # 当前区域待渲染 assign 语句

        # 输出 assign 按端口布局排序，保证输出桥接区域顺序贴近端口声明。
        if output_target_layouts is not None:

            # 输出端口布局排序不改变 assign 文本本身。
            list_assigns = self._sort_assigns_by_output_layout(list_assigns, output_target_layouts)  # 输出 assign 排序结果

        # 普通 assign 按实例亲缘和源端口区段排序，便于追踪连线来源。
        elif signal_layouts is not None:

            # 其他连线排序同时考虑左值亲缘和右值来源。
            list_assigns = self._sort_other_assigns_by_layout(  # 普通连线排序后的 assign
                list_assigns,  # 当前区域 assign 列表
                signal_layouts,  # 左值实例亲缘布局
                source_layouts,  # 右值端口来源布局
            )

        # 没有 assign 时不输出空区域横幅。
        if not list_assigns:

            # 空 assign 区域交给主体渲染跳过。
            return []

        # list_lines 以当前 assign 区域横幅开头。
        list_lines = [f"{self._indent(1)}{self.REGION_TITLES[region]}"]  # assign 区域输出行

        # str_current_label 记录主标签，避免同组重复写入横幅说明。
        str_current_label = "__start__"  # 当前 assign 主标签状态

        # str_current_source_section 记录普通连线的次级来源标签。
        str_current_source_section = "__start__"  # 当前来源区段标签

        # bool_rendered_assign 控制标签组之间是否需要空行。
        bool_rendered_assign = False  # 当前区域是否已有 assign 输出

        # 左值亲缘标签和右值来源标签提前计算，循环内只按索引读取。
        list_signal_labels = self._build_assign_signal_affinity_labels(  # assign 主标签序列
            list_assigns,  # 主标签计算使用的 assign 顺序
            signal_layouts,  # 左值信号亲缘来源
            min_members=1,  # 单成员也允许显示亲缘标签
        )  # 每条 assign 的主标签

        # 来源区段标签用于普通 assign 的二级分组。
        list_source_section_labels = self._build_assign_source_section_labels(  # assign 来源标签序列
            list_assigns,  # 排序后的 assign 语句
            signal_layouts,  # 左值亲缘匹配表
            source_layouts,  # 普通连线右值来源布局
        )  # 每条 assign 的来源标签

        # 二级来源标签只在同一个主标签组内去重。
        bool_rendered_assign_in_primary_cluster = False  # 当前主标签组内是否已有 assign

        # 逐条 assign 输出标签、前导注释和规范化后的 assign 语句。
        for index, assign in enumerate(list_assigns):

            # str_label 保存当前 assign 的主分组标签。
            str_label = ""  # 当前 assign 主分组标签

            # str_source_section 保存当前 assign 的来源分组标签。
            str_source_section = ""  # 当前 assign 右值来源标签

            # 前导注释会过滤掉与自动标签重复的内容。
            list_leading_comments = list(assign.leading_comments)  # 当前 assign 前导注释

            # 输出端口 assign 使用输出布局标签。
            if output_target_layouts is not None:

                # 输出布局标签来自目标端口分组。
                str_label = self._format_output_layout_label(  # 输出 assign 主标签
                    self._resolve_output_assign_layout(assign, output_target_layouts)  # 当前输出 assign 布局
                )

                # 去掉与自动主标签重复的前导注释。
                list_leading_comments = self._filter_assign_leading_comments(  # 输出 assign 保留的前导注释
                    list_leading_comments,  # 输出 assign 原始前导注释
                    primary_label=str_label,  # 输出端口分组标签
                )

                # 标签变化时输出新的主标签注释。
                str_current_label = self._begin_output_label_cluster(  # 输出 assign 当前主标签状态
                    list_lines,  # 输出标签写入的 assign 行缓存
                    str_label,  # 输出端口主标签
                    list_leading_comments,  # 过滤后的输出注释
                    str_current_label,  # 上一条输出主标签
                    bool_rendered_assign,  # 输出区域是否已有 assign
                )

            # 普通 assign 使用实例亲缘标签和来源端口区段标签。
            elif signal_layouts is not None:

                # 主标签按左值信号亲缘预先计算。
                str_label = list_signal_labels[index]  # 当前普通 assign 主标签

                # 次级标签按右值来源端口区段预先计算。
                str_source_section = list_source_section_labels[index]  # 当前普通 assign 来源标签

                # 删除与自动主/次标签重复的手写注释。
                list_leading_comments = self._filter_assign_leading_comments(  # 普通 assign 保留的前导注释
                    list_leading_comments,  # 普通 assign 原始前导注释
                    primary_label=str_label,  # 实例亲缘主标签
                    secondary_label=str_source_section,  # 端口来源二级标签
                )

                # 手写结构化标签可作为人工主分组边界。
                bool_manual_primary_boundary = not str_label and any(  # 手写主分组边界判定
                    self._comment_looks_like_structured_label(self._comment_body(comment))  # 手写标签形态检查
                    and self._comment_body(comment) != str_source_section  # 排除自动来源标签重复项
                    for comment in list_leading_comments  # 过滤后的前导注释
                )  # 是否由手写结构化注释触发主分组

                # bool_label_changed 表示自动主标签是否真的切换。
                bool_label_changed = bool(str_label) and str_label != str_current_label  # 自动主标签是否切换

                # 主标签变化会重置二级来源标签状态。
                bool_primary_changed = bool_label_changed or bool_manual_primary_boundary  # 是否开启新的主分组

                # 人工主边界前保留空行，避免贴到上一组 assign。
                if bool_manual_primary_boundary:

                    # 手写结构化注释作为分组边界时，需要先隔开上一组。
                    self._ensure_single_blank_line_before_cluster(list_lines, bool_rendered_assign)

                # 自动标签或人工边界存在时，尝试输出主标签。
                if str_label or bool_manual_primary_boundary:

                    # 主标签 helper 会避免重复输出已有手写标签。
                    str_current_label = self._begin_label_cluster(  # 普通 assign 当前主标签状态
                        list_lines,  # 主标签写入的 assign 行缓存
                        str_label,  # 普通 assign 主标签
                        list_leading_comments,  # 过滤后的普通注释
                        str_current_label,  # 上一条普通主标签
                        bool_rendered_assign,  # 区域内是否已有 assign
                    )

                # 主标签切换后，二级来源标签需要从头判断。
                if bool_primary_changed:

                    # 新主组内尚未输出任何来源区段。
                    str_current_source_section = "__start__"  # 新主组的来源标签起点

                    # 新主组内尚无 assign，因此二级标签前不需要空行。
                    bool_rendered_assign_in_primary_cluster = False  # 新主组内 assign 状态

                # 来源区段存在时输出二级标签。
                if str_source_section:

                    # 来源标签按主组内部状态去重。
                    str_current_source_section = self._begin_label_cluster(  # 普通 assign 当前来源标签
                        list_lines,  # 来源标签写入的 assign 行缓存
                        str_source_section,  # 普通 assign 来源区段
                        list_leading_comments,  # 来源标签去重后的注释
                        str_current_source_section,  # 上一条来源区段
                        bool_rendered_assign_in_primary_cluster,  # 主组内是否已有 assign
                    )

                # 没有来源标签时清空当前二级标签状态。
                else:

                    # 清空状态能让下一条来源标签重新输出 cluster。
                    str_current_source_section = ""  # 当前 assign 没有来源区段

            # 前导注释先于 assign 文本输出。
            list_lines.extend(self._render_leading_comments(list_leading_comments, 1))

            # delay 保留 Verilog assign 延迟语法。
            str_delay = f"{assign.delay} " if assign.delay else ""  # assign 延迟片段

            # lhs/rhs 只做空白规范化，避免改写表达式语义。
            str_lhs = self._normalize_expression_spacing(assign.lhs)  # assign 左值表达式

            # 右值表达式同样只规范空白。
            str_rhs = self._normalize_expression_spacing(assign.rhs)  # assign 右值表达式

            # assign 语句保持单行输出，尾注释由 helper 统一补齐。
            str_code = f"{self._indent(1)}assign {str_delay}{str_lhs} = {str_rhs};"  # assign 语句文本

            # 将格式化后的 assign 行加入当前区域。
            list_lines.append(self._append_trailing_comment(str_code, assign.comment, "assign"))

            # 当前 assign 已经写入区域，后续标签分隔可依赖该状态。
            bool_rendered_assign = True  # assign 区域已有有效语句

            # 普通 assign 的二级标签状态只在 signal_layouts 分支下维护。
            if signal_layouts is not None:

                # 当前主组已经输出 assign，后续来源标签前需要可读分隔。
                bool_rendered_assign_in_primary_cluster = True  # 当前主组内已有 assign 输出

        # 防御性检查：理论上空列表已提前返回。
        if not bool_rendered_assign:

            # 未渲染任何 assign 时不留下空横幅。
            return []

        # 区域末尾保留空行，方便后续区域自然分隔。
        list_lines.append("")

        # 返回 assign 区域完整文本行。
        return list_lines
