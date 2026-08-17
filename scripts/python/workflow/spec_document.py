"""验证 Verilog 模块规格并生成 WaveDrom 伴随交付物。

规格 JSON 是模块合同的唯一输入；本模块不会从 RTL 猜测行为。公共入口先
返回可序列化诊断，再由 ``write_spec_bundle`` 原子写出 ``*_spec.md``、
WaveJSON 与 SVG 文件。
"""

# 延迟解析联合类型，避免导入阶段引入非标准依赖。
from __future__ import annotations

# 标准库提供 JSON、正则、临时目录、路径和类型协议。
import json
import re
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence

# 所有外部 wavedrom 调用集中在受控 runtime，避免 workflow 自行拼接命令。
from scripts.python.toolchain.wavedrom_runtime import render_waveform

# 公共 IO 入口独立承载便捷读取和语义化渲染别名。
from scripts.python.workflow.spec_document_io import read_spec_document, render_spec_bundle

# 模块头正则同时覆盖 ANSI 与旧式端口声明。
MODULE_SOURCE_PATTERN = r"\bmodule\s+(?P<name>[A-Za-z_]\w*)\s*(?:#\s*\(.*?\))?\s*\((?P<ports>.*?)\)\s*;"  # 模块头文本规则

# 编译模块头文本，供每个源文件复用同一解析规则。
MODULE_PATTERN = re.compile(MODULE_SOURCE_PATTERN, re.IGNORECASE | re.DOTALL)  # 模块头解析表达式

# 非 ANSI 端口声明需要额外扫描 module 体。
PORT_SOURCE_PATTERN = r"\b(?:input|output|inout)\b(?P<body>[^;]+);"  # 端口声明文本规则

# 编译非 ANSI 声明规则，避免扫描逻辑重复构造正则。
PORT_DECL_PATTERN = re.compile(PORT_SOURCE_PATTERN, re.IGNORECASE)  # 端口声明提取表达式

# 统一提取标识符，过滤宽度表达式和类型关键词。
IDENTIFIER_PATTERN = re.compile(r"\b[A-Za-z_]\w*\b")  # Verilog 标识符边界

# endmodule 的独立正则避免把协议单词散落在扫描逻辑中。
ENDMODULE_TOKEN = "endmodule"  # 模块结束关键字

# 结束标记只需匹配关键字本身，调用方以 module 起点限制扫描范围。
ENDMODULE_PATTERN = re.compile(ENDMODULE_TOKEN, re.IGNORECASE)  # 模块结束标记

# 路径片段黑名单阻断目录回退和空段。
UNSAFE_PATH_PARTS = {"", ".", ".."}  # 不可进入交付路径的片段

# 这些词不会被识别为非 ANSI 端口名称。
PORT_KEYWORDS = {"input", "output", "inout", "wire", "reg", "logic", "signed", "unsigned"}  # Verilog 端口关键词

# 领域异常标记规格层可预期的失败边界。
class SpecDocumentError(ValueError):
    """表示模块规格缺失、冲突或无法发布。"""

# 文本字段 helper 统一空值检查和错误消息协议。
def _required_text(value: Any, field_path: str) -> str:
    """校验非空文本字段。

    参数:
        value: JSON 输入字段的原始值。
        field_path: 诊断中展示的字段路径。

    返回:
        去除首尾空白后的字段文本。

    异常:
        SpecDocumentError: 字段不是非空字符串时抛出。
    """

    # 空文本无法成为可追溯合同字段。
    if not isinstance(value, str) or not value.strip():

        # 失败消息保留字段路径，方便 CLI 定位输入。
        raise SpecDocumentError("> ERR: [Python] {} must be a non-empty string.".format(field_path))

    # 统一清洗外围空格，避免 Markdown 产生隐形差异。
    str_text = value.strip()  # 字段规范化文本

    # 将清洗结果交给后续路径或表格渲染。
    return str_text

# 文本列表 helper 统一行为、约束和验证字段的输入形态。
def _text_list(value: Any, field_path: str, *, required: bool = True) -> list[str]:
    """把说明字段归一化为非空文本列表。

    参数:
        value: 字符串、字符串列表或缺省值。
        field_path: 诊断所用字段路径。
        required: 是否至少保留一条有效说明。

    返回:
        去除空项后的文本列表。

    异常:
        SpecDocumentError: 输入类型或必需内容不满足合同。
    """

    # 单字符串是历史输入格式，转换后与列表保持相同语义。
    if isinstance(value, str):

        # 包装单项时保留用户原始说明顺序。
        list_values: list[Any] = [value]  # 单项说明容器

    # 列表格式是新的多条说明合同。
    elif isinstance(value, list):

        # 复制列表引用，后续清洗不会修改调用方对象。
        list_values = list(value)  # 多项说明副本

    # 其他 JSON 类型不能被安全解释成说明。
    else:

        # 字段路径用于给出稳定、可机器读取的错误。
        raise SpecDocumentError("> ERR: [Python] {} must be a string or list.".format(field_path))

    # 仅保留非空字符串，拒绝把数字隐式转成合同文本。
    list_result = [item.strip() for item in list_values if isinstance(item, str) and item.strip()]  # 清洗后的说明

    # 必需字段为空时应在生成前失败闭合。
    if required and not list_result:

        # 空说明无法支撑接口审查或验收追溯。
        raise SpecDocumentError("> ERR: [Python] {} must contain at least one item.".format(field_path))

    # 返回按输入顺序保留的说明条目。
    return list_result

# 路径 helper 在生成目录前阻断绝对路径和目录穿越。
def _safe_rtl_path(value: Any, field_path: str) -> str:
    """检查 RTL 相对路径并统一为 POSIX 形式。

    参数:
        value: 规格中的 rtl_path 原值。
        field_path: 错误诊断所用字段路径。

    返回:
        工作区内、以 ``.v`` 结尾的相对路径。

    异常:
        SpecDocumentError: 路径绝对、越界或后缀错误时抛出。
    """

    # 先确认类型，再统一 Windows 和 Linux 的分隔符。
    str_path = _required_text(value, field_path).replace("\\", "/")  # RTL 规范路径

    # PurePosixPath 让远程主机不受本地路径语法影响。
    path_posix = PurePosixPath(str_path)  # POSIX 路径对象

    # 绝对路径、驱动器片段和目录回退都会越过交付根。
    bool_unsafe = (  # 绝对路径、驱动器和回退片段的综合判定
        path_posix.is_absolute()  # 绝对路径标记
        or not path_posix.parts  # 空路径片段标记
        or ":" in path_posix.parts[0]  # 驱动器片段标记
        or any(part in UNSAFE_PATH_PARTS for part in path_posix.parts)  # 回退片段标记
    )  # 路径越界判定

    # 不安全路径必须在任何输出目录计算前阻断。
    if bool_unsafe:

        # 错误包含原字段位置而不暴露内部异常堆栈。
        raise SpecDocumentError("> ERR: [Python] {} must be a safe relative path.".format(field_path))

    # 规格只允许 Verilog-2001 源文件作为模块映射目标。
    if path_posix.suffix.lower() != ".v":

        # 其他扩展名无法和 Verilog 接口事实进行交叉核验。
        raise SpecDocumentError("> ERR: [Python] {} must end with .v.".format(field_path))

    # 所有产物链接使用统一的 POSIX 相对文本。
    str_normalized_path = path_posix.as_posix()  # 跨平台 RTL 路径

    # 返回稳定路径，避免调用方重复实现清洗规则。
    return str_normalized_path

