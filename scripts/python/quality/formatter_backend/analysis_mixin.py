"""提供 Verilog formatter 的结构分析、FSM 识别和不变量校验辅助。"""

# 延迟解析类型注解，避免 formatter backend 导入阶段牵动完整类型图
from __future__ import annotations

# 正则工具用于解析 RTL 片段和识别信号命名形态
import re

# cast 只用于让 current-project 质量门识别 dict.get 的可空模型类型
from typing import cast

# 低有效复位名称统一复用质量层的下划线语义段边界。
from ..reset_name_roles import is_low_active_reset_name
from ..declaration_region_policy import resolve_declaration_region

# formatter 错误类型和参数/端口模型支撑本模块的归一化与校验
from .models import (
    VerilogFormatterError,
    LValueRef,
    ParamDecl,
    PortDecl,
    PortLayoutInfo,
)

# 信号、赋值和控制树模型用于 always 拆分与输出亲缘关系分析
from .models import (
    OutputSignalLayout,
    AssignSourceLayout,
    InstanceSignalLayout,
    SignalDecl,
    AssignStmt,
)

# 控制结构模型用于 always 树遍历和实例布局分析
from .models import (
    BodyBlock,
    CaseItem,
    ControlNode,
    AlwaysBlock,
    InstanceBlock,
    GenerateBlock,
)

# GroupedDeclMap 描述 renderer 回流的声明区域到声明列表映射。
GroupedDeclMap = dict[str, list[SignalDecl]]  # 声明区域名到声明列表的映射

# GroupedAlwaysMap 描述 renderer 回流的 always 区域到过程块列表映射。
GroupedAlwaysMap = dict[str, list[AlwaysBlock]]  # always 区域名到过程块列表的映射

