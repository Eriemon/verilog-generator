"""共享 readable-verilog-generator 的双语 Verilog 文件头字面合同。"""

# 延迟类型注解求值，避免导入阶段提前解析复杂联合类型。
from __future__ import annotations

# copy 只用于返回可修改副本，避免调用方污染全局合同常量。
from copy import deepcopy

# Path 只用于定位 repo 内的 header 合同文本资产。
from pathlib import Path

# HEADER_TIMESCALE_LINE 固定标准 header 的首行 timescale 文本。
HEADER_TIMESCALE_LINE = "`timescale 1ns / 1ps"  # 标准双语 header 的固定 timescale 行

# HEADER_ENGLISH_BANNER 固定英文横幅的逐字符文本。
HEADER_ENGLISH_BANNER = "////////////////////////////////////English///////////////////////////////////////"  # 英文横幅完整文本

# HEADER_CHINESE_BANNER 固定中文横幅的逐字符文本。
HEADER_CHINESE_BANNER = "///////////////////////////////////Chinese////////////////////////////////////////"  # 中文横幅完整文本

# HEADER_ENGLISH_SEPARATOR 供质量门和解析器做横幅锚点匹配。
HEADER_ENGLISH_SEPARATOR = "/" * 36 + "English"  # 英文横幅匹配锚点

# HEADER_CHINESE_SEPARATOR 专门匹配中文横幅，避免和英文锚点说明重复。
HEADER_CHINESE_SEPARATOR = "/" * 35 + "Chinese"  # 中文横幅匹配锚点

# HEADER_PATH_TEMPLATES 固定英中文段的说明文档与仿真工程路径模板。
HEADER_PATH_TEMPLATES = {  # 双语 header 的固定路径模板集合
    "english": {  # 英文段固定路径模板
        "description": "description/{module_name}_Design.pdf",  # 英文段固定说明文档路径模板
        "simulations": "testbench/vivado/2021.1/{module_name}",  # 英文段固定仿真工程路径模板
    },
    "chinese": {  # 中文段固定路径模板
        "description": "Description/{module_name}_Design.pdf",  # 中文段固定说明文档路径模板
        "simulations": "TestBench/Vivado/2021.1/{module_name}",  # 中文段固定仿真工程路径模板
    },
}