# 端口 helper 保证 Markdown 接口表拥有完整语义字段。
def _normalize_port(value: Any, field_path: str) -> dict[str, Any]:
    """归一化端口合同并保留接口语义字段。

    参数:
        value: 单个端口对象。
        field_path: 端口所在规格路径。

    返回:
        含名称、方向、位宽、角色和描述的端口字典。

    异常:
        SpecDocumentError: 端口字段不完整或取值非法时抛出。
    """

    # 端口必须是对象，才能表达完整接口事实。
    if not isinstance(value, Mapping):

        # 非对象端口不能生成可审查的接口表行。
        raise SpecDocumentError("> ERR: [Python] {} must be an object.".format(field_path))

    # 名称和方向是 RTL 声明与 Markdown 表格的连接键。
    str_name = _required_text(value.get("name"), "{}.name".format(field_path))  # 端口名称

    # 方向字段独立记录，便于后续错误定位。
    str_direction = _required_text(value.get("direction"), "{}.direction".format(field_path)).lower()  # 端口方向

    # Verilog module 仅公开三种方向关键字。
    if str_direction not in {"input", "output", "inout"}:

        # 非法方向会让源接口交叉核验失去确定性。
        raise SpecDocumentError("> ERR: [Python] {}.direction is invalid.".format(field_path))

    # 位宽可以是正整数或带参数名的 Verilog 表达式。
    width_value = value.get("width")  # 端口原始位宽

    # 数值宽度保留整数形式，便于 Markdown 直接展示。
    if isinstance(width_value, int) and width_value > 0:

        # 正整数已经满足最小端口合同。
        normalized_width: int | str = width_value  # 整数位宽

    # 表达式宽度保留用户的参数化语义。
    elif isinstance(width_value, str) and width_value.strip():

        # 只清洗外围空格，不改写 Verilog 表达式本身。
        normalized_width = width_value.strip()  # 参数化位宽

    # 缺失或非正宽度无法形成合法接口声明。
    else:

        # 失败路径明确指向当前端口的 width 字段。
        raise SpecDocumentError("> ERR: [Python] {}.width must be positive.".format(field_path))

    # 角色和描述防止文档只记录信号名而丢失协议语义。
    str_role = _required_text(value.get("role", value.get("semantic_role")), "{}.role".format(field_path))  # 端口语义角色

    # 描述列承载协议事件或电气含义，必须独立保留。
    str_description = _required_text(value.get("description"), "{}.description".format(field_path))  # 端口行为描述

    # 时钟域是可选信息，但显式给出时必须是非空文本。
    clock_domain_value = value.get("clock_domain")  # 原始时钟域

    # 空值表示规格没有确认该端口的时钟域。
    if clock_domain_value is None:

        # 用空值表达未声明，而不是推测默认时钟。
        str_clock_domain: str | None = None  # 未声明时钟域

    # 非空时钟域进入统一文本校验。
    else:

        # 保留用户确认的时钟域名称用于接口表。
        str_clock_domain = _required_text(clock_domain_value, "{}.clock_domain".format(field_path))  # 显式时钟域

    # 只投影文档渲染需要的字段，避免把任意对象写入产物。
    dict_port: dict[str, Any] = {  # 规范化端口记录
        "name": str_name,  # 端口名称字段
        "direction": str_direction,  # 方向字段
        "width": normalized_width,  # 位宽字段
        "role": str_role,  # 语义角色字段
        "description": str_description,  # 描述字段
        "clock_domain": str_clock_domain,  # 时钟域字段
        "active_level": value.get("active_level"),  # 可选有效电平
    }

    # 返回稳定字段顺序，保证 JSON 和 Markdown 具有可重复性。
    return dict_port

# 时序图 helper 在调用 WaveDrom 前确认信号元数据完整。
def _normalize_diagram(value: Any, field_path: str) -> dict[str, Any]:
    """校验一张 WaveDrom 时序图合同。

    参数:
        value: timing_diagrams 中的单个图对象。
        field_path: 图对象所在规格路径。

    返回:
        含图元数据和 WaveJSON 的归一化字典。

    异常:
        SpecDocumentError: 图字段或 signal 数据缺失时抛出。
    """

    # 图对象必须提供可追踪的元数据。
    if not isinstance(value, Mapping):

        # 无法从非对象值生成 SVG 替代文本和链接。
        raise SpecDocumentError("> ERR: [Python] {} must be an object.".format(field_path))

    # id、标题、场景和描述共同构成图的审查锚点。
    str_id = _required_text(value.get("id"), "{}.id".format(field_path))  # 时序图标识

    # 标题文本会同时成为 Markdown 章节和图片替代文本。
    str_title = _required_text(value.get("title"), "{}.title".format(field_path))  # 时序图标题

    # 场景名称参与文件 slug，需保留用户确认的协议语义。
    str_scenario = _required_text(value.get("scenario", value.get("kind")), "{}.scenario".format(field_path))  # 时序场景名称

    # 图描述用于解释 signal 状态变化而不是装饰性标题。
    str_description = _required_text(value.get("description"), "{}.description".format(field_path))  # 时序图说明

    # wavejson 是正式字段，wave 仅作为历史输入别名。
    wavejson_value = value.get("wavejson", value.get("wave"))  # 读取正式 wavejson 字段并兼容历史 wave 别名

    # signal 列表是 WaveDrom 能够绘图的最低合同。
    bool_signal_list_ok = (  # WaveDrom signal 数组是否满足最小绘图合同
        isinstance(wavejson_value, Mapping)  # WaveJSON 对象判定
        and isinstance(wavejson_value.get("signal"), list)  # signal 容器判定
        and bool(wavejson_value["signal"])  # signal 非空判定
    )  # signal 列表判定

    # 空图会生成无法解释的 Markdown 图片。
    if not bool_signal_list_ok:

        # 错误指向具体图对象，便于编辑原始 JSON。
        raise SpecDocumentError("> ERR: [Python] {}.wavejson.signal must be a non-empty list.".format(field_path))

    # 逐信号确认名称和 wave 字符串都存在。
    for int_signal_index, signal_value in enumerate(wavejson_value["signal"]):

        # 每条信号都必须是可读的对象记录。
        bool_name_ok = (  # 当前 signal 是否含可追溯名称
            isinstance(signal_value, Mapping)  # 先确认当前条目能够承载信号字段
            and isinstance(signal_value.get("name"), str)  # 再确认 name 可作为可追溯文本
            and bool(signal_value["name"].strip())  # 最后拒绝只有空白字符的信号名
        )  # 信号名称判定

        # 匿名信号无法在接口规范中追溯。
        if not bool_name_ok:

            # 使用索引定位 WaveJSON 中的具体条目。
            raise SpecDocumentError(
                "> ERR: [Python] {}.wavejson.signal[{}].name is required.".format(
                    field_path,
                    int_signal_index,
                )
            )

        # wave 字符串必须非空，否则 WaveDrom 没有时序状态。
        bool_wave_ok = (  # 当前 signal 是否含非空 wave 状态
            isinstance(signal_value.get("wave"), str)  # 检查 wave 是否保持 WaveDrom 文本类型
            and bool(signal_value["wave"].strip())  # 检查波形状态是否包含有效字符
        )  # 波形字符串判定

        # 缺少波形状态时阻断外部渲染器调用。
        if not bool_wave_ok:

            # 将错误精确到 signal 数组索引和 wave 字段。
            raise SpecDocumentError(
                "> ERR: [Python] {}.wavejson.signal[{}].wave is required.".format(
                    field_path, int_signal_index
                )
            )

    # 复制 WaveJSON，防止下游修改用户输入对象。
    dict_wavejson = dict(wavejson_value)  # 复制顶层 WaveJSON，隔离调用方后续修改

    # 只复制 signal 数组，避免共享嵌套容器的可变引用。
    dict_wavejson["signal"] = [dict(signal) for signal in wavejson_value["signal"]]  # 信号记录副本

    # 规范化图对象只保留文档合同字段。
    dict_diagram: dict[str, Any] = {  # 时序图合同
        "id": str_id,  # 图标识字段
        "title": str_title,  # 图标题字段
        "scenario": str_scenario,  # 图场景字段
        "description": str_description,  # 图描述字段
        "wavejson": dict_wavejson,  # WaveDrom 输入字段
    }

    # 返回已确认 signal 结构的图记录。
    return dict_diagram

