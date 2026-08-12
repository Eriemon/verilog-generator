"""为 VerilogFormatterEngine 提供文件头、前置指令和修订历史辅助。"""

# future annotations 让 mixin 间的前向类型提示继续延迟求值。
from __future__ import annotations

# 标准库负责环境变量、正则、日期和 include 路径解析。
import os
import re
from datetime import datetime
from pathlib import Path

# banner 工具仅用于识别需要避开的装饰性横幅注释。
from .banners import is_banner_line

# formatter 模型只保留文件头元数据、原样保留块结构和统一异常类型。
from .models import HeaderMetadata, RawBlock, VerilogFormatterError

# HeaderMixin 负责文件头解析、前导预处理指令抽取和双语头部渲染。
class HeaderMixin:
    """维护 Verilog 文件头、前导编译指令和修订历史的解析与渲染逻辑。"""

    # 定位源码中的第一个 module 声明，供多个头部辅助函数复用。
    def _find_module_declaration(self, source: str) -> re.Match[str] | None:
        """
        查找源码中的第一个 module 声明。

        参数:
            source: 待扫描的 Verilog 源文本。
        返回:
            re.Match[str] | None: 命中的 module 起始匹配；没有命中时返回 None。
        """

        # 复用统一的 module 边界模式，避免各处写出不一致的查找规则。
        match_module = re.search(r"(?m)^\s*module\b", source)  # 首个 module 声明匹配结果

        # 把匹配结果直接交回调用方，后续由上层决定是否报错。
        return match_module

    # 解析 formatter 头部时间戳时优先尊重配置和环境变量覆盖。
    def _resolve_header_now(self) -> datetime:
        """
        解析本轮文件头渲染应使用的当前时间。

        参数:
            无外部业务参数。
        返回:
            datetime: 文件头渲染使用的当前时间。
        """

        # 读取 header 配置段，后续 fixed_now 与双语字段都从这里取值。
        dict_header_config = self.config.get("header", {})  # formatter 头部配置段

        # 先准备一个空字符串，便于依次叠加配置值和环境变量值。
        str_fixed_now = ""  # 头部固定时间文本

        # 只有 header 配置确实是字典时，才读取固定时间字段。
        if isinstance(dict_header_config, dict):

            # 配置中的 fixed_now 优先级最高，显式覆盖运行时当前时间。
            str_fixed_now = str(dict_header_config.get("fixed_now", "")).strip()  # 配置固定时间文本

        # 配置未提供固定时间时，再回退到环境变量输入。
        if not str_fixed_now:

            # 环境变量让 smoke、测试和基线生成保持可重复输出。
            str_fixed_now = os.environ.get("VERILOG_FORMATTER_FIXED_NOW", "").strip()  # 环境固定时间文本

        # 命中固定时间文本时，尝试按支持的日期格式解析。
        if str_fixed_now:

            # 先按文件头允许的日期样式解析字符串。
            datetime_fixed_now_value: datetime | None = self._parse_header_datetime(str_fixed_now)  # 固定时间解析结果

            # 固定时间可解析时，直接复用该时间作为本轮头部时间。
            if datetime_fixed_now_value is not None:

                # 配置或环境变量成功提供可复现时间时优先返回该值。
                return datetime_fixed_now_value

        # 固定时间缺失或格式不合法时，回退到系统当前时间。
        return datetime.now()

    # 从源码中切掉旧文件头，只保留从 module 开始的结构化正文。
    def _strip_existing_header(self, source: str) -> str:
        """
        去除源码中 module 声明之前的旧文件头内容。

        参数:
            source: 原始 Verilog 源文本。
        返回:
            str: 从第一个 module 声明开始的源码正文。
        异常:
            VerilogFormatterError: 找不到 module 声明时抛出严格 formatter 错误。
        """

        # 先定位 module 边界，只有找到主体后才能安全剥离文件头。
        match_module = self._find_module_declaration(source)  # module 声明边界匹配结果

        # 缺少 module 声明时，formatter 无法判断头部和正文的分界。
        if not match_module:

            # 抛出严格错误，提醒调用方先提供可综合的单模块源码。
            raise VerilogFormatterError(
                "> ERR: [Python] header parsing requires a module declaration."
            ) from self._strict_error(
                "unsupported_shape",
                "source does not contain a module declaration",
                "Provide a single synthesizable module declaration before running the formatter.",
            )

        # 保留从 module 开始的正文，前面的头部区域由 header 渲染逻辑重建。
        return source[match_module.start() :]

    # 同时提取旧文件头元数据和去头后的 module 正文，供 renderer 重建双语头部。
    def _extract_header_metadata_and_source(self, source: str) -> tuple[HeaderMetadata | None, str]:
        """
        提取旧文件头元数据并返回清理后的源码正文。

        参数:
            source: 原始 Verilog 源文本。
        返回:
            tuple[HeaderMetadata | None, str]: 已解析的头部元数据和去头后的 module 正文。
        """

        # 先统一移除旧文件头，后续正文重排只基于 module 主体进行。
        str_clean_source = self._strip_existing_header(source)  # 去除旧文件头后的源码正文

        # 再定位原始源码中的 module 边界，用于回切出头部注释区。
        match_module = self._find_module_declaration(source)  # 原始源码中的 module 声明边界

        # 缺少 module 时，前一步已抛错；这里保留兜底分支防止未来调用路径变化。
        if not match_module:

            # 无法再提取头部元数据时，仅回传已清理的正文。
            return None, str_clean_source

        # 截取 module 之前的文本片段，作为旧文件头候选区域。
        str_preamble_text = source[: match_module.start()].rstrip()  # 原始文件头候选文本

        # 旧源码根本没有 preamble 时，不需要再解析文件头字段。
        if not str_preamble_text:

            # 没有旧文件头时只返回清理后的正文。
            return None, str_clean_source

        # 模块功能中文说明需要在 header 解析前从 preamble 尾部单独拆出。
        tuple_purpose_preamble = self._extract_module_purpose_comment_from_preamble(  # 模块功能说明与纯 header preamble
            str_preamble_text  # module 之前的完整注释区文本
        )

        # str_module_purpose_comment 是需在 include_header=True 路径单独保真的中文说明。
        str_module_purpose_comment = tuple_purpose_preamble[0]  # header 后 module 前的中文功能说明

        # str_header_preamble_text 去掉模块功能说明后，仅保留标准 header 候选区域。
        str_header_preamble_text = tuple_purpose_preamble[1]  # 去掉模块功能说明后的 header 候选文本

        # 把旧头部文本解析成可重建的字段化元数据。
        header_metadata_payload: HeaderMetadata | None = self._parse_header_metadata(str_header_preamble_text)  # 旧文件头字段解析结果

        # 只有模块功能说明也应保留独立元数据容器，避免 include_header=True 时丢失说明。
        if header_metadata_payload is None and str_module_purpose_comment:

            # 缺少标准 header 时也创建最小元数据对象承载模块说明。
            header_metadata_payload = HeaderMetadata(module_purpose_comment=str_module_purpose_comment)  # 承载孤立模块说明的最小元数据对象

        # 成功解析 header 时，把模块功能说明一并挂到文件头元数据上。
        elif header_metadata_payload is not None and str_module_purpose_comment:

            # include_header=True 路径后续直接读取该字段输出中文说明。
            header_metadata_payload.module_purpose_comment = str_module_purpose_comment  # 把模块说明补回解析成功的 header 元数据

        # 把元数据和正文一起交给后续 renderer 使用。
        return header_metadata_payload, str_clean_source

    # 从 preamble 尾部拆出模块功能中文说明，避免被误解析成历史记录。
    def _extract_module_purpose_comment_from_preamble(self, preamble: str) -> tuple[str, str]:
        """
        从 module 前导区尾部提取模块功能中文说明。

        参数:
            preamble: module 声明之前的完整前导文本。
        返回:
            tuple[str, str]: 模块功能说明正文，以及移除该说明后的 header 候选文本。
        """

        # list_preamble_lines 保留原始物理行序，便于只裁掉尾部一行说明。
        list_preamble_lines = preamble.splitlines()  # module 前导区物理行列表

        # 逆序定位最后一条非空白行，候选模块功能说明只可能落在这里。
        int_last_nonempty = len(list_preamble_lines) - 1  # preamble 末尾非空行扫描游标

        # 尾部空白行不携带功能说明语义，先全部跳过。
        while int_last_nonempty >= 0 and not list_preamble_lines[int_last_nonempty].strip():

            # 当前尾部空白行已跳过，继续向上寻找最后一条非空行。
            int_last_nonempty -= 1  # 继续向上寻找最后一条非空白行

        # preamble 全空时无需继续提取模块功能说明。
        if int_last_nonempty < 0:

            # 没有任何可见文本时返回空说明和原始空 preamble。
            return "", preamble.rstrip()

        # 最后一条非空白行只有在符合候选条件时才视为模块功能说明。
        str_candidate_line = list_preamble_lines[int_last_nonempty].strip()  # 尾部候选说明行

        # 不满足候选条件时，不应把历史记录或 header 字段误当成模块说明。
        if not self._is_module_purpose_comment_candidate(str_candidate_line):

            # 当前 preamble 尾部没有可拆出的模块功能说明。
            return "", preamble.rstrip()

        # 说明正文只保留 `//` 后的可见语义文本，后续由 renderer 统一补注释前缀。
        str_comment_body = str_candidate_line[2:].strip()  # 模块功能说明正文

        # header preamble 只移除尾部说明行，其余 header 注释和空白全部原样保留。
        str_header_preamble = "\n".join(list_preamble_lines[:int_last_nonempty]).rstrip()  # 去掉模块说明后的 header 文本

        # 返回模块功能说明正文和纯 header preamble。
        return str_comment_body, str_header_preamble

    # 判断尾部纯注释是否属于模块功能中文说明，而非 header 字段或历史表内容。
    def _is_module_purpose_comment_candidate(self, stripped_line: str) -> bool:
        """
        判断一条尾部纯注释是否可视为模块功能中文说明。

        参数:
            stripped_line: 去掉左右空白后的完整源码行。
        返回:
            bool: True 表示该行应作为模块功能说明独立保留。
        """

        # 模块功能说明必须是普通 `//` 注释，横幅、空行和正文都不符合候选条件。
        if not stripped_line.startswith("//") or is_banner_line(stripped_line):

            # 非普通注释或装饰横幅都不应被当成模块功能说明。
            return False

        # str_comment_body 只保留注释正文，便于检查 header 字段和历史记录特征。
        str_comment_body = stripped_line[2:].strip()  # 候选注释正文

        # 空注释或纯分隔斜杠行都不携带模块功能语义。
        if not str_comment_body or set(str_comment_body) == {"/"}:

            # 当前注释正文没有功能说明价值。
            return False

        # 明确的字段名行一定属于标准 header，而非模块功能说明。
        tuple_header_labels = (  # 标准 header 字段标签集合
            "Company", "Engineer", "Create Date", "Design Name", "Module Name",  # 英文基础身份字段
            "Description", "Simulations", "References", "Dependencies",  # 英文说明与交付字段
            "Version", "Revision Date", "History", "Referrences",  # 英文版本与历史字段
            "版权归属", "开发人员", "创建日期", "设计名称", "模块名称",  # 中文基础身份字段
            "模块说明", "仿真工程", "参考资料", "依赖文件", "当前版本", "修订日期", "修订历史",  # 中文说明与历史字段
        )  # header 字段和段标题全集

        # 任何显式字段标签都属于 header 内部内容。
        if any(str_comment_body.startswith(f"{str_label}:") for str_label in tuple_header_labels):

            # 当前注释是 header 字段或段标题，不能视为模块功能说明。
            return False

        # 历史表头、参考资料表头和依赖表头也都不应被误判成模块功能说明。
        if (
            str_comment_body.lower().startswith(("time", "file format", "module name"))
            or str_comment_body.startswith(("时间", "文件格式", "模块名称"))
        ):

            # 表头文本只属于 header 表格块，不属于模块功能说明。
            return False

        # 类似 2026/05/03 V1.0 Erie Create file. 的历史记录也必须排除。
        if re.match(r"^\d{4}(?:/|年)\d{1,2}", str_comment_body):

            # 修订历史记录位于 header 内部，不属于模块功能说明。
            return False

        # table_mode 参考资料行形如 1.Book xxx，同样不是模块功能说明。
        if re.match(r"^\d+\.(?:Book|Journal|Paper)\b", str_comment_body, re.IGNORECASE):

            # 参考资料数据行只属于 header 表格块。
            return False

        # 剩余的单行普通注释可视为模块功能中文说明候选。
        return True

    # 收集 preamble 中真正可能承载 Vivado 头字段的注释行。
    def _collect_header_comment_lines(self, preamble: str) -> list[str]:
        """
        收集 preamble 中以 `//` 开头的文件头候选注释行。

        参数:
            preamble: module 声明之前的 preamble 文本。
        返回:
            list[str]: 仅包含非空 `//` 注释行的列表。
        """

        # 只保留去掉左右空白后仍以 // 开头的注释行。
        list_comment_lines = [  # 文件头候选注释行列表
            str_line.strip()  # 统一去掉行首尾空白，便于字段匹配
            for str_line in preamble.splitlines()  # 按物理行扫描 preamble
            if str_line.strip().startswith("//")  # 只保留 Vivado 风格注释行
        ]

        # 返回可供字段识别的注释行集合。
        return list_comment_lines

    # 判断注释行中是否至少出现一个可识别的头字段标记。
    def _has_header_markers(self, comment_lines: list[str]) -> bool:
        """
        判断注释行中是否出现可识别的文件头字段标记。

        参数:
            comment_lines: preamble 中收集到的候选注释行。
        返回:
            bool: True 表示这些注释行看起来像标准双语文件头。
        """

        # 汇总中英文文件头里常见的字段名，作为快速识别门槛。
        tuple_english_markers = (  # 英文头字段关键字组
            "Company:",  # 英文公司字段标记
            "Engineer:",  # 英文开发人员字段标记
            "Create Date:",  # 英文创建日期字段标记
            "Design Name:",  # 英文设计名称字段标记
            "Module Name:",  # 英文模块名称字段标记
            "Description:",  # 英文模块说明字段标记
            "Simulations:",  # 英文仿真工程字段标记
            "References:",  # 英文参考资料字段标记
            "Dependencies:",  # 英文依赖文件字段标记
            "Version:",  # 英文版本字段标记
            "History:",  # 英文修订历史字段标记
            "Project Name:",  # 英文工程名称字段标记
            "Target Devices:",  # 英文目标器件字段标记
            "Tool Versions:",  # 英文工具版本字段标记
            "Additional Comments:",  # 英文补充说明字段标记
            "Revision:",  # 英文 Revision 字段标记
        )

        # 中文模板字段单独分组，避免英文和中文标记混在一个长元组里。
        tuple_chinese_markers = (  # 中文头字段关键字组
            "版权归属:",  # 中文版权归属字段标记
            "开发人员:",  # 中文开发人员字段标记
            "创建日期:",  # 中文创建日期字段标记
            "设计名称:",  # 中文设计名称字段标记
            "模块名称:",  # 中文模块名称字段标记
            "模块说明:",  # 中文模块说明字段标记
            "仿真工程:",  # 中文仿真工程字段标记
            "参考资料:",  # 中文参考资料字段标记
            "依赖文件:",  # 中文依赖文件字段标记
            "当前版本:",  # 中文版本字段标记
            "修订历史:",  # 中文修订历史字段标记
        )

        # 英中文字段集合会被统一拼成最终的快速识别标记池。
        tuple_header_markers = (*tuple_english_markers, *tuple_chinese_markers)  # 文件头快速识别标记池

        # 只要任意注释行命中任意字段，就认为它是可解析文件头。
        return any(
            any(str_marker in str_line for str_marker in tuple_header_markers)
            for str_line in comment_lines
        )

    # 为不同语言的修订历史选择对应的落点列表。
    def _select_history_line_bucket(self, metadata: HeaderMetadata, language: str) -> list[str]:
        """
        根据当前语言上下文选择修订历史的目标列表。

        参数:
            metadata: 当前累计的文件头元数据对象。
            language: 当前修订历史所处的语言段标记。
        返回:
            list[str]: 对应语言的修订历史落点列表引用。
        """

        # 默认把没有语言标签的修订历史写入通用 history_lines。
        list_target_lines = metadata.history_lines  # 默认修订历史落点

        # 英文修订区需要落到独立的英文历史列表。
        if language == "en":

            # 英文文件头区域使用专门的 history_lines_en 容器。
            list_target_lines = metadata.history_lines_en  # 英文修订历史落点

        # 中文修订区需要落到独立的中文历史列表。
        elif language == "cn":

            # 中文修订轨迹应落到独立的 history_lines_cn 容器里。
            list_target_lines = metadata.history_lines_cn  # 中文修订历史落点

        # 把选中的历史行列表交给调用方追加内容。
        return list_target_lines

    # 把头部字段名统一映射到 HeaderMetadata 的单值属性。
    def _resolve_scalar_header_field_name(self, normalized_key: str) -> str:
        """
        把双语头字段名映射到 HeaderMetadata 的单值属性名。

        参数:
            normalized_key: 已归一化的头字段名。
        返回:
            str: 命中时返回对应属性名；未命中时返回空字符串。
        """

        # 公司归属字段无论中英文都落到 company 属性。
        if normalized_key in {"company", "版权归属"}:

            # 公司字段统一写入 HeaderMetadata.company。
            return "company"

        # 开发人员字段沿用 engineer 作为统一属性名。
        if normalized_key in {"engineer", "开发人员"}:

            # 开发人员字段统一写入 HeaderMetadata.engineer。
            return "engineer"

        # 创建日期字段需要共用 create_date 存储位。
        if normalized_key in {"create date", "创建日期"}:

            # 创建日期统一写入 HeaderMetadata.create_date。
            return "create_date"

        # 修订日期字段无论语言都落到 revision_date。
        if normalized_key in {"revision date", "修订日期"}:

            # 修订日期统一写入 HeaderMetadata.revision_date。
            return "revision_date"

        # 设计名称字段描述的是同一设计对象标识。
        if normalized_key in {"design name", "设计名称"}:

            # 设计名称统一写入 HeaderMetadata.design_name。
            return "design_name"

        # 模块名称字段需要落到 module_name，便于后续回填。
        if normalized_key in {"module name", "模块名称"}:

            # 模块名称统一写入 HeaderMetadata.module_name。
            return "module_name"

        # 模块说明字段统一描述设计文档或摘要路径。
        if normalized_key in {"description", "模块说明"}:

            # 模块说明统一写入 HeaderMetadata.description。
            return "description"

        # 仿真工程字段统一保存在 simulations。
        if normalized_key in {"simulations", "仿真工程"}:

            # 仿真工程统一写入 HeaderMetadata.simulations。
            return "simulations"

        # 当前版本字段在双语头里都映射到 version。
        if normalized_key in {"version", "当前版本"}:

            # 当前版本统一写入 HeaderMetadata.version。
            return "version"

        # 其余字段交给更高层的段落路由逻辑继续判断。
        return ""

    # 处理文件头中的中英文单值字段，并在需要时做版本号规范化。
    def _apply_scalar_header_value(
        self,
        metadata: HeaderMetadata,
        normalized_key: str,
        value: str,
    ) -> bool:
        """
        写入文件头中的单值字段，并告知调用方是否已处理。

        参数:
            metadata: 当前累计的文件头元数据对象。
            normalized_key: 归一化后的字段名。
            value: 字段右侧的原始文本值。
        返回:
            bool: True 表示该字段已经被识别并完成处理。
        """

        # 先把双语字段名归一到统一属性名，未命中时留给段落路由继续判断。
        str_field_name = self._resolve_scalar_header_field_name(normalized_key)  # 单值字段对应的元数据属性名

        # 预设为未处理状态，只有命中映射时才会转成 True。
        bool_handled = False  # 当前字段是否已被单值逻辑消费

        # 命中单值字段映射时，再决定是否把值写入元数据对象。
        if str_field_name:

            # 默认直接使用原值；版本字段会在下面单独规范化。
            str_value_to_store = value  # 即将写入元数据的字段值

            # 版本号字段需要统一补齐 V 前缀和标准点分格式。
            if normalized_key in {"version", "当前版本"}:

                # 版本文本统一走版本号规范化逻辑。
                str_value_to_store = self._normalize_header_version_text(value)  # 规范化后的版本文本

            # 只有新值非空且元数据尚未写入同字段时，才执行首次赋值。
            if str_value_to_store and not getattr(metadata, str_field_name):

                # 保留首次出现的字段值，避免中英文重复字段互相覆盖。
                setattr(metadata, str_field_name, str_value_to_store)

            # 无论是否真正写入值，只要命中映射就视为该字段已消费。
            bool_handled = True  # 当前字段已被单值写入逻辑消费

        # 把单值字段处理结果返回给上层路由逻辑。
        return bool_handled

    # 处理命中键值形式的文件头字段，并返回新的段落上下文。
    def _route_header_key_value(
        self,
        metadata: HeaderMetadata,
        normalized_key: str,
        value: str,
        current_language: str,
    ) -> str:
        """
        处理键值形式的文件头字段，并更新段落上下文。

        参数:
            metadata: 当前累计的文件头元数据对象。
            normalized_key: 归一化后的字段名。
            value: 字段右侧的文本值。
            current_language: 当前修订历史的语言上下文。
        返回:
            str: 后续自由文本行应归属的段落名。
        """

        # 单值字段优先直接落入元数据属性，不再进入段落路由。
        bool_scalar_handled = self._apply_scalar_header_value(metadata, normalized_key, value)  # 单值字段处理结果

        # 单值字段已经消费时，后续自由文本不应该继续沿用旧段落。
        if bool_scalar_handled:

            # 单值字段处理完成后，后续自由文本不再延续旧段落。
            return ""

        # Dependencies / 依赖文件 开启依赖段落，并在本行就收下首条内容。
        if normalized_key in {"dependencies", "依赖文件"}:

            # 依赖字段之后的自由文本都视为依赖列表内容。
            str_next_section = "dependencies"  # 当前字段切换到依赖段上下文

            # 首行依赖值既可能是 None，也可能已经包含一个实际文件名。
            if value:

                # None 占位也要保留下来，供后续 none_mode/table_mode 归一化判断。
                self._append_unique_header_line(metadata.dependency_lines, value)

            # 依赖字段会把后续自由文本继续留在依赖段中。
            return str_next_section

        # References / 参考资料 段既支持单行 None，也支持 table_mode 多行块。
        if normalized_key in {"referrences", "references", "参考资料"}:

            # 显式值优先保留在兼容入口 references 中，后续 renderer 再归一成两种总模板。
            if value and not metadata.references:

                # 单行 None 或历史摘要文本都先落入兼容入口。
                metadata.references = value  # 兼容入口里的单值参考资料文本

            # References 字段后的自由文本继续视为参考资料段。
            return "references"

        # History / 修订历史 开启历史段落，后续自由文本会按语言落桶。
        if normalized_key in {"history", "修订历史"}:

            # 历史字段之后的自由文本都视为修订历史内容。
            return "history"

        # Revision 行需要即时消费版本历史，同时把后续行继续留在修订段。
        if normalized_key == "revision":

            # Revision 本行带内容时，立即把它解析成版本或历史条目。
            if value:

                # Revision 一行可能同时携带版本号和修订描述。
                self._consume_revision_header_line(metadata, value, current_language)

            # Revision 字段会把后续自由文本继续保留在修订段中。
            return "revision"

        # Additional Comments 只改变上下文，不把内容映射到当前元数据字段。
        if normalized_key == "additional comments":

            # 额外说明段只保留上下文，不写回固定字段。
            return "additional_comments"

        # 这些工具字段不会写回 formatter 自有双语头部，只需清掉旧上下文。
        if normalized_key in {"project name", "target devices", "tool versions"}:

            # 这些工具环境字段不会影响后续自由文本的段落归属。
            return ""

        # 其余未知字段统一回退到无段落状态，保持解析保守。
        return ""

    # 处理没有显式键值前缀的头部自由文本行。
    def _consume_header_free_text(
        self,
        metadata: HeaderMetadata,
        body: str,
        current_section: str,
        current_language: str,
    ) -> None:
        """
        处理头部中的无键自由文本行。

        参数:
            metadata: 当前累计的文件头元数据对象。
            body: 去掉 `//` 前缀后的自由文本内容。
            current_section: 当前自由文本所属的段落名。
            current_language: 当前修订历史的语言上下文。
        返回:
            None: 该辅助函数只更新 metadata，不返回业务值。
        """

        # 参考资料段允许 table_mode 的列头和数据行全部进入 reference_lines。
        if current_section == "references":

            # References 多行块统一去重保留，供后续 renderer/quality gate 归一化。
            self._append_unique_header_line(metadata.reference_lines, body)

        # 依赖段落同样要保留 table_mode 的列头和数据行，而不是提前裁剪。
        elif current_section == "dependencies":

            # Dependencies 段需要原样保留候选数据行，后续才能区分表头、None 占位和真实依赖项。
            self._append_unique_header_line(metadata.dependency_lines, body)

        # 修订历史段会按当前语言上下文分别写入英中历史列表。
        elif current_section == "history":

            # 历史表头中的 Time / 时间 标题不能被误当成真实历史记录。
            bool_is_history_heading = body.lower().startswith("time") or body.startswith("时间")  # 修订历史表头占位行

            # 只有真实历史记录才会追加到对应的历史列表。
            if not bool_is_history_heading:

                # 根据语言上下文选择英文、中文或通用历史容器。
                list_target_lines = self._select_history_line_bucket(metadata, current_language)  # 当前语言的修订历史落点

                # 自由文本历史行保持唯一，避免重复 Revision 内容重复落入。
                self._append_unique_header_line(list_target_lines, body)

        # Revision 段中的自由文本继续按 Revision 语义解析版本和修订内容。
        elif current_section == "revision":

            # Revision 下的每一行都继续复用同一版本历史解析逻辑。
            self._consume_revision_header_line(metadata, body, current_language)

    # 判断解析出的元数据对象是否真的含有可写回的文件头内容。
    def _metadata_has_visible_header_fields(self, metadata: HeaderMetadata) -> bool:
        """
        判断元数据对象中是否至少存在一个可见文件头字段。

        参数:
            metadata: 已累计字段的文件头元数据对象。
        返回:
            bool: True 表示元数据中至少存在一个非空字段或列表。
        """

        # 只要出现任意字段或历史/依赖列表，就说明旧头部值得保留。
        bool_has_identity_fields = any(  # 旧头部是否携带归属和时间类基础字段
            (
                metadata.company,  # 公司字段存在
                metadata.engineer,  # 开发人员字段存在
                metadata.create_date,  # 创建日期字段存在
                metadata.revision_date,  # 修订日期字段存在
            )
        )

        # 设计描述字段单独判断，避免单个 any 里堆叠过多连续元素。
        bool_has_module_identity_fields = any(  # 旧头部是否携带模块身份与说明字段
            (
                metadata.design_name,  # 设计名称字段存在
                metadata.module_name,  # 模块名称字段存在
                metadata.description,  # 模块说明字段存在
            )
        )

        # 版本、参考资料与仿真工程决定头部是否具备完整的交付上下文。
        bool_has_delivery_fields = any(  # 旧头部是否携带交付与版本上下文字段
            (
                metadata.simulations,  # 仿真工程字段存在
                metadata.references,  # 参考资料字段存在
                metadata.version,  # 版本字段存在
            )
        )

        # 只要任意一组单值字段存在，就说明旧头部具备保留价值。
        bool_has_scalar_fields = bool_has_identity_fields or bool_has_module_identity_fields or bool_has_delivery_fields  # 单值字段总体可见性

        # 多行段落字段会额外决定头部是否值得保留。
        bool_has_history_fields = any(  # 旧头部是否携带任何语言的修订历史
            (
                metadata.history_lines,  # 通用修订历史存在
                metadata.history_lines_en,  # 英文修订历史存在
                metadata.history_lines_cn,  # 中文修订历史存在
            )
        )

        # 依赖与额外保留行代表头部里还有无法丢弃的附属信息。
        bool_has_supporting_lines = any(  # 旧头部是否携带依赖或额外保留文本
            (
                metadata.reference_lines,  # 参考资料表格行存在
                metadata.dependency_lines,  # 依赖文件列表存在
                metadata.extra_lines,  # 额外保留行存在
                metadata.module_purpose_comment,  # 模块功能中文说明存在
            )
        )

        # 任意一类多行信息存在时，都需要保留头部元数据对象。
        bool_has_multiline_fields = bool_has_history_fields or bool_has_supporting_lines  # 多行字段总体可见性

        # 只要单值字段或多行字段任一存在，就保留该元数据对象。
        return bool_has_scalar_fields or bool_has_multiline_fields

    # 把 preamble 中的双语头部注释解析成 HeaderMetadata。
    def _parse_header_metadata(self, preamble: str) -> HeaderMetadata | None:
        """
        解析 module 之前的双语文件头注释。

        参数:
            preamble: module 声明之前的 preamble 文本。
        返回:
            HeaderMetadata | None: 解析出的头部元数据；无法识别时返回 None。
        """

        # 先收集真正以 // 开头的候选行，排除空白和正文残留。
        list_comment_lines = self._collect_header_comment_lines(preamble)  # 文件头候选注释行集合

        # 缺少头字段标记时，直接把该 preamble 视为普通注释而不是文件头模板。
        if not self._has_header_markers(list_comment_lines):

            # 不具备标准文件头特征时，不返回结构化元数据。
            return None

        # 为当前 preamble 创建一个新的元数据容器。
        header_metadata_accumulator: HeaderMetadata = HeaderMetadata()  # 累积旧文件头字段的元数据对象

        # 当前段落决定无键自由文本应落到依赖、历史还是 revision 区。
        str_current_section = ""  # 自由文本行的当前段落名

        # 当前语言决定历史行应落到英文、中文还是通用历史列表。
        str_current_language = ""  # 历史行语言上下文

        # 逐行扫描头部注释，把字段和值映射到 HeaderMetadata。
        for str_raw_line in list_comment_lines:

            # 去掉 // 前缀后再判断语言标记、分节和键值对。
            str_body = str_raw_line[2:].strip()  # 当前头部注释的净文本

            # 空白注释行会终止普通段落，但保留依赖/历史一类多行段落。
            if not str_body:

                # 只有多行段落会跨过空白行继续保持上下文。
                if str_current_section not in {"history", "dependencies", "additional_comments"}:

                    # 普通单值字段之后遇到空白行时应回到无段落状态。
                    str_current_section = ""  # 普通字段后的空白行会清掉段落上下文

                # 当前空白行不携带可解析内容，继续扫描下一行。
                continue

            # 头部进入英文段时，后续自由文本历史行应写入英文历史列表。
            if "English" in str_body:

                # English 横幅代表后续历史文字应进入英文修订表。
                str_current_language = "en"  # 英文横幅会切换历史语言上下文

                # 英文半区开始后，上一段依赖或 Revision 上下文不再继续沿用。
                str_current_section = ""  # 英文横幅不会继承上一段段落状态

                # 横幅行本身不需要继续走字段解析。
                continue

            # 扫描到 Chinese 横幅时，说明后续内容进入中文头部半区。
            if "Chinese" in str_body:

                # 命中中文横幅后，后面的自由文本要优先落进中文历史栏位。
                str_current_language = "cn"  # 中文半区会把后续自由文本导向中文历史栏

                # 中文半区从这里重新起步，不能继续沿用英文段残留的段落状态。
                str_current_section = ""  # 中文半区要从头识别字段标题与段落边界

                # 横幅行本身不参与字段写入。
                continue

            # 纯斜杠分隔线只是视觉边界，不应被解析成真实字段。
            if set(str_body) == {"/"}:

                # 碰到纯分隔线时立即清掉段落上下文。
                str_current_section = ""  # 纯分隔线会终止当前段落上下文

                # 分隔线本身不继续进入字段或自由文本处理。
                continue

            # 先尝试把当前行拆成 “字段: 值” 形式。
            str_header_key, str_header_value = self._split_header_key_value(str_body)  # 当前行拆出的字段名和值

            # 命中显式键值格式时，交给字段路由逻辑处理。
            if str_header_key is not None:

                # 字段名需要统一归一化，避免大小写和空白差异影响路由。
                str_normalized_key = self._normalize_header_key(str_header_key)  # 当前头字段的归一化键名

                # 根据字段类型决定写入单值、切换段落或消费 Revision 内容。
                str_current_section = self._route_header_key_value(  # 键值行处理后的段落上下文
                    header_metadata_accumulator,  # 当前累计的头部元数据容器
                    str_normalized_key,  # 归一化后的头字段名
                    str_header_value,  # 当前头字段值文本
                    str_current_language,  # 当前历史段语言上下文
                )

            # 没有显式键值前缀时，把它视为当前段落的自由文本内容。
            else:

                # 依赖、修订历史和 Revision 段都允许继续消费自由文本行。
                self._consume_header_free_text(
                    header_metadata_accumulator,
                    str_body,
                    str_current_section,
                    str_current_language,
                )

        # 没有任何可见字段时，不要把普通注释误识别成文件头元数据。
        if not self._metadata_has_visible_header_fields(header_metadata_accumulator):

            # 当前 preamble 不足以构成可重建的双语头部。
            return None

        # 把解析出的头部元数据交给 renderer 重建新文件头。
        return header_metadata_accumulator

    # 把 “字段: 值” 风格的头部行拆成左右两部分。
    def _split_header_key_value(self, text: str) -> tuple[str | None, str]:
        """
        拆分头部注释中的 “字段: 值” 结构。

        参数:
            text: 去掉 `//` 前缀后的头部注释净文本。
        返回:
            tuple[str | None, str]: 字段名和字段值；没有冒号时字段名返回 None。
        """

        # 不含冒号的自由文本不应被误识别成键值字段。
        if ":" not in text:

            # 用 None 标记当前行应回退到段落自由文本处理逻辑。
            return None, ""

        # 只按首个冒号切分，保留描述文本中后续冒号的原始语义。
        str_key_text, str_value_text = text.split(":", 1)  # 头字段左右两部分原始文本

        # 返回去掉多余空白后的字段名和值。
        return str_key_text.strip(), str_value_text.strip()

    # 对头字段名做统一小写与空白折叠，便于中英文同义字段归类。
    def _normalize_header_key(self, text: str) -> str:
        """
        归一化头字段名，消除大小写与空白差异。

        参数:
            text: 原始字段名文本。
        返回:
            str: 归一化后的字段名。
        """

        # 先做首尾空白裁剪和小写转换。
        str_normalized_key = text.strip().lower()  # 初步归一化后的字段名

        # 旧头部里可能混入制表符，先统一替换成普通空格。
        str_normalized_key = str_normalized_key.replace("\t", " ")  # 统一空白后的字段名

        # 再把连续空白折叠成单个空格，避免多空格造成字段名不匹配。
        str_normalized_key = re.sub(r"\s+", " ", str_normalized_key)  # 最终归一化字段名

        # 返回适合做路由判断的字段名。
        return str_normalized_key

    # 追加依赖或修订历史行时保持唯一性，避免双语段与 Revision 重复灌入。
    def _append_unique_header_line(self, lines: list[str], line: str) -> None:
        """
        向头部多行字段追加唯一文本行。

        参数:
            lines: 目标行列表。
            line: 待追加的文本行。
        返回:
            None: 该辅助函数只更新列表，不返回业务值。
        """

        # 去掉首尾空白后再做去重，避免同一内容只因缩进不同而重复。
        str_stripped_line = line.strip()  # 去空白后的候选头部文本行

        # 只有非空且尚未出现过的内容才值得追加。
        if str_stripped_line and str_stripped_line not in lines:

            # 保留首次出现的文本行，维持原始头部信息的唯一性。
            lines.append(str_stripped_line)

    # 统一规范头部里的版本号文本，保证裸数字版本带上 V 前缀。
    def _normalize_header_version_text(self, text: str) -> str:
        """
        规范化头部版本号文本。

        参数:
            text: 原始版本号文本。
        返回:
            str: 规范化后的版本号文本。
        """

        # 版本文本需要先裁掉两端空白，避免比较和匹配受影响。
        str_version_text = text.strip()  # 去空白后的版本号文本

        # 空版本号不做填充，保持上层按“无值”处理。
        if not str_version_text:

            # 没有版本文本时返回空字符串。
            return ""

        # 纯数字点分版本统一补 V 前缀，保持 Erie 双语头部格式一致。
        if re.fullmatch(r"\d+(?:\.\d+)+", str_version_text):

            # 规范化点分版本时统一转成以 V 开头的版本文本。
            return f"V{str_version_text}"

        # 非标准点分版本保留原样，避免误改用户自定义标签。
        return str_version_text

    # 解析 Revision 段中的版本历史行，并按语言写入对应历史列表。
    def _consume_revision_header_line(
        self,
        metadata: HeaderMetadata,
        text: str,
        language: str = "",
    ) -> None:
        """
        解析 Revision 段中的版本历史文本。

        参数:
            metadata: 当前累计的文件头元数据对象。
            text: Revision 段中的当前文本行。
            language: 当前修订历史的语言上下文。
        返回:
            None: 该辅助函数只更新 metadata，不返回业务值。
        """

        # 去掉多余空白后再判断是否是有效的 Revision 文本。
        str_revision_text = text.strip()  # 去空白后的 Revision 文本

        # 空 Revision 行不携带任何历史信息。
        if not str_revision_text:

            # 空行直接忽略，不写入版本或历史列表。
            return

        # 优先识别 “Revision x.y - description” 这种标准修订格式。
        match_revision = re.match(r"Revision\s+([0-9.]+)\s*-\s*(.+)$", str_revision_text, re.IGNORECASE)  # 标准 Revision 结构

        # 标准 Revision 结构会同时提供版本号和修订描述。
        if match_revision:

            # 旧头部未显式提供 Version 字段时，可从 Revision 里补出版本号。
            if not metadata.version:

                # 只在版本字段为空时用 Revision 里的版本号补齐。
                metadata.version = self._normalize_header_version_text(match_revision.group(1))  # 用 Revision 里的版本号补齐显式版本字段

            # 先根据语言上下文选出英文、中文或通用历史列表。
            list_target_lines = self._select_history_line_bucket(metadata, language)  # Revision 当前语言对应的历史列表

            # 保留规范化后的 Revision 语句，避免丢失原始历史描述。
            self._append_unique_header_line(
                list_target_lines,
                f"Revision {match_revision.group(1)} - {match_revision.group(2).strip()}",
            )

        # 非标准 Revision 文本按当前语言原样追加到对应历史列表。
        else:

            # 自由格式 Revision 文本也需要落入当前语言的历史容器。
            list_target_lines = self._select_history_line_bucket(metadata, language)  # 非标准 Revision 行的历史落点

            # 非标准修订文本保持原样写入，尽量保留用户记录内容。
            self._append_unique_header_line(list_target_lines, str_revision_text)

    # 提取 module 之前的 include/define/timescale 一类前置指令。
    def _extract_preamble_directives(self, source: str) -> list[str]:
        """
        提取 module 声明之前的预处理指令行。

        参数:
            source: 原始 Verilog 源文本。
        返回:
            list[str]: module 之前出现的预处理指令列表。
        """

        # 只有首个 module 之前的区域才属于可提取前导指令的编译上下文。
        match_module = self._find_module_declaration(source)  # 指令抽取使用的 module 边界

        # 找不到 module 时，不尝试从整段文本里误收集指令。
        if not match_module:

            # 缺少 module 时返回空列表，让上层维持保守行为。
            return []

        # 初始化前置指令列表，后续按出现顺序收集。
        list_directives: list[str] = []  # module 之前的预处理指令行

        # 逐行扫描 preamble，并只保留显式反引号指令。
        for str_raw_line in source[: match_module.start()].splitlines():

            # 去掉缩进后再判断是否是反引号开头的预处理指令。
            str_stripped_line = str_raw_line.strip()  # 去空白后的 preamble 行

            # 只有真正的预处理指令才进入重建后的 preamble 区。
            if str_stripped_line.startswith("`"):

                # 保留原始指令文本，后续渲染会按顺序贴回文件头前。
                list_directives.append(str_stripped_line)

        # 返回收集好的前置指令列表。
        return list_directives

    # 判断当前头部渲染结果里是否已经显式包含 timescale 指令。
    def _header_contains_timescale(self, lines: list[str]) -> bool:
        """
        判断一组头部行中是否已经包含 `timescale` 指令。

        参数:
            lines: 待检查的头部文本行列表。
        返回:
            bool: True 表示头部里已经包含 `timescale` 指令。
        """

        # 逐行检查是否存在显式的 `timescale` 指令。
        return any(str_line.strip().startswith("`timescale") for str_line in lines)

    # 判断 RawBlock 列表里是否已经带有 timescale，避免默认值重复插入。
    def _raw_blocks_contain_timescale(self, blocks: list[RawBlock]) -> bool:
        """
        判断 RawBlock 列表中是否已经包含 `timescale` 指令。

        参数:
            blocks: 待检查的 RawBlock 列表。
        返回:
            bool: True 表示至少一个 RawBlock 中已有 `timescale` 指令。
        """

        # 逐块逐行扫描，确认是否已经显式存在 timescale。
        return any(
            any(str_line.strip().startswith("`timescale") for str_line in raw_block.lines)
            for raw_block in blocks
        )

    # 把 preamble RawBlock 渲染回文件前导区，并在需要时补默认 timescale。
    def _render_preamble_blocks(self, blocks: list[RawBlock], *, add_default_timescale: bool) -> list[str]:
        """
        把 preamble RawBlock 渲染回前导文本行列表。

        参数:
            blocks: 需要渲染的 RawBlock 列表。
            add_default_timescale: 是否在缺失 timescale 时补默认值。
        返回:
            list[str]: 渲染完成的 preamble 文本行列表。
        """

        # 汇总最终输出的前导文本行，顺序保持与输入 blocks 一致。
        list_rendered_lines: list[str] = []  # 渲染后的 preamble 文本行

        # 仅在调用方要求补缺且当前 blocks 里没有 timescale 时插入默认值。
        if add_default_timescale and not self._raw_blocks_contain_timescale(blocks):

            # 默认 timescale 保持与旧 formatter 兼容的 1ns / 1ps 约定。
            list_rendered_lines.append("`timescale 1ns / 1ps")

        # 逐个 RawBlock 先渲染前导注释，再渲染原样保留正文。
        for raw_block in blocks:

            # RawBlock 的前导注释需要先贴回到块内容之前。
            list_rendered_lines.extend(self._render_leading_comments(raw_block.leading_comments, 0))

            # 再把 RawBlock 内部原样保留的源码行逐行写回。
            list_rendered_lines.extend(self._render_raw_block_lines(raw_block.lines, 0))

        # 返回完整的前导渲染文本行列表。
        return list_rendered_lines

    # 把当前缓存的 preamble 指令和额外 RawBlock 合并成可写回的头部行。
    def _render_file_preamble_lines(self, extra_blocks: list[RawBlock] | None = None) -> list[str]:
        """
        渲染当前文件前导的预处理指令与额外 RawBlock。

        参数:
            extra_blocks: 需要追加到 preamble 的额外 RawBlock 列表。
        返回:
            list[str]: 最终写回文件前导区的文本行列表。
        """

        # 先把缓存中的反引号指令包成单行 RawBlock，复用统一渲染逻辑。
        list_blocks = [RawBlock(lines=[str_line]) for str_line in self._current_preamble_directives]  # 当前文件缓存的前置指令块

        # 再把调用方追加的原样块拼到后面，保持显式顺序。
        list_blocks.extend(extra_blocks or [])

        # 统一按 preamble 渲染逻辑输出，并在缺失时自动补 timescale。
        return self._render_preamble_blocks(list_blocks, add_default_timescale=True)

    # 从 module body 开头抽出 include/define/timescale 一类前导 preamble 块。
    def _extract_body_leading_preamble(self, body: str) -> tuple[list[RawBlock], str]:
        """
        从 module body 开头提取前导 preamble 块。

        参数:
            body: module body 文本。
        返回:
            tuple[list[RawBlock], str]: 提取出的 RawBlock 列表和剩余 body 文本。
        """

        # 按物理行扫描 body，只有开头连续的指令区才会被抽成 preamble。
        list_body_lines = body.splitlines()  # module body 的物理行列表

        # 收集需要从 body 前缀剥离出的 RawBlock。
        list_blocks: list[RawBlock] = []  # body 前导 preamble 块列表

        # 连续普通注释会作为下一个指令块的前导注释保留。
        list_pending_comments: list[str] = []  # 等待挂到下一个指令块的前导注释

        # 记录前导注释开始位置，便于遇到非指令正文时把注释还给 body。
        int_pending_comment_start: int | None = None  # 当前前导注释起始行号

        # 逐行扫描 body 前缀，直到遇到第一条非空且非前导指令的正文。
        int_index = 0  # body 前缀扫描游标

        # 只在 body 开头连续扫描 preamble 候选区。
        while int_index < len(list_body_lines):

            # 去掉当前行空白后再判断是否是空行、普通注释或预处理指令。
            str_stripped_line = list_body_lines[int_index].strip()  # 当前 body 行的净文本

            # 空行会截断“待挂到下一个指令块”的前导注释队列。
            if not str_stripped_line:

                # 空行说明注释与后续指令不再紧邻，需清空挂靠状态。
                list_pending_comments = []  # 空行会断开待挂接到指令块的注释队列

                # 同时清空注释起点，避免回切 body 起始位置出错。
                int_pending_comment_start = None  # 空行后不再保留注释块起点

                # 扫描继续推进到下一物理行。
                int_index += 1  # 空行处理后推进到下一物理行

                # 当前空行已处理完毕，继续扫描下一行。
                continue

            # 普通行注释可作为紧邻指令块的前导说明一起提取。
            if str_stripped_line.startswith("//") and not is_banner_line(str_stripped_line):

                # 第一次收集前导注释时记录注释块起始位置。
                if int_pending_comment_start is None:

                    # 后续若没有提取出指令块，需要从这里把注释还回正文。
                    int_pending_comment_start = int_index  # 首条前导注释决定潜在的正文回切位置

                # 收集这条普通注释，等待挂接到下一个 preamble 指令块。
                list_pending_comments.append(str_stripped_line)

                # 推进游标，继续尝试吸收后续紧邻注释。
                int_index += 1  # 当前注释行已收下，继续扫描后续紧邻注释

                # 当前注释行不构成终止条件，继续扫描下一行。
                continue

            # include、define 和 timescale 会被识别成前导 preamble RawBlock。
            if str_stripped_line.startswith(("`include", "`define", "`timescale")):

                # 当前指令连同其紧邻普通注释一起形成一个 RawBlock。
                list_blocks.append(
                    RawBlock(
                        lines=[str_stripped_line],
                        leading_comments=list(list_pending_comments),
                    )
                )

                # 当前指令已经消耗了待挂接注释，准备收集下一组注释。
                list_pending_comments = []  # 当前指令块消费完挂接注释后清空注释队列

                # 指令块落地后，注释起点也应随之清空。
                int_pending_comment_start = None  # 指令块落地后不再保留旧注释起点

                # 推进游标，继续检查后续是否还有连续前导指令。
                int_index += 1  # 当前指令块提取完成后继续扫描下一行

                # 当前指令块已经提取完毕，继续扫描下一行。
                continue

            # 遇到第一个非空且非前导指令的正文后，停止抽取 preamble。
            break

        # 没有提取出任何前导指令块时，body 原文不应被改动。
        if not list_blocks:

            # 没有前导 preamble 时直接回传原始 body。
            return [], body

        # 若停在一段尚未被消费的普通注释前，需要把 body 起点回退到注释起点。
        int_body_start = (  # 提取完成后剩余 body 的起始行号
            int_pending_comment_start  # 尚有待挂接注释时回退到注释起始行
            if list_pending_comments and int_pending_comment_start is not None  # 只有保留挂接注释时才执行回退
            else int_index  # 否则直接从当前扫描游标继续正文
        )  # 剩余正文起始行号

        # 返回剥离出的前导块，以及从正文起点重新拼接的剩余 body 文本。
        return list_blocks, "\n".join(list_body_lines[int_body_start:])

    # 从 preamble include 文件中收集 define stub 展开结果。
    def _load_macro_expansions_from_source(self, source: str) -> dict[str, str]:
        """
        从源码 preamble 的 include 文件中加载宏展开 stub。

        参数:
            source: 原始 Verilog 源文本。
        返回:
            dict[str, str]: 宏名到宏体文本的映射表。
        """

        # 宏展开扫描也只以首个 module 之前的 include 作为有效输入边界。
        match_module = self._find_module_declaration(source)  # include 扫描使用的 module 边界

        # 缺少 module 时不尝试解析 include，避免误扫整段文本。
        if not match_module:

            # 没有 preamble 区时返回空宏展开表。
            return {}

        # 截取 module 之前的 preamble 文本，后续只在这里找 include。
        str_preamble_text = source[: match_module.start()]  # module 之前的 preamble 文本

        # 汇总所有可解析 include 文件里的 `define` stub 展开结果。
        dict_expansions: dict[str, str] = {}  # include 派生的宏展开映射表

        # 按 include 指令逐个解析对应文件中的 define stub。
        for match_include in re.finditer(r'(?m)^\s*`include\s+"([^"]+)"', str_preamble_text):

            # 提取当前 include 指令引用的文件名。
            str_include_name = match_include.group(1)  # include 指令中的文件名

            # 解析 include 文件的真实路径，支持源文件目录和工作区根目录查找。
            path_include = self._resolve_preprocessor_include_path(str_include_name)  # include 文件解析结果路径

            # include 文件缺失时保持保守，跳过该 include。
            if path_include is None or not path_include.exists():

                # 缺失的 include 不参与 stub 展开，继续处理下一项。
                continue

            # 把当前 include 文件中的 define stub 合并到全局展开表。
            dict_expansions.update(self._parse_define_stub_file(path_include))

        # 返回汇总后的宏展开映射表。
        return dict_expansions

    # 按当前源文件目录和工作区根目录查找 include 文件的真实路径。
    def _resolve_preprocessor_include_path(self, include_name: str) -> Path | None:
        """
        解析预处理 include 文件的真实路径。

        参数:
            include_name: include 指令中给出的文件名或相对路径。
        返回:
            Path | None: 成功定位时返回真实路径；否则返回 None。
        """

        # 先把 include 名直接视为 Path，兼容绝对路径输入。
        path_candidate = Path(include_name)  # include 名对应的候选路径

        # 绝对路径且真实存在时，优先直接复用该路径。
        if path_candidate.is_absolute() and path_candidate.exists():

            # 绝对路径命中时无需再走工作区相对查找逻辑。
            return path_candidate

        # 先收集允许搜索 include 的根目录，顺序保留就近优先原则。
        list_search_roots: list[Path] = []  # include 文件允许搜索的根目录列表

        # 当前源文件所在目录是 include 相对路径的首选查找根。
        if self._current_source_path is not None:

            # 当前源文件目录优先保证局部 include 的相对解析行为。
            list_search_roots.append(self._current_source_path.parent)

        # 当前工作区根可覆盖跨目录 include 的共享头文件查找。
        if self._current_workspace_root is not None:

            # 工作区根作为第二查找层，兼容仓库级公共 include 文件。
            list_search_roots.append(self._current_workspace_root)

        # 按优先顺序逐个尝试组合 include 相对路径。
        for path_root in list_search_roots:

            # 在当前搜索根下解析并规范化 include 路径。
            path_resolved = (path_root / include_name).resolve()  # 当前搜索根下的 include 真实路径

            # 命中真实存在的 include 文件时立即返回。
            if path_resolved.exists():

                # 返回找到的第一个 include 文件路径，保持就近优先。
                return path_resolved

        # 所有允许的搜索根都未命中时，报告无法解析。
        return None

    # 解析 include stub 文件中的多行 `define` 展开体。
    def _parse_define_stub_file(self, path: Path) -> dict[str, str]:
        """
        解析 stub include 文件中的 `define` 宏展开体。

        参数:
            path: include stub 文件路径。
        返回:
            dict[str, str]: 宏名到宏体文本的映射表。
        """

        # 先按 UTF-8 读取整个 stub 文件，再按物理行解析 define。
        list_source_lines = path.read_text(encoding="utf-8").splitlines()  # stub 文件物理行列表

        # 汇总当前 stub 文件中定义的宏展开内容。
        dict_expansions: dict[str, str] = {}  # 当前 stub 文件解析出的宏展开映射

        # 顺序扫描文件，兼容带反斜杠续行的多行 define。
        int_index = 0  # stub 文件扫描游标

        # 逐行识别 `define` 宏起始行和后续续行。
        while int_index < len(list_source_lines):

            # 先规整当前物理行文本，便于识别反引号 define 起始语法。
            str_stripped_line = list_source_lines[int_index].strip()  # 当前 stub 物理行去空白后的 define 文本

            # 非 define 行不参与展开映射，直接跳到下一行。
            if not str_stripped_line.startswith("`define "):

                # 当前行与宏定义无关，推进扫描游标。
                int_index += 1  # 非 define 行跳过后推进到下一物理行

                # 当前非 define 行处理完成，继续扫描下一行。
                continue

            # 用正则拆出宏名和当前行已出现的宏体首段。
            match_define = re.match(r"^`define\s+(?P<name>\w+)\s*(?P<body>.*)$", str_stripped_line)  # define 起始行匹配结果

            # 不满足预期 define 结构时，保守跳过当前行。
            if not match_define:

                # 避免不规则 define 语法污染宏展开映射。
                int_index += 1  # 不规则 define 行跳过后推进到下一物理行

                # 当前不规则 define 行已跳过，继续扫描下一行。
                continue

            # 记录当前 define 的宏名，作为最终映射字典的键。
            str_macro_name = match_define.group("name")  # 当前 define 的宏名

            # 读取 define 起始行上的宏体首段文本。
            str_body_text = match_define.group("body").rstrip()  # define 起始行上的宏体首段

            # 先把起始行宏体放进部件列表，后续续行再继续追加。
            list_body_parts = [str_body_text[:-1].rstrip()] if str_body_text.endswith("\\") else [str_body_text]  # define 宏体分段列表

            # 起始行已经消费完毕，先移动到下一物理行。
            int_index += 1  # define 起始行处理完后先移动到下一物理行

            # 只有当前宏体以反斜杠结尾时，才继续吸收后续续行。
            while str_body_text.endswith("\\") and int_index < len(list_source_lines):

                # 读取 define 续行并保留其右侧空白裁剪行为。
                str_body_text = list_source_lines[int_index].rstrip()  # define 当前续行文本

                # 续行若仍以反斜杠结尾，则继续去尾反斜杠后存入部件表。
                list_body_parts.append(str_body_text[:-1].rstrip() if str_body_text.endswith("\\") else str_body_text)

                # 当前续行处理完毕，继续检查后面是否还有续行。
                int_index += 1  # 当前续行处理完后继续检查是否还有后续续行

            # 合并宏体分段时跳过空片段，避免多余空行污染展开结果。
            dict_expansions[str_macro_name] = "\n".join(str_part for str_part in list_body_parts if str_part)  # 当前宏名对应的完整展开文本

        # 返回当前 stub 文件解析出的全部宏展开映射。
        return dict_expansions

    # 把用户或环境提供的头部日期文本解析成 datetime。
    def _parse_header_datetime(self, text: str) -> datetime | None:
        """
        解析文件头中允许的日期时间文本。

        参数:
            text: 待解析的日期时间文本。
        返回:
            datetime | None: 成功解析时返回 datetime；失败时返回 None。
        """

        # 去掉两端空白后再尝试匹配支持的日期格式。
        str_candidate = text.strip()  # 去空白后的日期候选文本

        # 空字符串没有任何日期语义，直接视为未提供。
        if not str_candidate:

            # 没有候选日期文本时返回 None。
            return None

        # 折叠连续空白，兼容用户手写日期中的多空格分隔。
        str_normalized_candidate = re.sub(r"\s+", " ", str_candidate)  # 统一空白后的日期候选文本

        # 按 Erie 头部允许的时间格式逐个尝试解析。
        for str_pattern in (
            "%Y/%m/%d %H:%M:%S",
            "%m/%d/%Y %I:%M:%S %p",
            "%Y/%m/%d",
            "%Y年%m月%d日",
        ):

            # 每个日期格式都独立尝试，便于兼容旧模板和中文日期。
            try:

                # 当前格式成功命中时立即返回解析结果。
                return datetime.strptime(str_normalized_candidate, str_pattern)

            # 当前格式不匹配时继续尝试下一种允许格式。
            except ValueError:

                # 单个格式不匹配并不代表日期整体无效，继续试下一个。
                continue

        # 所有允许格式都失败时，报告无法解析。
        return None

    # 把头部日期文本统一折算成英文与中文两种展示样式。
    def _coerce_header_date_variants(self, text: str, *, fallback: datetime) -> tuple[str, str]:
        """
        生成头部日期的英文与中文展示样式。

        参数:
            text: 原始日期文本。
            fallback: 原始文本缺失或无法解析时使用的回退时间。
        返回:
            tuple[str, str]: 英文日期文本和中文日期文本。
        """

        # 先裁掉空白，便于区分“没有日期”与“提供了无法解析的日期”。
        str_candidate = text.strip()  # 去空白后的日期文本

        # 没有显式日期时，统一回退到当前渲染时间。
        if not str_candidate:

            # 缺省日期同时生成英文时间戳和中文日期两种样式。
            return fallback.strftime("%Y/%m/%d %H:%M:%S"), fallback.strftime("%Y年%m月%d日")

        # 对可见日期文本再尝试走统一日期解析逻辑。
        datetime_header_date_value: datetime | None = self._parse_header_datetime(str_candidate)  # 头部日期解析结果

        # 无法解析时尽量保持原文，避免误改用户手写日期说明。
        if datetime_header_date_value is None:

            # 含“年”字的中文日期直接双向复用原文。
            if "年" in str_candidate:

                # 中文原文已具备可读性时，不再二次转换格式。
                return str_candidate, str_candidate

            # 其余未知格式也原样保留，避免篡改外部系统生成的日期文本。
            return str_candidate, str_candidate

        # 成功解析标准日期时，统一输出 Erie 头部约定的英中格式。
        return (
            datetime_header_date_value.strftime("%Y/%m/%d %H:%M:%S"),
            datetime_header_date_value.strftime("%Y年%m月%d日"),
        )

    # 从原始源码头部中提取现有版本号，供 normalize 路由继承历史版本。
    def _extract_version(self, source: str) -> str | None:
        """
        从原始源码头部提取现有版本号。

        参数:
            source: 原始 Verilog 源文本。
        返回:
            str | None: 识别出的版本号；找不到时返回 None。
        """

        # 优先匹配显式 Version / 当前版本 字段。
        match_version = re.search(r"(?m)^\s*//\s*(?:Version|当前版本):\s*(V?\d+(?:\.\d+)*)", source)  # 头部显式版本字段匹配结果

        # 命中显式版本字段时直接返回规范化后的版本文本。
        if match_version:

            # Version 字段优先级最高，应直接作为当前版本号。
            return self._normalize_header_version_text(match_version.group(1))

        # 其次兼容从 Revision x.y - ... 语句中反推版本号。
        match_revision = re.search(r"(?m)^\s*//\s*Revision\s+([0-9.]+)\s*-", source)  # Revision 版本行匹配结果

        # 命中 Revision 版本行时，使用其中的版本号作为回退版本。
        if match_revision:

            # Revision 行提供版本号时，同样走统一版本文本规范化。
            return self._normalize_header_version_text(match_revision.group(1))

        # 旧源码没有可继承版本号时返回 None。
        return None

    # 读取 example_compat 开关，兼容旧 formatter 的示例输出模式。
    def _example_compat_enabled(self) -> bool:
        """
        判断是否启用 example_compat 兼容模式。

        参数:
            无外部业务参数。
        返回:
            bool: True 表示启用 example_compat 兼容模式。
        """

        # compat 段缺失时默认关闭示例兼容模式。
        return bool(self.config.get("compat", {}).get("example_compat", False))

    # 返回 header 布局配置；缺少显式 layout 时回退到内置空格版式默认值。
    def _header_layout_config(self) -> dict[str, object]:
        """
        返回当前 formatter 使用的 header 布局配置。

        参数:
            无外部业务参数。
        返回:
            dict[str, object]: 英文/中文字段前缀、表头与分隔策略配置。
        """

        # 先读取 header 配置段，layout 子段缺失时再回退到内置默认值。
        dict_header_config = self.config.get("header", {})  # 用于判定是否存在显式 layout 子段的原始 header 配置

        # 只有 layout 子段是字典时，才把它视为合法的结构化布局配置。
        if isinstance(dict_header_config, dict) and isinstance(dict_header_config.get("layout"), dict):

            # 调用方显式提供 layout 时优先复用该结构化配置。
            return dict_header_config["layout"]

        # 缺省时统一使用新的空格版式双语 header 默认布局。
        return {
            "english": {
                "separator": "////////////////////////////////////English///////////////////////////////////////",
                "company_prefix": "// Company:         ",
                "engineer_prefix": "// Engineer:        ",
                "blank_after_identity": "//",
                "create_date_prefix": "// Create Date:     ",
                "design_name_prefix": "// Design Name:     ",
                "module_name_prefix": "// Module Name:     ",
                "description_prefix": "// Description:     ",
                "simulations_prefix": "// Simulations:     ",
                "blank_before_references": "//",
                "references_prefix": "// References:     ",
                "references_table_header": "File Format      File Name",
                "dependencies_prefix": "// Dependencies:    ",
                "dependencies_table_header": "Module Name      Version",
                "section_blank": "//",
                "version_prefix": "// Version:         ",
                "revision_date_prefix": "// Revision Date:   ",
                "history_title": "// History:",
                "history_header": "// Time             Version     Revised by        Contents",
            },
            "chinese": {
                "separator": "///////////////////////////////////Chinese////////////////////////////////////////",
                "company_prefix": "// 版权归属:        ",
                "engineer_prefix": "// 开发人员:        ",
                "blank_after_identity": "//",
                "create_date_prefix": "// 创建日期:        ",
                "design_name_prefix": "// 设计名称:        ",
                "module_name_prefix": "// 模块名称:        ",
                "description_prefix": "// 模块说明:        ",
                "simulations_prefix": "// 仿真工程:        ",
                "blank_before_references": "//",
                "references_prefix": "// 参考资料:        ",
                "references_table_header": "文件格式         文件名称",
                "dependencies_prefix": "// 依赖文件:        ",
                "dependencies_table_header": "模块名称         版本",
                "section_blank": "//",
                "version_prefix": "// 当前版本:        ",
                "revision_date_prefix": "// 修订日期:        ",
                "history_title": "// 修订历史:",
                "history_header": "// 时间             版本        修订人            修订内容",
            },
        }

    # 用 header 配置里的模板文本渲染默认字段，并兼容 `$module$` 与 `{module_name}` 占位。
    def _render_header_template_text(self, template: str, module_name: str) -> str:
        """
        渲染 header 默认模板文本。

        参数:
            template: header 配置中的模板字符串。
            module_name: 当前 module 名称。
        返回:
            str: 用 module 名称替换后的 header 默认文本。
        """

        # 先把 `$module$` 与 `{module}` 兼容占位统一替换掉。
        str_template = str(template).replace("$module$", module_name).replace("{module}", module_name)  # 兼容占位替换后的模板文本

        # `{module_name}` 继续沿用 Python format 风格，未命中时原样回退。
        try:

            # 标准模板允许显式使用 `{module_name}` 占位。
            return str_template.format(module_name=module_name, module=module_name)

        # 模板若包含其他花括号，不应让 header 渲染流程直接失败。
        except (IndexError, KeyError, ValueError):

            # 保守回退到仅做兼容占位替换后的文本。
            return str_template

    # 统一规整 header 版式比较文本，便于识别中英文表头和旧版混合残留。
    def _normalize_header_compare_text(self, text: str) -> str:
        """
        规整 header 比较文本，折叠空白并统一大小写。

        参数:
            text: 待规整的 header 文本。
        返回:
            str: 适合比较的规整文本。
        """

        # 先把 tab 折叠成空格，再做首尾裁剪和连续空白折叠。
        return re.sub(r"\s+", " ", text.replace("\t", " ").strip()).lower()

    # 规范化 header 多行块，保留原顺序并去掉纯空行和重复项。
    def _normalize_header_block_lines(self, lines: list[str]) -> list[str]:
        """
        规范化 header 多行块内容。

        参数:
            lines: 原始多行块文本列表。
        返回:
            list[str]: 去空白、保序且去重后的行列表。
        """

        # list_normalized_lines 保存当前多行块的稳定工作副本。
        list_normalized_lines: list[str] = []  # header 多行块规范化结果

        # 逐条规整输入行，避免把空白或重复项继续带入渲染阶段。
        for str_line in lines:

            # 仅裁掉两端空白并去掉外层 `//`，内部空格保留给表格列宽使用。
            str_candidate_line = str_line.strip()  # 当前候选多行文本

            # 头部多行块中的纯空行没有结构意义，直接跳过。
            if not str_candidate_line:

                # 空行不进入最终多行块结果。
                continue

            # 兼容历史解析结果意外保留 `//` 前缀的情况。
            if str_candidate_line.startswith("//"):

                # 去掉注释前缀后只保留正文内容。
                str_candidate_line = str_candidate_line[2:].strip()  # 去掉历史残留注释前缀后的正文

            # 去掉前缀后的空正文也不保留。
            if not str_candidate_line:

                # 没有正文的候选项直接跳过。
                continue

            # 保持首次出现顺序，避免双语扫描或旧 Revision 残留重复灌入。
            if str_candidate_line not in list_normalized_lines:

                # 首次出现的正文进入最终规范化结果。
                list_normalized_lines.append(str_candidate_line)

        # 返回保序去重后的 header 多行块行列表。
        return list_normalized_lines

    # 从参考资料/依赖文件多行块里去掉 table header，占位行和无效空白。
    def _strip_header_table_headings(self, lines: list[str], headings: list[str]) -> list[str]:
        """
        去掉 header 表格块中的表头和 None 占位。

        参数:
            lines: 已规范化的表格块正文行。
            headings: 当前表格块允许出现的表头文本集合。
        返回:
            list[str]: 去掉表头后的真实数据行列表。
        """

        # set_heading_keys 把允许的表头文本规整成比较键，便于统一过滤。
        set_heading_keys = {self._normalize_header_compare_text(str_heading) for str_heading in headings}  # 表头比较键集合

        # 只保留不属于表头也不是 None 占位的真实数据行。
        return [
            str_line
            for str_line in lines  # 当前表格块规范化正文行
            if self._normalize_header_compare_text(str_line) not in set_heading_keys | {"none"}  # 跳过表头和占位
        ]

    # 把 references / dependencies 兼容数据统一归一到 none_mode 或 table_mode。
    def _build_header_reference_dependency_blocks(self, metadata: HeaderMetadata) -> dict[str, object]:
        """
        根据 header 元数据构造统一的 References/Dependencies 渲染块。

        参数:
            metadata: 当前 header 元数据对象。
        返回:
            dict[str, object]: mode、reference_rows 和 dependency_rows 的结构化结果。
        """

        # layout 配置同时提供英中两套表头文本，便于过滤历史输入中的 heading 行。
        dict_layout = self._header_layout_config()  # header 结构化布局配置

        # 兼容入口 references 只在非空时参与 two-mode 归一化。
        str_reference_scalar = (metadata.references or "").strip()  # 单值参考资料兼容入口

        # 多行参考资料表先统一规范化，后续再去掉英中文表头。
        list_reference_lines = self._normalize_header_block_lines(metadata.reference_lines)  # 参考资料多行块

        # 依赖文件多行块同样先统一规范化。
        list_dependency_lines = self._normalize_header_block_lines(metadata.dependency_lines)  # 依赖文件多行块

        # 依赖块要先去掉 None 占位，后续才能准确判断是否真的存在依赖数据。
        list_real_dependency_lines = [
            str_line for str_line in list_dependency_lines  # 逐条扫描归一后的依赖块
            if self._normalize_header_compare_text(str_line) != "none"  # 过滤掉 None 占位行
        ]  # 去掉 None 占位后的依赖数据行

        # 只要任一侧出现表格块或单值参考摘要，就统一切换到 table_mode。
        bool_table_mode = bool(  # 是否进入 references/dependencies 的表格渲染模式
            list_reference_lines  # 任一参考资料表格块都会触发表格模式
            or list_real_dependency_lines  # 真实依赖数据行同样需要进入表格模式
            or (str_reference_scalar and str_reference_scalar != "None")  # 旧单值 references 也视作表格线索
        )

        # 缺少任何表格线索时，直接回退到统一 none_mode。
        if not bool_table_mode:

            # none_mode 下两段都只渲染单行 None。
            return {
                "mode": "none",
                "reference_rows": [],
                "dependency_rows": [],
            }

        # 参考资料真实数据行要去掉英中表头和 None 占位。
        list_reference_rows = self._strip_header_table_headings(  # 去掉参考资料表头后的真实数据行
            list_reference_lines,  # 归一后的参考资料多行块
            [
                str(dict_layout["english"]["references_table_header"]),  # 英文 references 表头
                str(dict_layout["chinese"]["references_table_header"]),  # 中文 references 表头
            ],
        )

        # 旧版单值 references 摘要没有多行表格时，自动归一成一条 Book 样式数据行。
        if not list_reference_rows and str_reference_scalar and str_reference_scalar != "None":

            # 兼容旧数据时至少保留一条稳定的参考资料数据行。
            list_reference_rows = [f"1.Book           {str_reference_scalar}"]  # 兼容旧单值 references 的默认表格行

        # 依赖文件真实数据行同样要去掉英中表头和 None 占位。
        list_dependency_rows = self._strip_header_table_headings(  # 去掉依赖文件表头后的真实数据行
            list_dependency_lines,  # 归一后的依赖文件多行块
            [
                str(dict_layout["english"]["dependencies_table_header"]),  # 旧英文依赖表头
                str(dict_layout["chinese"]["dependencies_table_header"]),  # 中文依赖列标题
            ],
        )

        # 返回统一 table_mode 结构，允许某一侧只有表头而无真实数据行。
        return {
            "mode": "table",
            "reference_rows": list_reference_rows,
            "dependency_rows": list_dependency_rows,
        }

    # 把英中双语字段和统一的 none/table mode 结构渲染成一侧 header 段落。
    def _build_header_section(
        self,
        section_values: dict[str, str],
        history_lines: list[str],
        reference_dependency_blocks: dict[str, object],
        *,
        language: str,
    ) -> list[str]:
        """
        生成指定语言的 header 段落。

        参数:
            section_values: 当前语言下的头字段值字典。
            history_lines: 当前语言的修订历史正文行。
            reference_dependency_blocks: 统一的 none/table mode 参考资料与依赖块。
            language: `en` 或 `cn`。
        返回:
            list[str]: 指定语言的完整 header 段落文本行列表。
        """

        # section_layout 读取当前语言的精确前缀和表头版式。
        section_layout = self._header_layout_config()["english" if language == "en" else "chinese"]  # 当前语言布局配置

        # list_history_block 统一给历史正文补注释前缀。
        list_history_block = [f"// {str_line}" for str_line in history_lines]  # 当前语言修订历史正文行

        # list_section 先写出固定字段，再按 mode 追加 References/Dependencies。
        list_section = [  # 当前语言 header 固定字段区
            str(section_layout["separator"]),  # 头部横向分隔线
            f"{section_layout['company_prefix']}{section_values['copyright_owner']}",  # 版权归属字段
            f"{section_layout['engineer_prefix']}{section_values['developer']}",  # 开发人员字段
            str(section_layout["blank_after_identity"]),  # 身份字段后的空白行
            f"{section_layout['create_date_prefix']}{section_values['create_date']}",  # 创建日期字段
            f"{section_layout['design_name_prefix']}{section_values['design_name']}",  # 设计名称字段
            f"{section_layout['module_name_prefix']}{section_values['module_name']}",  # 模块名称字段
            f"{section_layout['description_prefix']}{section_values['description']}",  # 模块说明字段
            f"{section_layout['simulations_prefix']}{section_values['simulations']}",  # 仿真工程字段
            str(section_layout["blank_before_references"]),  # References 前的空白行
        ]

        # none_mode 下两段都只允许单行 None。
        if reference_dependency_blocks["mode"] == "none":

            # References 和 Dependencies 同时使用新的空格前缀与单行 None 占位。
            list_section.extend(
                [
                    f"{section_layout['references_prefix']}None",
                    str(section_layout["section_blank"]),
                    f"{section_layout['dependencies_prefix']}None",
                ]
            )

        # table_mode 下两段都必须使用表格块，允许另一侧只有表头零数据行。
        else:

            # References table block 先输出段标题，后续再集中追加列头和数据行。
            list_section.append(str(section_layout["references_prefix"]).rstrip())

            # References 表格块要把列头、真实数据行和段间空白一次组装完成。
            list_reference_block = [  # 完整 References 表格块
                f"// {section_layout['references_table_header']}",  # References 段列头行
                *[f"// {str_line}" for str_line in reference_dependency_blocks["reference_rows"]],  # References 真实数据行
                str(section_layout["section_blank"]),  # References 与 Dependencies 之间的空白行
            ]

            # 组装完成后再把整个 References 表格块并回当前语言段。
            list_section.extend(list_reference_block)

            # Dependencies table block 单独输出标题，再拼装依赖表格正文。
            list_section.append(str(section_layout["dependencies_prefix"]).rstrip())

            # Dependencies 段要把依赖表头和真实依赖文件名重新拼回输出注释块。
            list_dependency_block = [  # 仅含依赖列头与依赖条目的输出块
                f"// {section_layout['dependencies_table_header']}",  # 依赖文件表格的列名行
                *[f"// {str_line}" for str_line in reference_dependency_blocks["dependency_rows"]],  # 原始依赖文件条目列表
            ]

            # 完整依赖块组装完毕后，再接到当前语言段尾部。
            list_section.extend(list_dependency_block)

        # References/Dependencies 之后固定空出一条注释空行，再进入版本与历史段。
        list_section.extend(
            [
                str(section_layout["section_blank"]),
                f"{section_layout['version_prefix']}{section_values['version']}",
                f"{section_layout['revision_date_prefix']}{section_values['revision_date']}",
                str(section_layout["history_title"]),
                str(section_layout["history_header"]),
            ]
        )

        # 版本与历史表头之后再追加真实修订历史正文。
        list_section.extend(list_history_block)

        # 返回当前语言完整的 header 段落。
        return list_section

    # 生成英文双语文件头段落，统一复用结构化 layout 和 none/table mode 渲染。
    def _build_english_header_section(
        self,
        section_values: dict[str, str],
        reference_dependency_blocks: dict[str, object],
        history_lines_en: list[str],
    ) -> list[str]:
        """
        生成英文文件头段落。

        参数:
            section_values: 英文头部所需字段值字典。
            reference_dependency_blocks: 统一的 References/Dependencies 结构。
            history_lines_en: 英文修订历史行列表。
        返回:
            list[str]: 英文文件头段落文本行列表。
        """

        # 英文段直接复用通用 header section builder。
        return self._build_header_section(
            section_values,
            history_lines_en,
            reference_dependency_blocks,
            language="en",
        )

    # 生成中文双语文件头段落，保留中文标签、历史内容和结构化布局约束。
    def _build_chinese_header_section(
        self,
        section_values: dict[str, str],
        reference_dependency_blocks: dict[str, object],
        history_lines_cn: list[str],
    ) -> list[str]:
        """
        生成中文文件头段落。

        参数:
            section_values: 中文头部所需字段值字典。
            reference_dependency_blocks: 统一的 References/Dependencies 结构。
            history_lines_cn: 中文修订历史行列表。
        返回:
            list[str]: 中文文件头段落文本行列表。
        """

        # 中文段同样复用通用 header section builder。
        return self._build_header_section(
            section_values,
            history_lines_cn,
            reference_dependency_blocks,
            language="cn",
        )

    # 根据元数据和默认配置重建 Erie 双语文件头文本。
    def _render_header(self, module_name: str, version: str, metadata: HeaderMetadata | None = None) -> list[str]:
        """
        根据元数据和默认配置渲染 Erie 双语文件头。

        参数:
            module_name: 当前 module 名称。
            version: 当前文件头版本号。
            metadata: 旧文件头提取出的可选元数据。
        返回:
            list[str]: 完整的双语文件头文本行列表。
        """

        # 先解析本轮渲染应使用的当前时间，用作缺省日期和修订回退时间。
        datetime_now: datetime = self._resolve_header_now()  # 本轮文件头渲染时间

        # 缺少旧文件头元数据时，使用空的元数据容器走统一渲染路径。
        header_metadata_current: HeaderMetadata = metadata or HeaderMetadata()  # 当前渲染流程使用的文件头元数据

        # 创建日期需要同时准备英文与中文两套展示格式。
        str_create_date_en, str_create_date_cn = self._coerce_header_date_variants(  # 创建日期的英中文本
            header_metadata_current.create_date,  # 旧头部提取出的创建日期原文
            fallback=datetime_now,  # 缺省时回退到本轮渲染时间
        )

        # 修订日期优先使用显式 revision_date，没有时回退到 create_date。
        str_revision_source = header_metadata_current.revision_date or header_metadata_current.create_date  # 修订日期的原始来源文本

        # 修订日期同样需要生成英文与中文两套展示文本。
        str_revision_date_en, str_revision_date_cn = self._coerce_header_date_variants(  # 修订日期的英中文本
            str_revision_source,  # revision_date 缺失时沿用 create_date 的原始文本
            fallback=datetime_now,  # 修订日期完全缺失时改用本轮渲染时间
        )

        # header 配置段提供版权归属、开发人员和修订人等默认字段。
        dict_header_config = self.config["header"]  # 文件头默认字段配置段

        # 英文版权归属优先取显式 owner，其次兼容旧 company_en 字段。
        str_copyright_owner_en = (
            dict_header_config.get("copyright_owner_en")  # 新版配置中的英文版权归属
            or dict_header_config.get("company_en", "Erie")  # 旧字段缺省时回退到 company_en
        )  # 英文版权归属

        # 中文版权归属缺省时回退到英文版权归属，避免出现空字段。
        str_copyright_owner_cn = (
            dict_header_config.get("copyright_owner_cn")  # 新版配置中的中文版权归属
            or dict_header_config.get("company_cn", str_copyright_owner_en)  # 历史配置仍使用 company_cn 时继续兼容
        )  # 中文版权归属

        # 英文开发人员优先取 developer_en，再兼容旧 engineer_en 字段。
        str_developer_en = dict_header_config.get("developer_en") or dict_header_config.get("engineer_en", "Erie")  # 英文开发人员

        # 中文开发人员缺省时回退到英文开发人员字段。
        str_developer_cn = (
            dict_header_config.get("developer_cn")  # 新版配置中的中文开发人员
            or dict_header_config.get("engineer_cn", str_developer_en)  # 旧工程沿用 engineer_cn 时继续兼容
        )  # 中文开发人员

        # 英文修订人显式缺省时沿用英文开发人员。
        str_reviser_en = dict_header_config.get("reviser_en") or str_developer_en  # 英文修订人

        # 中文修订人优先使用显式配置，其次沿用中文开发人员，再回退到英文修订人。
        str_reviser_cn = (
            dict_header_config.get("reviser_cn")  # 显式配置的中文修订人
            or str_developer_cn  # 没配修订人时先沿用中文开发人员
            or str_reviser_en  # 中文名也缺失时再回退到英文修订人
        )  # 中文修订人

        # 设计名称缺失时，默认沿用当前 module 名称。
        str_design_name = header_metadata_current.design_name or module_name  # 文件头设计名称

        # 模块名称缺失时，同样回退到当前 module 名称。
        str_rendered_module_name = header_metadata_current.module_name or module_name  # 文件头模块名称

        # 模块说明默认模板支持通过配置参数化 module 名称占位。
        str_description_template = str(  # 模块说明缺失时回退使用的模板文本
            dict_header_config.get("description_template") or "Description/{module_name}_Design.pdf"  # 缺省模块说明模板正文
        )

        # 模块说明缺失时，按配置模板生成默认的说明文档路径。
        str_description = header_metadata_current.description or self._render_header_template_text(  # 最终写回文件头的模块说明
            str_description_template,  # 参与占位符替换的说明模板
            str_rendered_module_name,  # 用于替换 {module_name} 占位
        )

        # 仿真工程默认模板同样通过配置承载可变项目名。
        str_simulations_template = str(  # 仿真工程缺失时回退使用的模板文本
            dict_header_config.get("simulations_template") or "TestBench/Vivado/2021.1/{module_name}"  # 缺省仿真工程模板正文
        )

        # 仿真工程缺失时，按配置模板生成默认的 testbench 路径。
        str_simulations = header_metadata_current.simulations or self._render_header_template_text(  # 最终写回文件头的仿真工程路径
            str_simulations_template,  # 参与占位符替换的仿真模板
            str_rendered_module_name,  # 用于替换仿真工程模板里的 {module_name}
        )

        # 参考资料与依赖文件统一先归一成 none_mode 或 table_mode 两种结构。
        dict_reference_dependency_blocks = self._build_header_reference_dependency_blocks(  # References/Dependencies 统一结构
            header_metadata_current  # 当前 header 元数据中的旧版兼容入口和多行块
        )

        # 版本号优先沿用旧头部提取结果，否则回退到当前 formatter 版本输入。
        str_rendered_version = header_metadata_current.version or version  # 文件头当前版本文本

        # 修订历史需要先按旧元数据与回退规则生成英中两套历史行。
        list_history_lines_en, list_history_lines_cn = self._build_header_history_lines(  # 英中文修订历史正文
            header_metadata_current,  # 当前渲染流程使用的头部元数据
            str_rendered_version,  # 本轮头部版本号
            str_reviser_en, str_reviser_cn,  # 英中文修订人
            str_create_date_en, str_create_date_cn,  # 英中文创建日期
            datetime_now,  # 当前渲染时间
        )

        # 英文头部字段先打包成字典，避免私有 helper 参数列表继续膨胀。
        dict_english_section_values = {  # 英文头部字段字典
            "copyright_owner": str_copyright_owner_en,  # 英文版权归属字段值
            "developer": str_developer_en,  # 英文开发人员字段值
            "create_date": str_create_date_en,  # 英文创建日期字段值
            "design_name": str_design_name,  # 英文设计名称字段值
            "module_name": str_rendered_module_name,  # 英文模块名称字段值
            "description": str_description,  # 英文模块说明字段值
            "simulations": str_simulations,  # 英文仿真工程字段值
            "version": str_rendered_version,  # 英文版本字段值
            "revision_date": str_revision_date_en,  # 英文修订日期字段值
        }

        # 中文头部字段也打包成字典，保持双语 helper 的入参结构一致。
        dict_chinese_section_values = {  # 中文头部字段字典
            "copyright_owner": str_copyright_owner_cn,  # 中文版权归属字段值
            "developer": str_developer_cn,  # 中文开发人员字段值
            "create_date": str_create_date_cn,  # 中文创建日期字段值
            "design_name": str_design_name,  # 中文设计名称字段值
            "module_name": str_rendered_module_name,  # 中文模块名称字段值
            "description": str_description,  # 中文模块说明字段值
            "simulations": str_simulations,  # 中文仿真工程字段值
            "version": str_rendered_version,  # 中文版本字段值
            "revision_date": str_revision_date_cn,  # 中文修订日期字段值
        }

        # 先生成英文文件头段落，保持双语输出时英文块位于前半段。
        list_english_section = self._build_english_header_section(  # 英文头部段落
            dict_english_section_values,  # 供英文模板取值的字段包
            dict_reference_dependency_blocks,  # 英文段复用已归一的 references/dependencies 数据
            list_history_lines_en,  # 需要写回英文历史表的正文行
        )

        # 再生成中文文件头段落，补齐中文标签和中文修订历史内容。
        list_chinese_section = self._build_chinese_header_section(  # 中文头部段落
            dict_chinese_section_values,  # 中文标签到字段文本的映射包
            dict_reference_dependency_blocks,  # 中文段沿用同一份 references/dependencies 内容
            list_history_lines_cn,  # 最终贴到中文修订表的历史内容
        )

        # 双语头部最终按英文在前、中文在后的既有模板顺序输出。
        return [*list_english_section, *list_chinese_section]

    # 判断修订历史行里是否包含中文字符，用于英文/中文历史分流。
    def _history_line_contains_cjk(self, text: str) -> bool:
        """
        判断一条修订历史行里是否包含中文字符。

        参数:
            text: 待检查的修订历史文本行。
        返回:
            bool: True 表示文本里包含 CJK 中文字符。
        """

        # 只要出现任意一个中文字符，就把该历史行视为中文记录。
        return any("\u4e00" <= str_char <= "\u9fff" for str_char in text)

    # 按英中模板列宽生成统一的修订历史记录行。
    def _format_history_table_row(
        self, date_text: str, version: str,
        reviser: str, contents: str,
        *,
        language: str,
    ) -> str:
        """
        按当前语言模板列宽格式化一条修订历史记录。

        参数:
            date_text: 历史记录日期文本。
            version: 历史记录版本号。
            reviser: 历史记录修订人。
            contents: 历史记录修订内容。
            language: `en` 或 `cn`。
        返回:
            str: 按空格列宽格式化后的历史记录行。
        """

        # 英文日期列更宽，中文日期列相对紧凑，其余列宽保持一致。
        int_date_width = 17 if language == "en" else 14  # 英中日期列宽

        # 统一以空格列宽拼出日期、版本、修订人和内容四列。
        return f"{date_text:<{int_date_width}}{version:<12}{reviser:<18}{contents}"

    # 尝试把一条历史自由文本解析成结构化的日期/版本/修订人/内容四元组。
    def _parse_structured_history_row(
        self,
        text: str,
        *,
        language: str,
    ) -> tuple[str, str, str, str] | None:
        """
        解析显式历史记录行。

        参数:
            text: 去空白后的历史记录文本。
            language: 当前目标历史表语言。
        返回:
            tuple[str, str, str, str] | None: 成功时返回日期、版本、修订人和内容。
        """

        # 不同语言历史表的日期形态不同，先按目标语言选择记录正则。
        str_pattern = (  # 当前语言对应的结构化历史记录匹配正则
            r"^(?P<date>\d{4}/\d{1,2}/\d{1,2})(?:\s+\d{2}:\d{2}:\d{2})?\s+"
            r"(?P<version>V?\d+(?:\.\d+)*)\s+(?P<reviser>\S+)\s+(?P<contents>.+)$"
            if language == "en"  # 英文历史表采用斜杠日期格式
            else r"^(?P<date>\d{4}年\d{1,2}月\d{1,2}日)\s+"
            r"(?P<version>V?\d+(?:\.\d+)*)\s+(?P<reviser>\S+)\s+(?P<contents>.+)$"
        )  # 当前语言历史记录结构正则

        # obj_match 是当前历史文本的结构化匹配结果。
        obj_match = re.match(str_pattern, text)  # 历史记录结构匹配结果

        # 不满足标准结构时返回 None，让上层继续走自由文本回退逻辑。
        if obj_match is None:

            # 当前历史文本无法稳定拆成四列结构。
            return None

        # 返回规范化后的日期、版本、修订人和内容四元组。
        return (
            obj_match.group("date"),
            self._normalize_header_version_text(obj_match.group("version")),
            obj_match.group("reviser"),
            obj_match.group("contents").strip(),
        )

    # 在历史记录缺失时生成 Erie 模板默认的首条修订记录。
    def _default_header_history_line(
        self,
        date_text: str,
        version: str,
        reviser: str,
        *,
        language: str,
    ) -> str:
        """
        生成默认的文件头历史记录行。

        参数:
            date_text: 历史记录日期文本。
            version: 历史记录版本号。
            reviser: 历史记录修订人。
            language: 历史记录语言标记。
        返回:
            str: Erie 模板默认的单条修订历史记录文本。
        """

        # 默认历史记录在英文里写 Create file.，中文里写 创建文件。
        str_contents = "Create file." if language == "en" else "创建文件"  # 默认修订内容文本

        # 默认历史记录统一走新的空格列宽格式。
        return self._format_history_table_row(
            date_text,
            version,
            reviser,
            str_contents,
            language=language,
        )

    # 把通用或 Revision 风格的历史行转换成英中文历史表可用文本。
    def _render_history_fallback_line(
        self,
        line: str,
        *,
        language: str, default_version: str,
        reviser: str,
        fallback_date_en: str, fallback_date_cn: str,
    ) -> str | None:
        """
        把历史自由文本渲染成英中文历史表中的一条记录。

        参数:
            line: 原始历史文本行。
            language: 当前目标历史表的语言标记。
            default_version: 缺失版本号时的回退版本。
            reviser: 缺失修订人时使用的回退修订人。
            fallback_date_en: 英文历史表缺省日期。
            fallback_date_cn: 中文历史表缺省日期。
        返回:
            str | None: 成功转换时返回历史记录文本；不适用于当前语言时返回 None。
        """

        # 先去掉两端空白，避免把纯空行错误转换成历史记录。
        str_stripped_line = line.strip()  # 去空白后的历史文本

        # 空历史行没有有效内容，不应写入英文或中文历史表。
        if not str_stripped_line:

            # 空历史文本直接返回 None。
            return None

        # 优先识别 Revision x.y - description 这种可自动补齐日期的历史格式。
        match_revision = re.match(r"Revision\s+([0-9.]+)\s*-\s*(.+)$", str_stripped_line, re.IGNORECASE)  # 历史文本中的 Revision 结构

        # Revision 风格文本会显式提供版本号和修订描述。
        if match_revision:

            # 优先使用 Revision 行自己的版本号，没有时回退到当前版本。
            str_history_version = self._normalize_header_version_text(match_revision.group(1)) or default_version  # 历史记录版本号

            # 先提取 Revision 描述文本，后续再按 Create file 特例做双语映射。
            str_revision_tail = match_revision.group(2).strip()  # Revision 描述正文

            # 英文修订描述只在 “file created” 特例时替换成模板文本。
            str_contents_en = "Create file." if str_revision_tail.lower() == "file created" else str_revision_tail  # 英文修订内容

            # 中文修订描述同样在 “file created” 特例时转换成 创建文件。
            str_contents_cn = "创建文件" if str_revision_tail.lower() == "file created" else str_revision_tail  # 中文修订内容

            # 目标历史表是英文时，使用英文日期与英文修订内容。
            if language == "en":

                # Revision 风格统一按新的空格列宽格式输出。
                return self._format_history_table_row(
                    fallback_date_en[:10],
                    str_history_version,
                    reviser,
                    str_contents_en,
                    language="en",
                )

            # 中文历史表使用中文日期文本与中文修订内容，并统一成空格列宽。
            return self._format_history_table_row(
                fallback_date_cn,
                str_history_version,
                reviser,
                str_contents_cn,
                language="cn",
            )

        # 非 Revision 风格文本需要先判断它是否适合当前语言历史表。
        bool_contains_cjk = self._history_line_contains_cjk(str_stripped_line)  # 当前历史文本是否包含中文字符

        # 英文历史表应跳过明显带中文的历史记录。
        if language == "en" and bool_contains_cjk:

            # 中文历史记录不应被混进英文历史表。
            return None

        # 中文历史表应跳过明显纯英文的历史记录。
        if language == "cn" and not bool_contains_cjk:

            # 英文历史记录不应被混进中文历史表。
            return None

        # 已具备结构化日期/版本/修订人/内容的记录需统一归一成新的空格列宽。
        tuple_structured_history_row = self._parse_structured_history_row(  # 自由文本里可识别的结构化历史记录
            str_stripped_line,  # 当前待解析的历史正文
            language=language,  # 当前目标历史表语言
        )

        # 命中结构化历史记录时，统一按新模板列宽输出。
        if tuple_structured_history_row is not None:

            # 解包四元组后走统一的空格列宽格式化。
            return self._format_history_table_row(
                tuple_structured_history_row[0],
                tuple_structured_history_row[1] or default_version,
                tuple_structured_history_row[2] or reviser,
                tuple_structured_history_row[3],
                language=language,
            )

        # 通过语言过滤但无法结构化拆列的自由文本按原样保留。
        return str_stripped_line

    # 把通用历史列表转换成某种语言可直接写入头部的历史记录列表。
    def _collect_rendered_history_lines(
        self,
        history_lines: list[str],
        *,
        language: str, default_version: str,
        reviser: str,
        fallback_date_en: str, fallback_date_cn: str,
    ) -> list[str]:
        """
        把通用历史自由文本转换成目标语言的头部历史记录列表。

        参数:
            history_lines: 通用历史自由文本列表。
            language: 当前目标历史表语言。
            default_version: 缺失版本号时的回退版本。
            reviser: 缺失修订人时使用的回退修订人。
            fallback_date_en: 英文历史表缺省日期。
            fallback_date_cn: 中文历史表缺省日期。
        返回:
            list[str]: 适用于目标语言的历史记录文本列表。
        """

        # 当前语言的回退历史需要写入新的工作列表，避免修改原始 metadata。
        list_rendered_lines: list[str] = []  # 当前语言回退生成的历史记录

        # 逐条尝试把通用历史文本转换成目标语言的模板记录。
        for str_history_line in history_lines:

            # 每条原始历史文本都交给统一的 fallback renderer 做语言和版本修补。
            str_rendered_line = self._render_history_fallback_line(  # 当前历史文本转换后的模板行
                str_history_line,  # 当前待转换的通用历史文本
                language=language,  # 目标历史表语言
                default_version=default_version,  # 缺失版本号时的回退版本
                reviser=reviser,  # 缺失修订人时的回退修订人
                fallback_date_en=fallback_date_en, fallback_date_cn=fallback_date_cn,  # 英中文缺省日期
            )

            # 只有成功转换成目标语言历史行时，才把它收进结果列表。
            if str_rendered_line:

                # 保留转换成功的历史行，维持原始出现顺序。
                list_rendered_lines.append(str_rendered_line)

        # 返回当前语言可直接写回头部的历史行列表。
        return list_rendered_lines

    # 生成英中文两个历史表最终应写入的修订记录列表。
    def _build_header_history_lines(
        self,
        metadata: HeaderMetadata,
        version: str,
        reviser_en: str, reviser_cn: str,
        create_date_en: str, create_date_cn: str,
        now: datetime,
    ) -> tuple[list[str], list[str]]:
        """
        生成文件头英文与中文历史表的最终记录列表。

        参数:
            metadata: 已解析出的文件头元数据。
            version: 当前头部版本号。
            reviser_en: 英文修订人。
            reviser_cn: 中文修订人。
            create_date_en: 英文创建日期。
            create_date_cn: 中文创建日期。
            now: 本轮文件头渲染时间。
        返回:
            tuple[list[str], list[str]]: 英文历史记录列表和中文历史记录列表。
        """

        # 英文历史表缺省日期优先用创建日期，否则回退到当前渲染时间。
        str_fallback_date_en = create_date_en or now.strftime("%Y/%m/%d %H:%M:%S")  # 英文历史表回退日期

        # 中文历史表在缺少显式日期时，要回退到中文样式的当前渲染日期。
        str_fallback_date_cn = create_date_cn or now.strftime("%Y年%m月%d日")  # 中文历史表回退日期

        # 先复制英文显式历史，确保后续回退逻辑只修改工作副本。
        list_history_lines_en = list(metadata.history_lines_en)  # 英文历史记录工作副本

        # 先复制中文显式历史，避免中文回退流程污染原始 metadata。
        list_history_lines_cn = list(metadata.history_lines_cn)  # 中文历史记录工作副本

        # 英文显式历史缺失时，尝试从通用历史列表中过滤并渲染英文记录。
        if not list_history_lines_en:

            # 通用历史文本会被批量转换成英文历史表可接受的模板行。
            list_history_lines_en = self._collect_rendered_history_lines(  # 英文历史记录回退列表
                metadata.history_lines,  # 旧头部尚未分语种的历史文本
                language="en",  # 这批记录最终要回填到英文历史表
                default_version=version,  # Revision 缺版本号时沿用当前版本
                reviser=reviser_en,  # 英文历史缺修订人时沿用英文修订人
                fallback_date_en=str_fallback_date_en, fallback_date_cn=str_fallback_date_cn,  # 英文回退日期与跨语种转换日期
            )

        # 中文显式历史仍为空时，再从通用历史里筛出可落到中文栏位的记录。
        if not list_history_lines_cn:

            # 这里只回填中文栏位能接受的历史文本，不保留英文轨迹。
            list_history_lines_cn = self._collect_rendered_history_lines(  # 中文历史记录回退列表
                metadata.history_lines,  # 旧头部里尚未按语言拆开的历史文本
                language="cn",  # 这些回退结果只写入中文修订栏
                default_version=version,  # 中文表缺版本号时沿用当前头部版本
                reviser=reviser_cn,  # 中文历史缺修订人时沿用中文修订人
                fallback_date_en=str_fallback_date_en, fallback_date_cn=str_fallback_date_cn,  # 中文回填既要保留中文日期也要兼顾跨语种转换
            )

        # 英文历史仍为空时，补一条 Erie 模板默认的英文创建记录。
        if not list_history_lines_en:

            # 默认英文历史记录使用创建日期前十位和当前版本号。
            list_history_lines_en = [
                self._default_header_history_line(  # 默认英文创建记录
                    str_fallback_date_en[:10],  # 英文默认记录只保留日期列
                    version,  # 默认英文创建记录沿用当前版本号
                    reviser_en,  # 默认英文创建记录沿用英文修订人
                    language="en",  # 生成英文模板行
                )
            ]

        # 中文历史仍为空时，补一条 Erie 模板默认的中文创建记录。
        if not list_history_lines_cn:

            # 默认中文历史记录使用完整中文日期文本和当前版本号。
            list_history_lines_cn = [
                self._default_header_history_line(  # 默认中文创建记录
                    str_fallback_date_cn,  # 中文默认记录保留完整中文日期
                    version,  # 中文兜底创建记录同样沿用当前头部版本
                    reviser_cn,  # 默认中文创建记录沿用中文修订人
                    language="cn",  # 生成中文模板行
                )
            ]

        # 返回可直接写入双语文件头的英中文修订历史列表。
        return list_history_lines_en, list_history_lines_cn