# HEADER_LAYOUT 固定双语 header 的字段顺序、前缀、列头和横幅文本。
HEADER_LAYOUT = {  # 双语 header 的共享版式合同
    "english": {  # 英文段固定版式
        "banner": HEADER_ENGLISH_BANNER,  # 英文段横幅完整文本
        "separator": HEADER_ENGLISH_BANNER,  # 英文段横幅匹配时复用的完整分隔文本
        "company_prefix": "// Company:         ",  # 英文段公司字段前缀
        "engineer_prefix": "// Engineer:        ",  # 英文段开发人员字段前缀
        "blank_after_identity": "//",  # 身份字段后的固定注释空行
        "create_date_prefix": "// Create Date:     ",  # 英文段创建日期字段前缀
        "design_name_prefix": "// Design Name:     ",  # 英文段 Design Name 固定前缀
        "module_name_prefix": "// Module Name:     ",  # 英文段模块名称字段前缀
        "description_prefix": "// Description:     ",  # 英文段说明文档字段前缀
        "simulations_prefix": "// Simulations:     ",  # 英文段仿真工程字段前缀
        "blank_before_references": "//",  # 英文段进入 Referrences 前的固定空白注释行
        "references_prefix": "// Referrences:     ",  # 英文段参考资料字段前缀
        "references_line_none": "// Referrences:     None",  # 英文段 none_mode 参考资料整行
        "references_line_table": "// Referrences:",  # 英文段 table_mode 参考资料标题行
        "references_table_header": "File Format      File Name",  # 英文段参考资料表格列头
        "dependencies_prefix": "// Dependencies:    ",  # 英文段依赖字段前缀
        "dependencies_line_none": "// Dependencies:    None",  # 英文段 none_mode 依赖整行
        "dependencies_line_table": "// Dependencies:",  # 英文段 table_mode 依赖标题行
        "dependencies_table_header": "Module Name      Version",  # 英文段依赖表格列头
        "section_blank": "//",  # 英文段内部区块之间的固定空白注释行
        "version_prefix": "// Version:         ",  # 英文段版本字段前缀
        "revision_date_prefix": "// Revision Date:   ",  # 英文段修订日期字段前缀
        "history_title": "// History:",  # 英文段修订历史标题行
        "history_header": "// Time             Version     Revised by        Contents",  # 英文段修订历史表头行
    },
    "chinese": {  # 中文段固定版式
        "banner": HEADER_CHINESE_BANNER,  # 中文段横幅完整文本
        "separator": HEADER_CHINESE_BANNER,  # 中文段横幅匹配时复用完整中文横幅文本
        "company_prefix": "// 版权归属:        ",  # 中文段公司字段前缀
        "engineer_prefix": "// 开发人员:        ",  # 中文段开发人员字段前缀
        "blank_after_identity": "//",  # 中文段身份字段后的固定注释空行
        "create_date_prefix": "// 创建日期:        ",  # 中文段创建日期字段前缀
        "design_name_prefix": "// 设计名称:        ",  # 中文段设计名称字段前缀
        "module_name_prefix": "// 模块名称:        ",  # 中文段模块名称字段前缀
        "description_prefix": "// 模块说明:        ",  # 中文段说明文档字段前缀
        "simulations_prefix": "// 仿真工程:        ",  # 中文段仿真工程字段前缀
        "blank_before_references": "//",  # 中文段进入参考资料前的固定空白注释行
        "references_prefix": "// 参考资料:        ",  # 中文段参考资料字段前缀
        "references_line_none": "// 参考资料:        None",  # 中文段 none_mode 参考资料整行
        "references_line_table": "// 参考资料:",  # 中文段 table_mode 参考资料标题行
        "references_table_header": "文件格式         文件名称",  # 中文段参考资料表格列头
        "dependencies_prefix": "// 依赖文件:        ",  # 中文段依赖字段前缀
        "dependencies_line_none": "// 依赖文件:        None",  # 中文段 none_mode 依赖整行
        "dependencies_line_table": "// 依赖文件:",  # 中文段 table_mode 依赖标题行
        "dependencies_table_header": "模块名称         版本",  # 中文段依赖表格列头
        "section_blank": "//",  # 中文段内部区块之间的固定空白注释行
        "version_prefix": "// 当前版本:        ",  # 中文段版本字段前缀
        "revision_date_prefix": "// 修订日期:        ",  # 中文段修订日期字段前缀
        "history_title": "// 修订历史:",  # 中文段修订历史标题行
        "history_header": "// 时间             版本        修订人            修订内容",  # 中文段修订历史表头行
    },
}

# HEADER_CONTRACT_ASSET_DIR 固定指向 header 合同文本资产目录。
HEADER_CONTRACT_ASSET_DIR = Path(__file__).resolve().parents[2] / "assets" / "style_templates"  # header 合同文本资产目录

# _read_contract_asset_text 统一读取 header 合同文本资产并裁掉尾随换行。
def _read_contract_asset_text(file_name: str) -> str:
    """
    读取指定的 header 合同文本资产，并裁掉尾随换行。

    参数:
        file_name: 需要读取的合同文本资产文件名。
    返回:
        返回裁掉尾随换行后的合同文本。
    """

    # path_asset 指向当前要读取的合同文本资产文件。
    path_asset = HEADER_CONTRACT_ASSET_DIR / file_name  # 当前合同文本资产路径

    # 读取资产文本后裁掉尾随换行，避免 prompt 包装时多出空白行。
    return path_asset.read_text(encoding="utf-8").rstrip("\n")

# HEADER_PROMPT_NONE_MODE_TEXT 直接从文本资产读取 none_mode 合同样例。
HEADER_PROMPT_NONE_MODE_TEXT = _read_contract_asset_text("header_prompt_none_mode.txt")  # none_mode prompt 样例完整文本

# HEADER_PROMPT_TABLE_MODE_TEXT 直接读取带参考资料表格的 table_mode 合同样例。
HEADER_PROMPT_TABLE_MODE_TEXT = _read_contract_asset_text("header_prompt_table_mode.txt")  # table_mode 表格合同样例文本