# 参数 helper 只负责容器归一化，不猜测参数语义。
def _normalize_parameters(value: Any, field_path: str) -> list[Any]:
    """将参数字典或列表转换成 Markdown 参数条目。

    参数:
        value: 参数字典、参数列表或 None。
        field_path: 参数字段诊断路径。

    返回:
        参数条目列表；None 转换为空列表。

    异常:
        SpecDocumentError: 参数容器不是字典或列表时抛出。
    """

    # 缺省参数表示模块没有可配置项。
    if value is None:

        # 返回新列表，防止默认容器跨调用共享。
        return []

    # 字典快捷写法按插入顺序展开默认值。
    if isinstance(value, Mapping):

        # 每个键生成一行可渲染参数记录。
        list_parameters = [{"name": key, "default": item, "description": ""} for key, item in value.items()]  # 参数条目列表

        # 字典输入的顺序就是用户确认的展示顺序。
        return list_parameters

    # 列表输入保留扩展字段，交由 Markdown 层按已知键读取。
    if isinstance(value, list):

        # 复制容器但不猜测用户自定义字段。
        list_parameters = list(value)  # 参数列表副本

        # 返回结构化参数条目。
        return list_parameters

    # 标量值既没有名称也没有默认值语义。
    raise SpecDocumentError("> ERR: [Python] {} must be a list or object.".format(field_path))

# 模块 helper 组合端口、行为、约束和波形的必需字段。
def _normalize_module(module_value: Any, int_index: int) -> dict[str, Any]:
    """校验单个模块的接口、行为和波形合同。

    参数:
        module_value: modules 数组中的原始对象。
        int_index: 模块在数组中的零基索引。

    返回:
        可供文档和渲染流程消费的模块字典。

    异常:
        SpecDocumentError: 任一必需字段缺失或重复时抛出。
    """

    # 索引前缀贯穿模块内所有诊断，避免报告失去定位信息。
    str_prefix = "modules[{}]".format(int_index)  # 当前模块字段前缀

    # 模块必须是 JSON object。
    if not isinstance(module_value, Mapping):

        # 非对象无法提供 name、rtl_path 和接口结构。
        raise SpecDocumentError("> ERR: [Python] {} must be an object.".format(str_prefix))

    # 名称是 RTL 文件、Markdown 和波形文件的共同命名锚点。
    str_name = _required_text(module_value.get("name"), "{}.name".format(str_prefix))  # 模块名称

    # RTL 路径是源交叉核验和产物布局的第二连接键。
    str_rtl_path = _safe_rtl_path(module_value.get("rtl_path"), "{}.rtl_path".format(str_prefix))  # 统一后的 RTL 路径用于源文件交叉核验

    # 接口必须含非空 ports，空模块没有严谨的信号规范。
    interfaces_value = module_value.get("interfaces")  # 接口原始对象

    # 端口列表状态在归一化前单独计算，便于 fail-closed。
    bool_ports_ok = (  # interfaces.ports 是否为非空列表
        isinstance(interfaces_value, Mapping)  # 确认接口字段是可读取的映射对象
        and isinstance(interfaces_value.get("ports"), list)  # 确认 ports 使用有序列表表达端口
        and bool(interfaces_value["ports"])  # 确认接口至少声明一个可审查端口
    )  # 端口列表判定

    # 缺少接口端口时提前阻断 Markdown 生成。
    if not bool_ports_ok:

        # 错误明确指出 ports 结构而不是泛化为模块无效。
        raise SpecDocumentError("> ERR: [Python] {}.interfaces.ports must be non-empty.".format(str_prefix))

    # 端口归一化后再检测重复名称。
    list_ports = [  # 归一化模块端口清单
        _normalize_port(item, "{}.interfaces.ports[{}]".format(str_prefix, index))  # 当前端口归一化
        for index, item in enumerate(interfaces_value["ports"])  # 端口顺序遍历
    ]  # 规范端口列表

    # 只比较名称集合，保留端口输入的原始顺序用于渲染。
    set_port_names = {port["name"] for port in list_ports}  # 端口名称集合

    # 重复名称会让接口表和 RTL 交叉核验产生歧义。
    if len(set_port_names) != len(list_ports):

        # 当前模块是重复端口的最小诊断范围。
        raise SpecDocumentError("> ERR: [Python] {}.interfaces.ports contains duplicate names.".format(str_prefix))

    # timing_diagrams 是每个模块必须拥有的 WaveDrom 证据集合。
    diagrams_value = module_value.get("timing_diagrams")  # 时序图原始列表

    # 空图列表会违反 spec-first 波形引用合同。
    if not isinstance(diagrams_value, list) or not diagrams_value:

        # 不允许生成没有接口波形说明的 *_spec.md。
        raise SpecDocumentError("> ERR: [Python] {}.timing_diagrams must be non-empty.".format(str_prefix))

    # 每张图单独校验元数据和信号状态。
    list_diagrams = [  # 归一化模块 WaveDrom 图清单
        _normalize_diagram(item, "{}.timing_diagrams[{}]".format(str_prefix, index))  # 当前图归一化
        for index, item in enumerate(diagrams_value)  # 图顺序遍历
    ]  # 规范时序图列表

    # 行为和约束字段必须留下可审计文本。
    list_behavior = _text_list(module_value.get("behavior"), "{}.behavior".format(str_prefix))  # 周期行为说明

    # 综合约束独立存放，便于文档审查与后续门禁复用。
    list_constraints = _text_list(module_value.get("constraints"), "{}.constraints".format(str_prefix))  # 综合约束说明

    # 边界列表描述反压、溢出和非法序列等风险场景。
    list_corner_cases = _text_list(module_value.get("corner_cases"), "{}.corner_cases".format(str_prefix))  # 边界场景说明

    # 验证列表是文档交付后的验收索引。
    list_verification = _text_list(module_value.get("verification_cases"), "{}.verification_cases".format(str_prefix))  # 验证验收说明

    # 参数允许为空，但容器类型仍然必须明确。
    list_parameters = _normalize_parameters(module_value.get("parameters", []), "{}.parameters".format(str_prefix))  # 参数条目

    # 时钟与复位未确认时使用空对象，不推断设计语义。
    clock_value = module_value.get("clock", {})  # 时钟对象

    # 复位对象单独保留，避免把时钟配置误当作复位策略。
    reset_value = module_value.get("reset", {})  # 复位对象

    # 非对象时无法安全序列化时序配置。
    if not isinstance(clock_value, Mapping) or not isinstance(reset_value, Mapping):

        # 同时指出两个字段的公共类型要求。
        raise SpecDocumentError("> ERR: [Python] {}.clock/reset must be objects.".format(str_prefix))

    # 复制顶层扩展后覆写所有下游依赖的规范字段。
    dict_module = dict(module_value)  # 模块扩展副本

    # 接口扩展字段保留，但 ports 必须采用归一化列表。
    dict_interfaces = dict(interfaces_value)  # 接口扩展副本

    # 归一化端口列表成为接口表和源核验的唯一来源。
    dict_interfaces["ports"] = list_ports  # 归一化端口集合

    # 名称覆盖用户输入，确保输出命名使用清洗后的文本。
    dict_module["name"] = str_name  # 规范模块名称

    # RTL 路径覆盖用户输入，确保跨平台格式一致。
    dict_module["rtl_path"] = str_rtl_path  # 将清洗后的 RTL 相对路径写回模块以保持产物可追溯

    # 统一写回接口、行为、约束和波形字段。
    dict_module["interfaces"] = dict_interfaces  # 规范接口对象

    # 周期行为使用已经清洗的非空说明列表。
    dict_module["behavior"] = list_behavior  # 规范行为列表

    # 综合约束使用已经清洗的非空说明列表。
    dict_module["constraints"] = list_constraints  # 规范约束列表

    # 边界场景保持输入顺序供 Markdown 验收章节展示。
    dict_module["corner_cases"] = list_corner_cases  # 规范边界列表

    # 验证案例保持输入顺序供测试计划追踪。
    dict_module["verification_cases"] = list_verification  # 规范验证列表

    # timing_diagrams 是每个模块的 WaveDrom 证据清单。
    dict_module["timing_diagrams"] = list_diagrams  # 规范波形列表

    # 参数表使用结构化列表，便于 Markdown 稳定渲染。
    dict_module["parameters"] = list_parameters  # 规范参数列表

    # 时钟与复位拷贝后独立于原始输入对象。
    dict_module["clock"] = dict(clock_value)  # 规范时钟对象

    # 复位配置也复制为独立字典。
    dict_module["reset"] = dict(reset_value)  # 规范复位对象

    # 返回已经满足必需字段的规范模块。
    return dict_module