# AnalysisMixin 承担 formatter normalize 前后的结构判定，不直接执行文件 I/O
class AnalysisMixin:
    """封装 FSM、always 拆分、不变量校验和输出亲缘关系分析。"""

    # _normalize_param 生成参数归一化后的不可变副本。
    def _normalize_param(self, param: ParamDecl, state: bool) -> ParamDecl:
        """
        按 formatter 命名前缀规则归一化参数声明。

        :param param: 待归一化的参数或 localparam 声明。
        :param state: 当前参数是否已确认属于 FSM 状态常量。
        :return: 名称规范化后的参数声明副本。
        """

        # 原始声明文本表示用户已经手写完整参数格式，不能再重命名
        if param.raw_text:

            # 克隆时复制 leading_comments，避免后续渲染共享同一个列表
            return ParamDecl(
                param.keyword,
                param.name,
                param.value,

                # 声明细节和注释字段必须原样保留。
                param.decl_spec,
                param.comment,
                list(param.leading_comments),

                # 原始文本与 synthetic 标记决定后续渲染路径。
                param.raw_text,
                param.synthetic,
            )

        # 去掉既有命名前缀，作为 parameter/localparam 重新命名的基础
        normalized_name = self._strip_known_prefixes(param.name.upper())  # 去前缀后的大写参数名

        # FSM 状态常量优先使用 state_prefix，和普通 parameter 区分
        if state:

            # state 参数保持大写常量风格，同时补齐状态前缀
            name = self._apply_prefix(param.name.upper(), self.config["naming"]["state_prefix"])  # 规范化 FSM 状态参数名

        # 普通 parameter 走 parameter_prefix，localparam 保留去前缀基础名
        elif param.keyword == "parameter":

            # parameter 输出到模块头时应有统一参数前缀
            name = self._apply_prefix(param.name.upper(), self.config["naming"]["parameter_prefix"])  # 规范化模块参数名

        # localparam 或其他参数声明不额外套 parameter 前缀
        else:

            # 使用去前缀名称，避免 localparam 被重复加前缀
            name = normalized_name  # 规范化后的本地参数名

        # 返回新的参数模型，保持除名称外的渲染字段不变
        return ParamDecl(
            param.keyword,
            name,
            param.value,

            # 声明规格和注释不参与命名归一化。
            param.decl_spec,
            param.comment,
            list(param.leading_comments),

            # 原始文本和 synthetic 标记继续透传给 renderer。
            param.raw_text,
            param.synthetic,
        )

    # _is_state_param 识别已经按状态参数前缀命名的常量。
    def _is_state_param(self, name: str) -> bool:
        """
        判断参数名是否使用配置中的 FSM 状态前缀。

        :param name: 待检查的参数名。
        :return: 命中状态参数前缀时返回 True。
        """

        # state_prefix 是本 formatter 识别 FSM 状态常量的唯一参数前缀
        return name.startswith(self.config["naming"]["state_prefix"])

    # _is_state_signal_name 识别明确的 current/next state 信号名。
    def _is_state_signal_name(self, name: str) -> bool:
        """
        判断信号名是否是明确的状态寄存器或下一态信号。

        :param name: 待检查的信号名。
        :return: 符合 state_cur/state_next 等固定形态时返回 True。
        """

        # 只接受明确的 current/next state 信号名，避免误判普通业务信号
        return bool(re.fullmatch(r"state_(?:cur|current|next)|cur_state|next_state", name))

    # _looks_like_true_fsm_state_candidate_name 判断宽松状态信号候选名。
    def _looks_like_true_fsm_state_candidate_name(self, name: str) -> bool:
        """
        判断信号名是否像两段式或三段式 FSM 状态信号。

        :param name: 待检查的信号名。
        :return: 命名形态包含 current/next/state 语义时返回 True。
        """

        # FSM 识别统一使用小写文本，避免用户大小写风格影响判断
        str_lowered_name = name.lower()  # 小写化后的候选信号名

        # current/next state 常见命名和 *_state 命名都视作候选
        return bool(
            re.fullmatch(r"state_(?:cur|current|next)", str_lowered_name)
            or re.fullmatch(r"(?:cur|next)_state", str_lowered_name)
            or re.fullmatch(r"(?:n_)?[a-z_]\w*_state", str_lowered_name)
        )

    # _extract_case_selector_name 从 case 头部提取单一 selector。
    def _extract_case_selector_name(self, header: str) -> str | None:
        """
        从 case/casez/casex 头部提取可用于 FSM 推断的 selector 名称。

        :param header: 原始 case 头部文本。
        :return: 单标识符 selector 名称；复杂表达式或不完整头部返回 None。
        """

        # case header 先走 statement 归一化，统一 case(...) 的空白形态
        str_normalized_header = self._normalize_statement_line(header.strip())  # 规范化后的 case 头部

        # 只从 case/casez/casex 头部提取选择信号
        if not str_normalized_header.startswith(("case(", "casez(", "casex(")):

            # 非 case 头部不参与状态常量推断
            return None

        # 左括号位置用于匹配完整选择表达式范围
        int_open_index = str_normalized_header.find("(")  # case 选择表达式左括号位置

        # 缺少左括号表示 header 不完整，交由上层忽略
        if int_open_index == -1:

            # 无法定位选择表达式时不推断状态参数
            return None

        # 匹配右括号，避免 case(expr[3:0]) 这类内部括号干扰
        int_close_index = self._find_matching_paren_in_text(str_normalized_header, int_open_index)  # selector 外层括号的闭合位置

        # 未闭合的 case 头部不生成候选 selector
        if int_close_index == -1:

            # 不完整 header 由格式化主流程继续报结构错误
            return None

        # 选择表达式只接受单个信号名，复杂表达式不作为 FSM selector
        str_selector = str_normalized_header[int_open_index + 1 : int_close_index].strip()  # case 选择信号候选文本

        # 复杂表达式或常量不适合参与状态参数名推断
        if not re.fullmatch(r"[A-Za-z_]\w*", str_selector):

            # 非标识符 selector 直接跳过
            return None

        # 返回可用于 FSM 状态 case 推断的 selector 名称
        return str_selector

    # _extract_case_selector_signal 只保留明确状态信号 selector。
    def _extract_case_selector_signal(self, header: str) -> str | None:
        """
        从 case 头部提取已经符合状态信号命名的 selector。

        :param header: 原始 case 头部文本。
        :return: 明确状态信号 selector；非状态 selector 返回 None。
        """

        # 先提取 case selector，再确认是否符合 state 信号命名
        str_selector = self._extract_case_selector_name(header)  # case 选择信号名

        # 无 selector 时该 case 不能用于状态机识别
        if str_selector is None:

            # 空 selector 保持 None 语义
            return None

        # 只有明确 state 当前/下一状态信号才作为 FSM selector
        return str_selector if self._is_state_signal_name(str_selector) else None

    # _extract_case_label_identifiers 提取 case 标签中的参数标识符候选。
    def _extract_case_label_identifiers(self, label: str) -> set[str]:
        """
        从 case label 中提取可能引用参数常量的标识符。

        :param label: 原始 case item 标签文本。
        :return: 去除数值常量和 default 后的标识符集合。
        """

        # 先规整基数数字空白，便于下一步剔除 Verilog 数值常量
        str_cleaned_label = self._normalize_based_number_spacing(label.strip())  # 规范化后的 case 标签文本

        # 数值型 case label 不是参数名，应从标识符扫描前移除
        str_cleaned_label = re.sub(r"\d+'\s*[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+", " ", str_cleaned_label)  # 去掉数字常量后的标签文本

        # 返回标签中可能引用的参数名，default 不参与 FSM 状态常量推断
        return {
            identifier
            for identifier in re.findall(r"[A-Za-z_]\w*", str_cleaned_label)
            if identifier != "default"
        }

    # _collect_state_case_param_candidates 递归汇总状态 case 标签候选。
    def _collect_state_case_param_candidates(
        self,
        nodes: list[ControlNode],
        selectors: set[str] | None = None,
    ) -> set[str]:
        """
        从控制树中递归收集可视为 FSM 状态参数的 case label 标识符。

        :param nodes: 待扫描的控制树节点列表。
        :param selectors: 限定可贡献状态参数的 case selector 集合。
        :return: case 标签中提取到的状态参数候选集合。
        """

        # set_candidates 汇总所有 case label 中疑似状态参数的标识符
        set_candidates: set[str] = set()  # case label 参数候选集合

        # 递归遍历控制树，覆盖嵌套 if/case 和 alternate 分支
        for node in nodes:

            # 只有 case 节点需要读取 selector；其他节点只递归子树
            str_selector = self._extract_case_selector_name(node.header) if node.kind == "case" else None  # 状态参数扫描使用的 case 选择信号

            # selector 命中状态机信号或显式允许集合时，读取其标签参数
            if node.kind == "case" and (
                (selectors is not None and str_selector in selectors)
                or (selectors is None and self._extract_case_selector_signal(node.header))
            ):

                # 每个 case item 的 label 都可能包含状态参数名
                for item in node.items:

                    # 合并该标签中的状态参数候选
                    set_candidates.update(self._extract_case_label_identifiers(item.label))

            # 递归收集主子树中的状态参数候选
            set_candidates.update(self._collect_state_case_param_candidates(node.children, selectors))

            # 递归收集 else/elsif 分支中的状态参数候选
            set_candidates.update(self._collect_state_case_param_candidates(node.alternate, selectors))

            # case item 子树需要单独遍历，因为 items 不在 children 字段内
            for item in node.items:

                # 合并当前 item 内嵌控制树的候选参数
                set_candidates.update(self._collect_state_case_param_candidates(item.children, selectors))

        # 返回所有可能的状态参数名，后续会再和真实 param 名集合求交
        return set_candidates

    # _state_candidate_targets 提取 always 中命名上像状态信号的目标。
    def _state_candidate_targets(self, block: AlwaysBlock) -> list[str]:
        """
        从 always 目标列表中筛出命名形态像状态信号的候选。

        :param block: 当前待分析的 always 块。
        :return: 命名上像 current/next/state 的目标信号列表。
        """

        # 返回 block.targets 中命名形态符合状态机候选的信号。
        return [
            target  # 命名形态像状态信号的当前目标
            for target in block.targets  # 当前 always 写入的全部目标
            if self._looks_like_true_fsm_state_candidate_name(target)  # 保留 FSM 状态候选命名
        ]

    # _index_next_state_blocks_by_target 为 next-state 组合逻辑建立目标索引。
    def _index_next_state_blocks_by_target(self, always_blocks: list[AlwaysBlock]) -> dict[str, list[AlwaysBlock]]:
        """
        扫描组合 always，为唯一状态目标建立 next-state 候选索引。

        :param always_blocks: 已解析出的 always 块列表。
        :return: next-state 目标名到组合 always 候选块列表的映射。
        """

        # dict_next_blocks_by_target 保存 next-state 目标到组合块的索引。
        dict_next_blocks_by_target: dict[str, list[AlwaysBlock]] = {}  # next-state 目标到组合 always 的索引

        # 只把唯一状态目标的组合块记入索引，供寄存块回查。
        for block in always_blocks:

            # 时序寄存块不会提供 next-state 组合转移逻辑。
            if not block.is_combinational:

                # 当前块不是组合逻辑时跳过索引建立。
                continue

            # list_state_targets 保存该组合块中唯一允许的状态目标候选。
            list_state_targets = self._state_candidate_targets(block)  # 当前组合块中的状态目标候选

            # 多目标或无目标的组合块不参与 current/next 推断。
            if len(list_state_targets) != 1:

                # 模糊组合块不会建立 next-state 索引。
                continue

            # 记录写同一 next-state 目标的组合块候选。
            dict_next_blocks_by_target.setdefault(list_state_targets[0], []).append(block)

        # 返回供时序寄存块回查的 next-state 索引。
        return dict_next_blocks_by_target

    # _has_fsm_observer_block 确认状态对还被第三类逻辑引用，避免误判寄存器链。
    def _has_fsm_observer_block(
        self,
        always_blocks: list[AlwaysBlock],
        register_block: AlwaysBlock,
        next_state_block: AlwaysBlock,
        current_target: str,
        next_target: str,
    ) -> bool:
        """
        检查除寄存块和 next-state 组合块外，是否还有逻辑引用该状态对。

        :param always_blocks: 当前模块的全部 always 块。
        :param register_block: 写 current-state 的时序寄存块。
        :param next_state_block: 写 next-state 的组合块。
        :param current_target: current-state 信号名。
        :param next_target: next-state 信号名。
        :return: 存在第三类引用逻辑时返回 True。
        """

        # 只有存在额外观察者块时才把该对识别成完整状态机。
        return any(
            other is not register_block
            and other is not next_state_block
            and self._block_references_any_signal_name(other, {current_target, next_target})
            for other in always_blocks
        )

    # _detect_true_fsm_pair_for_register_block 从单个时序块反推出 current/next 状态对。
    def _detect_true_fsm_pair_for_register_block(
        self,
        block: AlwaysBlock,
        always_blocks: list[AlwaysBlock],
        next_blocks_by_target: dict[str, list[AlwaysBlock]],
    ) -> tuple[str, str] | None:
        """
        基于时序寄存块和 next-state 组合块关系识别单个 FSM 状态对。

        :param block: 当前待分析的时序 always 块。
        :param always_blocks: 当前模块的全部 always 块。
        :param next_blocks_by_target: next-state 组合块索引。
        :return: 识别出的 `(current_state, next_state)`；无法确认时返回 None。
        """

        # 非唯一状态目标的时序块不参与标准 FSM current-state 推断。
        list_state_targets = self._state_candidate_targets(block)  # 当前时序块中的状态目标候选

        # 只有唯一状态目标的寄存块才继续参与 current/next 推断。
        if len(list_state_targets) != 1:

            # 多目标寄存块无法唯一确定 current-state。
            return None

        # current_target 是当前时序块写入的 current-state 信号。
        current_target = list_state_targets[0]  # 当前态寄存器目标名

        # set_state_sources 保存 current-state 简单赋值右值中像状态信号的来源。
        set_state_sources = {
            source  # 可能作为 next-state 的右值来源
            for source in self._extract_simple_state_sources_for_target(block, current_target, set())  # 寄存赋值来源集合
            if self._looks_like_true_fsm_state_candidate_name(source)  # 只保留状态命名形态
        }

        # 来源不唯一时无法确认 next-state。
        if len(set_state_sources) != 1:

            # 复位或普通寄存器链会在这里被保守排除。
            return None

        # str_next_target 是 current-state 寄存块读取的下一态信号。
        str_next_target = next(iter(set_state_sources))  # 状态寄存器读取的下一态信号名

        # 自保持赋值不能证明存在独立 next-state 逻辑。
        if str_next_target == current_target:

            # current 自赋值的寄存块不是两段式状态机。
            return None

        # block_next_state 保存写 next-state 且引用 current-state 的组合块。
        block_next_state = next(  # 确认后的 next-state 组合块
            (
                candidate  # 引用 current-state 的 next-state 组合块
                for candidate in next_blocks_by_target.get(str_next_target, [])  # 写 next-state 的组合块候选
                if self._block_references_any_signal_name(candidate, {current_target})  # 确认状态转移依赖
            ),
            None,  # 没有命中组合转移块时的默认值
        )

        # 缺少 next-state 组合块时不建立 FSM 状态对。
        if block_next_state is None:

            # next-state 没有依赖 current-state 时更像普通寄存器链。
            return None

        # 第三类观察者块用于区分完整状态机和简单寄存链。
        if not self._has_fsm_observer_block(
            always_blocks,
            register_block=block,
            next_state_block=block_next_state,
            current_target=current_target,
            next_target=str_next_target,
        ):

            # 缺少任务或输出逻辑引用时不视作真实 FSM。
            return None

        # 返回该时序块识别出的 current/next 状态对。
        return (current_target, str_next_target)

    # _detect_true_fsm_state_pairs 从 always 关系确认 current/next 状态对。
    def _detect_true_fsm_state_pairs(self, always_blocks: list[AlwaysBlock]) -> set[tuple[str, str]]:
        """
        从组合与时序 always 的连接关系中识别真实 FSM 状态信号配对。

        :param always_blocks: 已解析出的 always 块列表。
        :return: current-state 与 next-state 信号名组成的配对集合。
        """

        # 构造按 next-state 目标回查的组合逻辑索引。
        dict_next_blocks_by_target = self._index_next_state_blocks_by_target(always_blocks)  # next-state 组合块索引

        # set_detected_pairs 保存 current-state 与 next-state 的真实配对。
        set_detected_pairs: set[tuple[str, str]] = set()  # FSM 状态信号配对集合

        # 第二轮扫描时序 always，从寄存赋值关系反推出 current/next。
        for block in always_blocks:

            # 组合块已经在索引中处理过，此处只看状态寄存块。
            if block.is_combinational:

                # 寄存块扫描阶段跳过组合逻辑。
                continue

            # state_pair 汇总当前寄存块是否形成完整的状态机链路。
            state_pair = self._detect_true_fsm_pair_for_register_block(block, always_blocks, dict_next_blocks_by_target)  # 当前寄存块形成的状态对

            # 没有识别出状态对时继续扫描后续时序块。
            if state_pair is None:

                # 当前寄存块不满足真实 FSM 的三段关系约束。
                continue

            # 三段关系都成立时才登记 FSM 状态对。
            set_detected_pairs.add(state_pair)

        # 返回确认后的 current/next 状态信号配对。
        return set_detected_pairs

    # _infer_state_param_names 从 case 标签和状态信号配对推断状态参数。
    def _infer_state_param_names(self, params: list[ParamDecl], always_blocks: list[AlwaysBlock]) -> set[str]:
        """
        推断应按 FSM 状态常量格式化的参数名集合。

        :param params: 模块内已解析的参数声明。
        :param always_blocks: 模块内已解析的 always 块。
        :return: 已声明且被状态机 case 引用的参数名集合。
        """

        # 参数名集合用于过滤 case label 中的非参数标识符。
        set_param_names = {param.name for param in params if param.name}  # 已声明参数名集合

        # set_inferred 汇总 formatter 需要按状态常量处理的参数。
        set_inferred: set[str] = set()  # 推断得到的状态参数名集合

        # 先从明确 state selector 的 case label 中提取状态参数。
        for block in always_blocks:

            # 没有控制树的 always 不能贡献 case label。
            if not block.nodes:

                # 空控制树的 always 无法提供状态枚举标签。
                continue

            # 只接受实际声明过的参数名。
            set_inferred.update(self._collect_state_case_param_candidates(block.nodes) & set_param_names)

        # 再用 current/next 状态配对收窄组合状态转移块。
        for current_target, next_target in self._detect_true_fsm_state_pairs(always_blocks):

            # set_allowed_selectors 限定能贡献状态枚举的 case selector。
            set_allowed_selectors = {current_target, next_target}  # 当前 FSM 允许的 selector 集合

            # 只检查写入 next-state 目标的组合 always。
            for block in always_blocks:

                # 非组合块、空控制树或非 next-state 目标都跳过。
                if not block.nodes or not block.is_combinational or next_target not in block.targets:

                    # 只处理当前状态对对应的组合转移块。
                    continue

                # 取 case label 候选与已声明参数的交集。
                set_inferred.update(
                    self._collect_state_case_param_candidates(block.nodes, set_allowed_selectors) & set_param_names
                )

        # 返回所有被确认可作为 FSM 状态常量的参数名。
        return set_inferred

    # _collect_true_fsm_state_targets 筛选已声明的状态类 always 目标。
    def _collect_true_fsm_state_targets(self, block: AlwaysBlock, decl_names: set[str]) -> list[str]:
        """
        返回 always 块中已声明且像 FSM 状态信号的写入目标。

        :param block: 待检查的 always 块。
        :param decl_names: 当前模块已经声明的信号名集合。
        :return: 可参与 FSM 状态归一化的目标名列表。
        """

        # 只返回已声明且命名形态符合 FSM 状态信号的 always 目标。
        return [
            target
            for target in block.targets
            if target in decl_names and self._looks_like_true_fsm_state_candidate_name(target)
        ]

    # _extract_simple_state_sources_for_target 提取状态寄存赋值右侧来源。
    def _extract_simple_state_sources_for_target(
        self,
        block: AlwaysBlock,
        target: str,
        decl_names: set[str] | None,
    ) -> set[str]:
        """
        提取目标信号简单赋值右侧出现的状态信号来源。

        :param block: 待扫描的 always 块。
        :param target: current-state 寄存器目标名。
        :param decl_names: 可选的已声明信号白名单。
        :return: 赋值右侧命名像状态信号的来源集合。
        """

        # set_sources 收集 target 简单赋值右侧出现的单标识符来源。
        set_sources: set[str] = set()  # 简单状态来源名集合

        # 该模式只接受 target <= name; 或 target = name; 的状态寄存赋值。
        pattern_state_assignment: re.Pattern[str] = re.compile(  # 简单状态赋值正则
            rf"\b{re.escape(target)}\b\s*(?:<=|=)\s*([A-Za-z_]\w*)\s*;"  # 简单状态赋值片段
        )

        # 逐行处理 always body，避免注释文本干扰赋值识别。
        for raw_line in block.lines:

            # 去掉 Verilog 行注释后再规范化空白。
            str_code, _ = self._split_comment(raw_line)  # 不含行注释的 Verilog 代码

            # normalized 用于兼容不同行内空白写法。
            str_normalized_line = self._normalize_statement_line(str_code.strip())  # 规范化后的语句文本

            # 空行和纯注释行没有状态来源。
            if not str_normalized_line:

                # 纯注释或空白行不包含赋值来源。
                continue

            # 同一行可能包含多个简单赋值片段。
            for match in pattern_state_assignment.finditer(str_normalized_line):

                # source 是当前赋值右侧的候选状态信号。
                str_source = match.group(1)  # 简单赋值右侧信号名

                # 来源需要像状态信号，并满足可选声明白名单。
                if self._looks_like_true_fsm_state_candidate_name(str_source) and (
                    decl_names is None or not decl_names or str_source in decl_names
                ):

                    # 符合白名单的来源才参与状态配对推断。
                    set_sources.add(str_source)

        # 返回当前 target 读取到的状态来源集合。
        return set_sources

    # _block_references_any_signal_name 检查 always 文本是否引用给定信号。
    def _block_references_any_signal_name(self, block: AlwaysBlock, names: set[str]) -> bool:
        """
        判断 always 块头部或正文是否引用任一指定信号名。

        :param block: 待检查的 always 块。
        :param names: 需要按词边界匹配的信号名集合。
        :return: 任一信号名在块文本中出现时返回 True。
        """

        # 空集合无法形成引用命中。
        if not names:

            # 没有候选信号时必然无法命中。
            return False

        # header 与 body 一起检查，覆盖 sensitivity、case 和赋值表达式。
        str_block_text = "\n".join([block.header, *block.lines])  # always 头部和正文拼接文本

        # 词边界匹配避免 state 命中 state_next 这类前缀信号。
        return any(re.search(rf"\b{re.escape(name)}\b", str_block_text) for name in names)

    # _build_true_fsm_state_rename_map 构造 current/next 状态信号重命名表。
    def _build_true_fsm_state_rename_map(
        self,
        decls: list[SignalDecl],
        always_blocks: list[AlwaysBlock],
    ) -> dict[str, str]:
        """
        为确认的单组 FSM current/next 状态信号构造标准命名映射。

        :param decls: 模块内信号声明列表。
        :param always_blocks: 模块内 always 块列表。
        :return: 旧状态信号名到标准状态信号名的映射。
        """

        # 缺少声明或 always 时不能可靠建立 FSM 信号重命名。
        if not decls or not always_blocks:

            # 信息不足时保持用户原始状态信号名。
            return {}

        # 声明名集合排除只在表达式文本中出现的伪候选。
        set_decl_names = {decl.name for decl in decls if decl.name}  # 模块内信号声明名集合

        # 仅保留 current/next 两端都真实声明的 FSM 配对。
        set_detected_pairs = {
            (current_target, next_target)  # 两端都在声明表中的 FSM 状态信号配对
            for current_target, next_target in self._detect_true_fsm_state_pairs(always_blocks)  # 候选 current/next 配对
            if current_target in set_decl_names and next_target in set_decl_names  # 排除表达式伪信号
        }  # 已声明状态信号配对集合

        # 多组配对会造成命名歧义，保持用户原始命名。
        if len(set_detected_pairs) != 1:

            # 多状态机或不完整关系不做自动重命名。
            return {}

        # 唯一配对展开为 current/next 标准名。
        str_current_target, str_next_target = next(iter(set_detected_pairs))  # 唯一确认的 current/next 状态信号名

        # 防御异常重复配对，避免生成无意义映射。
        if len({str_current_target, str_next_target}) not in {1, 2}:

            # 该分支保留兼容防御，正常二元集合不会触发。
            return {}

        # dict_rename_map 定义 formatter 输出使用的标准状态名。
        dict_rename_map = {
            str_current_target: "state_current",  # current-state 标准输出名
            str_next_target: "state_next",  # 下一态组合信号标准输出名
        }  # FSM 状态信号重命名映射

        # 已经是标准名的条目无需进入替换表。
        return {old: new for old, new in dict_rename_map.items() if old != new}

    # _collect_direct_output_ports 汇总 assign/generate 直接驱动的输出端口。
    def _collect_direct_output_ports(
        self,
        ports: list[PortDecl],
        assigns: list[AssignStmt],
        generate_blocks: list[GenerateBlock],
    ) -> set[str]:
        """
        收集被连续赋值或 generate 控制树直接驱动的 output 端口。

        :param ports: 模块端口声明列表。
        :param assigns: 顶层连续赋值列表。
        :param generate_blocks: 模块内 generate 块列表。
        :return: 直接驱动 output 端口名集合。
        """

        # 输出端口名集合限定 direct output 检查边界。
        set_output_names = {port.name for port in ports if port.direction == "output"}  # 模块 output 端口名集合

        # set_direct_outputs 记录 assign/generate 直接驱动的 output 端口。
        set_direct_outputs: set[str] = set()  # 直接驱动的 output 端口集合

        # 连续赋值左值需要通过 lvalue parser 统一展开。
        for assign in assigns:

            # 左值不稳定时保持原先宽容行为，由后续 strict gate 报告。
            try:

                # l_value_ref_assign 保留连续赋值左侧的结构化解析结果。
                l_value_ref_assign: LValueRef = self._parse_lvalue(  # 连续赋值左值解析结果
                    assign.lhs,  # assign 左侧原始文本
                    "lvalue_normalization_violation",  # 左值解析失败分类
                    "Use a stable assign left-hand side such as foo, foo[idx], foo[msb:lsb], or {foo, bar}.",  # 用户修复建议
                    allow_concat=True,  # 允许 concat 左值展开为多个基础信号
                )

            # 非稳定左值由后续 Verilog 质量门继续报告。
            except VerilogFormatterError:

                # 当前连续赋值不能可靠判断 direct output。
                continue

            # concat 或位选左值会展开为基础信号名。
            for lhs in self._extract_lvalue_bases(l_value_ref_assign):

                # 只记录直接写模块 output 端口的左值。
                if lhs in set_output_names:

                    # 顶层 assign 已直接驱动该 output 端口。
                    set_direct_outputs.add(lhs)

        # generate 控制树中的过程赋值也可能直接写 output。
        for block in generate_blocks:

            # 合并当前 generate 块中发现的直接 output 写入。
            set_direct_outputs |= self._collect_direct_output_ports_from_nodes(block.nodes, set_output_names)  # generate 内直接驱动端口

        # 返回全部直接驱动的 output 端口名。
        return set_direct_outputs

    # _collect_direct_output_ports_from_node 扫描单个控制节点中的 output 直写目标。
    def _collect_direct_output_ports_from_node(self, node: ControlNode, output_names: set[str]) -> set[str]:
        """
        扫描单个控制节点，递归收集其中直接写 output 端口的目标。

        :param node: 当前待扫描的控制节点。
        :param output_names: 模块 output 端口名集合。
        :return: 当前节点及其子树中直接驱动的 output 端口集合。
        """

        # statement 节点只需要检查当前语句左值。
        if node.kind == "statement":

            # 返回当前语句中命中的 output 左值集合。
            return {
                lhs  # 当前 statement 直接写入的 output 端口
                for lhs in self._extract_assign_lvalue_bases(node.text, "lvalue_normalization_violation")  # 当前语句的赋值左值基础信号
                if lhs in output_names  # 只保留模块 output 端口
            }

        # set_direct_outputs 先收集 children 子树里已经出现的 output 直写目标。
        set_direct_outputs = self._collect_direct_output_ports_from_nodes(node.children, output_names)  # children 子树命中的 output 集合

        # set_alt_direct_outputs 负责补扫 else 和 elsif 路径里的 output 写入。
        set_alt_direct_outputs = self._collect_direct_output_ports_from_nodes(  # else/elsif 路径里直写的 output 端口
            self._expanded_alternate_nodes(node.alternate),  # 展开后的反向控制路径
            output_names,  # 本模块 output 端口名集合
        )

        # 把 alternate 路径的命中结果并回主集合，供 case item 分支继续累加。
        set_direct_outputs |= set_alt_direct_outputs  # 汇总 children 与 alternate 的 output 直写目标

        # case item 子树不在 children 字段中，需要逐项递归。
        for item in node.items:

            # 合并当前 case item 分支中的 output 写入。
            set_direct_outputs |= self._collect_direct_output_ports_from_nodes(item.children, output_names)  # 当前 case item 的 output 直写目标

        # 返回单个控制节点及其全部分支命中的 output 集合。
        return set_direct_outputs

    # _collect_direct_output_ports_from_nodes 递归扫描控制树内的 output 写入。
    def _collect_direct_output_ports_from_nodes(self, nodes: list[ControlNode], output_names: set[str]) -> set[str]:
        """
        从控制树节点中递归收集直接写 output 端口的目标。

        :param nodes: 待扫描的控制树节点列表。
        :param output_names: 模块 output 端口名集合。
        :return: 当前控制树范围内直接驱动的 output 端口集合。
        """

        # set_direct_outputs 汇总当前控制树范围内直接写 output 的目标。
        set_direct_outputs: set[str] = set()  # 控制树内直接驱动的 output 端口集合

        # 深度遍历 if/case/else 等控制树节点。
        for node in nodes:

            # 合并当前节点及其递归子树中的 output 直写目标。
            set_direct_outputs |= self._collect_direct_output_ports_from_node(node, output_names)  # 当前节点及其子树的 output 直写目标

        # 返回当前控制树中发现的 direct output 端口。
        return set_direct_outputs

    # _split_always_blocks 按配置决定是否拆分 always 块。
    def _split_always_blocks(self, blocks: list[AlwaysBlock]) -> list[AlwaysBlock]:
        """
        根据配置把可拆分 always 块展开为单目标 always 序列。

        :param blocks: 解析后的 always 块列表。
        :return: 原始 always 块或拆分后的 always 块列表。
        """

        # 配置关闭自动拆分时保留原 always 列表。
        if not self.config["always_rules"]["auto_split"]:

            # 用户关闭自动拆分时不改变 always 结构。
            return blocks

        # list_split_blocks 保存所有拆分后的 always，顺序跟随输入块。
        list_split_blocks: list[AlwaysBlock] = []  # 自动拆分后的 always 块序列

        # 每个 always 独立拆分，便于错误定位到原始块。
        for block in blocks:

            # 当前 always 的拆分结果按原顺序追加。
            list_split_blocks.extend(self._split_single_always_block(block))

        # 返回 renderer 可直接消费的 always 序列。
        return list_split_blocks

    # _always_split_suggestion 生成 always 拆分失败时的用户提示。
    def _always_split_suggestion(self, *, inside_generate: bool = False) -> str:
        """
        返回 always 自动拆分失败时的人工修复建议。

        :param inside_generate: 当前 always 是否位于 generate 作用域内。
        :return: 面向用户的拆分建议文本。
        """

        # generate 内部提示需要强调作用域，便于用户手工处理。
        if inside_generate:

            # generate 作用域内不能安全自动移动 always。
            return "Split multi-target always blocks inside generate into one target per block before formatting."

        # 顶层 always 保留既有用户提示文本。
        return "Split this multi-target always block manually before formatting."

    # _split_single_always_block 将单个多目标 always 拆成若干单目标块。
    def _split_single_always_block(self, block: AlwaysBlock, *, inside_generate: bool = False) -> list[AlwaysBlock]:
        """
        将结构可隔离的多目标 always 块拆分为单目标 always 块。

        :param block: 待拆分的 always 块。
        :param inside_generate: 当前 always 是否位于 generate 作用域内。
        :return: 原块或拆分后的 always 块列表。
        :raises VerilogFormatterError: strict 模式下遇到不可安全拆分的多目标 always 时抛出。
        """

        # 配置关闭时不改变当前 always。
        if not self.config["always_rules"]["auto_split"]:

            # 用户关闭自动拆分时直接保留原块。
            return [block]

        # 单目标 always 已满足结构要求。
        if len(block.targets) <= 1:

            # 单目标块无需进入控制树过滤流程。
            return [block]

        # set_known_targets 是拆分期间允许出现的赋值目标全集。
        set_known_targets = set(block.targets)  # 当前多目标 always 的目标集合

        # 控制树无法按目标隔离时不能自动拆分。
        if not self._can_split_case_based_always(block.nodes, set_known_targets):

            # 示例兼容模式只记录保留原因，不抛出 strict error。
            if self._example_compat_enabled():

                # str_location_suffix 标识该 always 是否来自 generate 作用域。
                str_location_suffix = " inside generate" if inside_generate else ""  # 多目标 always 保留位置说明

                # 示例模式记录保留原因，避免改变旧 fixture 输出。
                self.violations.append(
                    f"Retained multi-target always block{str_location_suffix} without split: {block.header}"
                )

                # 示例兼容路径保留原始多目标 always。
                return [block]

            # verilog_formatter_error_split 保存原有 strict 诊断内容，外层补齐项目错误前缀。
            verilog_formatter_error_split: VerilogFormatterError = self._strict_error(  # 不可拆分 always 的原始异常
                "always_split_violation",  # strict 错误分类
                block.header,  # 触发拆分失败的 always 头
                self._always_split_suggestion(inside_generate=inside_generate),  # 面向用户的拆分建议
            )

            # strict 模式要求用户先手工拆分。
            raise VerilogFormatterError(
                f"> ERR: [Python] Strict always split failure: {verilog_formatter_error_split}"
            )

        # 记录自动拆分动作，便于 formatter 输出协议保留诊断。
        self.violations.append(f"Split structured always block into {len(block.targets)} single-target blocks.")

        # list_split_blocks 按 target 顺序保存新 always。
        list_split_blocks: list[AlwaysBlock] = []  # 当前 always 拆出的单目标块

        # 每个目标独立过滤控制树并重新分析 always 模型。
        for target in block.targets:

            # 自保持语句用于补齐拆分后缺失赋值的分支。
            str_hold_statement = self._build_split_self_hold_statement(  # 当前 target 的自保持赋值语句
                block,  # 原始多目标 always 块
                target,  # 本轮拆分输出信号名
                set_known_targets,  # 原 always 写入目标白名单
            )

            # list_filtered_nodes 只包含写入当前 target 的控制树片段。
            list_filtered_nodes = self._filter_control_nodes_for_target(  # 当前 target 的过滤控制树
                block.nodes,  # 原始 always 控制树
                target,  # 保留语句归属的目标名
                set_known_targets,  # 允许自动移除的兄弟目标集合
                str_hold_statement,  # 缺失分支自保持语句
            )

            # 某个 target 没有可渲染节点时需要退回或报错。
            if not list_filtered_nodes:

                # 示例兼容模式保留原块，避免输出空 always。
                if self._example_compat_enabled():

                    # str_location_suffix 标识过滤失败发生在哪个 always 作用域。
                    str_location_suffix = " inside generate" if inside_generate else ""  # 空过滤节点的作用域说明

                    # 示例模式保留原始块，并记录空拆分原因。
                    self.violations.append(
                        f"Retained multi-target always block{str_location_suffix} without split: {block.header}"
                    )

                    # 空过滤结果不能生成安全的单目标 always。
                    return [block]

                # verilog_formatter_error_empty_split 保存空过滤拆分的原始 strict 诊断。
                verilog_formatter_error_empty_split: VerilogFormatterError = self._strict_error(  # 空拆分结果异常
                    "always_split_violation",  # 空过滤结果归入 always 拆分违规
                    block.header,  # 无法生成目标节点的 always 头
                    self._always_split_suggestion(inside_generate=inside_generate),  # 按所在作用域生成修复提示
                )

                # strict 模式拒绝不完整拆分。
                raise VerilogFormatterError(
                    f"> ERR: [Python] Empty always split result: {verilog_formatter_error_empty_split}"
                )

            # 渲染过滤后的控制树，再复用 always 分析入口建立模型。
            list_filtered_lines = self._render_control_nodes(list_filtered_nodes, 0)  # 单目标控制树渲染行

            # always_block_split 沿用原 header，但目标集合收窄为当前 target。
            always_block_split: AlwaysBlock = self._analyze_always_block(  # 重新分析后的单目标 always 块
                block.header,  # 沿用原 always 头
                list_filtered_lines,  # 当前 target 的控制树渲染文本
                [target],  # 单目标 always 的目标集合
                "\n".join([block.header, *list_filtered_lines]),  # 重新分析使用的 always 原文
            )

            # 用户原始 leading comments 随拆分块复制，避免注释丢失。
            always_block_split.leading_comments = list(block.leading_comments)  # 拆分块继承原始前导注释

            # 追加到结果列表，维持原 target 顺序。
            list_split_blocks.append(always_block_split)

        # 返回当前 always 的全部单目标拆分结果。
        return list_split_blocks

    # _can_split_case_based_always 判断控制树是否可按目标隔离。
    def _can_split_case_based_always(self, nodes: list[ControlNode], expected_targets: set[str]) -> bool:
        """
        判断 case/if 控制树是否满足按目标拆分 always 的条件。

        :param nodes: 待检查的控制树节点列表。
        :param expected_targets: 当前 always 允许写入的目标集合。
        :return: 控制树可按目标隔离时返回 True。
        """

        # 拆分合法性由控制树递归检查统一判定。
        return self._control_nodes_are_target_isolatable(nodes, expected_targets)

    # _statement_targets_for_split 提取单条语句的拆分目标。
    def _statement_targets_for_split(self, text: str, expected_targets: set[str]) -> set[str] | None:
        """
        从过程赋值语句中提取可用于 always 拆分的目标集合。

        :param text: 待分析的过程语句文本。
        :param expected_targets: 当前 always 允许写入的目标集合。
        :return: 语句写入目标集合；无法安全解析或越界时返回 None。
        """

        # 过程赋值左值需要先统一解析，才能判断是否可隔离。
        list_lvalues = self._extract_procedural_lvalues(  # 当前语句中的过程赋值左值
            text,  # 待分析的过程赋值语句
            "always_split_violation",  # 复杂左值触发的 strict 分类
            "Use a stable assignment target form such as foo, foo[idx], foo[msb:lsb], or {foo, bar} before formatting.",  # 左值稳定性建议
        )

        # 无赋值语句不会影响 always 目标隔离。
        if not list_lvalues:

            # 空目标集合表示当前语句可安全保留在每个拆分块中。
            return set()

        # set_statement_targets 汇总该语句实际写入的 always 目标。
        set_statement_targets: set[str] = set()  # 当前语句实际写入的目标基础名集合

        # 每个左值必须能解析成唯一基础信号。
        for lvalue in list_lvalues:

            # 拼接赋值跨多个目标，无法安全分配给单一拆分块。
            if lvalue.kind == "concat":

                # 拼接左值会同时覆盖多个基础目标，不能放入单目标 always。
                return None

            # list_bases 是位选、切片或普通左值对应的基础信号。
            list_bases = self._extract_lvalue_bases(lvalue)  # 左值基础信号列表

            # 无法唯一确定基础信号时不自动拆分。
            if len(list_bases) != 1:

                # 多基础目标或空解析结果都会破坏目标隔离。
                return None

            # str_base 是当前赋值写入的基础目标名。
            str_base = list_bases[0]  # 当前语句写入的基础信号名

            # 写入 expected_targets 之外的信号说明该语句不可复制。
            if str_base not in expected_targets:

                # 外部目标赋值不能安全复制到每个拆分块。
                return None

            # 当前基础目标参与语句隔离判定。
            set_statement_targets.add(str_base)

        # 多目标语句不能归属到单一拆分块。
        if len(set_statement_targets) > 1:

            # 单条语句写多个 always 目标时必须保留原块。
            return None

        # 返回该语句唯一写入目标或空集合。
        return set_statement_targets

    # _expanded_alternate_nodes 把 alternate 路径统一展开为可递归扫描的节点列表。
    def _expanded_alternate_nodes(self, alternate: list[ControlNode]) -> list[ControlNode]:
        """
        把 else 包装和 elsif 节点统一转换成递归 helper 可直接消费的节点列表。

        :param alternate: if 节点的 alternate 控制节点列表。
        :return: 展开后的 alternate 节点序列。
        """

        # 空 alternate 直接返回空列表，调用方可无条件递归。
        if not alternate:

            # 当前 if 没有 else/elsif 路径。
            return []

        # else 包装节点需要展开 children，elsif 等节点保持原样递归。
        return alternate[0].children if alternate[0].kind == "else" else alternate

    # _control_node_is_target_isolatable 判定单个控制节点能否按目标安全隔离。
    def _control_node_is_target_isolatable(self, node: ControlNode, expected_targets: set[str]) -> bool:
        """
        检查单个控制节点及其子树是否满足按目标拆分 always 的安全条件。

        :param node: 当前待检查的控制节点。
        :param expected_targets: 当前 always 允许写入的目标集合。
        :return: 当前节点及其子树可安全隔离时返回 True。
        """

        # 注释节点不写信号，天然满足目标隔离。
        if node.kind == "comment":

            # 注释不携带赋值目标。
            return True

        # statement 节点必须能解析成空目标或单一合法目标。
        if node.kind == "statement":

            # None 表示无法安全归属到单一拆分目标。
            return self._statement_targets_for_split(node.text, expected_targets) is not None

        # 条件分支节点要求 then 和 else/elsif 两侧都能安全隔离。
        if node.kind == "if":

            # then/alternate 两侧同时通过时当前 if 才可拆分。
            return self._control_nodes_are_target_isolatable(
                node.children,
                expected_targets,
            ) and self._control_nodes_are_target_isolatable(
                self._expanded_alternate_nodes(node.alternate),
                expected_targets,
            )

        # case 节点要求每个标签子树都可独立隔离。
        if node.kind == "case":

            # 任一 item 子树失败都会阻止拆分。
            return all(
                self._control_nodes_are_target_isolatable(item.children, expected_targets)
                for item in node.items
            )

        # 包装节点自身不赋值，只递归检查内部子树。
        if node.kind in {"loop", "generate", "else", "block", "always_block"}:

            # 容器内部全部可隔离时当前包装节点可保留。
            return self._control_nodes_are_target_isolatable(node.children, expected_targets)

        # 未显式分类的节点保持旧行为，按不影响拆分处理。
        return True

    # _control_nodes_are_target_isolatable 递归判定控制树拆分安全性。
    def _control_nodes_are_target_isolatable(self, nodes: list[ControlNode], expected_targets: set[str]) -> bool:
        """
        递归检查控制树是否只包含可按目标隔离的语句。

        :param nodes: 待检查的控制树节点列表。
        :param expected_targets: 当前 always 允许写入的目标集合。
        :return: 所有节点都可安全隔离时返回 True。
        """

        # 控制树节点逐个验证，任一节点失败即阻止自动拆分。
        for node in nodes:

            # 单个节点不可隔离时整体立即失败。
            if not self._control_node_is_target_isolatable(node, expected_targets):

                # 当前节点或其子树存在越界/复杂写入。
                return False

        # 所有控制节点均满足拆分隔离条件。
        return True

    # _has_noncomment_control_content 判断控制树是否包含可渲染语义节点。
    def _has_noncomment_control_content(self, nodes: list[ControlNode]) -> bool:
        """
        判断控制树节点列表是否包含注释以外的可渲染内容。

        :param nodes: 待检查的控制树节点列表。
        :return: 存在 statement、case、if 或其他语义子树时返回 True。
        """

        # 递归扫描控制节点，跳过纯注释。
        for node in nodes:

            # 注释节点不算可渲染语义内容。
            if node.kind == "comment":

                # 纯注释节点不影响空分支判定。
                continue

            # statement 节点代表真实 Verilog 语句。
            if node.kind == "statement":

                # 语句节点说明该控制树仍有需要渲染的内容。
                return True

            # case 节点需要检查每个 item 的子树。
            if node.kind == "case":

                # 任一 case item 含语义内容即可视为非空。
                if any(self._has_noncomment_control_content(item.children) for item in node.items):

                    # case 分支内存在真实语句，外层 case 需要保留。
                    return True

                # 当前 case 全为空时继续检查兄弟节点。
                continue

            # children 子树中存在语义内容时当前节点非空。
            if self._has_noncomment_control_content(node.children):

                # 主子树保留语句时当前包装节点仍需渲染。
                return True

            # alternate 子树同样可能保存 else/elsif 的可渲染内容。
            if self._has_noncomment_control_content(node.alternate):

                # alternate 路径中存在语义内容时当前节点非空。
                return True

        # 全部节点都是注释或空包装节点时视为无语义内容。
        return False

    # _extract_assignment_operator_from_statement 提取过程赋值操作符。
    def _extract_assignment_operator_from_statement(self, text: str) -> str | None:
        """
        从过程赋值语句中提取首个赋值操作符。

        :param text: 待分析的 Verilog 过程语句文本。
        :return: 首个赋值操作符 ``<=`` 或 ``=``；无赋值时返回 None。
        """

        # list_code_parts 保存去注释后的非空代码片段。
        list_code_parts: list[str] = []  # 去注释后的过程语句片段

        # 多行语句逐行去掉行注释再拼接。
        for raw_line in text.splitlines():

            # str_code 是当前行去掉 Verilog 行注释后的代码。
            str_code, _ = self._split_comment(raw_line)  # 不含行注释的语句片段

            # str_stripped_code 用于跳过空行和纯注释行。
            str_stripped_code = str_code.strip()  # 去空白后的语句片段

            # 非空片段参与后续赋值操作符扫描。
            if str_stripped_code:

                # 保留片段顺序，避免跨行赋值被打乱。
                list_code_parts.append(str_stripped_code)

        # str_working 是去注释后合并成单行的过程语句。
        str_working = " ".join(list_code_parts)  # 单行化过程语句文本

        # 没有可扫描语句时不返回操作符。
        if not str_working:

            # 空文本无法推断赋值操作符。
            return None

        # list_assignment_positions 保存所有赋值操作符起始位置。
        list_assignment_positions = self._find_procedural_assignment_operators(str_working)  # 过程赋值操作符位置

        # 无赋值操作符时不参与自保持语句推断。
        if not list_assignment_positions:

            # 未发现过程赋值时调用方继续使用默认操作符。
            return None

        # int_operator_index 定位第一个赋值操作符。
        int_operator_index = list_assignment_positions[0]  # 首个过程赋值操作符位置

        # 根据实际文本区分非阻塞和阻塞赋值。
        return "<=" if str_working.startswith("<=", int_operator_index) else "="

    # _assignment_operator_for_statement_target 从单条 statement 中提取目标赋值操作符。
    def _assignment_operator_for_statement_target(
        self,
        text: str,
        target: str,
        known_targets: set[str],
    ) -> str | None:
        """
        检查单条过程语句是否写入 target，并返回该语句使用的赋值操作符。

        :param text: 当前待检查的 statement 文本。
        :param target: 需要查找赋值操作符的目标信号名。
        :param known_targets: 当前 always 的合法目标集合。
        :return: 命中 target 时返回 `=` 或 `<=`；否则返回 None。
        """

        # set_target_bases 保存当前语句写入的可隔离目标集合。
        set_target_bases = self._statement_targets_for_split(text, known_targets)  # 当前语句写入目标集合

        # 只有命中 target 的语句才需要提取赋值操作符。
        if set_target_bases and target in set_target_bases:

            # 返回目标首次出现时采用的阻塞或非阻塞赋值符号。
            return self._extract_assignment_operator_from_statement(text)

        # 当前 statement 没有写入目标信号。
        return None

    # _find_target_assignment_operator_in_node 递归查找单个控制节点中的目标赋值操作符。
    def _find_target_assignment_operator_in_node(
        self,
        node: ControlNode,
        target: str,
        known_targets: set[str],
    ) -> str | None:
        """
        递归扫描单个控制节点，查找目标信号首次赋值使用的操作符。

        :param node: 当前待扫描的控制节点。
        :param target: 需要查找赋值操作符的目标信号名。
        :param known_targets: 当前 always 的合法目标集合。
        :return: 命中的赋值操作符；未找到时返回 None。
        """

        # statement 节点可直接分析当前语句是否写入 target。
        if node.kind == "statement":

            # 当前 statement 只在命中 target 时返回赋值操作符。
            return self._assignment_operator_for_statement_target(node.text, target, known_targets)

        # 条件分支节点需要先查 then，再查 else/elsif。
        if node.kind == "if":

            # str_operator 保存 then 分支中找到的赋值操作符。
            str_operator = self._find_target_assignment_operator_for_split(  # then 分支命中的赋值操作符
                node.children,  # if 主分支节点
                target,  # 待查找赋值操作符的信号
                known_targets,  # 可忽略的兄弟拆分目标
            )

            # then 分支命中后直接返回。
            if str_operator is not None:

                # then 路径已经提供目标赋值操作符。
                return str_operator

            # 继续扫描 else/elsif 路径。
            return self._find_target_assignment_operator_for_split(
                self._expanded_alternate_nodes(node.alternate),
                target,
                known_targets,
            )

        # case 节点按标签顺序递归搜索目标赋值。
        if node.kind == "case":

            # 每个 case item 都保持原始顺序搜索。
            for item in node.items:

                # 沿当前 case 标签子树继续搜索目标的首个赋值操作符。
                str_operator = self._find_target_assignment_operator_for_split(item.children, target, known_targets)  # 当前 case 标签命中的赋值操作符

                # 命中后无需继续扫描后续标签。
                if str_operator is not None:

                    # 当前 case item 已经给出目标赋值操作符。
                    return str_operator

            # 当前 case 的全部标签都没有覆盖目标信号。
            return None

        # 包装节点自身不赋值，继续向内部子树查找。
        if node.kind in {"loop", "generate", "else", "block", "always_block"}:

            # 递归扫描容器节点内部语句树。
            return self._find_target_assignment_operator_for_split(node.children, target, known_targets)

        # 其他节点类型不提供目标赋值操作符。
        return None

    # _find_target_assignment_operator_for_split 查找目标信号的赋值操作符。
    def _find_target_assignment_operator_for_split(
        self,
        nodes: list[ControlNode],
        target: str,
        known_targets: set[str],
    ) -> str | None:
        """
        在控制树中查找指定 target 使用的过程赋值操作符。

        :param nodes: 待扫描的控制树节点列表。
        :param target: 需要查找赋值操作符的目标信号名。
        :param known_targets: 当前 always 的合法目标集合。
        :return: target 首次赋值使用的操作符；未找到时返回 None。
        """

        # 深度优先搜索控制树，保持原始语句顺序。
        for node in nodes:

            # str_operator 保存当前控制节点命中的目标赋值操作符。
            str_operator = self._find_target_assignment_operator_in_node(  # 当前节点内找到的赋值操作符
                node,  # 当前待扫描的控制节点
                target,  # 正在生成自保持赋值的目标信号名
                known_targets,  # 当前 always 的合法目标集合
            )

            # 命中后立即返回首个赋值操作符。
            if str_operator is not None:

                # 当前节点已经提供目标首次赋值使用的操作符。
                return str_operator

        # 所有节点都未写入目标信号。
        return None

    # _build_split_self_hold_statement 为拆分块生成自保持赋值。
    def _build_split_self_hold_statement(self, block: AlwaysBlock, target: str, known_targets: set[str]) -> str:
        """
        为拆分后的单目标 always 生成缺省自保持赋值语句。

        :param block: 原始多目标 always 块。
        :param target: 当前拆分块负责驱动的目标信号。
        :param known_targets: 原 always 的目标信号集合。
        :return: 保持 target 当前值的 Verilog 赋值语句。
        """

        # str_default_operator 由 always 类型决定，组合逻辑使用阻塞赋值。
        str_default_operator = "=" if block.is_combinational else "<="  # 自保持赋值默认操作符

        # str_operator 优先沿用原语句中该 target 实际使用的操作符。
        str_operator = (  # 自保持赋值操作符
            self._find_target_assignment_operator_for_split(  # 原语句中该信号的实际赋值符
                block.nodes,  # 用来追溯 target 原赋值写法的控制树
                target,  # 自保持赋值左值
                known_targets,  # 可安全跳过的其他左值
            )
            or str_default_operator  # 未找到原赋值时回退到 always 类型默认值
        )

        # 返回拆分块缺失分支中使用的自保持语句。
        return f"{target} {str_operator} {target};"

    # _filter_control_nodes_for_target 保留单个目标信号相关的控制树片段。
    def _filter_control_nodes_for_target(
        self,
        nodes: list[ControlNode],
        target: str,
        known_targets: set[str],
        hold_statement: str | None = None,
    ) -> list[ControlNode]:
        """
        为拆分后的单目标 always 过滤控制树节点。

        :param nodes: 原始 always 控制树节点列表。
        :param target: 当前拆分块负责写入的目标信号。
        :param known_targets: 原始 always 中可拆分的目标信号集合。
        :param hold_statement: 需要补齐缺失分支时使用的自保持赋值语句。
        :return: 只保留 target 相关语义后的控制节点列表。
        :raises VerilogFormatterError: strict 模式下遇到不可隔离语句时抛出。
        """

        # list_filtered_nodes 收集当前目标信号保留下来的控制树节点。
        list_filtered_nodes: list[ControlNode] = []  # 拆分后属于 target 的控制节点

        # 逐个节点过滤，保持原控制树顺序不变。
        for node in nodes:

            # 注释节点不携带赋值目标，直接随目标分支保留。
            if node.kind == "comment":

                # 注释节点随目标分支复制，保留用户原始说明。
                list_filtered_nodes.append(node)

                # 当前注释无需继续进入赋值归属判断。
                continue

            # 普通语句根据左值归属决定保留、跳过或报错。
            if node.kind == "statement":

                # statement_node 为空表示该结构边界行或其他目标赋值应跳过。
                statement_node = self._filter_statement_node_for_target(  # 归属到本拆分块的语句
                    node,  # 待判断的 statement 节点
                    target,  # 本次保留的写入信号
                    known_targets,  # 可以移除的其他写入信号
                )

                # 只把真实归属当前 target 的语句放回过滤结果。
                if statement_node is not None:

                    # 目标相关语句保留在当前拆分 always 中。
                    list_filtered_nodes.append(statement_node)

                # 当前 statement 已经完成归属判断。
                continue

            # 条件控制节点需要同时检查 then 分支和 alternate 分支。
            if node.kind == "if":

                # if_node 在单侧缺失时可能补入自保持语句，避免拆分后 latch 风险。
                if_node = self._filter_if_node_for_target(  # 过滤后的条件控制节点
                    node,  # 原始 if 控制节点
                    target,  # 条件分支内要保留的左值
                    known_targets,  # 当前 always 的拆分目标集合
                    hold_statement,  # 缺边时补入的保持语句
                )

                # 两侧都没有目标相关语句时丢弃该条件结构。
                if if_node is not None:

                    # 条件节点仍驱动当前 target 时加入过滤结果。
                    list_filtered_nodes.append(if_node)

                # 条件节点处理完成后继续兄弟节点。
                continue

            # case 节点只保留仍然驱动当前 target 的分支项。
            if node.kind == "case":

                # case_node 在所有分支都无目标内容时返回 None。
                case_node = self._filter_case_node_for_target(  # 过滤后的 case 控制节点
                    node,  # 原始 case 节点
                    target,  # case 分支中保留的目标
                    known_targets,  # 允许剔除的其他目标
                    hold_statement,  # 分支补齐所需保持语句
                )

                # 有保留分支才把 case 放入新控制树。
                if case_node is not None:

                    # 目标相关 case 节点保留原标签和分支顺序。
                    list_filtered_nodes.append(case_node)

                # 当前 case 已完成目标过滤，后续节点仍可能包含该 target。
                continue

            # loop/generate/block 等容器节点递归过滤其子节点。
            if node.kind in {"loop", "generate", "else", "block", "always_block"}:

                # nested_node 为空表示容器子树没有目标相关内容。
                nested_node = self._filter_nested_node_for_target(  # 过滤后的容器控制节点
                    node,  # loop/generate/block 包装节点
                    target,  # 子树中保留的赋值目标
                    known_targets,  # 多目标拆分上下文
                    hold_statement,  # 条件分支缺失时沿用的保持语句
                )

                # 保留仍有有效赋值内容的容器节点。
                if nested_node is not None:

                    # 容器节点保留其 header/label 并替换过滤后的子树。
                    list_filtered_nodes.append(nested_node)

        # 返回拆分 always 中属于当前 target 的控制节点序列。
        return list_filtered_nodes

    # _filter_statement_node_for_target 按过程赋值左值过滤普通语句。
    def _filter_statement_node_for_target(
        self,
        node: ControlNode,
        target: str,
        known_targets: set[str],
    ) -> ControlNode | None:
        """
        按语句左值归属过滤 always 拆分后的单条语句。

        :param node: 当前待过滤的 statement 控制节点。
        :param target: 当前拆分块负责写入的目标信号。
        :param known_targets: 原始 always 中允许拆分的目标信号集合。
        :return: 保留在当前拆分块中的语句节点；无关语句返回 None。
        :raises VerilogFormatterError: strict 模式下遇到多目标或复杂左值语句时抛出。
        """

        # 归一化后只为识别 begin/end 结构边界，不改变原始语句文本。
        str_normalized = self._normalize_statement_line(node.text.strip())  # 去空白后的规范化语句

        # 单独的 begin/end 只是控制树边界，拆分渲染时不作为语句保留。
        if self._is_begin_header(str_normalized) or str_normalized == "end":

            # 边界行交给父级控制节点渲染。
            return None

        # 提取语句左值归属，None 表示该语句无法安全拆进单目标 always。
        set_target_bases = self._statement_targets_for_split(node.text, known_targets)  # 语句写入的目标信号集合

        # 多目标或复杂左值会破坏拆分语义，保持 strict error 行为。
        if set_target_bases is None:

            # verilog_formatter_error_statement 保存语句过滤失败的原始 strict 诊断。
            verilog_formatter_error_statement: VerilogFormatterError = self._strict_error(  # 复杂语句拆分异常
                "always_split_violation",  # 普通语句无法安全拆分的违规分类
                node.text,  # 触发多目标或复杂左值的原始语句
                "Split this multi-target always block manually before formatting.",  # 多目标语句的人工拆分提示
            )

            # 报错类别和建议沿用原实现，外层补齐项目错误前缀。
            raise VerilogFormatterError(
                f"> ERR: [Python] Statement cannot be split safely: {verilog_formatter_error_statement}"
            )

        # 无左值语句通常是任务调用或空语句，应随目标分支保留。
        if not set_target_bases:

            # 返回原节点以保留注释、文本和后续渲染细节。
            return node

        # 只有写入当前 target 的语句才进入该 target 的拆分 always。
        if target in set_target_bases:

            # 返回原节点以保持赋值文本和行内注释不变。
            return node

        # 写入其他目标的语句在当前拆分分支中移除。
        return None

    # _filtered_if_alternate_context 过滤 if 的 alternate 路径并保留结构元信息。
    def _filtered_if_alternate_context(
        self,
        node: ControlNode,
        target: str,
        known_targets: set[str],
        hold_statement: str | None,
    ) -> tuple[list[ControlNode], str, str]:
        """
        过滤 if 的 alternate 路径，并返回恢复结构所需的 kind/label 信息。

        :param node: 当前待处理的 if 控制节点。
        :param target: 当前拆分块负责写入的目标信号。
        :param known_targets: 原始 always 中允许拆分的目标信号集合。
        :param hold_statement: alternate 内部 if 子树可复用的自保持语句。
        :return: `(<过滤后的 alternate 子树>, <alternate kind>, <alternate label>)`。
        """

        # 没有 alternate 时返回空子树和空元信息。
        if not node.alternate:

            # 当前 if 只包含 then 分支。
            return ([], "", "")

        # control_node_alt_root 保存 alternate 外层 else/elsif 结构节点。
        control_node_alt_root = node.alternate[0]  # alternate 外层结构节点

        # list_alt_source 把 else 包装和 elsif 节点统一展开为过滤输入。
        list_alt_source = self._expanded_alternate_nodes(node.alternate)  # alternate 待过滤节点列表

        # list_alt_nodes 保存按 target 过滤后的 alternate 子树。
        list_alt_nodes = self._filter_control_nodes_for_target(  # else/elsif 按 target 过滤后的子树
            list_alt_source,  # 展开后的 else/elsif 待过滤节点
            target,  # 当前拆分块负责的写入目标
            known_targets,  # 原 always 中可剔除的兄弟目标
            hold_statement,  # else/elsif 缺少 target 赋值时的兜底语句
        )

        # 返回过滤后的 alternate 子树和原结构元信息。
        return (list_alt_nodes, control_node_alt_root.kind, control_node_alt_root.label)

    # _normalized_filtered_branch_nodes 把只剩注释的分支统一压成空列表。
    def _normalized_filtered_branch_nodes(self, nodes: list[ControlNode]) -> tuple[list[ControlNode], bool]:
        """
        规范化过滤后的分支节点列表，把只剩注释的分支压成空列表。

        :param nodes: 过滤后待规范化的分支节点列表。
        :return: `(<规范化后的节点列表>, <是否仍有非注释内容>)`。
        """

        # bool_has_content 标记该分支是否仍含有可渲染语义节点。
        bool_has_content = self._has_noncomment_control_content(nodes)  # 当前分支有效内容标记

        # 只有包含真实语义节点的分支才保留原节点列表。
        if bool_has_content:

            # 当前分支仍有需要保留的语义内容。
            return (nodes, True)

        # 只剩注释时统一压成空分支，避免生成空壳控制结构。
        return ([], False)

    # _apply_if_hold_statement 在 if 两侧不对称时补齐自保持语句。
    def _apply_if_hold_statement(
        self, node: ControlNode, hold_statement: str | None, child_nodes: list[ControlNode],
        child_has_content: bool, alt_nodes: list[ControlNode], alt_has_content: bool, alternate_kind: str,
    ) -> tuple[list[ControlNode], list[ControlNode], str]:
        """
        在原 if 含 alternate 且仅一侧写入 target 时补齐自保持语句。

        :param node: 当前待处理的 if 控制节点。
        :param hold_statement: 需要补入的自保持赋值语句。
        :param child_nodes: then 分支过滤后的节点列表。
        :param child_has_content: then 分支是否仍含有非注释内容。
        :param alt_nodes: alternate 分支过滤后的节点列表。
        :param alt_has_content: alternate 分支是否仍含有非注释内容。
        :param alternate_kind: 原 alternate 的外层节点类型。
        :return: `(<修正后的 then>, <修正后的 alternate>, <修正后的 alternate kind>)`。
        """

        # 不满足补齐条件时直接返回原分支结果。
        if not hold_statement or not node.alternate or child_has_content == alt_has_content:

            # 当前 if 不需要额外补自保持语句。
            return (child_nodes, alt_nodes, alternate_kind)

        # hold_node 作为缺失路径的占位赋值，保住拆分后 target 的自保持语义。
        hold_node = ControlNode(kind="statement", text=hold_statement)  # 单边缺失路径补入的保持赋值节点

        # then 缺失时把自保持补到 then。
        if not child_has_content:

            # then 分支获得 target 自保持赋值。
            return ([hold_node], alt_nodes, alternate_kind)

        # alternate 缺失时补到 else，并强制恢复 else 包装。
        return (child_nodes, [hold_node], "else")

    # _restore_filtered_if_alternate 把过滤后的 alternate 子树挂回 if 节点。
    def _restore_filtered_if_alternate(
        self,
        control_node_clone: ControlNode,
        original_alternate: list[ControlNode],
        alt_nodes: list[ControlNode],
        alternate_kind: str,
        alternate_label: str,
    ) -> None:
        """
        根据原始 alternate 形态，把过滤后的子树恢复到克隆 if 节点上。

        :param control_node_clone: 当前待写回 alternate 的 if 克隆节点。
        :param original_alternate: 原始 if 节点的 alternate 列表。
        :param alt_nodes: 过滤并补齐后的 alternate 子树。
        :param alternate_kind: 需要恢复的 alternate 外层类型。
        :param alternate_label: 需要恢复的 alternate 首节点标签。
        :return: 本函数原地更新 control_node_clone，不返回业务值。
        """

        # 原节点没有 alternate 时无需恢复结构。
        if not original_alternate:

            # 当前 if 仍保持单分支结构。
            return

        # 没有 alternate 内容时不写回空 else/elsif。
        if not alt_nodes:

            # 过滤后 alternate 已经完全移除。
            return

        # 普通 else 需要重新包一层 else 节点。
        if alternate_kind == "else":

            # 恢复 renderer 约定的 else 包装节点。
            control_node_clone.alternate = [ControlNode(kind="else", label=alternate_label, children=alt_nodes)]  # 恢复 else 包装后的 alternate 列表

            # else 包装恢复完成后无需继续写回其他 alternate 形态。
            return

        # elsif 或其他 alternate 节点已经是完整控制节点列表。
        control_node_clone.alternate = alt_nodes  # 直接挂回过滤后的 elsif/扩展 alternate 列表

    # _filter_if_node_for_target 过滤条件分支并补齐必要自保持语句。
    def _filter_if_node_for_target(
        self,
        node: ControlNode,
        target: str,
        known_targets: set[str],
        hold_statement: str | None,
    ) -> ControlNode | None:
        """
        过滤 if 控制节点，并在缺失分支中补齐自保持语句。

        :param node: 当前待过滤的 if 控制节点。
        :param target: 当前拆分块负责写入的目标信号。
        :param known_targets: 原始 always 中允许拆分的目标信号集合。
        :param hold_statement: 缺失分支需要补入的自保持赋值语句。
        :return: 保留 target 相关内容后的 if 控制节点；无相关内容时返回 None。
        """

        # list_child_nodes 只保留 then 路径里仍会驱动 target 的控制节点。
        list_child_nodes = self._filter_control_nodes_for_target(  # then 子树中过滤后的 target 相关节点
            node.children,  # then 路径原始子树
            target,  # 当前拆分块负责保留的目标信号
            known_targets,  # 原始 always 允许拆分的目标集合
            hold_statement,  # 缺失路径可补入的保持赋值
        )

        # tuple_alt_context 带回反向路径过滤结果，以及稍后恢复 else/elsif 外壳所需标签。
        tuple_alt_context = self._filtered_if_alternate_context(  # alternate 子树与包装信息
            node, target, known_targets, hold_statement,  # 原始 if、拆分目标、目标全集与保持语句
        )

        # 解包后即可分别判断反向路径是否还有语义内容，以及最终该恢复成何种外壳。
        list_alt_nodes, str_alternate_kind, str_alternate_label = tuple_alt_context  # alternate 子树与恢复标签

        # then 路径若只剩注释，就把这一侧视为真正的空分支。
        list_child_nodes, bool_child_has_content = self._normalized_filtered_branch_nodes(  # then 子树规范化后的节点与内容标记
            list_child_nodes,  # then 路径过滤后待压缩的控制节点
        )

        # 这里专门检查反向路径是不是已经空到只剩注释，避免后面硬保留一个没有业务意义的 else/elsif 外壳。
        list_alt_nodes, bool_alt_has_content = self._normalized_filtered_branch_nodes(  # 反向路径压缩后的节点列表与有效内容标记
            list_alt_nodes,  # 反向路径过滤后仍待压缩的控制节点
        )

        # 两侧都没有目标相关内容时整个 if 可删除。
        if not list_child_nodes and not list_alt_nodes:

            # 当前 target 不依赖该条件节点。
            return None

        # 到这一步 only-one-side 写入已经坐实，需要立刻补回保持赋值，避免拆分后的控制树制造 latch。
        list_child_nodes, list_alt_nodes, str_alternate_kind = self._apply_if_hold_statement(  # 回填保持赋值后的分支子树与包装类型
            node, hold_statement,  # 原始 if 与可补入的保持赋值
            list_child_nodes, bool_child_has_content,  # then 结果与其语义保留标记
            list_alt_nodes, bool_alt_has_content,  # else/elsif 路径筛完后的节点列表与语义存在标记
            str_alternate_kind,  # 反向路径当前还原所需的外层类型
        )

        # 克隆 if 节点，保持 header/label 并替换过滤后的 children。
        control_node_clone: ControlNode = ControlNode(  # 过滤后的 if 节点
            kind="if",  # 克隆后仍交给条件渲染器处理
            header=node.header,  # 原始 if 条件头
            label=node.label,  # 原始命名块标签
            children=list_child_nodes,  # 过滤后的 then 子树
        )

        # 恢复 alternate 包装形态，保持 else/elsif 结构稳定。
        self._restore_filtered_if_alternate(
            control_node_clone,
            node.alternate,
            list_alt_nodes,
            str_alternate_kind,
            str_alternate_label,
        )

        # 返回保留当前 target 语义后的 if 控制节点。
        return control_node_clone

    # _filter_case_node_for_target 过滤 case 分支并移除无关标签。
    def _filter_case_node_for_target(
        self,
        node: ControlNode,
        target: str,
        known_targets: set[str],
        hold_statement: str | None,
    ) -> ControlNode | None:
        """
        过滤 case 分支，只保留当前目标信号相关的 item。

        :param node: 当前待过滤的 case 控制节点。
        :param target: 当前拆分块负责写入的目标信号。
        :param known_targets: 原始 always 中允许拆分的目标信号集合。
        :param hold_statement: 递归过滤 if 子树时可用的自保持赋值语句。
        :return: 保留 target 相关 item 的 case 节点；无相关 item 时返回 None。
        """

        # case item 列表按原顺序重建，保持标签渲染稳定。
        list_case_items: list[CaseItem] = []  # 过滤后的 case 分支项

        # 每个 case item 单独递归，避免一个标签污染其他标签内容。
        for item in node.items:

            # 当前标签下的子节点按 target 过滤。
            list_item_children = self._filter_control_nodes_for_target(  # 当前 case 标签下属于 target 的子树
                item.children,  # 待过滤的 case item 原始语句
                target,  # 当前拆分目标
                known_targets,  # 同一 always 的目标集合
                hold_statement,  # case 标签内条件分支缺赋值时的兜底语句
            )

            # 空标签分支不应出现在拆分后的 case 中。
            if not self._has_noncomment_control_content(list_item_children):

                # 跳过没有目标赋值的 case item。
                continue

            # 保留标签、block label 和前导注释，避免拆分过程破坏分支语义说明。
            list_case_items.append(
                CaseItem(
                    label=item.label,
                    children=list_item_children,
                    block_label=item.block_label,
                    leading_comments=list(item.leading_comments),
                )
            )

        # 所有分支都被过滤掉时删除整个 case。
        if not list_case_items:

            # 当前 target 不受该 case 影响。
            return None

        # 返回只包含目标相关分支的 case 控制节点。
        return ControlNode(
            kind="case",  # 渲染器仍按 case 节点输出
            header=node.header,  # 原始 case 表达式头
            items=list_case_items,  # 过滤后仍驱动目标的标签项
        )

    # _filter_nested_node_for_target 递归处理 loop/generate/block 包装节点。
    def _filter_nested_node_for_target(
        self,
        node: ControlNode,
        target: str,
        known_targets: set[str],
        hold_statement: str | None,
    ) -> ControlNode | None:
        """
        过滤 loop/generate/block 这类包装节点的子树。

        :param node: 当前待过滤的包装控制节点。
        :param target: 当前拆分块负责写入的目标信号。
        :param known_targets: 原始 always 中允许拆分的目标信号集合。
        :param hold_statement: 递归过滤 if 子树时可用的自保持赋值语句。
        :return: 替换为过滤后子树的包装节点；子树无语义内容时返回 None。
        """

        # 容器节点本身不写信号，是否保留取决于子树内容。
        list_child_nodes = self._filter_control_nodes_for_target(  # 容器内保留的子树
            node.children,  # 原包装节点子节点
            target,  # 需要留在子树里的写入目标
            known_targets,  # 可被剔除的兄弟写入目标
            hold_statement,  # 子条件结构可复用的保持赋值
        )

        # 子树没有真实语句时丢弃外层容器。
        if not self._has_noncomment_control_content(list_child_nodes):

            # 空容器不参与后续渲染。
            return None

        # 保留容器元信息并替换为过滤后的子节点。
        return ControlNode(
            kind=node.kind,  # 保持 loop/generate/block 原节点类型
            header=node.header,  # 保留容器头文本
            label=node.label,  # loop/generate 命名块标签
            children=list_child_nodes,  # 替换为过滤后的子树
        )

    # _validate_parameter_check_statement_text 校验单条参数检查 statement 的每一行。
    def _validate_parameter_check_statement_text(self, text: str, allowed_tasks: tuple[str, ...]) -> None:
        """
        校验参数检查 statement 是否只包含允许的系统任务调用。

        :param text: 当前待检查的 statement 文本。
        :param allowed_tasks: 允许出现在 example-compat initial 中的系统任务前缀。
        :return: 本函数只在校验失败时抛出异常，不返回业务值。
        :raises VerilogFormatterError: 遇到非允许系统任务时抛出。
        """

        # 多行 statement 逐行规范化，兼容解析阶段保留的换行。
        for raw_line in text.splitlines():

            # str_stripped_line 保存去掉外层空白后的规范化语句。
            str_stripped_line = self._normalize_statement_line(raw_line.strip())  # 参数检查语句文本

            # 空行不携带系统任务，直接跳过。
            if not str_stripped_line:

                # 空文本不会影响参数检查块安全性。
                continue

            # 非允许系统任务说明 initial 已超出参数检查范围。
            if not str_stripped_line.startswith(allowed_tasks):

                # verilog_formatter_error_parameter_check 保存参数检查语句违规的原始诊断。
                verilog_formatter_error_parameter_check: VerilogFormatterError = self._strict_error(  # 参数检查任务异常
                    "unsupported_construct",  # 参数检查 initial 只允许系统任务的违规分类
                    str_stripped_line,  # 非参数检查系统任务语句
                    (
                        "Keep example-compat initial blocks limited to parameter-check system tasks "
                        "such as $display/$error/$fatal/$warning."
                    ),  # 面向用户的 initial 块约束建议
                )

                # strict 模式拒绝把普通过程逻辑伪装成参数检查 initial。
                raise VerilogFormatterError(
                    "> ERR: [Python] Unsupported parameter check task: "
                    f"{verilog_formatter_error_parameter_check}"
                )

    # _validate_parameter_check_node 递归校验单个参数检查控制节点。
    def _validate_parameter_check_node(self, node: ControlNode, allowed_tasks: tuple[str, ...]) -> None:
        """
        递归校验单个参数检查控制节点及其子树。

        :param node: 当前待检查的控制节点。
        :param allowed_tasks: 允许的系统任务前缀集合。
        :return: 本函数只在校验失败时抛出异常，不返回业务值。
        """

        # statement 节点直接逐行校验系统任务前缀。
        if node.kind == "statement":

            # 当前 statement 必须完全由允许的参数检查任务构成。
            self._validate_parameter_check_statement_text(node.text, allowed_tasks)

            # statement 路径已经校验完成，无需继续递归子树。
            return

        # case 节点需要逐个标签递归检查。
        if node.kind == "case":

            # 每个 case item 子树必须同样只包含参数检查任务。
            for item in node.items:

                # 递归检查当前 case 标签下的语句。
                self._validate_parameter_check_nodes(item.children)

            # case 路径已经完成全部标签递归校验。
            return

        # 普通包装节点的主子树继续递归检查。
        self._validate_parameter_check_nodes(node.children)

        # alternate 子树可能保存 else/elsif 参数检查路径。
        self._validate_parameter_check_nodes(node.alternate)

    # _validate_parameter_check_nodes 限定 example-compat initial 只包含参数检查任务。
    def _validate_parameter_check_nodes(self, nodes: list[ControlNode]) -> None:
        """
        校验参数检查 initial 块中只出现允许的系统任务。

        :param nodes: 待检查的 initial 控制树节点列表。
        :return: 本函数只在校验失败时抛出异常，不返回业务值。
        :raises VerilogFormatterError: 遇到非参数检查系统任务语句时抛出。
        """

        # tuple_allowed_tasks 限定 example-compat 可接受的参数检查与终止系统任务。
        tuple_allowed_tasks = ("$display", "$error", "$fatal", "$warning", "$finish", "$stop")  # 参数检查 initial 白名单任务

        # 递归扫描参数检查控制树中的所有语句。
        for node in nodes:

            # 单个节点的具体递归分支由 helper 统一处理。
            self._validate_parameter_check_node(node, tuple_allowed_tasks)

    # _is_parameter_check_initial_text 粗判 initial 文本是否是参数检查块。
    def _is_parameter_check_initial_text(self, lines: list[str]) -> bool:
        """
        判断 initial 原始文本是否像 example-compat 参数检查块。

        :param lines: initial 块内部的原始文本行。
        :return: 只包含显示、报错或告警系统任务且无过程赋值特征时返回 True。
        """

        # str_joined_text 用换行保留原始 initial 语句边界。
        str_joined_text = "\n".join(lines)  # initial 块合并文本

        # 没有参数检查或终止系统任务时不应按参数检查 initial 处理。
        if not re.search(r"\$(display|error|fatal|warning|finish|stop)\b", str_joined_text):

            # 缺少可见参数检查任务，保持普通 initial 校验路径。
            return False

        # tuple_forbidden_tokens 捕捉普通过程逻辑或赋值痕迹。
        tuple_forbidden_tokens = ("<=", "assign ", "always", "posedge", "negedge")  # initial 中禁止的过程逻辑片段

        # 返回文本是否未命中普通过程逻辑关键片段。
        return not any(token in str_joined_text for token in tuple_forbidden_tokens)

    # _validate_body_blocks 校验实例块解析结果具备模块名和实例名。
    def _validate_body_blocks(self, blocks: list[BodyBlock]) -> None:
        """
        校验模块 body 中实例块是否满足 formatter 支持的结构。

        :param blocks: 模块 body 解析得到的块列表。
        :return: 校验通过时不返回业务值。
        :raises VerilogFormatterError: 实例块缺少模块名或实例名时抛出。
        """

        # list_instance_blocks 仅保留需要检查 payload 的实例块。
        list_instance_blocks = [block for block in blocks if block.block_type == "instance_block"]  # 需要验证命名完整性的实例块

        # 没有实例块时无需执行实例结构校验。
        if not list_instance_blocks:

            # 当前模块没有实例化语句，body block 校验结束。
            return

        # 遍历实例块，逐项确认解析出的模块名和实例名。
        for block in list_instance_blocks:

            # payload 是当前 body block 的具体实例解析结果。
            payload = block.payload  # 可能包含 InstanceBlock 解析结果的块载荷

            # 缺失模块名说明实例语句不是标准实例化形态。
            if isinstance(payload, InstanceBlock) and not payload.module_name:

                # verilog_formatter_error_module_name 保存缺少模块名的 strict 诊断。
                verilog_formatter_error_module_name: VerilogFormatterError = self._strict_error(  # 实例模块名缺失异常
                    "unsupported_shape",  # 实例化缺少模块名的结构分类
                    block.source,  # 缺少模块名的实例原文
                    "Use a standard '<module> #(params) instance_name (...)' instance form.",  # 实例语句修复建议
                )

                # strict 模式要求实例语句明确写出模块名。
                raise VerilogFormatterError(
                    f"> ERR: [Python] Instance module name is missing: {verilog_formatter_error_module_name}"
                )

            # 缺失实例名会导致端口布局和渲染阶段无法稳定引用。
            if isinstance(payload, InstanceBlock) and not payload.instance_name:

                # verilog_formatter_error_instance_name 保存缺少实例名的 strict 诊断。
                verilog_formatter_error_instance_name: VerilogFormatterError = self._strict_error(  # 实例名缺失异常
                    "unsupported_shape",  # 实例化缺少实例名的结构分类
                    block.source,  # 缺少实例名的实例原文
                    "Provide an explicit instance name between the module name and the port list.",  # 实例名修复建议
                )

                # strict 模式要求模块名和端口表之间存在显式实例名。
                raise VerilogFormatterError(
                    f"> ERR: [Python] Instance name is missing: {verilog_formatter_error_instance_name}"
                )

    # _has_low_active_reset_branch 检查顺序块首个条件是否是低有效复位。
    def _has_low_active_reset_branch(self, block: AlwaysBlock) -> bool:
        """
        判断 always 块首个条件分支是否表达低有效复位。

        :param block: 待检查的 always 块模型。
        :return: 首个 if 分支包含低有效复位条件时返回 True。
        """

        # 没有控制节点时无法识别复位分支。
        if not block.nodes:

            # 空 always 不能证明存在低有效复位。
            return False

        # control_node_first 保存首个控制节点，用于判断 reset 分支位置。
        control_node_first: ControlNode = block.nodes[0]  # 异步 guard 检查的入口 if 候选

        # 低有效复位应出现在首个条件分支。
        if control_node_first.kind != "if":

            # 首节点不是条件分支时不满足 reset branch 约定。
            return False

        # str_header 保存规范化后的条件头文本，便于正则匹配。
        str_header = self._normalize_statement_line(control_node_first.header)  # 用于匹配 reset 名的条件头

        # 返回条件头是否命中低有效 reset 常见写法。
        return bool(
            re.search(r"==\s*1'b0\b", str_header)
            or re.search(r"!=\s*1'b1\b", str_header)
            or re.search(r"\b!\s*[A-Za-z_]\w*\b", str_header)
        )

    # _has_async_edge_guard_branch 判断异步敏感表是否已有 reset 守卫。
    def _has_async_edge_guard_branch(self, block: AlwaysBlock) -> bool:
        """
        判断异步 always 的首个条件分支是否引用 reset 信号。

        :param block: 待检查的 always 块模型。
        :return: 块声明了 reset 且首个 if 条件引用该 reset 时返回 True。
        """

        # 缺少 reset 字段或控制节点时无法识别异步 guard。
        if not block.reset or not block.nodes:

            # 未解析到 reset 或语句体为空时视为没有 guard。
            return False

        # control_node_first 保存首个控制节点，用于识别 reset guard。
        control_node_first: ControlNode = block.nodes[0]  # always 首个控制节点

        # reset guard 必须出现在首个条件分支。
        if control_node_first.kind != "if":

            # 首节点不是条件分支时不能证明 reset guard 存在。
            return False

        # str_header 保存规范化条件头，便于按 reset 名称匹配。
        str_header = self._normalize_statement_line(control_node_first.header)  # 首个条件头文本

        # 返回条件头是否直接引用已解析 reset 信号。
        return bool(re.search(rf"\b{re.escape(block.reset)}\b", str_header))

    # _validate_always_headers 检查顺序 always 的复位敏感表约定。
    def _validate_always_headers(self, blocks: list[AlwaysBlock]) -> None:
        """
        校验 always 头部是否满足 strict reset 约束。

        :param blocks: 待检查的 always 块列表。
        :return: 校验通过时不返回业务值。
        :raises VerilogFormatterError: strict 模式下顺序块缺少低有效 reset 时抛出。
        """

        # 配置未启用缺失 clock/reset 失败策略时跳过本门禁。
        if not self.config["strict_mode"]["fail_on_missing_clock_reset"]:

            # 用户未要求 strict reset 检查时保持旧输出。
            return

        # bool_allow_missing_reset 表示示例兼容模式下可以宽容旧 fixture。
        bool_allow_missing_reset = self._example_compat_enabled()  # 示例兼容复位宽容标记

        # 逐个 always 检查异步边沿和顺序 reset 约束。
        for block in blocks:

            # bool_has_mixed_edges 标记敏感表同时出现 posedge 和 negedge。
            bool_has_mixed_edges = "posedge" in block.header and "negedge" in block.header  # 混合边沿敏感表标记

            # 提取敏感表中的全部 negedge 标识符，避免依赖 rstn 必须位于名称结尾。
            tuple_negedge_names = tuple(  # 当前 always 敏感表中的下降沿信号
                re.findall(r"\bnegedge\s+([A-Za-z_]\w*)", block.header, flags=re.IGNORECASE)  # 下降沿标识符序列
            )

            # 任一下降沿信号含完整低有效复位段即可证明复位边沿存在。
            bool_has_low_reset_edge = any(  # 敏感表是否声明低有效复位边沿
                is_low_active_reset_name(str_edge_name)  # 共享低有效复位角色判断
                for str_edge_name in tuple_negedge_names  # 遍历当前敏感表下降沿名称
            )

            # 混合边沿敏感表必须包含低有效 reset 或显式 guard。
            if bool_has_mixed_edges and not bool_has_low_reset_edge:

                # 已在语句体中看到异步 reset guard 时保持兼容。
                if self._has_async_edge_guard_branch(block):

                    # guard 分支已说明 reset 语义，跳过该块。
                    continue

                # verilog_formatter_error_edge_reset 保存敏感表 reset 约束失败诊断。
                verilog_formatter_error_edge_reset: VerilogFormatterError = self._strict_error(  # 敏感表 reset 异常
                    "clock_reset_violation",  # 混合边沿缺少 reset 声明的分类
                    block.header,  # 缺少低有效 reset 边沿的 always 头
                    "Use an active-low reset such as i_rstn in the sequential always sensitivity list.",  # reset 敏感表建议
                )

                # strict 模式要求异步顺序块显式声明低有效 reset。
                raise VerilogFormatterError(
                    f"> ERR: [Python] Active-low reset edge is missing: {verilog_formatter_error_edge_reset}"
                )

            # bool_missing_sequential_reset 汇总顺序块缺失 reset 的判定。
            bool_missing_sequential_reset = (  # 顺序块缺失低有效 reset 标记
                not block.is_combinational  # 仅检查顺序 always
                and not block.reset  # 解析结果没有 reset 信号
                and not self._has_low_active_reset_branch(block)  # 首分支也不是低有效 reset
            )

            # 顺序 always 缺少 reset 时根据兼容模式决定跳过或报错。
            if bool_missing_sequential_reset:

                # 示例模式保留旧输入中的无 reset 顺序块。
                if bool_allow_missing_reset:

                    # 兼容路径不改变历史 fixture 的 formatter 行为。
                    continue

                # verilog_formatter_error_missing_reset 保存顺序块缺失 reset 的 strict 诊断。
                verilog_formatter_error_missing_reset: VerilogFormatterError = self._strict_error(  # 顺序块 reset 缺失异常
                    "clock_reset_violation",  # 顺序块缺少低有效 reset 的分类
                    f"sequential always block is missing an active-low reset: {block.header}",  # 缺失 reset 的 always 摘要
                    "Add 'or negedge i_rstn' and a low-active reset branch before formatting.",  # reset 补齐建议
                )

                # strict 模式要求顺序块存在低有效 reset。
                raise VerilogFormatterError(
                    f"> ERR: [Python] Sequential reset is missing: {verilog_formatter_error_missing_reset}"
                )

    # _extract_simple_signal_base 从简单信号表达式中提取基础名。
    def _extract_simple_signal_base(self, text: str, category: str) -> str | None:
        """
        从简单信号或位选表达式中提取基础信号名。

        :param text: 待分析的 Verilog 表达式文本。
        :param category: 解析失败时沿用的 strict 错误分类。
        :return: 唯一基础信号名；复杂表达式或解析失败时返回 None。
        """

        # str_candidate 去掉行注释后保留待解析表达式主体。
        str_candidate, _ = self._split_comment(text)  # 不含行注释的表达式片段

        # str_candidate 继续去掉空白和末尾分号，形成左值解析输入。
        str_candidate = str_candidate.strip().rstrip(";")  # 简单信号候选文本

        # 空表达式无法提供基础信号。
        if not str_candidate:

            # 没有实际文本时返回 None 交给调用方跳过。
            return None

        # 简单信号只允许标识符加可选位选或切片。
        if not re.fullmatch(r"[A-Za-z_]\w*(?:\s*\[[^\[\]]+\]\s*)*", str_candidate):

            # 复杂表达式不参与输出亲缘推断。
            return None

        # 左值解析可能因为位选不稳定而失败，失败时保持宽容。
        try:

            # l_value_ref_signal 保存候选表达式的结构化左值。
            l_value_ref_signal: LValueRef = self._parse_lvalue(  # 简单信号左值解析结果
                str_candidate,  # 待解析的简单信号表达式
                category,  # 调用方指定的简单信号解析失败分类
                "Use a stable signal expression such as foo, foo[idx], or foo[msb:lsb].",  # 简单信号修复建议
                allow_concat=False,  # 简单基础名提取不允许 concat
            )

        # 左值解析失败表示该表达式不能作为简单基础信号。
        except VerilogFormatterError:

            # 调用方会把 None 视为不可传播亲缘关系。
            return None

        # list_bases 保存候选左值解析出的基础信号名。
        list_bases = self._extract_lvalue_bases(l_value_ref_signal)  # 候选表达式基础信号列表

        # 唯一基础名才能参与输出亲缘传播。
        if len(list_bases) == 1:

            # 返回该简单表达式对应的基础信号名。
            return list_bases[0]

        # 多基础或无基础表达式都不是简单信号。
        return None

    # _build_output_affinity 汇总 output 端口和内部桥接信号的布局亲缘。
    def _build_output_affinity(
        self,
        ports: list[PortDecl],
        output_internal_names: dict[str, str],
        assigns: list[AssignStmt],
    ) -> dict[str, object]:
        """
        构建 output 端口到内部桥接信号的布局亲缘索引。

        :param ports: 模块端口声明列表。
        :param output_internal_names: output 端口到 formatter 合成内部信号的映射。
        :param assigns: 模块内连续赋值列表。
        :return: 渲染阶段使用的 output 端口、内部信号和布局索引字典。
        """

        # output affinity 只从模块边界端口向内部桥接信号传播。
        set_output_ports = {port.name for port in ports if port.direction == "output"}  # 可向内部信号传播布局的 output 名

        # dict_port_layouts 保存端口自身的布局信息。
        dict_port_layouts: dict[str, OutputSignalLayout] = {}  # output 端口到布局快照的映射

        # dict_signal_layouts 保存由 output 桥接推导出的内部信号布局。
        dict_signal_layouts: dict[str, OutputSignalLayout] = {}  # output 相关内部信号到端口亲缘的映射

        # 按端口声明顺序生成稳定 port_rank。
        for index, port in enumerate(ports):

            # 非 output 端口不参与输出区域亲缘排序。
            if port.direction != "output":

                # 输入和 inout 端口不参与 output 内部信号排序。
                continue

            # output_signal_layout_port 保留端口分组、位宽和属性，供内部信号继承。
            output_signal_layout_port: OutputSignalLayout = OutputSignalLayout(  # output 端口原始布局快照
                port_rank=index,  # 端口声明顺序

                # 接口分组字段决定内部信号渲染靠近哪个用户接口区域。
                group=port.group,  # 用户接口分组用于内部信号继承
                subgroup=port.subgroup,  # 更细的接口槽位标签也要继续传给内部输出区
                subgroup_mode=port.subgroup_mode,  # 输出镜像区也要沿用端口的槽位展示策略
                section=port.section,  # 后续渲染区域跟随端口原始 section

                # 声明形态字段让合成内部信号保持端口宽度和维度语义。
                width=port.width,  # 端口位宽文本
                signed=port.signed,  # signed 修饰标记
                unpacked=port.unpacked,  # unpacked 维度文本

                # 端口属性需要随布局快照传播到内部信号声明。
                attributes=port.attributes,  # 端口属性列表
            )

            # 以端口名索引布局，assign bridge 可直接查找。
            dict_port_layouts[port.name] = output_signal_layout_port  # 端口名到布局的索引项

        # set_output_signals 先纳入显式 output_internal_names 的内部信号。
        set_output_signals = set(output_internal_names.values())  # 输出桥接内部信号集合

        # 显式桥接映射让内部信号继承对应 output 端口布局。
        for output_name, internal_name in output_internal_names.items():

            # output_signal_layout_explicit 缺失表示该 output_name 不是当前模块 output。
            output_signal_layout_explicit_source: OutputSignalLayout | None = cast(  # 显式 bridge 的 output 端口亲缘
                OutputSignalLayout | None,  # 明确 dict.get 返回可空 output 布局
                dict_port_layouts.get(output_name),  # 合成内部信号对应的外部端口名
            )

            # 只有真实 output 才传播布局。
            if output_signal_layout_explicit_source:

                # 显式内部桥接信号继承对应 output 端口布局。
                self._record_output_signal_layout(
                    dict_signal_layouts,  # output 内部信号布局索引
                    internal_name,  # 显式 bridge 生成的内部信号名
                    output_signal_layout_explicit_source,  # 对应 output 端口布局来源
                )

        # 扫描 assign bridge，推导 output 端口右侧内部信号的布局。
        for assign in assigns:

            # str_lhs_base 必须是简单 output 端口，复杂表达式不传播亲缘。
            str_lhs_base = self._extract_simple_signal_base(assign.lhs, "lvalue_normalization_violation")  # assign 左侧基础信号

            # 非 output 左值不是输出桥接。
            if str_lhs_base not in set_output_ports:

                # 普通连续赋值不参与 output affinity 传播。
                continue

            # str_rhs_base 是可能继承 output 布局的内部信号。
            str_rhs_base = self._extract_simple_signal_base(assign.rhs, "lvalue_normalization_violation")  # assign 右侧基础信号

            # 状态信号区域有独立排序规则，不继承 output 区域布局。
            if str_rhs_base and not str_rhs_base.startswith(self.config["naming"]["state_signal_prefix"]):

                # assign 右侧内部信号加入 output 相关信号集合。
                set_output_signals.add(str_rhs_base)

                # 根据左侧 output 查找布局来源。
                output_signal_layout_bridge_source: OutputSignalLayout | None = cast(  # 连续赋值左侧端口亲缘
                    OutputSignalLayout | None,  # 明确 assign bridge 查找结果的可空类型
                    dict_port_layouts.get(str_lhs_base),  # 作为布局来源的 output 左值基础名
                )

                # 记录右侧内部信号的 output 亲缘。
                if output_signal_layout_bridge_source:

                    # 右侧内部信号继承左侧 output 的声明布局。
                    self._record_output_signal_layout(
                        dict_signal_layouts,  # assign bridge 推导出的内部信号布局表
                        str_rhs_base,  # assign 右侧内部信号名
                        output_signal_layout_bridge_source,  # assign 左侧 output 端口布局
                    )

        # 返回渲染阶段需要的 output 端口、内部信号和布局索引。
        return {
            "ports": set_output_ports,
            "signals": set_output_signals,
            "targets": set_output_ports | set_output_signals,
            "layouts": dict_signal_layouts,
            "target_layouts": {**dict_port_layouts, **dict_signal_layouts},
        }

    # _partition_assigns 将 output bridge assign 与普通 assign 分开。
    def _partition_assigns(
        self,
        assigns: list[AssignStmt],
        ports: list[PortDecl],
        output_internal_names: dict[str, str],
    ) -> tuple[list[AssignStmt], list[AssignStmt]]:
        """
        按左值是否为 output 端口拆分连续赋值列表。

        :param assigns: 用户原始连续赋值列表。
        :param ports: 模块端口声明列表。
        :param output_internal_names: formatter 合成的 output 内部桥接映射。
        :return: 二元组，依次为普通 assign 列表和 output bridge assign 列表。
        """

        # assign 分区只关心左值是否落在模块 output 边界上。
        set_output_ports = {port.name for port in ports if port.direction == "output"}  # 可触发 bridge 分区的端口名

        # list_other_assigns 保留普通连续赋值。
        list_other_assigns: list[AssignStmt] = []  # 留在普通 assign 区域的语句

        # output bridge 先包含 formatter 合成的端口到内部信号赋值。
        list_output_assigns = self._build_output_assigns(ports, output_internal_names)  # 已合成的端口桥接语句

        # 用户原始 assign 按左值归入 output 或普通区域。
        for assign in assigns:

            # str_lhs_base 只接受简单左值作为 output bridge 判定依据。
            str_lhs_base = self._extract_simple_signal_base(assign.lhs, "lvalue_normalization_violation")  # 用于判定输出桥接的左值名

            # output 左值进入 output assign 区域。
            if str_lhs_base in set_output_ports:

                # 用户显式 output bridge 追加到 output assign 区。
                list_output_assigns.append(assign)

            # 其他 assign 留在普通区域。
            else:

                # 非 output 左值保持在普通连续赋值区域。
                list_other_assigns.append(assign)

        # 返回普通 assign 和 output bridge assign 的分区结果。
        return list_other_assigns, list_output_assigns

    # _validate_output_signal_invariants 校验 output 归一化后的结构不变量。
    def _validate_output_signal_invariants(
        self,
        ports: list[PortDecl],
        decls: list[SignalDecl],
        output_assigns: list[AssignStmt],
    ) -> None:
        """
        校验 output 内部化过程中不能出现端口重声明或自桥接。

        :param ports: 模块端口声明列表。
        :param decls: 模块内部信号声明列表。
        :param output_assigns: output bridge 连续赋值列表。
        :return: 校验通过时不返回业务值。
        :raises VerilogFormatterError: output 归一化不变量被破坏时抛出。
        """

        # set_output_ports 建立 output 端口名集合，供声明和 assign 校验复用。
        set_output_ports = {port.name for port in ports if port.direction == "output"}  # 不允许被内部声明覆盖的端口名

        # list_errors 收集所有 output 归一化不变量错误。
        list_errors: list[str] = []  # 延迟到末尾统一报告的不变量摘要

        # 内部声明不得重复声明 output 端口本身。
        for decl in decls:

            # 合成 assign 哨兵不属于真实内部声明。
            if decl.kind == "__assign__":

                # assign 哨兵由后续 output_assigns 分支单独校验。
                continue

            # 内部声明名称撞到 output 端口时记录不变量错误。
            if decl.name in set_output_ports:

                # 记录端口重声明，统一在函数末尾抛出。
                list_errors.append(f"internal declaration collides with output port '{decl.name}'")

        # output bridge 不能把端口自赋值给自身。
        for assign in output_assigns:

            # str_lhs_base 提取 output bridge 左侧基础名。
            str_lhs_base = self._extract_simple_signal_base(assign.lhs, "lvalue_normalization_violation")  # bridge 左侧端口候选名

            # 非 output 左值不是 output bridge 不变量关注对象。
            if str_lhs_base not in set_output_ports:

                # 不是 output 端口的 assign 留给其他规则处理。
                continue

            # str_rhs_base 提取 output bridge 右侧基础名。
            str_rhs_base = self._extract_simple_signal_base(assign.rhs, "lvalue_normalization_violation")  # bridge 右侧内部信号候选名

            # 自桥接会生成没有意义的 output 归一化结果。
            if str_rhs_base == str_lhs_base:

                # 记录自赋值桥接，统一在函数末尾抛出。
                list_errors.append(f"self-assign output bridge '{assign.lhs} = {assign.rhs}'")

        # 任一不变量错误都阻止继续渲染。
        if list_errors:

            # str_error_detail 汇总所有 output 不变量错误明细。
            str_error_detail = "\n- ".join(list_errors)  # output 不变量错误明细文本

            # 抛出统一 formatter 异常，保留所有错误摘要。
            raise VerilogFormatterError(
                f"> ERR: [Python] Output normalization invariant violated:\n- {str_error_detail}"
            )

    # _group_declarations 按渲染区域把信号声明分组。
    def _group_declarations(
        self,
        decls: list[SignalDecl] | GroupedDeclMap,
        output_signal_names: set[str],
        output_signal_layouts: dict[str, OutputSignalLayout],
        instance_signal_layouts: dict[str, InstanceSignalLayout] | None = None,
    ) -> dict[str, list[SignalDecl]]:
        """
        按 formatter 结构区域对声明和 assign 哨兵分组。

        :param decls: 待分组的信号声明列表。
        :param output_signal_names: output 内部信号名集合。
        :param output_signal_layouts: output 内部信号布局索引。
        :param instance_signal_layouts: 实例连接信号布局索引；为空时按空映射处理。
        :return: 以结构区域名为键的声明列表字典。
        """

        # grouped decls 已经完成区域归类时，只需复制以避免共享可变列表。
        if isinstance(decls, dict):

            # 返回带稳定区域顺序的声明分组副本。
            return self._copy_grouped_decls(decls)

        # dict_instance_signal_layouts 为空时使用空映射，避免修改默认参数。
        dict_instance_signal_layouts = instance_signal_layouts or {}  # 实例信号布局索引

        # dict_groups 预创建所有渲染区域，保持输出区域顺序稳定。
        dict_groups: dict[str, list[SignalDecl]] = {  # 声明区域分组表
            key: []  # 当前区域的声明列表
            for key in self.config["structure"]["region_order"]  # 配置声明的区域顺序
        }

        # 按原始声明顺序分配区域。
        for decl in decls:

            # str_target_region 保存当前声明应进入的渲染区域。
            str_target_region = self._resolve_decl_region(  # 声明目标区域
                decl,  # 当前待分组声明
                output_signal_names,  # output 内部信号名集合
                set(dict_instance_signal_layouts),  # 实例连接信号名集合
            )

            # assign 哨兵需要转换成 assign 类型声明供后续渲染。
            if decl.kind == "__assign__":

                # signal_decl_assign_item 复制原哨兵字段并改为 assign 声明。
                signal_decl_assign_item: SignalDecl = SignalDecl(  # assign 哨兵的渲染声明副本
                    "assign",  # 渲染阶段使用的声明类型
                    decl.width,  # 原始 assign 宽度字段

                    # assign 左右值和注释字段保留解析阶段抽取结果。
                    decl.name,  # assign 左侧名称
                    decl.init,  # assign 右侧初始化表达式
                    decl.comment,  # assign 行内注释

                    # 形态修饰字段原样复制，避免渲染阶段丢失声明附加信息。
                    decl.signed,  # signed 字段原样保留
                    decl.unpacked,  # unpacked 维度原样保留
                    decl.attributes,  # 属性列表原样保留

                    # 后缀和前导注释用于维持用户原始格式语义。
                    decl.suffix,  # 声明后缀原样保留
                    list(decl.leading_comments),  # 前导注释副本
                )

                # assign 哨兵进入解析出的 assign 区域。
                dict_groups[str_target_region].append(signal_decl_assign_item)

                # assign 已完成转换，不再按普通声明追加。
                continue

            # 普通声明直接追加到对应结构区域。
            dict_groups[str_target_region].append(decl)

        # output_internal 区域按 output 亲缘布局重排。
        dict_groups["output_internal"] = self._sort_output_internal_decls(  # 按端口亲缘重排 output_internal
            dict_groups["output_internal"],  # 尚未排序的 output 内部声明区
            output_signal_layouts,  # 内部信号到 output 端口亲缘
        )

        # instance_signal 区域按实例端口布局重排。
        dict_groups["instance_signal"] = self._sort_instance_signal_decls(  # 按实例端口亲缘重排连线声明
            dict_groups["instance_signal"],  # 尚未排序的实例连接声明区
            dict_instance_signal_layouts,  # actual 信号到实例端口亲缘
        )

        # 返回完整声明区域分组。
        return dict_groups

    # _copy_grouped_decls 复制已分组的声明映射，并补齐缺失区域。
    def _copy_grouped_decls(self, grouped_decls: GroupedDeclMap) -> GroupedDeclMap:
        """
        复制已分组的声明映射，并保持 formatter 配置声明的区域顺序。

        :param grouped_decls: 已按区域聚合的声明映射。
        :return: 复制后的声明分组字典。
        """

        # grouped_decl_map_copy 先按配置区域顺序预建稳定输出骨架。
        grouped_decl_map_copy: GroupedDeclMap = {  # 声明区域分组副本
            key: list(grouped_decls.get(key, []))  # 已知区域复制对应声明列表
            for key in self.config["structure"]["region_order"]  # formatter 声明区域顺序
        }

        # 兼容未来新增区域键，避免静默丢弃未知分组。
        for region, list_items in grouped_decls.items():

            # 已经按稳定顺序复制的区域无需重复写回。
            if region in grouped_decl_map_copy:

                # 当前区域已完成复制。
                continue

            # 未知区域补到副本尾部，并复制当前区域的声明列表。
            grouped_decl_map_copy[region] = list(list_items)  # 未知区域的声明列表副本

        # 返回不会与调用方共享列表对象的声明分组副本。
        return grouped_decl_map_copy

    # _flatten_grouped_decls 把声明列表或已分组映射统一还原成线性序列。
    def _flatten_grouped_decls(self, decls: list[SignalDecl] | GroupedDeclMap) -> list[SignalDecl]:
        """
        把原始声明列表或已分组声明映射统一展开成声明序列。

        :param decls: 原始声明列表或已分组声明映射。
        :return: 供分析 helper 继续扫描的线性声明列表。
        """

        # 原始列表路径只复制一份，避免调用方后续共享修改。
        if not isinstance(decls, dict):

            # 保持当前声明顺序并复制列表容器。
            return list(decls)

        # list_flattened_decls 按稳定区域顺序收集声明。
        list_flattened_decls: list[SignalDecl] = []  # 展平后的声明序列

        # set_seen_regions 记录已经按配置顺序消费过的区域。
        set_seen_regions: set[str] = set()  # 已按稳定顺序展开的区域集合

        # 先按 formatter 的既定区域顺序展开已知区域。
        for region in self.config["structure"]["region_order"]:

            # 当前区域的声明保持已有分组内部顺序。
            list_flattened_decls.extend(decls.get(region, []))

            # 标记当前区域已被展开。
            set_seen_regions.add(region)

        # 再补未知区域，避免未来扩展键被静默忽略。
        for region, list_items in decls.items():

            # 已按稳定顺序处理的区域不再重复展开。
            if region in set_seen_regions:

                # 当前区域已经合并进结果。
                continue

            # 未知区域沿输入顺序追加到尾部。
            list_flattened_decls.extend(list_items)

        # 返回统一的线性声明序列。
        return list_flattened_decls

    # _is_assign_like_decl 统一识别 assign 哨兵和已转换的 assign 声明。
    def _is_assign_like_decl(self, decl: SignalDecl) -> bool:
        """
        判断声明是否表示连续赋值语义。

        :param decl: 待识别的声明对象。
        :return: assign 哨兵或 assign 渲染声明返回 True。
        """

        # 两种 kind 都表示连续赋值路径，不应再当作普通声明参与传播分析。
        return decl.kind in {"__assign__", "assign"}

    # _resolve_decl_region 根据名称前缀和亲缘集合选择声明区域。
    def _resolve_decl_region(
        self,
        decl: SignalDecl,
        output_signal_names: set[str],
        instance_signal_names: set[str],
    ) -> str:
        """
        判断单个声明应进入的 formatter 结构区域。

        :param decl: 待分类的信号声明。
        :param output_signal_names: output 内部信号名集合。
        :param instance_signal_names: 实例连接信号名集合。
        :return: formatter 结构区域名。
        """

        # assign 哨兵根据是否写 output 相关信号进入不同 assign 区。
        if self._is_assign_like_decl(decl):

            # output 内部 assign 与普通 assign 使用不同渲染区域。
            return "output_assign" if decl.name in output_signal_names else "other_assign"

        # 普通声明统一交给共享优先级策略，避免 formatter 与门禁分叉。
        return resolve_declaration_region(
            decl.name,
            decl.kind,
            output_signal_names,
            instance_signal_names,
            self.config["naming"],
        )

    # _instance_signal_exclusion_prefixes 返回不应归入实例连线区域的命名前缀。
    def _instance_signal_exclusion_prefixes(self) -> tuple[str, ...]:
        """
        返回实例信号候选需要排除的命名前缀。

        :param self: 使用当前 formatter 配置读取命名前缀。
        :return: register、counter、state 等非实例连线区域前缀。
        """

        # 返回配置驱动的排除前缀，避免实例信号区吞掉专用区域信号。
        return (
            self.config["naming"]["register_prefix"],  # 寄存器区域前缀
            self.config["naming"]["counter_prefix"],  # 计数器计数值不按实例连线排序
            self.config["naming"]["state_signal_prefix"],  # FSM 状态寄存器留在状态区域
            self.config["naming"]["flag_prefix"],  # flag 控制位保留在标志区域
            self.config["naming"]["encoder_prefix"],  # encoder 中间量保留专用区域
            self.config["naming"]["decoder_prefix"],  # decoder 输出前暂存量留给解码器区域
            "int_",  # 项目约定普通内部中间量不归入实例连线
        )

    # _is_instance_signal_candidate_name 判断信号名是否可归入实例连线区域。
    def _is_instance_signal_candidate_name(self, name: str) -> bool:
        """
        判断声明名是否适合作为实例连线信号候选。

        :param name: 待检查的信号声明名称。
        :return: 未命中专用区域前缀时返回 True。
        """

        # str_lowered_name 用于大小写不敏感地匹配配置前缀。
        str_lowered_name = name.lower()  # 小写信号名称

        # 返回该名称是否避开所有实例区域排除前缀。
        return not any(
            str_lowered_name.startswith(prefix.lower())
            for prefix in self._instance_signal_exclusion_prefixes()
        )

    # _strip_instance_comments_for_parse 移除实例文本中的行注释。
    def _strip_instance_comments_for_parse(self, text: str) -> str:
        """
        去掉实例化文本中的行注释，供实例 parser 复用。

        :param text: 原始实例化语句文本。
        :return: 去掉行注释和空行后的实例化文本。
        """

        # list_cleaned_lines 保存仍需交给 instance parser 的非空行。
        list_cleaned_lines: list[str] = []  # 去注释后的实例代码行

        # 逐行剥离 Verilog 行注释。
        for raw_line in text.splitlines():

            # str_raw_code 是当前行不含注释的代码片段。
            str_raw_code, _ = self._split_comment(raw_line)  # 实例行代码片段

            # str_stripped_line 用于过滤空白行。
            str_stripped_line = str_raw_code.strip()  # 去空白后的实例行

            # 非空代码行才参与实例解析。
            if str_stripped_line:

                # 保留当前实例化代码行，避免注释干扰端口解析。
                list_cleaned_lines.append(str_stripped_line)

        # 返回 parser 可消费的实例化文本。
        return "\n".join(list_cleaned_lines)

    # _extract_instance_association_actual_text 提取端口关联的 actual 表达式。
    def _extract_instance_association_actual_text(self, item: str) -> str:
        """
        从 `.formal(actual)` 端口关联中提取 actual 文本。

        :param item: 单个实例端口关联文本。
        :return: actual 表达式文本；非命名关联时返回规范化后的原文本。
        """

        # str_stripped_item 折叠空白，便于正则识别命名端口关联。
        str_stripped_item = " ".join(item.strip().split())  # 规范化端口关联文本

        # match_association 保存 `.formal(actual)` 结构匹配结果。
        match_association = re.match(  # actual 提取用命名端口关联匹配
            r"^\.(?P<formal>[A-Za-z_]\w*)\((?P<actual>.*)\)$",  # `.formal(actual)` 端口关联模式
            str_stripped_item,  # 已折叠空白的端口关联文本
        )

        # 命名端口关联优先返回括号内 actual。
        if match_association:

            # actual 文本去掉外层空白后用于信号名提取。
            return match_association.group("actual").strip()

        # 非命名关联直接返回规范化文本。
        return str_stripped_item

    # _extract_instance_association_formal_name 提取命名端口关联的 formal 名。
    def _extract_instance_association_formal_name(self, item: str) -> str | None:
        """
        从 `.formal(actual)` 端口关联中提取 formal 端口名。

        :param item: 单个实例端口关联文本。
        :return: 命名关联中的 formal 端口名；位置关联时返回 None。
        """

        # str_stripped_item 先压平端口关联空白，避免换行影响 formal 名提取。
        str_stripped_item = " ".join(item.strip().split())  # formal 提取前的关联文本

        # match_association 只在命名端口关联形态下捕获 formal 名。
        match_association = re.match(  # formal 端口捕获匹配
            r"^\.(?P<formal>[A-Za-z_]\w*)\((?P<actual>.*)\)$",  # formal/actual 分组正则
            str_stripped_item,  # 已压平空白的端口关联文本
        )

        # 命名端口关联可提供 formal 名。
        if match_association:

            # 返回接口元数据查询所需的 formal 端口名。
            return match_association.group("formal")

        # 非命名关联没有 formal 端口名。
        return None

    # _extract_layout_signal_names_from_text 从文本中提取已知布局信号。
    def _extract_layout_signal_names_from_text(
        self,
        text: str,
        seed_layouts: dict[str, object],
        *,
        exclude: set[str] | None = None,
    ) -> list[str]:
        """
        按文本出现顺序提取可继承布局的已知信号名。

        :param text: 待扫描的表达式或语句文本。
        :param seed_layouts: 可提供布局亲缘的信号布局索引。
        :param exclude: 需要排除的信号名集合。
        :return: 文本中首次出现且存在布局的信号名列表。
        """

        # set_excluded_names 保存本次扫描需要忽略的信号名。
        set_excluded_names = exclude or set()  # 排除信号名集合

        # list_seen 按文本出现顺序收集布局信号名。
        list_seen: list[str] = []  # 已按表达式顺序确认的布局信号

        # 逐个标识符检查是否能从 seed_layouts 继承布局。
        for token in re.findall(r"\b[A-Za-z_]\w*\b", text):

            # 排除项、未知布局项和重复项都不进入结果。
            if token in set_excluded_names or token not in seed_layouts or token in list_seen:

                # 当前 token 不提供新的布局亲缘。
                continue

            # 首次命中的布局信号按出现顺序记录。
            list_seen.append(token)

        # 返回去重且顺序稳定的布局信号名。
        return list_seen

    # _collect_locally_driven_signal_names 汇总模块内部已经驱动的信号名。
    def _collect_locally_driven_signal_names(
        self,
        decls: list[SignalDecl] | GroupedDeclMap,
        assigns: list[AssignStmt],
        always_blocks: list[AlwaysBlock] | GroupedAlwaysMap,
    ) -> set[str]:
        """
        收集声明初始化、连续赋值和 always 写入的本地驱动信号。

        :param decls: 模块内部信号声明列表。
        :param assigns: 模块连续赋值列表。
        :param always_blocks: 模块 always 块列表。
        :return: 模块内部已经被本地逻辑驱动的信号名集合。
        """

        # set_driven 汇总所有可确认由本模块逻辑驱动的信号。
        set_driven: set[str] = set()  # 接口方向未知时用于排除实例输出的本地写入信号

        # list_decl_items 统一展开声明输入，兼容 renderer 回传的分组结果。
        list_decl_items = self._flatten_grouped_decls(decls)  # 本地驱动分析使用的线性声明序列

        # 再把过程块输入拉平成目标扫描序列，补上 grouped always 对本地驱动的贡献。
        list_always_items = self._flatten_grouped_always(always_blocks)  # 本地驱动判定使用的过程块目标序列

        # 声明初始化和 assign 哨兵都表示本地驱动。
        for decl in list_decl_items:

            # 初始化声明或 assign 哨兵写入当前声明名。
            if self._is_assign_like_decl(decl) or decl.init:

                # 记录声明侧确认的本地驱动信号。
                set_driven.add(decl.name)

        # 连续赋值左值提供本地驱动信号。
        for assign in assigns:

            # str_lhs_name 只接受简单信号左值。
            str_lhs_name = self._extract_exact_simple_signal_name(assign.lhs)  # assign 左侧简单信号名

            # 简单左值可直接记录为本地驱动。
            if str_lhs_name is not None:

                # 连续赋值写入的左侧信号由本模块驱动。
                set_driven.add(str_lhs_name)

        # always 目标集合记录过程赋值驱动的信号。
        for block in list_always_items:

            # 合并当前 always 分析得到的赋值目标。
            set_driven.update(block.targets)

        # 返回完整本地驱动信号集合。
        return set_driven

    # _collect_top_level_instances 递归收集顶层和条件分支里的实例块。
    def _collect_top_level_instances(self, items: dict[str, list]) -> list[InstanceBlock]:
        """
        从解析后的 items 结构中收集顶层实例块。

        :param items: parse_mixin 产生的模块 items 字典。
        :return: 当前层级和条件分支中的实例块列表。
        """

        # list_instances 先收集当前层级已经解析出的实例。
        list_instances = list(items.get("instances", []))  # 顶层实例块列表

        # 条件生成分支内部也可能包含实例块。
        for conditional in items.get("conditionals", []):

            # 递归合并 true 分支实例。
            list_instances.extend(self._collect_top_level_instances(conditional.true_items))

            # 递归合并 generate 条件的 false 分支实例。
            list_instances.extend(self._collect_top_level_instances(conditional.false_items))

        # 返回按源码遍历顺序收集的实例块。
        return list_instances

    # _instance_signal_decl_indices 为实例信号布局推导建立候选声明索引。
    def _instance_signal_decl_indices(self, decl_items: list[SignalDecl]) -> dict[str, int]:
        """
        扫描声明列表，收集允许进入 instance_signal 区域的候选声明顺序。

        :param decl_items: 已展开的模块内部声明序列。
        :return: 候选声明名到源码顺序的索引。
        """

        # 返回普通声明中命中实例信号命名规则的顺序索引。
        return {
            decl.name: index  # 候选声明名及其原始顺序
            for index, decl in enumerate(decl_items)  # 扫描模块内部声明
            if not self._is_assign_like_decl(decl) and self._is_instance_signal_candidate_name(decl.name)  # 排除连续赋值声明和专用区域前缀
        }

    # _instance_port_layout_metadata 提取实例接口的方向、布局和端口名映射。
    def _instance_port_layout_metadata(
        self,
        instance: InstanceBlock,
    ) -> tuple[dict[str, str], dict[str, PortLayoutInfo], dict[str, str]]:
        """
        查询当前实例对应模块的接口元数据。

        :param instance: 当前待分析的实例块。
        :return: `(<port directions>, <port layouts>, <port alias map>)`。
        """

        # 无模块名时无法查询接口元数据，直接返回空映射。
        if not instance.module_name:

            # 接口未知时沿用本地驱动关系做保守判断。
            return ({}, {}, {})

        # dict_interface 保存当前被例化模块的接口元数据。
        dict_interface: dict[str, object] | None = self._resolve_module_interface(instance.module_name)  # 例化模块接口元数据

        # 缺少接口描述时退回空元数据路径。
        if dict_interface is None:

            # 后续调用方会按未知接口处理方向和布局。
            return ({}, {}, {})

        # dict_port_directions 提供 formal 端口到方向的映射。
        dict_port_directions = cast(dict[str, str], dict_interface.get("port_directions", {}))  # formal 端口方向元数据

        # dict_port_layouts 提供 formal 端口到布局信息的映射。
        dict_port_layouts = cast(dict[str, PortLayoutInfo], dict_interface.get("port_layouts", {}))  # formal 端口布局元数据

        # dict_port_name_map 处理接口别名或 canonical formal 名对齐。
        dict_port_name_map = cast(dict[str, str], dict_interface.get("ports", {}))  # 实例 formal 名到接口 canonical 名的映射

        # 返回当前实例可用的接口方向、布局和端口名映射。
        return (dict_port_directions, dict_port_layouts, dict_port_name_map)

    # _instance_signal_layout_rank 生成实例布局的稳定排序键。
    def _instance_signal_layout_rank(self, layout: InstanceSignalLayout) -> tuple[int, int, int]:
        """
        生成实例布局比较时使用的排序键。

        :param layout: 当前待比较的实例信号布局。
        :return: `(instance_rank, association_rank, decl_index)` 排序元组。
        """

        # 排序时优先比较实例顺序、端口顺序和声明顺序。
        return (layout.instance_rank, layout.association_rank, layout.decl_index)

    # _record_preferred_instance_signal_layout 只保留同一信号最靠前的实例布局。
    def _record_preferred_instance_signal_layout(
        self,
        layouts: dict[str, InstanceSignalLayout],
        signal_name: str,
        layout: InstanceSignalLayout,
    ) -> None:
        """
        以最小排序键为准，为同一 actual 信号保留最靠前的实例布局来源。

        :param layouts: 当前已收集的实例信号布局索引。
        :param signal_name: 当前 actual 信号名。
        :param layout: 当前实例端口关联提供的布局候选。
        :return: 本函数原地更新 layouts，不返回业务值。
        """

        # instance_signal_layout_existing_source 保存该信号已经登记的最优布局。
        instance_signal_layout_existing_source = cast(InstanceSignalLayout | None, layouts.get(signal_name))  # 当前信号已登记的实例布局

        # 当前布局更靠前时覆盖已有记录。
        if (
            instance_signal_layout_existing_source is None
            or self._instance_signal_layout_rank(layout)
            < self._instance_signal_layout_rank(instance_signal_layout_existing_source)
        ):

            # 更新该 actual 信号当前最靠前的实例布局。
            layouts[signal_name] = layout  # actual 信号最优实例布局

    # _instance_signal_layout_candidate_for_association 生成单个端口关联的实例布局候选。
    def _instance_signal_layout_candidate_for_association(
        self,
        instance: InstanceBlock, item: str,
        dict_decl_indices: dict[str, int], set_locally_driven: set[str],
        tuple_port_metadata: tuple[dict[str, str], dict[str, PortLayoutInfo], dict[str, str]],
        instance_rank: int, association_rank: int,
    ) -> tuple[str, InstanceSignalLayout] | None:
        """
        从单个实例端口关联中提取可用于 instance_signal 排序的布局候选。

        :param instance: 当前实例块。
        :param item: 当前端口关联文本。
        :param dict_decl_indices: 候选声明顺序索引。
        :param set_locally_driven: 本模块本地已驱动的信号名集合。
        :param tuple_port_metadata: 当前实例的方向、布局和端口名映射。
        :param instance_rank: 实例在源码中的顺序。
        :param association_rank: 端口关联在实例中的顺序。
        :return: `(<actual signal name>, <layout candidate>)`；不满足条件时返回 None。
        """

        # 先把接口元数据拆成方向、布局和 canonical 端口名映射。
        dict_port_directions, dict_port_layouts, dict_port_name_map = tuple_port_metadata  # 当前实例的接口方向与布局元数据

        # str_actual_text 是实例连接括号中的真实信号表达式。
        str_actual_text = self._extract_instance_association_actual_text(item)  # actual 端连接表达式

        # str_actual_name 只接受简单信号名作为实例信号布局目标。
        str_actual_name = self._extract_exact_simple_signal_name(str_actual_text)  # actual 端简单信号名

        # 复杂表达式或未声明信号不能参与实例布局排序。
        if str_actual_name is None or str_actual_name not in dict_decl_indices:

            # 非候选声明不进入实例信号区域排序。
            return None

        # str_formal_name 用于查接口方向和布局。
        str_formal_name = self._extract_instance_association_formal_name(item) or ""  # 命名关联 formal 端口名

        # str_resolved_formal_name 对齐接口元数据中的 canonical formal 名。
        str_resolved_formal_name = dict_port_name_map.get(str_formal_name, str_formal_name)  # 接口元数据中的 canonical formal 名

        # 有接口方向时只把 output/inout 关联视作实例驱动信号。
        if dict_port_directions:

            # 缺少 formal 名或方向不是输出侧时跳过。
            if not str_formal_name or dict_port_directions.get(str_resolved_formal_name) not in {"output", "inout"}:

                # 非实例驱动方向不应影响声明区域。
                return None

        # 无接口方向时，本地已驱动信号按输入或内部信号保守处理。
        elif str_actual_name in set_locally_driven:

            # 已被本模块驱动的信号不按未知实例输出处理。
            return None

        # port_layout_info_default_port_layout 在 formal 缺少布局记录时把信号回收到用户接口组。
        port_layout_info_default_port_layout: PortLayoutInfo = PortLayoutInfo(group="用户接口")  # 缺少接口布局元数据时的兜底分组

        # port_layout_candidate 先拿到接口元数据里登记的 formal 布局记录。
        port_layout_candidate = dict_port_layouts.get(  # formal 名映射出的布局记录
            str_resolved_formal_name, port_layout_info_default_port_layout,  # canonical 名与兜底布局
        )

        # port_layout_info_selected 决定 actual 信号后续进入哪个声明分区。
        port_layout_info_selected: PortLayoutInfo = cast(PortLayoutInfo, port_layout_candidate)  # actual 端继承到的 formal 布局元数据

        # instance_signal_layout_candidate 记录当前实例关联能提供的布局来源。
        instance_signal_layout_candidate: InstanceSignalLayout = InstanceSignalLayout(  # 当前实例端口候选布局
            module_name=instance.module_name,  # 布局来源实例的模块名
            group=port_layout_info_selected.group or "用户接口",  # 当前 formal 端口归属的分组
            section=port_layout_info_selected.section,  # formal 端口所在的声明区域
            instance_rank=instance_rank,  # 实例在源码中的出现顺序
            association_rank=association_rank,  # 端口连接在实例中的顺序
            decl_index=dict_decl_indices[str_actual_name],  # actual 信号自身的声明顺序
        )

        # 返回当前 actual 信号名及其布局候选。
        return (str_actual_name, instance_signal_layout_candidate)

    # _collect_instance_signal_layouts 为实例输出/双向端口连接信号建立布局亲缘。
    def _collect_instance_signal_layouts(
        self,
        decls: list[SignalDecl] | GroupedDeclMap,
        assigns: list[AssignStmt],
        always_blocks: list[AlwaysBlock] | GroupedAlwaysMap,
        items: dict[str, list],
    ) -> dict[str, InstanceSignalLayout]:
        """
        从实例端口连接关系推导实例信号声明的渲染布局。

        :param decls: 模块内部信号声明列表。
        :param assigns: 模块连续赋值列表。
        :param always_blocks: 模块 always 块列表。
        :param items: 解析阶段输出的模块条目字典。
        :return: 信号名到实例布局亲缘的索引。
        """

        # 顶层实例是实例信号布局亲缘的唯一来源。
        list_instances = self._collect_top_level_instances(items)  # 可提供实例端口亲缘的实例序列

        # list_decl_items 统一展开声明输入，兼容 renderer 回流的分组声明。
        list_decl_items = self._flatten_grouped_decls(decls)  # 实例布局分析使用的线性声明序列

        # list_always_items 用于识别已经被本地逻辑驱动的信号。
        list_always_items = self._flatten_grouped_always(always_blocks)  # 实例布局分析使用的过程块扫描序列

        # 缺少实例或声明时无需建立 instance_signal 布局。
        if not list_decl_items or not list_instances:

            # 缺少任一来源时实例信号区保持为空。
            return {}

        # dict_decl_indices 提供实例候选声明的稳定顺序。
        dict_decl_indices = self._instance_signal_decl_indices(list_decl_items)  # 实例候选信号到声明顺序的索引

        # 没有候选声明时不需要继续解析实例端口。
        if not dict_decl_indices:

            # 当前模块不存在可归入 instance_signal 区域的声明。
            return {}

        # set_locally_driven 用来拦住已被本模块驱动的信号，避免误判成实例输出。
        set_locally_driven = self._collect_locally_driven_signal_names(  # 接口未知时需要排除的本地驱动信号
            list_decl_items,  # 当前模块展开后的声明序列
            assigns,  # 当前模块的连续赋值集合
            list_always_items,  # 当前模块展开后的过程块序列
        )

        # dict_layouts 保存每个信号最靠前的实例端口布局。
        dict_layouts: dict[str, InstanceSignalLayout] = {}  # actual 信号到最早实例端口来源

        # 逐个实例按源码顺序扫描，稳定保留最早命中的布局亲缘。
        for instance_rank, instance in enumerate(list_instances):

            # dict_parsed_instance 去掉注释后再解析，避免注释噪声影响端口提取。
            dict_parsed_instance = self._parse_instance_for_render(  # 当前实例的端口关联解析快照
                self._strip_instance_comments_for_parse(instance.text),  # 去掉注释后的实例文本
            )

            # 解析失败的实例保留原渲染路径，不影响声明分组。
            if dict_parsed_instance is None:

                # 注释剥离后的实例文本仍无法被 renderer 解析，放弃用它推导声明布局。
                continue

            # list_port_items 记录当前实例文本里按顺序出现的端口关联项。
            list_port_items = dict_parsed_instance["ports"]  # 当前实例的端口关联列表

            # tuple_port_metadata 此时承载的是整个被调模块的接口契约，后面每个端口关联都要复用这份元数据。
            tuple_port_metadata = self._instance_port_layout_metadata(instance)  # 被调模块接口方向/分组/canonical 端口名的缓存快照

            # 每个端口关联都带上实例内顺序，便于后续复现源码中的布局偏好。
            for association_rank, item in enumerate(list_port_items):

                # tuple_layout_candidate 只在关联项满足驱动条件时返回布局候选。
                tuple_layout_candidate = self._instance_signal_layout_candidate_for_association(  # 当前端口关联提取出的 actual 布局候选
                    instance, item, dict_decl_indices,  # 当前实例、关联文本与声明索引
                    set_locally_driven, tuple_port_metadata,  # 本地驱动过滤集合与接口元数据
                    instance_rank, association_rank,  # 实例顺序和端口顺序
                )

                # 当前端口关联不能提供稳定布局时跳过。
                if tuple_layout_candidate is None:

                    # 非候选 actual 或非实例驱动方向不会影响排序。
                    continue

                # 解包后即可把 actual 信号和布局来源写回全局索引。
                str_actual_name, instance_signal_layout_candidate = tuple_layout_candidate  # 当前端口关联命中的 actual 名和布局

                # 只保留同一 actual 信号最靠前的实例布局来源。
                self._record_preferred_instance_signal_layout(
                    dict_layouts,
                    str_actual_name,
                    instance_signal_layout_candidate,
                )

        # 返回实例输出/双向端口推导出的信号布局。
        return dict_layouts

    # _signal_affinity_identity 生成实例布局去重比较用身份。
    def _signal_affinity_identity(self, layout: InstanceSignalLayout) -> tuple[str, str, str]:
        """
        提取实例布局在传播判定中的业务身份。

        :param layout: 已知实例信号布局。
        :return: 由模块名、分组和 section 组成的身份元组。
        """

        # 返回去除排序字段后的实例亲缘身份。
        return (layout.module_name, layout.group, layout.section)

    # _assign_source_layout_identity 生成 assign 源布局去重比较用身份。
    def _assign_source_layout_identity(self, layout: AssignSourceLayout) -> tuple[str, str, str]:
        """
        提取 assign 源布局在传播判定中的业务身份。

        :param layout: 已知 assign 源布局。
        :return: 由 group、subgroup 和 section 组成的身份元组。
        """

        # 返回去除端口排序字段后的 assign 源身份。
        return (layout.group, layout.subgroup, layout.section)

    # _extract_affinity_signal_names_from_text 提取可传播实例亲缘的信号名。
    def _extract_affinity_signal_names_from_text(
        self,
        text: str,
        seed_layouts: dict[str, InstanceSignalLayout],
        *,
        exclude: set[str] | None = None,
    ) -> list[str]:
        """
        从文本中按出现顺序提取已具备实例布局的信号名。

        :param text: 待扫描的 Verilog 文本。
        :param seed_layouts: 已知实例信号布局索引。
        :param exclude: 本轮扫描需要排除的信号名。
        :return: 可作为实例亲缘来源的信号名列表。
        """

        # 复用通用布局文本扫描逻辑，保持 assign/always 传播口径一致。
        return self._extract_layout_signal_names_from_text(text, seed_layouts, exclude=exclude)

    # _collect_port_assign_source_layouts 收集端口作为 assign 传播来源的布局。
    def _collect_port_assign_source_layouts(self, ports: list[PortDecl]) -> dict[str, AssignSourceLayout]:
        """
        将模块端口声明转换为 assign 布局传播种子。

        :param ports: 模块端口声明列表。
        :return: 端口名到 assign 源布局的映射。
        """

        # dict_layouts 把端口名映射到 assign 传播使用的源布局。
        dict_layouts: dict[str, AssignSourceLayout] = {}  # 可被右值引用继承的端口种子表

        # 端口顺序作为传播排序的基础 rank。
        for index, port in enumerate(ports):

            # raw_text 端口保持用户原始渲染，不参与自动布局传播。
            if not port.name or port.raw_text:

                # 非结构化端口不提供稳定的布局元数据。
                continue

            # 保存端口的分组、子组和 section 供下游信号继承。
            dict_layouts[port.name] = AssignSourceLayout(  # assign 传播入口端口布局
                group=port.group,  # 端口在用户接口中的分组
                subgroup=port.subgroup,  # 接口分组内部的细分标签
                section=port.section,  # formatter 区域继承的端口片段
                port_rank=index,  # assign 传播选择来源时使用的端口顺序
            )

        # 返回可作为 assign 传播种子的端口布局。
        return dict_layouts

    # _assign_source_decl_indices 建立可接收 assign 源布局传播的声明索引。
    def _assign_source_decl_indices(self, decl_items: list[SignalDecl]) -> dict[str, int]:
        """
        扫描声明列表，收集允许接收 assign 源布局传播的声明顺序。

        :param decl_items: 已展开的模块内部声明序列。
        :return: 普通声明名到源码顺序的索引。
        """

        # 返回排除 assign 哨兵后的普通声明顺序索引。
        return {
            decl.name: index  # 普通声明名及其原始顺序
            for index, decl in enumerate(decl_items)  # 扫描所有声明
            if not self._is_assign_like_decl(decl)  # 连续赋值声明不接收传播布局
        }

    # _assign_source_layout_seed_from_ref_names 从引用列表中选出唯一可传播的布局来源。
    def _assign_source_layout_seed_from_ref_names(
        self,
        ref_names: list[str],
        dict_combined_layouts: dict[str, AssignSourceLayout],
    ) -> AssignSourceLayout | None:
        """
        从右值引用的已知布局中选出唯一且稳定的 assign 源布局来源。

        :param ref_names: 右值文本中命中的已知布局信号名列表。
        :param dict_combined_layouts: 当前可传播的布局索引。
        :return: 唯一布局来源；存在混合亲缘时返回 None。
        """

        # set_unique_affinities 用于判断所有引用是否属于同一端口亲缘。
        set_unique_affinities = {self._assign_source_layout_identity(dict_combined_layouts[name]) for name in ref_names}  # 引用集合对应的端口亲缘身份

        # 多个亲缘混合会导致分组含义不确定。
        if len(set_unique_affinities) != 1:

            # 混合来源不会向下游信号继续传播布局。
            return None

        # 返回最早端口 rank 对应的稳定布局来源。
        return min(
            (dict_combined_layouts[name] for name in ref_names),
            key=lambda layout: layout.port_rank,
        )

    # _propagated_assign_source_layout_candidate_from_assign 从单条 assign 推导传播布局。
    def _propagated_assign_source_layout_candidate_from_assign(
        self,
        assign: AssignStmt,
        dict_decl_indices: dict[str, int],
        dict_combined_layouts: dict[str, AssignSourceLayout],
    ) -> tuple[str, AssignSourceLayout] | None:
        """
        检查单条连续赋值，推导是否能为 lhs 产生新的 assign 源布局。

        :param assign: 当前待分析的连续赋值语句。
        :param dict_decl_indices: 可接收传播布局的声明索引。
        :param dict_combined_layouts: 当前已知的种子与链式传播布局索引。
        :return: `(<lhs signal>, <layout>)`；无法推导时返回 None。
        """

        # str_lhs_name 是可能继承布局的简单左值信号。
        str_lhs_name = self._extract_simple_signal_base(assign.lhs, "signal_affinity_propagation")  # assign 源布局接收端信号名

        # 未声明、复杂左值或已有布局的信号不需要传播。
        if str_lhs_name is None or str_lhs_name not in dict_decl_indices or str_lhs_name in dict_combined_layouts:

            # 当前 assign 不会产生新的传播布局。
            return None

        # list_ref_names 是 rhs 中已经具有布局的引用信号。
        list_ref_names = self._extract_layout_signal_names_from_text(  # rhs 文本命中的已知布局来源
            assign.rhs, dict_combined_layouts, exclude={str_lhs_name},  # 右值文本、布局索引与自环排除目标
        )

        # 没有来源信号时无法推导。
        if not list_ref_names:

            # rhs 不引用已知布局时跳过。
            return None

        # assign_source_layout_seed 代表 rhs 在单一端口亲缘下选出的传播来源。
        assign_source_layout_seed = self._assign_source_layout_seed_from_ref_names(  # rhs 收敛出的唯一端口亲缘
            list_ref_names, dict_combined_layouts,  # 命中的来源名列表与综合布局索引
        )

        # 混合来源不会产生可交付的传播布局。
        if assign_source_layout_seed is None:

            # 右值引用了多个不同端口亲缘。
            return None

        # 返回当前 lhs 信号及其新推导出的传播布局。
        return (str_lhs_name, assign_source_layout_seed)

    # _propagated_assign_source_layout_candidate_from_block 从单目标 always 推导传播布局。
    def _propagated_assign_source_layout_candidate_from_block(
        self,
        block: AlwaysBlock,
        dict_decl_indices: dict[str, int],
        dict_combined_layouts: dict[str, AssignSourceLayout],
    ) -> tuple[str, AssignSourceLayout] | None:
        """
        检查单目标 always，推导是否能为 target 产生新的 assign 源布局。

        :param block: 当前待分析的 always 块。
        :param dict_decl_indices: 可接收传播布局的声明索引。
        :param dict_combined_layouts: 当前已知的种子与链式传播布局索引。
        :return: `(<target signal>, <layout>)`；无法推导时返回 None。
        """

        # 多目标块无法确定唯一布局接收端。
        if len(block.targets) != 1:

            # 混合目标 always 不参与布局传播。
            return None

        # str_target 是 always 唯一写入目标。
        str_target = block.targets[0]  # always 唯一写入目标

        # 非声明目标或已有布局目标不重复处理。
        if str_target not in dict_decl_indices or str_target in dict_combined_layouts:

            # 当前 always 没有新的布局接收端。
            return None

        # str_block_text 把 always 头和正文拼成统一扫描输入。
        str_block_text = "\n".join([block.header, *block.lines])  # 当前 always 的完整文本视图

        # list_ref_names 收集这个过程块里命中的已知布局来源信号。
        list_ref_names = self._extract_layout_signal_names_from_text(  # 当前过程块命中的布局来源信号
            str_block_text, dict_combined_layouts, exclude={str_target},  # 文本、综合布局索引与排除目标
        )

        # 引用列表为空时，说明这个过程块没有可继承的布局输入。
        if not list_ref_names:

            # 该过程块不会为 target 带来新的端口亲缘。
            return None

        # assign_source_layout_seed 只在引用同一端口亲缘时才会成立。
        assign_source_layout_seed = self._assign_source_layout_seed_from_ref_names(  # 当前 always 能继承的唯一布局来源
            list_ref_names, dict_combined_layouts,  # 命中的来源信号与当前综合布局索引
        )

        # 一旦混入多个端口亲缘，就不再为该 target 推导布局。
        if assign_source_layout_seed is None:

            # 该过程块同时读到了不同来源的布局信号。
            return None

        # 返回 always 唯一目标及其新推导出的传播布局。
        return (str_target, assign_source_layout_seed)

    # _collect_propagated_assign_source_layouts 沿 assign/always 传播端口布局亲缘。
    def _collect_propagated_assign_source_layouts(
        self,
        decls: list[SignalDecl] | GroupedDeclMap,
        assigns: list[AssignStmt],
        always_blocks: list[AlwaysBlock] | GroupedAlwaysMap,
        seed_layouts: dict[str, AssignSourceLayout],
    ) -> dict[str, AssignSourceLayout]:
        """
        沿连续赋值和单目标 always 传播端口布局亲缘。

        :param decls: 模块内部信号声明列表。
        :param assigns: 模块连续赋值列表。
        :param always_blocks: 模块 always 块列表。
        :param seed_layouts: 已知端口或上游信号布局种子。
        :return: 本函数新推导出的 assign 源布局索引。
        """

        # 没有种子布局时无法向内部信号传播 assign 亲缘。
        if not seed_layouts:

            # 无来源布局时传播结果为空。
            return {}

        # list_decl_items 统一展开声明输入，兼容 renderer 传回的分组结果。
        list_decl_items = self._flatten_grouped_decls(decls)  # assign 源传播使用的线性声明序列

        # list_always_items 统一展开过程块输入，兼容 renderer 传回的 always 分组。
        list_always_items = self._flatten_grouped_always(always_blocks)  # assign 源传播使用的线性 always 序列

        # dict_decl_indices 限定哪些声明可以接收传播布局。
        dict_decl_indices = self._assign_source_decl_indices(list_decl_items)  # 可接收 assign 源布局的声明索引

        # 没有普通声明时直接结束。
        if not dict_decl_indices:

            # 没有接收端声明时无传播目标。
            return {}

        # dict_propagated 只保存本轮新增的传播结果，避免覆盖输入种子。
        dict_propagated: dict[str, AssignSourceLayout] = {}  # 新传播出的 assign 源布局

        # dict_combined_layouts 把种子和新增结果并在一起，供后续链式引用。
        dict_combined_layouts: dict[str, AssignSourceLayout] = dict(seed_layouts)  # 种子和链式传播结果的合并索引

        # assign 路径负责把端口亲缘继续传给简单左值信号。
        for assign in assigns:

            # tuple_layout_candidate 为空时，说明 rhs 没有形成唯一亲缘来源。
            tuple_layout_candidate = self._propagated_assign_source_layout_candidate_from_assign(  # 当前 assign 产生的布局传播候选
                assign,  # 当前连续赋值语句
                dict_decl_indices,  # 允许接收传播的声明索引
                dict_combined_layouts,  # 种子与链式传播的综合布局索引
            )

            # 当前 assign 不能产生新布局时跳过。
            if tuple_layout_candidate is None:

                # rhs 不引用唯一布局来源时不传播。
                continue

            # 解包后把 assign 左值的传播结果登记到本轮索引。
            str_target_name, assign_source_layout_seed = tuple_layout_candidate  # assign 传播得到的目标名和布局

            # 登记传播结果，并允许后续链路继续引用。
            dict_propagated[str_target_name] = assign_source_layout_seed  # assign 左值的新传播布局

            # 更新综合布局索引，支持多级 assign 链。
            dict_combined_layouts[str_target_name] = assign_source_layout_seed  # 后续传播可复用的布局来源

        # 单目标 always 也允许从内部引用的已知亲缘继续扩散布局。
        for block in list_always_items:

            # tuple_layout_candidate 为 None 时，说明该过程块没有稳定传播源。
            tuple_layout_candidate = self._propagated_assign_source_layout_candidate_from_block(  # 过程块扩散出的端口亲缘候选
                block, dict_decl_indices, dict_combined_layouts,  # 过程块、声明索引与综合布局索引
            )

            # 候选为空时说明这条过程块没有形成可登记的端口亲缘结果。
            if tuple_layout_candidate is None:

                # 这个过程块读到的来源已经不再唯一，所以不允许再把端口亲缘扩散给后续信号。
                continue

            # 拆包后得到的是“谁接收了亲缘”和“接收到的亲缘是什么”，二者后面会写进不同索引。
            str_target_name, assign_source_layout_seed = tuple_layout_candidate  # 过程块抽出的目标名与端口亲缘

            # 先把过程块给出的亲缘写入本轮结果，供摘要统计和后续合并复用。
            dict_propagated[str_target_name] = assign_source_layout_seed  # always 目标的新传播布局

            # 再把同一亲缘并回综合索引，让后续链路可以继续接力传播。
            dict_combined_layouts[str_target_name] = assign_source_layout_seed  # 后续链路可引用的 always 布局

        # 返回本轮新推导的 assign source 布局。
        return dict_propagated

    # _signal_layout_decl_indices_for_region 建立指定声明区域的实例布局接收端索引。
    def _signal_layout_decl_indices_for_region(
        self,
        decl_items: list[SignalDecl],
        output_signal_names: set[str],
        target_region: str,
    ) -> dict[str, int]:
        """
        扫描声明列表，收集目标区域内允许接收实例亲缘传播的声明顺序。

        :param decl_items: 已展开的模块内部声明序列。
        :param output_signal_names: output 内部信号名集合。
        :param target_region: 允许接收传播布局的声明区域。
        :return: 目标区域声明名到源码顺序的索引。
        """

        # dict_decl_indices 只登记目标区域内的普通声明。
        dict_decl_indices: dict[str, int] = {}  # 可接收实例亲缘的声明顺序表

        # 普通循环更容易表达每个跳过条件。
        for index, decl in enumerate(decl_items):

            # assign 哨兵不是可接收实例布局的声明。
            if self._is_assign_like_decl(decl):

                # 连续赋值哨兵由 assign 传播逻辑单独处理。
                continue

            # str_decl_region 复用声明分区规则确认传播边界。
            str_decl_region = self._resolve_decl_region(decl, output_signal_names, set())  # 当前声明所属渲染区域

            # 跨区域传播会打乱 formatter 的声明分组。
            if str_decl_region != target_region:

                # 当前声明不属于本轮允许接收传播的区域。
                continue

            # 登记目标区域声明的源码顺序。
            dict_decl_indices[decl.name] = index  # 目标区域声明的源码顺序

        # 返回目标区域内可接收传播的声明索引。
        return dict_decl_indices

    # _copied_instance_signal_layout_for_decl 用来源布局和目标声明顺序构造传播结果。
    def _copied_instance_signal_layout_for_decl(
        self,
        layout_seed: InstanceSignalLayout,
        decl_index: int,
    ) -> InstanceSignalLayout:
        """
        复制实例布局来源，并替换成目标声明自己的顺序。

        :param layout_seed: 传播来源实例布局。
        :param decl_index: 目标信号的声明顺序。
        :return: 复制后的实例信号布局。
        """

        # 返回继承来源实例元信息、但使用目标声明顺序的布局副本。
        return InstanceSignalLayout(
            module_name=layout_seed.module_name,
            group=layout_seed.group,
            section=layout_seed.section,
            instance_rank=layout_seed.instance_rank,
            association_rank=layout_seed.association_rank,
            decl_index=decl_index,
        )

    # _instance_affinity_seed_from_ref_names 从引用列表中选出唯一可传播的实例亲缘来源。
    def _instance_affinity_seed_from_ref_names(
        self,
        ref_names: list[str],
        seed_layouts: dict[str, InstanceSignalLayout],
    ) -> InstanceSignalLayout | None:
        """
        从引用的实例布局信号中选出唯一且稳定的亲缘来源。

        :param ref_names: 文本中命中的已知实例布局信号名列表。
        :param seed_layouts: 已知实例信号布局索引。
        :return: 唯一实例亲缘来源；存在混合亲缘时返回 None。
        """

        # set_unique_affinities 用于确认所有引用是否属于同一实例亲缘。
        set_unique_affinities = {self._signal_affinity_identity(seed_layouts[name]) for name in ref_names}  # 引用集合对应的实例亲缘身份

        # 引用多个不同实例亲缘时保持保守。
        if len(set_unique_affinities) != 1:

            # 混合实例亲缘不会继续向下游信号传播。
            return None

        # 唯一亲缘时沿用首个来源，保留文本出现顺序。
        return seed_layouts[ref_names[0]]

    # _propagated_instance_signal_layout_candidate_from_assign 从单条 assign 推导实例亲缘传播。
    def _propagated_instance_signal_layout_candidate_from_assign(
        self,
        assign: AssignStmt,
        dict_decl_indices: dict[str, int],
        seed_layouts: dict[str, InstanceSignalLayout],
        dict_propagated: dict[str, InstanceSignalLayout],
    ) -> tuple[str, InstanceSignalLayout] | None:
        """
        检查单条连续赋值，推导是否能为 lhs 产生新的实例亲缘布局。

        :param assign: 当前待分析的连续赋值语句。
        :param dict_decl_indices: 目标区域声明顺序索引。
        :param seed_layouts: 已知实例信号布局索引。
        :param dict_propagated: 当前轮已经推导出的布局索引。
        :return: `(<lhs signal>, <layout>)`；无法推导时返回 None。
        """

        # str_lhs_name 是可能接收实例布局的简单左值信号。
        str_lhs_name = self._extract_simple_signal_base(assign.lhs, "signal_affinity_propagation")  # assign 布局接收端信号名

        # str_rhs_name 是可能提供实例布局的简单右值信号。
        str_rhs_name = self._extract_simple_signal_base(assign.rhs, "signal_affinity_propagation")  # assign 布局来源端信号名

        # 只有 lhs 在目标区域且 rhs 已有实例布局时才传播。
        if (
            str_lhs_name is None
            or str_rhs_name is None
            or str_lhs_name not in dict_decl_indices
            or str_rhs_name not in seed_layouts
            or str_lhs_name in dict_propagated
        ):

            # 当前 assign 不满足单源实例布局传播条件。
            return None

        # instance_signal_layout_seed 保存 rhs 已登记的实例亲缘来源。
        instance_signal_layout_seed = seed_layouts[str_rhs_name]  # rhs 对应的实例亲缘来源

        # 返回当前 lhs 信号及其复制后的实例布局。
        return (
            str_lhs_name,
            self._copied_instance_signal_layout_for_decl(
                instance_signal_layout_seed,
                dict_decl_indices[str_lhs_name],
            ),
        )

    # _propagated_instance_signal_layout_candidate_from_block 从单目标 always 推导实例亲缘传播。
    def _propagated_instance_signal_layout_candidate_from_block(
        self,
        block: AlwaysBlock,
        dict_decl_indices: dict[str, int],
        seed_layouts: dict[str, InstanceSignalLayout],
        dict_propagated: dict[str, InstanceSignalLayout],
    ) -> tuple[str, InstanceSignalLayout] | None:
        """
        检查单目标 always，推导是否能为 target 产生新的实例亲缘布局。

        :param block: 当前待分析的 always 块。
        :param dict_decl_indices: 目标区域声明顺序索引。
        :param seed_layouts: 已知实例信号布局索引。
        :param dict_propagated: 当前轮已经推导出的布局索引。
        :return: `(<target signal>, <layout>)`；无法推导时返回 None。
        """

        # 多目标 always 无法判断哪个 target 继承实例亲缘。
        if len(block.targets) != 1:

            # 目标不唯一时避免传播错误区域。
            return None

        # str_target 指向这个过程块里唯一可能承接实例亲缘的写目标。
        str_target = block.targets[0]  # 接收实例亲缘传播的 always 目标

        # 只处理目标区域且尚未从 assign 推导过的信号。
        if str_target not in dict_decl_indices or str_target in dict_propagated:

            # 跳过跨区域目标或本轮已推导过的目标。
            return None

        # 先把过程块文本拼完整，后面的实例亲缘扫描只认这一份线性化视图。
        str_block_text = "\n".join([block.header, *block.lines])  # 供实例亲缘扫描消费的线性化过程块文本

        # list_affinity_names 收集这个过程块里命中的实例亲缘来源信号。
        list_affinity_names = self._extract_affinity_signal_names_from_text(  # always 命中的实例亲缘来源
            str_block_text, seed_layouts, exclude={str_target},  # 文本、亲缘索引与排除目标
        )

        # 没有实例布局引用时不能推导。
        if not list_affinity_names:

            # 当前 always 中没有可继承的实例亲缘引用。
            return None

        # instance_signal_layout_seed 只有在引用来源收敛到同一实例亲缘时才会成立。
        instance_signal_layout_seed = self._instance_affinity_seed_from_ref_names(  # 当前 always 选中的实例亲缘
            list_affinity_names, seed_layouts,  # 命中的亲缘来源信号与已知亲缘索引
        )

        # 只要实例亲缘出现分叉，就不能继续把它复制给当前 target。
        if instance_signal_layout_seed is None:

            # 当前 always 引用了多个不同实例亲缘。
            return None

        # 这里返回的是 target 及其复制出的实例布局，调用方会按目标区域顺序把它登记回结果表。
        return (
            str_target,
            self._copied_instance_signal_layout_for_decl(
                instance_signal_layout_seed,
                dict_decl_indices[str_target],
            ),
        )

    # _collect_propagated_signal_layouts 沿 assign/always 传播实例信号布局亲缘。
    def _collect_propagated_signal_layouts(
        self,
        decls: list[SignalDecl] | GroupedDeclMap,
        assigns: list[AssignStmt],
        always_blocks: list[AlwaysBlock] | GroupedAlwaysMap,
        seed_layouts: dict[str, InstanceSignalLayout],
        output_signal_names: set[str],
        *,
        # target_region 限定哪些声明可以接收实例亲缘传播。
        target_region: str,
    ) -> dict[str, InstanceSignalLayout]:
        """
        在指定声明区域内传播实例信号布局亲缘。

        :param decls: 模块内部信号声明列表。
        :param assigns: 模块连续赋值列表。
        :param always_blocks: 模块 always 块列表。
        :param seed_layouts: 已知实例信号布局索引。
        :param output_signal_names: output 内部信号名集合。
        :param target_region: 允许接收传播布局的声明区域。
        :return: 目标区域内新推导出的实例信号布局索引。
        """

        # 没有实例布局种子时无法向同区域信号传播。
        if not seed_layouts:

            # 缺少亲缘来源时传播结果为空。
            return {}

        # list_decl_items 统一展开声明输入，兼容 renderer 回流的声明分组。
        list_decl_items = self._flatten_grouped_decls(decls)  # 实例布局传播使用的线性声明序列

        # list_always_items 用于单目标 always 的二次实例布局扩散。
        list_always_items = self._flatten_grouped_always(always_blocks)  # 实例亲缘传播使用的过程块观察序列

        # dict_decl_indices 只登记目标区域内的普通声明，供传播目标筛选。
        dict_decl_indices = self._signal_layout_decl_indices_for_region(  # 目标区域可接收实例亲缘的声明顺序表
            list_decl_items, output_signal_names, target_region,  # 声明序列、输出集合与目标区域
        )

        # 没有目标区域声明时无需传播。
        if not dict_decl_indices:

            # 当前区域没有可接收传播的声明。
            return {}

        # dict_propagated 保存从实例信号推导出的同区域布局。
        dict_propagated: dict[str, InstanceSignalLayout] = {}  # 新传播出的实例信号布局

        # 连续赋值支持 rhs 实例信号向 lhs 目标传播布局。
        for assign in assigns:

            # tuple_layout_candidate 保存当前 assign 推导出的实例布局。
            tuple_layout_candidate = self._propagated_instance_signal_layout_candidate_from_assign(  # 当前 assign 的实例布局候选
                assign, dict_decl_indices, seed_layouts, dict_propagated,  # 赋值语句、声明索引、亲缘索引与结果表
            )

            # 当前 assign 不能产生实例布局时跳过。
            if tuple_layout_candidate is None:

                # 没有抽出单一实例亲缘来源的 assign 不会继续复制布局。
                continue

            # 先把 assign 推导出的目标名和实例亲缘拆开，方便更新结果表。
            str_target_name, propagated_layout = tuple_layout_candidate  # assign 路径得到的目标名和实例布局

            # 把 assign 复制出的实例亲缘写入本轮结果，供后续过程块继续引用。
            dict_propagated[str_target_name] = propagated_layout  # assign 推导出的实例布局

        # always 单目标块可以从内部引用的实例信号传播布局。
        for block in list_always_items:

            # 先看这个 always 有没有产出可登记的实例亲缘候选。
            tuple_layout_candidate = self._propagated_instance_signal_layout_candidate_from_block(  # 过程块扩散出的实例亲缘候选
                block, dict_decl_indices, seed_layouts, dict_propagated,  # 过程块、声明索引、亲缘索引与结果表
            )

            # 候选为空说明这条过程块没有形成可登记的实例亲缘复制结果。
            if tuple_layout_candidate is None:

                # 一旦来源不再唯一，这个过程块就在这里终止实例亲缘复制，避免把不同实例的亲缘混到一起。
                continue

            # 拆包后同时拿到目标名和复制出来的实例亲缘，后面就能直接写回结果表。
            str_target_name, propagated_layout = tuple_layout_candidate  # 过程块抽出的目标名与实例亲缘

            # 把这个过程块复制出的实例亲缘写回结果表，保证同一区域里的传播顺序仍然可重现。
            dict_propagated[str_target_name] = propagated_layout  # 过程块复制出的实例亲缘结果

        # 返回目标区域内从实例信号传播出的布局。
        return dict_propagated

    # _record_output_signal_layout 记录 output 内部信号最靠前的端口布局。
    def _record_output_signal_layout(
        self,
        layouts: dict[str, OutputSignalLayout],
        signal_name: str,
        layout: OutputSignalLayout,
    ) -> None:
        """
        以最小端口顺序为准记录 output 相关信号布局。

        :param layouts: 待更新的 output 信号布局索引。
        :param signal_name: 当前 output 内部信号名。
        :param layout: 从端口或 bridge 推导出的布局。
        :return: 本函数原地更新 layouts，不返回业务值。
        """

        # output_signal_layout_existing 保存同名信号已记录的布局。
        output_signal_layout_existing_source: OutputSignalLayout | None = cast(  # 当前信号已登记的最早端口亲缘
            OutputSignalLayout | None,  # 明确已有 output 布局可为空
            layouts.get(signal_name),  # 准备更新布局的 output 内部信号名
        )

        # 只有更靠前的端口亲缘才能覆盖已有记录。
        if (
            output_signal_layout_existing_source is None
            or layout.port_rank < output_signal_layout_existing_source.port_rank
        ):

            # 记录当前信号最优的 output 布局来源。
            layouts[signal_name] = layout  # 当前信号最靠前的端口布局

    # _sort_output_internal_decls 按 output 端口亲缘排序内部信号声明。
    def _sort_output_internal_decls(
        self,
        decls: list[SignalDecl],
        output_signal_layouts: dict[str, OutputSignalLayout],
    ) -> list[SignalDecl]:
        """
        对 output_internal 区域声明执行稳定排序。

        :param decls: output_internal 区域内的声明列表。
        :param output_signal_layouts: output 内部信号布局索引。
        :return: 按端口亲缘和原始顺序排序后的声明列表。
        """

        # 空区域直接返回新列表，避免后续排序创建哨兵布局。
        if not decls:

            # 没有声明时保持空 output_internal 区域。
            return []

        # int_fallback_rank 放在所有已知 output 布局之后。
        int_fallback_rank: int = len(output_signal_layouts) + len(decls) + 1  # 未知布局的兜底 rank

        # list_ranked_decls 逐项计算 rank，避免排序 key 里创建复杂哨兵。
        list_ranked_decls: list[tuple[int, int, SignalDecl]] = []  # output_internal 排序元组列表

        # 每个声明先转换为显式 rank 元组。
        for index, decl in enumerate(decls):

            # output_signal_layout_item 为空时使用兜底 rank。
            output_signal_layout_sort_source: OutputSignalLayout | None = cast(  # 排序用 output 端口亲缘
                OutputSignalLayout | None,  # 明确排序布局查找结果可为空
                output_signal_layouts.get(decl.name),  # 正在排序的 output_internal 声明名
            )

            # int_port_rank 是 output_internal 排序的第一关键字。
            int_port_rank: int = (  # 当前声明的 output 亲缘 rank
                output_signal_layout_sort_source.port_rank  # 已知 output 布局的端口顺序
                if output_signal_layout_sort_source  # 声明具备 output 亲缘布局
                else int_fallback_rank  # 未知布局声明排在已知布局之后
            )

            # 追加 rank、原索引和声明，确保排序稳定。
            list_ranked_decls.append((int_port_rank, index, decl))

        # 按端口亲缘和原始顺序排序。
        list_ranked_decls.sort(key=lambda item: (item[0], item[1]))

        # 返回排序后的声明对象列表。
        return [decl for _, _, decl in list_ranked_decls]

    # _sort_instance_signal_decls 按实例端口亲缘排序实例连接声明。
    def _sort_instance_signal_decls(
        self,
        decls: list[SignalDecl],
        instance_signal_layouts: dict[str, InstanceSignalLayout],
    ) -> list[SignalDecl]:
        """
        对 instance_signal 区域声明执行稳定排序。

        :param decls: instance_signal 区域内的声明列表。
        :param instance_signal_layouts: 实例信号布局索引。
        :return: 按实例、端口关联和原始顺序排序后的声明列表。
        """

        # 空 instance_signal 区域无需构造兜底排序键。
        if not decls:

            # 返回新空列表，避免调用方误改原输入。
            return []

        # int_fallback_rank 让未知实例亲缘的信号排在已知布局之后。
        int_fallback_rank: int = len(instance_signal_layouts) + len(decls) + 1  # 未知实例亲缘的兜底排序值

        # list_ranked_decls 显式保存排序键，避免在 sort key 中反复查布局。
        list_ranked_decls: list[tuple[int, int, int, int, SignalDecl]] = []  # 实例亲缘排序键与声明

        # 每个声明转换为显式排序元组。
        for index, decl in enumerate(decls):

            # instance_signal_layout_item 为空时后续使用兜底排序值。
            instance_signal_layout_sort_source: InstanceSignalLayout | None = cast(  # 当前声明的实例排序来源
                InstanceSignalLayout | None,  # 明确实例排序布局查找结果可为空
                instance_signal_layouts.get(decl.name),  # 查询实例亲缘的声明名
            )

            # int_instance_rank 是实例出现顺序排序键。
            int_instance_rank: int = (  # 当前声明所属实例 rank
                instance_signal_layout_sort_source.instance_rank  # 已知实例布局的实例顺序
                if instance_signal_layout_sort_source  # 声明具备实例亲缘布局
                else int_fallback_rank  # 未知实例布局排在已知布局之后
            )

            # int_association_rank 是实例端口连接顺序排序键。
            int_association_rank: int = (  # 当前声明所属端口连接 rank
                instance_signal_layout_sort_source.association_rank  # 已知实例布局的端口连接顺序
                if instance_signal_layout_sort_source  # 声明具备实例连接布局
                else int_fallback_rank  # 未知布局使用兜底连接顺序
            )

            # int_decl_rank 为空布局时回落到原始声明顺序。
            int_decl_rank: int = (  # 声明在实例信号排序中的稳定 rank
                instance_signal_layout_sort_source.decl_index  # 实例布局保存的原声明顺序
                if instance_signal_layout_sort_source  # 声明已有实例布局
                else index  # 未知布局沿用当前区域顺序
            )

            # 追加完整排序键，最后一列保留声明对象。
            list_ranked_decls.append(  # 追加实例信号排序元组
                (int_instance_rank, int_association_rank, int_decl_rank, index, decl)  # 完整排序键和声明对象
            )

        # 按实例、端口连接和声明顺序排序。
        list_ranked_decls.sort(key=lambda item: (item[0], item[1], item[2], item[3]))

        # 返回按实例亲缘排列的声明。
        return [decl for _, _, _, _, decl in list_ranked_decls]

    # _always_group_region_and_kind 为单个 always 块解析结构化渲染区域与 block_kind。
    def _always_group_region_and_kind(
        self,
        block: AlwaysBlock,
        output_targets: set[str],
    ) -> tuple[str, str]:
        """
        基于 always 目标和状态引用关系，解析该块所属的渲染区域与 block_kind。

        :param block: 当前待分类的 always 块。
        :param output_targets: output 端口和 output 内部信号目标集合。
        :return: `(<group name>, <block kind>)`。
        """

        # 先把目标列表折成集合，后面的区域判定只关心“写到了哪些信号”，不关心重复次数。
        set_targets = set(block.targets)  # 当前 always 写入目标集合

        # bool_writes_state 记录该块是否写入状态信号。
        bool_writes_state = any(  # 当前 always 是否写入状态信号
            target.startswith(self.config["naming"]["state_signal_prefix"])  # 逐个检查目标名是否带状态前缀
            for target in set_targets  # 本块真实写出的目标信号集合
        )

        # 输出目标优先进入 output_always 区域。
        if set_targets & output_targets:

            # 直接驱动输出的过程块靠近接口桥接逻辑渲染。
            return ("output_always", "output_always")

        # 写状态信号的组合或时序 always 统一进入 state_machine。
        if bool_writes_state:

            # 组合 next-state 和顺序状态寄存共享同一渲染区域。
            return ("state_machine", "state_transition_always")

        # 不写状态但引用状态的任务逻辑单独靠近状态机输出。
        if block.references_state:

            # 状态依赖任务逻辑进入 state_task 区域。
            return ("state_task", "state_task_always")

        # 其余 always 归入主任务区域。
        return ("main_task", "main_task_always")

    # _group_always 按输出、状态机和主任务语义分组 always 块。
    def _group_always(
        self,
        always_blocks: list[AlwaysBlock] | GroupedAlwaysMap,
        output_targets: set[str],
    ) -> dict[str, list[AlwaysBlock]]:
        """
        将 always 块分配到 formatter 的结构化渲染区域。

        :param always_blocks: 已分析出的 always 块列表。
        :param output_targets: output 端口和 output 内部信号目标集合。
        :return: 区域名到 always 块列表的分组字典。
        """

        # grouped always 已经完成区域分类时，只需复制以避免共享可变列表。
        if isinstance(always_blocks, dict):

            # 返回保持区域顺序的 always 分组副本。
            return self._copy_grouped_always(always_blocks)

        # dict_groups 预创建渲染区域，保持后续输出顺序稳定。
        dict_groups: dict[str, list[AlwaysBlock]] = {  # always 渲染区域分组表
            "output_always": [],  # 输出寄存或组合逻辑 always
            "state_machine": [],  # 状态寄存和状态转移 always
            "state_task": [],  # 引用状态但不写状态的任务逻辑
            "main_task": [],  # 普通主任务 always
        }

        # 逐块分类并写回 block_kind，供渲染阶段复用。
        for block in always_blocks:

            # str_group_name 和 str_block_kind 保存当前 always 的归类结果。
            str_group_name, str_block_kind = self._always_group_region_and_kind(block, output_targets)  # 当前 always 的区域和 block_kind

            # block_kind 会被后续 renderer 直接复用。
            block.block_kind = str_block_kind  # 当前 always 的渲染分类

            # always 块按解析出的结构区域写回对应分组。
            dict_groups[str_group_name].append(block)  # 当前 always 加入目标渲染区域

        # 返回按区域聚合后的 always 分组。
        return dict_groups

    # _copy_grouped_always 复制已分组的 always 映射，并补齐缺失区域。
    def _copy_grouped_always(self, grouped_always: GroupedAlwaysMap) -> GroupedAlwaysMap:
        """
        复制已分组的 always 映射，并保持 renderer 既定区域顺序。

        :param grouped_always: 已按区域聚合的 always 映射。
        :return: 复制后的 always 分组字典。
        """

        # tuple_region_order 固定 copy helper 采用的已知 always 区域顺序。
        tuple_region_order = ("output_always", "state_machine", "state_task", "main_task")  # copy helper 的已知 always 顺序

        # grouped_always_map_copy 先复制已知区域，保持顺序稳定。
        grouped_always_map_copy: GroupedAlwaysMap = {  # always 分组副本
            region: list(grouped_always.get(region, []))  # 当前区域的过程块列表副本
            for region in tuple_region_order  # 既定 always 区域顺序
        }

        # 未知区域仍要原样保留，避免未来扩展键被静默丢弃。
        for region, list_items in grouped_always.items():

            # 已按稳定顺序复制的区域不需要重复写回。
            if region in grouped_always_map_copy:

                # 既定区域在预建骨架时已经写入，这里不应覆盖其原有顺序。
                continue

            # 未知区域补到副本尾部，并复制当前区域的过程块列表。
            grouped_always_map_copy[region] = list(list_items)  # 新出现区域的过程块列表副本

        # 返回不会与调用方共享列表对象的 always 分组副本。
        return grouped_always_map_copy

    # _flatten_grouped_always 把过程块列表或已分组映射统一还原成线性序列。
    def _flatten_grouped_always(self, always_blocks: list[AlwaysBlock] | GroupedAlwaysMap) -> list[AlwaysBlock]:
        """
        把原始 always 列表或已分组映射统一展开成过程块序列。

        :param always_blocks: 原始过程块列表或已分组映射。
        :return: 供分析 helper 继续扫描的线性 always 列表。
        """

        # 原始列表路径只复制容器，避免外部列表被共享修改。
        if not isinstance(always_blocks, dict):

            # 保持调用方当前过程块顺序。
            return list(always_blocks)

        # tuple_region_order 在 flatten 路径中定义既知 always 区域的展开顺序。
        tuple_region_order = ("output_always", "state_machine", "state_task", "main_task")  # flatten helper 的已知 always 展开顺序

        # list_flattened_blocks 按稳定区域顺序收集过程块。
        list_flattened_blocks: list[AlwaysBlock] = []  # 展平后的 always 序列

        # set_seen_regions 记录已经展开过的区域键。
        set_seen_regions: set[str] = set()  # 已按稳定顺序消费的 always 区域

        # 先按既定区域顺序展开已知区域。
        for region in tuple_region_order:

            # 当前区域保持已有分组内部顺序。
            list_flattened_blocks.extend(always_blocks.get(region, []))

            # 标记该区域已完成展开。
            set_seen_regions.add(region)

        # 再补未知区域，避免未来新增键丢失。
        for region, list_items in always_blocks.items():

            # 已按稳定顺序处理过的区域不再重复展开。
            if region in set_seen_regions:

                # 当前区域已经包含在结果内。
                continue

            # 未登记的新区域按输入顺序补到线性尾部，保留调用方扩展键的相对位置。
            list_flattened_blocks.extend(list_items)

        # 返回统一的线性过程块序列。
        return list_flattened_blocks

    # _validate_state_machine_blocks 校验状态机分组是否同时包含组合和顺序部分。
    def _validate_state_machine_blocks(
        self,
        local_params: list[ParamDecl],
        grouped_always: dict[str, list[AlwaysBlock]],
        all_blocks: list[AlwaysBlock],
    ) -> None:
        """
        校验检测到状态信号时是否存在稳定的状态机结构。

        :param local_params: 模块 localparam 列表。
        :param grouped_always: 已分组的 always 块索引。
        :param all_blocks: 模块内全部 always 块。
        :return: 校验通过时不返回业务值。
        :raises VerilogFormatterError: 状态机结构不满足 strict 约束时抛出。
        """

        # 示例兼容模式跳过新增状态机结构门禁。
        if self._example_compat_enabled():

            # 历史 fixture 保持旧行为。
            return

        # bool_has_state_params 表示模块定义了状态枚举参数。
        bool_has_state_params: bool = any(self._is_state_param(param.name) for param in local_params)  # 状态参数存在标记

        # bool_has_state_signals 表示 always 写入了状态前缀信号。
        bool_has_state_signals: bool = any(  # 状态信号写入存在标记
            target.startswith(self.config["naming"]["state_signal_prefix"])  # 写入目标命中状态信号前缀
            for block in all_blocks  # 扫描模块内全部 always
            for target in block.targets  # 检查每个 always 写入目标
        )

        # 没有状态痕迹时不执行 FSM 成对校验。
        if not (bool_has_state_params or bool_has_state_signals):

            # 普通模块无需状态机结构约束。
            return

        # bool_has_comb_state 标记状态机分组内是否有组合转移逻辑。
        bool_has_comb_state: bool = any(block.is_combinational for block in grouped_always["state_machine"])  # 组合状态逻辑标记

        # bool_has_seq_state 标记状态机分组内是否有顺序状态寄存逻辑。
        bool_has_seq_state: bool = any(not block.is_combinational for block in grouped_always["state_machine"])  # 顺序状态逻辑标记

        # 写状态信号时必须同时具备组合和顺序状态逻辑。
        if bool_has_state_signals and not (bool_has_comb_state and bool_has_seq_state):

            # verilog_formatter_error_state_machine 保存 FSM 结构违规诊断。
            verilog_formatter_error_state_machine: VerilogFormatterError = self._strict_error(  # 状态机结构异常
                "state_machine_violation",  # 状态信号缺少组合/顺序成对结构的分类
                "state-like signals detected but no stable combinational and sequential state-machine pair was found",  # 不完整 FSM 摘要
                "Restructure the FSM into next-state combinational logic plus a registered state update block.",  # FSM 拆分建议
            )

            # strict 模式拒绝不完整的状态机结构。
            raise VerilogFormatterError(
                f"> ERR: [Python] State-machine structure is incomplete: {verilog_formatter_error_state_machine}"
            )

    # _build_output_assigns 为 output 内部化生成 bridge assign。
    def _build_output_assigns(self, ports: list[PortDecl], output_internal_names: dict[str, str]) -> list[AssignStmt]:
        """
        根据 output 端口到内部信号映射构建连续赋值桥接。

        :param ports: 模块端口声明列表。
        :param output_internal_names: output 端口到内部信号名映射。
        :return: formatter 合成的 output bridge assign 列表。
        """

        # list_assigns 收集由 formatter 自动生成的 output bridge。
        list_assigns: list[AssignStmt] = []  # formatter 合成的端口到内部信号 assign

        # 按端口声明顺序生成 assign，保持渲染稳定。
        for port in ports:

            # 只有需要内部化的 output 端口才生成 bridge。
            if port.direction == "output" and port.name in output_internal_names:

                # 追加从端口到内部信号的连续赋值。
                list_assigns.append(
                    AssignStmt(port.name, output_internal_names[port.name], "output bridge")
                )

        # 返回合成的 output bridge assign 列表。
        return list_assigns