# header_layout_config 返回共享 layout 的深拷贝，供调用方安全修改。
def header_layout_config() -> dict[str, dict[str, str]]:
    """
    返回可安全修改的双语 header 布局副本。

    参数:
        本函数没有业务参数，固定返回共享常量的深拷贝。
    返回:
        返回英中文段的共享布局副本。
    """

    # 返回深拷贝副本，避免调用方直接污染全局合同常量。
    return deepcopy(HEADER_LAYOUT)

# render_header_template_text 统一替换 header 模板中的模块名占位。
def render_header_template_text(template: str, module_name: str) -> str:
    """
    把 header 模板中的模块名占位替换成目标模块名。

    参数:
        template: 需要执行占位替换的 header 模板文本。
        module_name: 要写入模板的目标模块名。
    返回:
        返回完成占位替换后的模板文本。
    """

    # 先兼容 `$module$` 与 `{module}` 两种旧模板占位写法。
    str_template = str(template).replace("$module$", module_name).replace("{module}", module_name)  # 完成兼容占位预替换后的模板文本

    # 再尝试处理 `{module_name}` 风格的标准 format 占位。
    try:

        # 标准 format 占位统一复用同一个 module 名称。
        return str_template.format(module_name=module_name, module=module_name)

    # 模板若包含其他花括号，则保守回退到预替换结果。
    except (IndexError, KeyError, ValueError):

        # 发生 format 失败时仍返回已完成兼容占位替换的文本。
        return str_template

# default_header_paths 根据模块名生成英中文段的固定路径。
def default_header_paths(module_name: str) -> dict[str, dict[str, str]]:
    """
    返回目标模块在英中文段落中的固定说明文档与仿真工程路径。

    参数:
        module_name: 需要写入固定路径模板的目标模块名。
    返回:
        返回按语言分组的固定路径映射。
    """

    # dict_paths 汇总英中文段最终要写入 header 的固定路径。
    dict_paths: dict[str, dict[str, str]] = {}  # 目标模块的双语固定路径集合

    # 逐个语言展开模板并完成模块名占位替换。
    for str_language, dict_templates in HEADER_PATH_TEMPLATES.items():

        # dict_language_paths 单独累积当前语言段的字段路径。
        dict_language_paths: dict[str, str] = {}  # 当前语言的固定路径映射

        # 逐个字段渲染最终说明文档和仿真工程路径。
        for str_field, str_template in dict_templates.items():

            # 当前字段路径统一复用模板渲染 helper 生成。
            dict_language_paths[str_field] = render_header_template_text(str_template, module_name)  # 当前字段的最终固定路径

        # 当前语言段的固定路径回填到总结果里。
        dict_paths[str_language] = dict_language_paths  # 当前语言段的路径集合

    # 返回目标模块的完整双语固定路径映射。
    return dict_paths

# reference_dependency_blocks 统一构造 none/table 两种合法区块形态。
def reference_dependency_blocks(
    *,
    mode: str,
    reference_rows: tuple[str, ...] | list[str] | None = None,
    dependency_rows: tuple[str, ...] | list[str] | None = None,
) -> dict[str, object]:
    """
    返回双语 header 共享的 Referrences/Dependencies 结构化区块。

    参数:
        mode: 只能是 `none` 或 `table` 的总模板模式名。
        reference_rows: table_mode 下要写入的参考资料数据行集合。
        dependency_rows: table_mode 下要写入的依赖数据行集合。
    返回:
        返回供 formatter、workflow 和测试共用的结构化区块字典。
    异常:
        ValueError: `mode` 不是 `none` 或 `table` 时抛出。
    """

    # 非法 mode 必须立即阻断，避免第三种混合形态静默流入主链。
    if mode not in {"none", "table"}:

        # 非法模式统一抛出带项目前缀的错误消息。
        raise ValueError("> ERR: [Python] Header reference/dependency mode must be `none` or `table`.")

    # 返回结构化区块，供各入口统一消费同一份合法模式描述。
    return {
        "mode": mode,
        "reference_rows": list(reference_rows or []),
        "dependency_rows": list(dependency_rows or []),
    }