# 顶层归一化 helper 维护模块名和 RTL 路径的全局唯一性。
def normalize_spec_document(raw: Mapping[str, Any]) -> dict[str, Any]:
    """归一化单模块或多模块规格文档。

    参数:
        raw: 单模块对象或包含 modules 列表的对象。

    返回:
        含规范化 modules 数组的文档对象。

    异常:
        SpecDocumentError: 根节点、模块或路径合同无效时抛出。
    """

    # 根节点必须是对象，防止列表被误认为模块集合。
    if not isinstance(raw, Mapping):

        # 不明确的根结构无法建立可审计模块清单。
        raise SpecDocumentError("> ERR: [Python] spec document root must be an object.")

    # 兼容旧单模块输入，同时优先使用新 modules 数组。
    raw_modules = raw.get("modules")  # 原始模块容器

    # 没有 modules 时仅在 name 存在的情况下包装顶层对象。
    if raw_modules is None:

        # 缺失 name 的空文档应当明确失败。
        list_raw_modules = [dict(raw)] if raw.get("name") is not None else []  # 兼容单模块列表

    # 新格式保留用户输入顺序。
    elif isinstance(raw_modules, list):

        # 复制列表以隔离后续归一化操作。
        list_raw_modules = list(raw_modules)  # 多模块输入列表

    # 其他类型无法提供模块清单。
    else:

        # 错误字段与 JSON 键保持一致。
        raise SpecDocumentError("> ERR: [Python] spec.modules must be a list.")

    # 至少一个模块是发布规范包的必要条件。
    if not list_raw_modules:

        # 空包不能产生任何模块级 Markdown。
        raise SpecDocumentError("> ERR: [Python] spec.modules must contain at least one module.")

    # 逐项归一化并检查模块名、RTL 路径的全局唯一性。
    list_modules: list[dict[str, Any]] = []  # 规范模块序列

    # 名称集合用于阻断两个模块共享同一 Markdown 文件名。
    set_names: set[str] = set()  # 已使用模块名

    # 路径集合用于阻断两个模块共享同一 RTL 交叉核验目标。
    set_paths: set[str] = set()  # 已使用 RTL 路径

    # 保持输入顺序，确保文档 diff 可审查。
    for int_index, module_value in enumerate(list_raw_modules):

        # 当前模块的字段规则集中在 helper 内。
        dict_module = _normalize_module(module_value, int_index)  # 当前规范模块

        # 重复名称会覆盖同名 Markdown 和 SVG。
        if dict_module["name"] in set_names:

            # 模块索引让重复合同可以直接修复。
            raise SpecDocumentError("> ERR: [Python] duplicate module name at modules[{}].".format(int_index))

        # 重复 RTL 路径会导致不同模块共享交付目标。
        if dict_module["rtl_path"] in set_paths:

            # 路径冲突不能由渲染阶段静默解决。
            raise SpecDocumentError("> ERR: [Python] duplicate rtl_path at modules[{}].".format(int_index))

        # 登记名称和路径后再加入输出序列。
        set_names.add(dict_module["name"])  # 模块名占用登记

        # 记录路径占用状态，避免后续模块覆盖产物。
        set_paths.add(dict_module["rtl_path"])  # RTL 路径占用登记

        # 保留用户模块顺序，确保文档 diff 可审计。
        list_modules.append(dict_module)  # 规范模块加入清单

    # 顶层扩展字段保留，但 modules 成为唯一权威来源。
    dict_normalized = dict(raw)  # 顶层扩展副本

    # 模块列表覆盖任何旧版顶层投影，作为发布唯一来源。
    dict_normalized["modules"] = list_modules  # 归一化模块清单

    # 单模块输入继续暴露旧版顶层投影，保持已有调用方兼容。
    if len(list_modules) == 1:

        # 投影只复制字段，不改变 modules 的权威结构。
        dict_normalized.update(list_modules[0])

    # 返回可直接验证、渲染和序列化的规格文档。
    return dict_normalized

# 公共验证入口把领域异常转换成稳定机器可读报告。
def validate_spec_document(raw: Mapping[str, Any], *, source_paths: Sequence[Path] | None = None) -> dict[str, Any]:
    """返回规格结构和可选 RTL 交叉核验的机器可读报告。

    参数:
        raw: 单模块或多模块原始规格对象。
        source_paths: 可选 Verilog 文件路径序列。

    返回:
        含 ``ok``、``issues`` 和 ``spec`` 的报告字典；失败时 spec 为 None。
    """

    # 规范错误属于预期输入失败，应转换成稳定报告。
    try:

        # 归一化阶段只检查 JSON 合同，不读取 RTL。
        dict_spec = normalize_spec_document(raw)  # 规范化规格

        # 只有调用方显式传入 source_paths 时才核验源接口。
        list_source_issues = validate_verilog_sources(dict_spec, source_paths or []) if source_paths else []  # RTL 交叉核验结果

    # 统一捕获领域异常，避免 CLI 输出 traceback。
    except SpecDocumentError as exc:

        # 失败报告保留原始诊断并明确不可发布。
        dict_failure = {"ok": False, "issues": [str(exc)], "spec": None}  # 规格失败报告

        # 将失败对象交给 CLI 或 facade 序列化。
        return dict_failure

    # 成功报告保留规范对象，后续写出无需重复校验。
    dict_success = {"ok": not list_source_issues, "issues": list_source_issues, "spec": dict_spec}  # 规格成功报告

    # 返回可直接消费的状态对象。
    return dict_success

# RTL 提取入口只报告源文件中实际观察到的 module 和端口。
def extract_verilog_interfaces(source_text: str) -> dict[str, set[str]]:
    """提取 Verilog module 名称与公开端口集合。

    参数:
        source_text: Verilog-2001 源码文本。

    返回:
        module 名到端口名称集合的映射。
    """

    # 删除注释，防止示例文本伪装成接口事实。
    str_clean = re.sub(r"//[^\n]*|/\*.*?\*/", "", source_text, flags=re.DOTALL)  # 去注释源码

    # 建立模块到端口集合的累积映射。
    dict_interfaces: dict[str, set[str]] = {}  # 模块接口事实

    # module 头先提供 ANSI 端口候选。
    for match_module in MODULE_PATTERN.finditer(str_clean):

        # 模块名是源文件与 spec 的连接键。
        str_name = match_module.group("name")  # 从模块头捕获实际 RTL module 标识

        # 过滤头部中的类型关键词和宽度表达式标识符。
        set_ports = {  # 仅保留 ANSI module 头中可追溯到接口的标识符
            token  # 保存当前候选名称，供端口集合比较
            for token in IDENTIFIER_PATTERN.findall(match_module.group("ports"))  # 扫描模块头的标识符序列
            if token.lower() not in PORT_KEYWORDS  # 排除方向、类型和符号关键字
        }  # ANSI 端口集合

        # 从当前 module 头位置开始定位非 ANSI 声明。
        body_start = match_module.end()  # 模块体起始位置

        # 结束标记搜索限定在当前模块起点之后。
        endmodule_match = ENDMODULE_PATTERN.search(str_clean[body_start:])  # 模块结束位置

        # 找不到结束标记时只使用剩余文本，保持保守提取。
        if endmodule_match is None:

            # 不完整源码仍可用于报告已观察到的端口。
            str_body = str_clean[body_start:]  # 截断模块体

        # 完整 module 只扫描自己的声明区间。
        else:

            # 结束标记位置相对 body_start，需要恢复绝对切片。
            str_body = str_clean[body_start : body_start + endmodule_match.start()]  # 当前模块体

        # 非 ANSI 端口声明的最后一个标识符是信号名称。
        for match_decl in PORT_DECL_PATTERN.finditer(str_body):

            # 宽度表达式中的关键词已被过滤，只保留候选信号。
            list_tokens = [  # 非 ANSI 声明中的候选信号名
                token  # 当前声明标识符
                for token in IDENTIFIER_PATTERN.findall(match_decl.group("body"))  # 声明标识符遍历
                if token.lower() not in PORT_KEYWORDS  # 声明关键字过滤
            ]  # 声明候选名

            # 空声明段不贡献接口事实。
            if list_tokens:

                # Verilog 非 ANSI 声明按最后一个标识符命名信号。
                set_ports.add(list_tokens[-1])

        # 登记当前 module 的端口全集。
        dict_interfaces[str_name] = set_ports  # 模块端口事实

    # 返回接口事实供 spec 交叉核验。
    return dict_interfaces

# 源交叉核验入口确保 spec 与 RTL 接口集合一一对应。
def validate_verilog_sources(spec: Mapping[str, Any], source_paths: Sequence[Path]) -> list[str]:
    """严格比对规格模块与 Verilog 源文件的 module/port 集合。

    参数:
        spec: 已归一化或至少含 modules 列表的规格对象。
        source_paths: 待核验的 Verilog 文件路径。

    返回:
        空列表表示通过；否则返回带 ``> ERR`` 前缀的诊断列表。
    """

    # 空源列表表示调用方选择 spec-only 渲染路径。
    if not source_paths:

        # 没有源文件时不做反向推断。
        return []

    # 未归一化的 spec 无法提供稳定模块清单。
    list_modules = spec.get("modules") if isinstance(spec, Mapping) else None  # 规格模块列表

    # 交叉核验必须 fail-closed，而不是猜测缺失模块。
    if not isinstance(list_modules, list):

        # 返回列表让上层继续汇总其他诊断。
        return ["> ERR: [Python] spec.modules is required for source cross-check."]

    # 转换路径对象，统一本地和远程文件检查。
    list_paths = [Path(path) for path in source_paths]  # RTL 源路径列表

    # 汇总来自多个 RTL 文件的模块接口事实。
    dict_interfaces: dict[str, set[str]] = {}  # 汇总后的 RTL 接口

    # 逐文件读取并合并 module/port 事实。
    for path_source in list_paths:

        # 缺失源文件时立即阻断，避免部分读取造成假阳性。
        if not path_source.is_file():

            # 返回精确路径，便于用户修复 source_paths。
            return ["> ERR: [Python] Verilog source does not exist: {}".format(path_source)]

        # 读取结果合并到跨文件接口索引，重复 module 由后续集合检查发现。
        dict_interfaces.update(extract_verilog_interfaces(path_source.read_text(encoding="utf-8")))

    # 规格声明名称是额外 RTL module 检查的基线。
    set_expected_names = {str(item.get("name")) for item in list_modules if isinstance(item, Mapping)}  # 规格模块名称

    # 计算只出现在 RTL 中的模块名集合。
    set_extra_names = set(dict_interfaces) - set_expected_names  # RTL 额外模块集合

    # 每个额外模块都生成独立诊断行。
    list_issues = [  # RTL 额外模块诊断列表
        "> ERR: [Python] RTL module {} is not declared in spec.modules.".format(name)  # 当前额外模块诊断
        for name in sorted(set_extra_names)  # 额外模块顺序遍历
    ]  # 额外模块诊断

    # 每个规格模块必须在源中存在且端口集合完全一致。
    for module_value in list_modules:

        # 归一化流程通常已保证对象类型，此处保留公共函数的保护边界。
        if not isinstance(module_value, Mapping):

            # 无法读取名称的扩展条目直接跳过。
            continue

        # 当前模块名称连接两侧接口事实。
        str_name = str(module_value.get("name"))  # 读取规格模块名作为源文件匹配键

        # 缺失 module 时记录诊断并继续检查其他模块。
        if str_name not in dict_interfaces:

            # 源文件没有兑现该模块的 RTL 合同。
            list_issues.append("> ERR: [Python] Spec module {} is absent from RTL sources.".format(str_name))

            # 不存在模块没有端口集合可供比较。
            continue

        # 读取规格端口名称作为精确集合。
        list_port_values = module_value.get("interfaces", {}).get("ports", [])  # 规格端口条目

        # 过滤扩展条目后再进行精确集合比较。
        set_expected_ports = {str(item.get("name")) for item in list_port_values if isinstance(item, Mapping)}  # 规格端口集合

        # RTL 端口集合是当前模块的实际观察结果。
        set_actual_ports = dict_interfaces[str_name]  # 取得解析器观察到的 RTL 端口名集合

        # 将规格缺失端口转换为可审计的错误列表。
        list_issues.extend(
            [
                "> ERR: [Python] Spec port {}.{} is absent from RTL sources.".format(
                    str_name, port
                )
                for port in sorted(set_expected_ports - set_actual_ports)
            ]
        )

        # 追加源文件中未被规格声明的信号。
        list_issues.extend(
            [
                "> ERR: [Python] RTL port {}.{} is not declared in spec.".format(
                    str_name, port
                )
                for port in sorted(set_actual_ports - set_expected_ports)
            ]
        )

    # 返回完整诊断列表，调用方决定是否阻断发布。
    return list_issues

# 路径计算 helper 负责 feature 目录与模块 spec 文件命名。
def _module_paths(out_root: Path, module: Mapping[str, Any]) -> dict[str, Path]:
    """根据 rtl_path 和 feature 计算模块交付布局。

    参数:
        out_root: 暂存或目标工件根目录。
        module: 已归一化模块对象。

    返回:
        含 module_dir、wave_dir 和 spec_path 的路径字典。
    """

    # RTL 相对路径决定模块在 spec 下的层级。
    path_rtl = PurePosixPath(str(module["rtl_path"]))  # 按 POSIX 规则解析 RTL 层级，供跨平台布局使用

    # 列表形式方便后续移除约定的 rtl 根目录。
    list_parts = list(path_rtl.parts)  # 复制 RTL 层级片段，便于安全移除约定前缀

    # 约定的 rtl 根目录不重复出现在 spec 归档中。
    if list_parts and list_parts[0].lower() == "rtl":

        # 仅移除首片段，保留 feature/module 子目录。
        list_parts = list_parts[1:]  # 去除 rtl 前缀

    # feature 目录为空时使用 Path() 保持布局紧凑。
    str_feature = str(module.get("feature", "")).strip()  # 读取功能归档名以稳定 spec 子目录

    # 将 feature 的 POSIX 片段转换成本地 Path。
    path_feature = Path(*PurePosixPath(str_feature).parts) if str_feature else Path()  # 将功能片段转成本地相对目录

    # 剩余 RTL 片段构成模块在 spec 下的相对层级。
    path_relative = Path(*list_parts)  # 保留 RTL 父目录以避免同名模块覆盖

    # 将模块 spec 固定放入 spec/<feature>/<rtl-parent>，保持布局和文件命名可追溯。
    path_module_dir = out_root / "spec" / path_feature / path_relative.parent  # 生成 feature 与 RTL 父目录拼接的 spec 位置

    # 波形数据和 SVG 放在模块规范目录下，链接可局部追踪。
    path_wave_dir = path_module_dir / "waveforms"  # 在模块 spec 旁建立 WaveDrom 工件目录

    # 文件名严格绑定模块名，满足 <module>_spec.md 合同。
    path_spec = path_module_dir / "{}_spec.md".format(path_relative.stem)  # 固定 <module>_spec.md 交付命名

    # 返回下游写入和 Markdown 链接共同使用的路径集合。
    dict_paths: dict[str, Path] = {  # 汇总 Markdown 与波形工件的唯一路径事实
        "module_dir": path_module_dir,  # 模块 Markdown 所在目录
        "wave_dir": path_wave_dir,  # 模块 WaveDrom 工件目录
        "spec_path": path_spec,  # 模块 spec Markdown 文件路径
    }

    # 调用方不再自行拼接目录，避免布局漂移。
    return dict_paths