# render_bilingual_header 按共享合同渲染完整双语 header 行列表。
def render_bilingual_header(
    *,
    english_values: dict[str, str],
    chinese_values: dict[str, str],
    english_history_lines: list[str] | tuple[str, ...], chinese_history_lines: list[str] | tuple[str, ...],
    reference_dependency_block: dict[str, object],
    include_timescale: bool,
) -> list[str]:
    """
    按共享合同渲染完整双语 header。

    参数:
        english_values: 英文段字段值映射。
        chinese_values: 中文段字段值映射。
        english_history_lines: 英文修订历史正文行集合。
        chinese_history_lines: 中文修订历史正文行集合。
        reference_dependency_block: Referrences/Dependencies 的统一结构化区块。
        include_timescale: 是否在 header 最前面补上固定 timescale 行。
    返回:
        返回可直接 join 成最终 RTL 文本的双语 header 行列表。
    """

    # list_header_lines 顺序累积最终要写回的完整 header 行。
    list_header_lines: list[str] = []  # 双语 header 的最终物理行列表

    # 需要完整 header 时，先补固定 timescale 行和一条空行。
    if include_timescale:

        # timescale 行和后续空行必须与用户样例逐字一致。
        list_header_lines.extend([HEADER_TIMESCALE_LINE, ""])

    # list_english_section 单独承接英文段，便于保持英文在前的固定顺序。
    list_english_section = _render_language_section(  # 英文段完整 header 行列表
        english_values,  # 英文段字段值映射
        english_history_lines,  # 英文段修订历史正文集合
        reference_dependency_block,  # 共享的 references/dependencies 区块
        "english",  # 当前语言键
    )

    # 先把英文段按固定顺序并入总 header。
    list_header_lines.extend(list_english_section)

    # list_chinese_section 单独承接中文段，便于保持中文紧跟英文的固定顺序。
    list_chinese_section = _render_language_section(  # 中文段完整 header 行列表
        chinese_values,  # 中文段字段值映射
        chinese_history_lines,  # 中文段修订历史正文集合
        reference_dependency_block,  # 中文段沿用同一份 references/dependencies 区块
        "chinese",  # 中文段语言键
    )

    # 再把中文段按固定顺序并入总 header。
    list_header_lines.extend(list_chinese_section)

    # 返回完整双语 header 行列表。
    return list_header_lines

# prompt_header_contract_text 返回 prompt 侧逐字注入的两种合法合同样例。
def prompt_header_contract_text() -> str:
    """
    返回 prompt 侧需要逐字注入的两种合法 header 合同样例。

    参数:
        本函数没有业务参数，固定返回 none_mode 与 table_mode 两段样例文本。
    返回:
        返回带 Markdown 代码块包裹的双模式合同样例文本。
    """

    # 返回 prompt 直接拼接使用的两段合法合同示例文本。
    return (
        "None-mode header:\n"
        f"```verilog\n{HEADER_PROMPT_NONE_MODE_TEXT}\n```\n\n"
        "Table-mode header:\n"
        f"```verilog\n{HEADER_PROMPT_TABLE_MODE_TEXT}\n```"
    )