# 场景 slug helper 让多个时序图可以并存而不发生文件名碰撞。
def _diagram_slug(scenario: str) -> str:
    """把时序场景转换为安全且稳定的文件名片段。

    参数:
        scenario: 图对象中的 scenario 文本。

    返回:
        只含 ASCII 字母、数字和连字符的片段。
    """

    # 非文件名字符折叠成单个连字符，便于跨平台归档。
    str_slug = re.sub(r"[^A-Za-z0-9-]+", "-", scenario).strip("-")  # 场景文件名片段

    # 全符号场景使用可识别的稳定后备名称。
    if not str_slug:

        # 不能让空 slug 造成多个图文件重名。
        str_slug = "scenario"  # 缺省场景片段

    # 返回可用于 JSON5 与 SVG 的文件名片段。
    return str_slug

# 时序图 Markdown helper 将每张图片与对应 WaveJSON 链接绑定。
def _render_waveform_markdown(
    module: Mapping[str, Any],
    paths: Mapping[str, Path],
) -> list[str]:
    """生成模块时序图章节中的图片和 WaveJSON 链接。

    参数:
        module: 已归一化模块对象。
        paths: ``_module_paths`` 返回的模块路径集合。

    返回:
        可直接追加到 Markdown 缓冲区的时序图行列表。
    """

    # 相对链接必须以最终 Markdown 文件位置为计算基准。
    path_spec = paths["spec_path"]  # 以 spec 文件位置作为链接相对基准

    # WaveJSON 与 SVG 共用模块专属的波形目录。
    path_wave_dir = paths["wave_dir"]  # 记录当前模块的 WaveDrom 目录

    # 先收集完整波形章节，调用方只需追加一个连续代码块。
    list_lines: list[str] = ["", "## 6. WaveDrom 时序图", ""]  # 时序图章节缓冲

    # 保留输入图顺序，使审查 diff 在重复生成时保持确定。
    for diagram_value in module["timing_diagrams"]:

        # 将场景文本转换为跨平台文件名片段。
        str_slug = _diagram_slug(diagram_value["scenario"])  # 将场景文本压缩为跨平台文件名片段

        # 模块和场景身份共同构成可追溯的 JSON5 文件名。
        str_json_name = "{}_{}.json5".format(module["name"], str_slug)  # 组成可追溯 WaveJSON 文件名

        # SVG 文件名复用 JSON5 身份，确保一一配对。
        str_svg_name = "{}_{}.svg".format(module["name"], str_slug)  # 生成配套 SVG 文件名

        # 将源 JSON5 放入模块专属的波形目录。
        path_json = path_wave_dir / str_json_name  # 定位模块专属的 WaveJSON 文件

        # 将渲染后的 SVG 放在源波形描述旁边。
        path_svg = path_wave_dir / str_svg_name  # 定位与 JSON5 配对的 SVG 文件

        # 相对 JSON5 链接让 Markdown bundle 可以整体迁移。
        str_json_link = path_json.relative_to(path_spec.parent).as_posix()  # 计算 WaveJSON 相对链接

        # 相对 SVG 链接支持离线打开 Markdown 图片。
        str_svg_link = path_svg.relative_to(path_spec.parent).as_posix()  # 计算 SVG 图片相对链接

        # 每张图同时写入标题、描述、图片和源链接四类证据。
        list_lines.extend(
            [
                "### {}".format(diagram_value["title"]),
                "",
                diagram_value["description"],
                "",
                "![{}]({})".format(diagram_value["title"], str_svg_link),
                "",
                "- WaveJSON：[{}]({})".format(str_json_name, str_json_link),
                "",
            ]
        )

    # 只返回波形章节，文件写入仍由 bundle 层统一负责。
    return list_lines

# 参数 Markdown helper 展开可配置项以及时钟、复位合同。
def _render_parameter_markdown(module: Mapping[str, Any]) -> list[str]:
    """生成参数、时钟和复位章节。

    参数:
        module: 已归一化模块对象。

    返回:
        可追加到模块 Markdown 的参数与时序控制行列表。
    """

    # 先建立固定表头，避免参数为空时章节结构漂移。
    list_lines: list[str] = [  # 参数章节的稳定 Markdown 表头
        "## 2. 参数",  # 参数章节标题
        "",  # 标题与表格之间的 Markdown 分隔行
        "| 参数 | 默认值 | 说明 |",  # 参数表列名
        "| --- | --- | --- |",  # 参数表对齐规则
    ]

    # 过滤扩展值，只保留可以安全展示的参数映射。
    list_parameter_values: list[Mapping[str, Any]] = [  # 收集可渲染参数，过滤扩展值
        item  # 保留当前参数的名称、默认值和描述
        for item in module.get("parameters", [])  # 按规格输入顺序遍历参数
        if isinstance(item, Mapping)  # 仅接受结构化参数记录
    ]  # 可渲染参数项

    # 每个结构化参数都保留默认值和描述。
    list_lines.extend(
        [
            "| {} | {} | {} |".format(
                item.get("name", ""),
                item.get("default", ""),
                item.get("description", ""),
            )
            for item in list_parameter_values
        ]
    )

    # 没有参数时写出明确占位，避免读者误解为生成遗漏。
    if not list_parameter_values:

        # 空参数行维持 Markdown 表格合法性。
        list_lines.append("| — | — | 本模块没有可配置参数。 |")

    # 时钟和复位用 JSON 展示，避免擅自解释时序语义。
    str_clock_json: str = json.dumps(module.get("clock", {}), ensure_ascii=False)  # 序列化时钟合同供 Markdown 检索

    # 复位对象单独序列化，避免混淆两个时序控制域。
    str_reset_json: str = json.dumps(module.get("reset", {}), ensure_ascii=False)  # 序列化复位合同并保留原始键值

    # 将两类时序配置放入同一章节，保持 JSON 可搜索性。
    list_lines.extend(
        [
            "",
            "## 3. 时钟与复位",
            "",
            "- 时钟：`{}`".format(str_clock_json),
            "- 复位：`{}`".format(str_reset_json),
            "",
        ]
    )

    # 返回参数和时序控制章节，接口 helper 独立维护信号表。
    return list_lines

# 接口 Markdown helper 展开端口证据和周期行为章节。
def _render_interface_markdown(module: Mapping[str, Any]) -> list[str]:
    """生成接口信号表和周期行为章节。

    参数:
        module: 已归一化模块对象。

    返回:
        可追加到模块 Markdown 的接口与行为行列表。
    """

    # 固定表头顺序承载方向、位宽、时钟域和语义描述。
    list_lines: list[str] = [  # 接口章节的稳定列定义
        "## 4. 接口信号规范",  # 接口章节标题
        "",  # 标题与信号表之间的 Markdown 分隔行
        "| 信号 | 方向 | 位宽 | 时钟域 | 语义角色 | 描述 |",  # 接口表列名
        "| --- | --- | --- | --- | --- | --- |",  # 接口表对齐规则
    ]

    # 归一化端口保证每一列都能稳定读取。
    for port_value in module["interfaces"]["ports"]:

        # 未声明时钟域以中文占位而非猜测默认时钟。
        str_clock_domain = port_value.get("clock_domain") or "未声明"  # 为接口表选择明确的时钟域显示值

        # 端口行按固定列顺序呈现全部接口证据。
        str_port_row = "| `{}` | {} | {} | {} | {} | {} |".format(  # Markdown 行固定承载 name、direction、width、clock_domain、role、description 六列
            port_value["name"],  # 接口信号名称列
            port_value["direction"],  # 接口方向字段列
            port_value["width"],  # 接口位宽字段列
            str_clock_domain,  # 端口时钟域字段列
            port_value["role"],  # 信号语义角色列
            port_value["description"],  # 端口行为描述列
        )  # 接口表行文本

        # 将当前端口行追加到 Markdown 缓冲。
        list_lines.append(str_port_row)

    # 行为章节按确认顺序列出周期、延迟和握手语义。
    list_lines.extend(["", "## 5. 周期行为与延迟", ""])  # 行为章节起始

    # 行为条目独立追加，避免章节标题和内容耦合。
    list_lines.extend(["- {}".format(item) for item in module["behavior"]])  # 行为条目

    # 返回接口与行为章节，调用方随后追加 WaveDrom 和风险章节。
    return list_lines

# Markdown helper 把接口与每张 WaveDrom 图绑定到同一模块文档。
def _render_markdown(module: Mapping[str, Any], paths: Mapping[str, Path], language: str) -> str:
    """渲染单模块中文或英文 Markdown 规范正文。

    参数:
        module: 已归一化模块对象。
        paths: ``_module_paths`` 返回的模块路径集合。
        language: ``zh`` 或 ``en``。

    返回:
        可直接写入 ``*_spec.md`` 的 UTF-8 文本。
    """

    # 标题后缀随 CLI 语言参数变化，表格字段保持稳定。
    str_title_suffix = "模块规范" if language == "zh" else "Module Specification"  # 文档标题后缀

    # 规格链接必须从 Markdown 所在目录计算相对路径。
    path_spec = paths["spec_path"]  # Markdown 目标路径

    # 先写入标题和 RTL 事实，让文档首屏具备身份信息。
    list_lines: list[str] = ["# {} {}".format(module["name"], str_title_suffix), "", "> RTL: `{}`".format(module["rtl_path"]), ""]  # 初始化标题和 RTL 追溯行，作为文档首屏身份

    # 目的章节只展示用户确认的 description。
    list_lines.extend(
        ["## 1. 模块目的", "", str(module.get("description", "未提供模块目的。")), ""]
    )

    # 参数、时序、接口和行为章节各自遵守稳定布局合同。
    list_lines.extend(_render_parameter_markdown(module))  # 追加参数及时钟复位章节

    # 接口 helper 追加端口表和周期行为，保持章节顺序固定。
    list_lines.extend(_render_interface_markdown(module))  # 追加接口与周期行为章节

    # WaveDrom 章节把接口合同与已渲染工件绑定。
    list_lines.extend(_render_waveform_markdown(module, paths))  # 追加完整 WaveDrom 时序图章节

    # 边界、约束和验收章节把剩余合同字段完整呈现。
    list_lines.extend(["## 7. 边界、反压与错误", ""])  # 边界章节标题

    # 边界条目直接对应规格中的风险场景。
    list_lines.extend(["- {}".format(item) for item in module["corner_cases"]])  # 边界条目

    # CDC 和综合约束使用独立章节，避免与行为混淆。
    list_lines.extend(["", "## 8. CDC 与综合约束", ""])  # 约束章节标题

    # 约束条目保持规格输入顺序。
    list_lines.extend(["- {}".format(item) for item in module["constraints"]])  # 约束条目

    # 验证章节标题将后续列表绑定到验收语义。
    list_lines.extend(["", "## 9. 验证与验收", ""])  # 验证章节标题

    # 验收条目为远程或本地测试计划提供索引。
    list_lines.extend(["- {}".format(item) for item in module["verification_cases"]])  # 验收条目

    # 追溯章节再次绑定模块、RTL 与同版本伴随文件。
    list_lines.extend(
        [
            "",
            "## 10. 可追溯性",
            "",
            "- 规格模块：`{}`".format(module["name"]),
            "- RTL 路径：`{}`".format(module["rtl_path"]),
            "- 交付要求：本文档、WaveJSON 和 SVG 必须与同一版本规格一同发布。",
            "",
        ]
    )

    # 统一使用单换行结尾，保证生成文件 diff 稳定。
    str_markdown = "\n".join(list_lines)  # 合并行缓冲为可写入的 Markdown 正文

    # 返回待写入的 UTF-8 文本。
    return str_markdown

# 波形写入 helper 将 WaveJSON 和 SVG 成对放入暂存目录。
def _write_waveforms(
    module: Mapping[str, Any],
    paths: Mapping[str, Path],
    renderer: Callable[..., Any] | None,
) -> list[dict[str, Any]]:
    """写入单模块 WaveJSON 并调用 WaveDrom 渲染器。

    参数:
        module: 已归一化模块对象。
        paths: 模块暂存目录路径集合。
        renderer: 可选测试替身或真实 ``render_waveform``。

    返回:
        每张图的 JSON5、SVG 和渲染报告路径记录。

    异常:
        SpecDocumentError: 外部渲染器失败时抛出原始错误。
    """

    # 默认渲染器统一走 WaveDrom runtime。
    func_renderer = renderer or render_waveform  # 当前波形渲染器

    # 输出列表保留每张图的 JSON5、SVG 和渲染摘要。
    list_outputs: list[dict[str, Any]] = []  # 波形输出清单

    # 暂存目录已由调用方建立，写入顺序保持图列表顺序。
    for diagram_value in module["timing_diagrams"]:

        # 根据场景生成稳定文件名并创建路径。
        str_slug = _diagram_slug(diagram_value["scenario"])  # 波形场景片段

        # JSON5 路径使用模块名和场景 slug 组成唯一文件名。
        path_json = paths["wave_dir"] / "{}_{}.json5".format(module["name"], str_slug)  # 定位当前图的 WaveJSON 输入文件

        # SVG 路径与 JSON5 共用同一波形身份。
        path_svg = paths["wave_dir"] / "{}_{}.svg".format(module["name"], str_slug)  # 定位当前图的 SVG 渲染输出文件

        # JSON5 文件保存与渲染器相同的 WaveJSON 输入。
        str_wavejson = json.dumps(diagram_value["wavejson"], ensure_ascii=False, indent=2)  # 序列化 WaveDrom 输入以保持渲染和归档一致

        # 写入 JSON5 后再调用 renderer，保证输入文件已经可读取。
        path_json.write_text(str_wavejson + "\n", encoding="utf-8")

        # 测试替身可接受路径或已解析对象两种稳定调用形态。
        try:

            # 真实 runtime 接受 JSON 文件路径并把 SVG 写入目标路径。
            dict_render = func_renderer(path_json, path_svg)  # 路径形式渲染结果

        # 仅在注入替身不接受路径时尝试对象形式。
        except TypeError:

            # 默认 runtime 的 TypeError 必须原样暴露，避免隐藏真实错误。
            if renderer is None:

                # 裸 re-raise 保留渲染器堆栈和错误类型。
                raise

            # 仅在显式注入 renderer 时调用替身，并传入已经校验过的 WaveJSON 对象。
            dict_render = func_renderer(diagram_value["wavejson"], path_svg)  # 对象形式渲染结果

        # 记录当前图的所有交付路径和 renderer 摘要。
        list_outputs.append(
            {"json5": str(path_json), "svg": str(path_svg), "render": dict_render}
        )  # 单图渲染记录

    # 返回本模块波形清单供 bundle 报告引用。
    return list_outputs

# 发布 helper 只复制已经完整渲染的暂存树。
def _publish_stage(stage_root: Path, out_root: Path) -> None:
    """把已经完整写入的暂存树复制到目标根目录。

    参数:
        stage_root: 与目标同父目录的完整暂存树。
        out_root: 用户请求的交付根目录。

    返回:
        无返回值；复制失败时保留异常给调用方。
    """

    # 目标文件只在全部渲染成功后才开始写入。
    for stage_file in stage_root.rglob("*"):

        # 目录由文件父路径按需建立，避免复制空目录噪声。
        if stage_file.is_file():

            # 计算相对于暂存树的发布位置。
            target_file = out_root / stage_file.relative_to(stage_root)  # 目标交付路径

            # 先创建父目录，确保发布单元的第一个可见副作用不依赖外部预创建。
            target_file.parent.mkdir(parents=True, exist_ok=True)

            # 以二进制复制保留 renderer 生成的 SVG 和 JSON 原始字节，避免重新编码。
            target_file.write_bytes(stage_file.read_bytes())