# _render_header_section 渲染单个语言段的固定 header 物理行。
def _render_header_section(
    section_values: dict[str, str],
    history_lines: list[str],
    reference_dependency_block: dict[str, object],
    language_key: str,
) -> list[str]:
    """
    渲染单个语言段的固定 header 合同。

    参数:
        section_values: 当前语言段的字段值映射。
        history_lines: 当前语言段的修订历史正文行集合。
        reference_dependency_block: 当前 header 使用的结构化 references/dependencies 区块。
        language_key: 当前语言键，只允许 `english` 或 `chinese`。
    返回:
        返回单个语言段按固定顺序展开后的物理行列表。
    """

    # dict_layout 读取当前语言段的共享布局副本，避免原地污染全局常量。
    dict_layout = header_layout_config()[language_key]  # 当前语言段的共享布局副本

    # list_history_block 把历史正文统一补成 `// ` 注释行。
    list_history_block = [f"// {str_line}" for str_line in history_lines]  # 当前语言段的历史注释行集合

    # list_section 先写入横幅、身份、路径和固定字段顺序的前半段。
    list_section = [  # 当前语言段的前半段固定字段行列表
        str(dict_layout["banner"]),  # 当前语言段横幅
        f"{dict_layout['company_prefix']}{section_values['copyright_owner']}",  # 当前语言段公司字段
        f"{dict_layout['engineer_prefix']}{section_values['developer']}",  # 当前语言段开发人员字段
        str(dict_layout["blank_after_identity"]),  # 身份字段后的固定空白注释行
        f"{dict_layout['create_date_prefix']}{section_values['create_date']}",  # 当前语言段创建日期字段
        f"{dict_layout['design_name_prefix']}{section_values['design_name']}",  # 当前语言段设计名称字段
        f"{dict_layout['module_name_prefix']}{section_values['module_name']}",  # 当前语言段模块名称字段
        f"{dict_layout['description_prefix']}{section_values['description']}",  # 当前语言段说明文档路径字段
        f"{dict_layout['simulations_prefix']}{section_values['simulations']}",  # 当前语言段仿真工程路径字段
        str(dict_layout["blank_before_references"]),  # References 之前的固定空白注释行
    ]

    # str_mode 收敛当前区块模式，避免 `if` 条件里重复展开取值逻辑。
    str_mode = str(reference_dependency_block.get("mode", "none"))  # 当前 references/dependencies 总模板模式

    # none_mode 只允许单行 None，不得继续夹带表头或自由文本。
    if str_mode == "none":

        # none_mode 直接补入 Referrences 和 Dependencies 的单行占位。
        list_section.extend(
            [
                str(dict_layout["references_line_none"]),
                str(dict_layout["section_blank"]),
                str(dict_layout["dependencies_line_none"]),
            ]
        )

    # table_mode 必须补齐段标题、列头和真实数据行。
    else:

        # table_mode 直接补入列头和真实数据行，保持用户样例的固定顺序。
        list_section.extend(
            [
                str(dict_layout["references_line_table"]),
                f"// {dict_layout['references_table_header']}",
                *[f"// {str_line}" for str_line in reference_dependency_block["reference_rows"]],
                str(dict_layout["section_blank"]),
                str(dict_layout["dependencies_line_table"]),
                f"// {dict_layout['dependencies_table_header']}",
                *[f"// {str_line}" for str_line in reference_dependency_block["dependency_rows"]],
            ]
        )

    # 最后把版本、修订日期和历史表头统一接到语言段尾部。
    list_section.extend(
        [
            str(dict_layout["section_blank"]),
            f"{dict_layout['version_prefix']}{section_values['version']}",
            f"{dict_layout['revision_date_prefix']}{section_values['revision_date']}",
            str(dict_layout["history_title"]),
            str(dict_layout["history_header"]),
            *list_history_block,
        ]
    )

    # 返回单个语言段的完整物理行列表。
    return list_section

# _render_language_section 负责把历史序列先规整成列表，再交给底层语言段渲染 helper。
def _render_language_section(
    section_values: dict[str, str],
    history_lines: list[str] | tuple[str, ...],
    reference_dependency_block: dict[str, object],
    language_key: str,
) -> list[str]:
    """
    把单语言段输入规整后交给 `_render_header_section` 渲染。

    参数:
        section_values: 当前语言段的字段值映射。
        history_lines: 当前语言段的修订历史正文行集合。
        reference_dependency_block: 当前 header 使用的结构化 references/dependencies 区块。
        language_key: 当前语言键，只允许 `english` 或 `chinese`。
    返回:
        返回单个语言段按固定顺序展开后的物理行列表。
    """

    # list_history_lines 把 tuple 或其他序列统一收敛成列表，便于下游遍历和补前缀。
    list_history_lines = list(history_lines)  # 当前语言段的历史正文列表

    # 把规整后的字段和值交给底层 renderer 执行固定合同渲染。
    return _render_header_section(section_values, list_history_lines, reference_dependency_block, language_key)