# 清理 helper 删除本次生成的暂存证据，不碰目标根旧文件。
def _cleanup_stage(stage_parent: Path) -> None:
    """删除单次 bundle 生成使用的暂存树。

    参数:
        stage_parent: ``tempfile.mkdtemp`` 创建的暂存父目录。

    返回:
        无返回值；清理失败时向上暴露异常。
    """

    # 先删除文件再反向删除目录，避免残留半成品。
    for path_item in sorted(stage_parent.rglob("*"), reverse=True):

        # 文件和链接可以直接解除引用。
        if path_item.is_file() or path_item.is_symlink():

            # 解除文件引用后才能安全逐层删除暂存树；missing_ok 允许外部已清理异常路径。
            path_item.unlink(missing_ok=True)

        # 空目录按深度逆序移除。
        elif path_item.is_dir():

            # 移除不应携带用户文件的空暂存目录，保证后续 stage_parent.rmdir 成功。
            path_item.rmdir()

    # 最后移除最外层目录，确保工作区不残留本轮生成的临时证据。
    stage_parent.rmdir()

# 公共 bundle 入口在全部模块成功后原子发布交付文件。
def write_spec_bundle(
    spec: Mapping[str, Any] | Path,
    out_dir: Path,
    *,
    source_paths: Sequence[Path] | None = None,
    language: str = "zh",
    renderer: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """原子生成每个模块的 ``*_spec.md``、WaveJSON 和 SVG。

    参数:
        spec: 已加载规格对象或 JSON 文件路径。
        out_dir: 交付工件根目录。
        source_paths: 可选 RTL 文件集合，用于接口交叉核验。
        language: 文档语言，支持 ``zh`` 和 ``en``。
        renderer: 可选 WaveDrom 渲染替身。

    返回:
        含 ``ok``、``spec_root`` 和模块输出清单的报告。

    异常:
        SpecDocumentError: 规格、源接口或渲染过程无法通过时抛出。
    """

    # 语言值在任何文件系统副作用前完成检查。
    if language not in {"zh", "en"}:

        # 双语之外的值没有对应 Markdown 合同。
        raise SpecDocumentError("> ERR: [Python] language must be zh or en.")

    # Path 输入只读取 JSON，不从 RTL 反推模块行为。
    if isinstance(spec, Path):

        # 文件读取异常需要转换成稳定领域错误。
        try:

            # 先在内存中完成 JSON 解析，失败时不创建输出目录。
            dict_raw_spec = json.loads(spec.read_text(encoding="utf-8"))  # JSON 原始对象

        # 文件不存在或 JSON 损坏都属于用户输入错误。
        except (OSError, json.JSONDecodeError) as exc:

            # 使用 format 让质量门禁能够确认 ERR 前缀和消息正文。
            raise SpecDocumentError("> ERR: [Python] cannot read spec JSON: {}".format(exc)) from exc

    # Mapping 输入复制为普通字典，隔离调用方后续修改。
    else:

        # 非 mapping 会在 normalize 阶段以领域错误拒绝。
        dict_raw_spec = dict(spec)  # 内存规格副本

    # 先完成完整结构和可选源接口验证。
    dict_validation = validate_spec_document(dict_raw_spec, source_paths=source_paths)  # 规格验证报告

    # 任何诊断都会阻断暂存目录和目标目录写入。
    if not dict_validation["ok"]:

        # 聚合问题文本让 CLI 一次展示完整失败原因。
        str_issues = "; ".join(dict_validation["issues"])  # 规格问题摘要

        # 将所有字段诊断包装成统一的发布错误。
        raise SpecDocumentError("> ERR: [Python] {}".format(str_issues))

    # 取得已归一化规格，后续阶段不再重复推断字段。
    dict_spec = dict_validation["spec"]  # 已确认规格

    # 输出根解析为绝对路径，便于阶段目录和链接转换。
    path_out_root = Path(out_dir).resolve()  # 绝对交付根目录

    # 先建立目标根的同父暂存层级，把目录创建限制为验证通过后的首个可见副作用。
    path_out_root.mkdir(parents=True, exist_ok=True)

    # 暂存父目录与目标根同父，保证最终复制不跨文件系统。
    path_stage_parent = Path(tempfile.mkdtemp(prefix="spec-bundle-", dir=path_out_root.parent))  # 暂存父目录

    # 暂存根目录使用目标目录名，便于 relative_to 映射。
    path_stage_root = path_stage_parent / path_out_root.name  # 暂存工件根目录

    # 建立暂存根后才进入模块渲染循环。
    path_stage_root.mkdir(parents=True, exist_ok=True)

    # 该列表保存每个模块的暂存路径和渲染摘要。
    list_outputs: list[dict[str, Any]] = []  # 模块输出报告

    # 所有文件先写暂存树；finally 确保异常也清理临时目录。
    try:

        # 按 modules 顺序准备目录、波形和 Markdown。
        for module_value in dict_spec["modules"]:

            # 路径 helper 统一 feature 目录和文件命名规则。
            dict_paths = _module_paths(path_stage_root, module_value)  # 当前模块路径集合

            # 先建立 Markdown 所在的模块目录和配套波形目录，再写入文件。
            dict_paths["module_dir"].mkdir(parents=True, exist_ok=True)

            # 再建立该模块专属的 WaveDrom 子目录。
            dict_paths["wave_dir"].mkdir(parents=True, exist_ok=True)

            # 波形全部成功后才生成 Markdown，避免出现断链文档。
            list_wave_outputs = _write_waveforms(module_value, dict_paths, renderer)  # 当前模块波形报告

            # Markdown 读取同一组已渲染波形的相对路径。
            str_markdown = _render_markdown(module_value, dict_paths, language)  # 当前模块 Markdown

            # 文档写入暂存根，目标根仍保持旧版本不变。
            dict_paths["spec_path"].write_text(str_markdown, encoding="utf-8")

            # 记录暂存路径，发布完成后再转换成目标路径。
            list_outputs.append(
                {
                    "module": module_value["name"],
                    "spec": str(dict_paths["spec_path"]),
                    "waveforms": list_wave_outputs,
                }
            )  # 模块输出记录

        # 只有所有模块都成功后，发布 helper 才复制暂存树中的完整文件覆盖目标目录。
        _publish_stage(path_stage_root, path_out_root)

    # 不吞掉渲染或 IO 异常，调用方需要看到失败原因。
    finally:

        # finally 清理暂存目录，同时不影响目标根中的旧 bundle，即使渲染异常也不残留临时目录。
        _cleanup_stage(path_stage_parent)

    # 将报告中的暂存前缀替换为实际交付根，便于调用方定位产物。
    for module_output in list_outputs:

        # spec 路径直接从 stage_root 映射到 out_root。
        module_output["spec"] = str(path_out_root / Path(module_output["spec"]).relative_to(path_stage_root))  # 发布后的 spec 路径

        # 每个波形记录也转换为目标路径。
        for waveform_output in module_output["waveforms"]:

            # 按同一相对路径先映射 JSON5，再映射配套 SVG。
            waveform_output["json5"] = str(path_out_root / Path(waveform_output["json5"]).relative_to(path_stage_root))  # 把暂存 JSON5 映射为最终交付路径

            # SVG 与 JSON5 使用同一相对目录，保持链接配对。
            waveform_output["svg"] = str(path_out_root / Path(waveform_output["svg"]).relative_to(path_stage_root))  # 将 SVG 路径改写为目标根下的图片工件位置

    # 返回结构化成功报告，CLI 可直接序列化但不打印正文文件。
    return {"ok": True, "spec_root": str(path_out_root / "spec"), "modules": list_outputs}
