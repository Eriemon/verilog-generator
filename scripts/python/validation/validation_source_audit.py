"""validate_verilog_skill 的源码、文档与发布卫生审计 helper。"""

# future annotations 让上下文与正则类型提示保持前向引用友好。
from __future__ import annotations

# re 负责 frontmatter、Load 规则和路径模式匹配。
import re

# Callable 描述 facade 透传的文件枚举与路径映射回调签名。
from collections.abc import Callable

# dataclass 用来收束源码审计 helper 共享的上下文依赖。
from dataclasses import dataclass

# pathlib 负责表达 skill 根目录、项目根目录和 release 目录路径。
from pathlib import Path

# Any 允许 settings 与局部 JSON 载荷保持宽类型兼容。
from typing import Any

# SourceAuditContext 汇总源码、文档与发布卫生审计会反复读取的依赖。
@dataclass(frozen=True)
class SourceAuditContext:
    """
    保存源码、文档与发布卫生审计 helper 需要复用的上下文。

    :param path_skill_root: 当前 readable-verilog-generator skill 根目录。
    :param path_project_root: 当前仓库根目录。
    :param bool_source_repository_layout: 当前运行是否仍处于源码仓布局。
    :param func_iter_skill_files: 返回当前 skill 应纳入审计的文件列表。
    :param func_project_relative: 把任意路径压成项目相对路径的回调。
    :param tuple_legacy_terms: 需要在源码里扫描的旧领域词集合。
    :param tuple_pattern_names: 设计文档必须保留的模式名称集合。
    :param tuple_skill_description_workflow_terms: SKILL.md description 禁止出现的 workflow 术语。
    :param pattern_absolute_path: 扫描私有绝对路径泄漏的正则。
    :param pattern_ref_dependency: 扫描 ref 临时目录依赖的正则。
    :param pattern_skill_name: 校验 SKILL.md name 字段的正则。
    :return: 不返回业务值；实例化完成即表示源码审计上下文已可供 helper 复用。
    """

    # path_skill_root 用于把 skill 内路径压成相对路径并限制审计边界。
    path_skill_root: Path  # 当前 skill 根目录

    # path_project_root 用于定位 release 目录和仓库级治理文件。
    path_project_root: Path  # 当前仓库根目录

    # bool_source_repository_layout 表示当前运行是否需要额外检查源码仓专属路径。
    bool_source_repository_layout: bool  # 是否处于源码仓布局

    # func_iter_skill_files 负责返回当前应纳入源码审计的 skill 文件集合。
    func_iter_skill_files: Callable[[], list[Path]]  # skill 文件枚举回调

    # func_project_relative 负责把路径压成项目相对文本，避免报错里泄漏本机绝对路径。
    func_project_relative: Callable[[Path], str]  # 项目相对路径映射回调

    # tuple_legacy_terms 保存旧领域词扫描使用的匹配片段。
    tuple_legacy_terms: tuple[str, ...]  # 旧领域词集合

    # tuple_pattern_names 保存 skill 设计文档必须持续提到的模式名。
    tuple_pattern_names: tuple[str, ...]  # 设计模式名称集合

    # tuple_skill_description_workflow_terms 保存 SKILL.md description 禁用术语。
    tuple_skill_description_workflow_terms: tuple[str, ...]  # description 禁用 workflow 术语

    # pattern_absolute_path 用于扫描本地私有绝对路径是否误入源码或文档。
    pattern_absolute_path: re.Pattern[str]  # 本地绝对路径扫描正则

    # pattern_ref_dependency 用于扫描 ref 临时目录是否泄漏进活跃文件。
    pattern_ref_dependency: re.Pattern[str]  # ref 临时目录依赖扫描正则

    # pattern_skill_name 用于校验 SKILL.md frontmatter 里的 skill 名称格式。
    pattern_skill_name: re.Pattern[str]  # skill 名称合法性正则

# verify_markdown_ascii 确认 Markdown 默认保持 ASCII-only，只有白名单例外。
def verify_markdown_ascii(
    settings: dict[str, Any] | None,
    source_audit_context: SourceAuditContext,
) -> None:
    """
    检查 Markdown 是否只在白名单路径里出现非 ASCII 文本。

    :param settings: 当前 validate 链路加载出的治理配置；为空时退回空配置。
    :param source_audit_context: Markdown 审计依赖的路径与规则上下文。
    :return: 不返回业务值；通过时表示 Markdown 的字符集边界符合安装安全要求。
    :raises AssertionError: 当白名单外 Markdown 文件出现非 ASCII 文本时抛出。
    """

    # dict_settings 兼容调用方未传 validation 配置的场景。
    dict_settings = settings or {}  # Markdown 审计使用的配置字典

    # list_allowlist_values 保存允许出现非 ASCII 的相对路径白名单。
    list_allowlist_values = dict_settings.get("validation", {}).get("markdown_non_ascii_allowlist", [])  # Markdown 非 ASCII 白名单原始配置

    # set_allowlist 把白名单路径统一规范成 `/` 分隔的相对路径。
    set_allowlist = {  # Markdown 非 ASCII 白名单集合
        str(path_value).replace("\\", "/").lstrip("./")  # 单个白名单路径的规范化文本
        for path_value in list_allowlist_values  # 遍历原始白名单路径配置项
    }

    # list_violations 累计所有命中非 ASCII 的 Markdown 行号定位。
    list_violations: list[str] = []  # Markdown 非 ASCII 违规列表

    # 逐个检查当前 skill 纳入审计的文件。
    for path_file in source_audit_context.func_iter_skill_files():

        # 只对 Markdown 文件执行 ASCII-only 审计。
        if path_file.suffix.lower() != ".md":

            # 非 Markdown 文件不参与当前 ASCII-only 审计。
            continue

        # str_relative_path 用于白名单命中和错误摘要输出。
        str_relative_path = path_file.relative_to(source_audit_context.path_skill_root).as_posix()  # skill 相对 Markdown 路径

        # 白名单路径允许保留非 ASCII 文本，直接跳过后续逐行检查。
        if str_relative_path in set_allowlist:

            # 白名单路径允许保留非 ASCII 文本，不再继续逐行扫描。
            continue

        # 逐行扫描 Markdown 文本，定位非 ASCII 字符出现的位置。
        for int_line_number, str_line in enumerate(
            path_file.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):

            # 任一字符超出 ASCII 范围时，就把路径和行号登记进违规列表。
            if any(ord(str_char) > 127 for str_char in str_line):

                # 记录当前 Markdown 文件命中的非 ASCII 违规位置。
                list_violations.append(f"{str_relative_path}:{int_line_number}")

    # 存在违规时，要把排序后的摘要显式暴露给上层 confidence gate。
    if list_violations:

        # str_violation_summary 统一压成单行，方便人工定位所有违规路径。
        str_violation_summary = ", ".join(sorted(list_violations))  # Markdown 非 ASCII 违规摘要

        # 用统一 ERR 前缀抛出安装安全违规。
        raise AssertionError(
            "> ERR: [Python] Markdown files must be ASCII-only for install safety: "
            + str_violation_summary
        )

# verify_skill_standards 校验 SKILL.md、设计文档与评估资产的发布契约。
def verify_skill_standards(source_audit_context: SourceAuditContext) -> None:
    """
    检查 SKILL.md、设计文档与评估资产是否满足当前发布约束。

    :param source_audit_context: skill 标准审计依赖的路径与规则上下文。
    :return: 不返回业务值；通过时表示 skill 标准文档与评估资产齐全且格式正确。
    :raises AssertionError: 当 SKILL.md、设计文档或评估资产缺失/违规时抛出。
    """

    # str_skill_text 保存供 frontmatter 和 Load 规则复用的原始 SKILL.md 全文。
    str_skill_text = (source_audit_context.path_skill_root / "SKILL.md").read_text(encoding="utf-8")  # 供后续多道校验复用的 SKILL.md 全文

    # str_frontmatter 复用统一解析结果，避免重复切分 YAML 头部。
    str_frontmatter = parse_skill_frontmatter(str_skill_text)  # 已解析出的 YAML frontmatter 正文

    # 先检查 frontmatter 的字段结构与 description 约束。
    verify_skill_frontmatter(str_skill_text, str_frontmatter, source_audit_context)

    # 再确认 Load 规则引用的 supporting resources 都实际存在。
    verify_skill_load_resources(str_skill_text, source_audit_context)

    # 继续校验设计文档是否仍保留要求的模式说明。
    verify_skill_design_documents(source_audit_context)

    # 最后检查效果评估运行时资产是否完整。
    verify_skill_eval_assets(source_audit_context)

# parse_skill_frontmatter 负责抽取 SKILL.md 顶部 YAML frontmatter。
def parse_skill_frontmatter(str_skill_text: str) -> str:
    """
    解析 SKILL.md 顶部的 YAML frontmatter 文本。

    :param str_skill_text: SKILL.md 的完整文本。
    :return: 返回 YAML frontmatter 的正文部分，不含起止 `---`。
    :raises AssertionError: 当 SKILL.md 缺少 frontmatter、结构损坏或头部过长时抛出。
    """

    # 没有标准 frontmatter 起始标记时，必须立即阻断。
    if not str_skill_text.startswith("---\n"):

        # 缺少 frontmatter 起始标记时，立即返回统一的结构错误。
        raise AssertionError("> ERR: [Python] SKILL.md must start with YAML frontmatter.")

    # 只按前三段切分，确保 body 中的 `---` 不会误伤头部解析。
    try:

        # 仅切分前三段，保留正文里后续 `---` 的原始含义。
        _, str_frontmatter, _str_skill_body = str_skill_text.split("---", 2)  # frontmatter 与正文的三段切分结果

    # frontmatter 切分失败时，直接暴露结构损坏错误。
    except ValueError as exc:

        # frontmatter 结构损坏时，用统一 ERR 前缀阻断后续校验。
        raise AssertionError("> ERR: [Python] SKILL.md frontmatter is malformed.") from exc

    # frontmatter 过长会破坏安装时的 manifest 读取边界。
    if len(str_frontmatter) > 1024:

        # 头部超长会破坏安装器对 manifest 的安全读取边界。
        raise AssertionError("> ERR: [Python] SKILL.md frontmatter must stay within 1024 characters.")

    # 返回 frontmatter 正文，供后续字段校验复用。
    return str_frontmatter

# verify_skill_frontmatter 校验 name/description 的字段顺序和内容约束。
def verify_skill_frontmatter(
    str_skill_text: str,
    str_frontmatter: str,
    source_audit_context: SourceAuditContext,
) -> None:
    """
    检查 SKILL.md frontmatter 的字段集合、名称格式和 description 语义。

    :param str_skill_text: SKILL.md 的完整文本。
    :param str_frontmatter: 已解析出的 frontmatter 正文。
    :param source_audit_context: frontmatter 校验依赖的模式与禁词上下文。
    :return: 不返回业务值；通过时表示 frontmatter 字段与 description 契约合法。
    :raises AssertionError: 当字段顺序、name、description 或禁词规则不满足时抛出。
    """

    # list_fields 按出现顺序收集 frontmatter 顶层字段名。
    list_fields: list[str] = []  # frontmatter 顶层字段顺序

    # 逐行提取非缩进且包含冒号的顶层字段。
    for str_line in str_frontmatter.splitlines():

        # 空行、缩进行和非键值行都不参与字段顺序校验。
        if not str_line.strip() or str_line.startswith(" ") or ":" not in str_line:

            # 非顶层键值行不参与 frontmatter 字段顺序校验。
            continue

        # 把字段名按原始出现顺序登记进列表。
        list_fields.append(str_line.split(":", 1)[0].strip())

    # frontmatter 只允许出现 name 和 description 两个顶层字段。
    if list_fields != ["name", "description"]:

        # 字段集合或顺序偏离 name/description 合同时，立即阻断发布。
        raise AssertionError(
            f"> ERR: [Python] SKILL.md frontmatter fields must be exactly name/description, got {list_fields}."
        )

    # match_name 提取单行 name 字段的最终值。
    match_name = re.search(r"^name:\s*([^\n]+)$", str_frontmatter, flags=re.MULTILINE)  # frontmatter 中 name 字段的匹配结果

    # match_description 提取 folded description 的缩进行正文块。
    match_description = re.search(r"description:\s*>-\s*\n((?:\s{2}.+\n?)*)", str_skill_text)  # folded description 正文块匹配结果

    # name 或 description 任一缺失都说明 frontmatter 不完整。
    if not match_name or not match_description:

        # 任一关键字段缺失时，立即阻断当前 frontmatter 校验。
        raise AssertionError("> ERR: [Python] SKILL.md frontmatter must define both name and folded description.")

    # str_skill_name 保存规范化后的 skill 名称。
    str_skill_name = match_name.group(1).strip()  # frontmatter 里的 skill 名称

    # str_description 把折叠 description 合并成单行，方便后续长度与前缀校验。
    str_description = " ".join(str_line.strip() for str_line in match_description.group(1).splitlines()).strip()  # 规范化后的单行 description 文本

    # skill 名称必须符合短横线命名规范。
    if not source_audit_context.pattern_skill_name.fullmatch(str_skill_name):

        # name 字段不满足短横线命名规范时，立即阻断发布。
        raise AssertionError("> ERR: [Python] SKILL.md frontmatter name is invalid.")

    # description 必须从触发语义 `Use when` 起始。
    if not str_description.startswith("Use when"):

        # description 缺少触发语义前缀时，不能作为合法 skill 摘要发布。
        raise AssertionError("> ERR: [Python] SKILL.md description must start with 'Use when'.")

    # description 过长会破坏安装器对 manifest 摘要的可读边界。
    if len(str_description) > 500:

        # 超出长度上限时，立即阻断 manifest 摘要发布。
        raise AssertionError(
            f"> ERR: [Python] SKILL.md description must stay within 500 characters, got {len(str_description)}."
        )

    # str_lowered_description 统一小写后用于 workflow 禁词扫描。
    str_lowered_description = str_description.lower()  # 小写化后的 description 文本

    # 逐个检查 workflow-only 术语是否误入触发描述。
    for str_term in source_audit_context.tuple_skill_description_workflow_terms:

        # 命中 workflow-only 术语时，必须阻断 SKILL.md 发布。
        if str_term in str_lowered_description:

            # 触发 workflow-only 禁词时，立即阻断当前 description。
            raise AssertionError("> ERR: [Python] SKILL.md description contains workflow-only terms.")

# verify_skill_load_resources 校验 SKILL.md Load 规则引用的 supporting resources。
def verify_skill_load_resources(
    str_skill_text: str,
    source_audit_context: SourceAuditContext,
) -> None:
    """
    检查 SKILL.md Load 规则引用的 supporting resources 是否存在。

    :param str_skill_text: SKILL.md 的完整文本。
    :param source_audit_context: Load 资源存在性校验依赖的路径上下文。
    :return: 不返回业务值；通过时表示 Load 规则存在且引用的资源都可解析。
    :raises AssertionError: 当 Load 规则缺失或引用缺失资源时抛出。
    """

    # list_load_lines 收集所有 `- Load ...` 规则行。
    list_load_lines = [  # SKILL.md 中的 Load 规则行列表
        str_line.strip()  # 规范化后的单条 Load 规则
        for str_line in str_skill_text.splitlines()  # SKILL.md 的全部原始行
        if str_line.strip().startswith("- Load ")  # 仅保留显式 Load 规则
    ]

    # 没有 Load 规则时，progressive-disclosure 资源暴露契约不成立。
    if not list_load_lines:

        # 缺少 Load 规则时，立即阻断 supporting resources 发布契约。
        raise AssertionError(
            "> ERR: [Python] SKILL.md must expose progressive-disclosure Load rules for supporting resources."
        )

    # list_missing_resources 累积所有被引用但缺失的资源路径。
    list_missing_resources: list[str] = []  # 缺失的 Load 资源列表

    # 逐条解析反引号里的资源路径并验证存在性。
    for str_line in list_load_lines:

        # match_resource 提取当前 Load 规则首个反引号资源路径。
        match_resource = re.search(r"`([^`]+)`", str_line)  # 当前 Load 规则资源匹配结果

        # 没有资源路径的 Load 行交给其他规则处理，这里不重复报错。
        if match_resource is None:

            # 缺少反引号资源路径的行留给其他规则处理，这里先跳过。
            continue

        # str_resource 保存当前 Load 规则引用的资源相对路径。
        str_resource = match_resource.group(1)  # 当前 Load 规则资源路径

        # 资源文件不存在时，登记进缺失列表等待统一报错。
        if not (source_audit_context.path_skill_root / str_resource).exists():

            # 记录当前 Load 规则引用但实际缺失的资源路径。
            list_missing_resources.append(str_resource)

    # 有缺失资源时，要把去重后的摘要一次性暴露给上层。
    if list_missing_resources:

        # str_missing_resource_summary 用排序后的唯一资源名构造错误摘要。
        str_missing_resource_summary = ", ".join(sorted(set(list_missing_resources)))  # 缺失 Load 资源摘要

        # 用统一 ERR 前缀抛出缺失资源错误。
        raise AssertionError(
            "> ERR: [Python] SKILL.md Load rules reference missing resources: "
            + str_missing_resource_summary
        )

# verify_skill_design_documents 检查 skill 标准文档与工程目标文档的模式说明。
def verify_skill_design_documents(source_audit_context: SourceAuditContext) -> None:
    """
    检查设计标准文档和工程目标文档是否仍保留要求的模式说明。

    :param source_audit_context: 设计文档审计依赖的路径与模式集合上下文。
    :return: 不返回业务值；通过时表示设计文档中的关键模式说明完整存在。
    :raises AssertionError: 当标准文档或工程目标文档缺失关键模式说明时抛出。
    """

    # path_standards 指向必须存在的 skill 标准文档。
    path_standards = source_audit_context.path_skill_root / "references" / "skill" / "skill-standards.md"  # 需要保留模式说明的 skill 标准文档路径

    # 缺少标准文档时，直接阻断 skill 标准审计。
    if not path_standards.exists():

        # 缺少标准文档时，立即阻断设计说明完整性校验。
        raise AssertionError("> ERR: [Python] references/skill/skill-standards.md is required.")

    # str_standards_text 保存标准文档全文，供模式名和关键短语双重校验。
    str_standards_text = path_standards.read_text(encoding="utf-8")  # skill 标准文档全文

    # str_standards_lower 把标准文档统一转小写，方便大小写无关短语检查。
    str_standards_lower = str_standards_text.lower()  # 小写化后的标准文档文本

    # 逐个确认标准文档仍然显式提到所有设计模式名称。
    for str_marker in source_audit_context.tuple_pattern_names:

        # 任一模式名缺失时，都说明 skill 标准文档已偏离当前治理要求。
        if str_marker not in str_standards_text:

            # 标准文档缺少设计模式名时，立即阻断发布前校验。
            raise AssertionError(
                f"> ERR: [Python] references/skill/skill-standards.md must mention {str_marker!r}."
            )

    # 继续确认标准文档保留 progressive-disclosure 和评估闭环关键短语。
    for str_marker in (
        "progressive disclosure",
        "pass-rate delta",
        "with and without the skill",
    ):

        # 任一关键短语缺失时，都说明文档丢失了既定工程目标。
        if str_marker not in str_standards_lower:

            # 关键短语缺失时，立即阻断 skill 标准文档通过。
            raise AssertionError(
                f"> ERR: [Python] references/skill/skill-standards.md must mention {str_marker!r}."
            )

    # str_goals_text 保存工程目标文档全文，供模式说明完整性复核。
    str_goals_text = (source_audit_context.path_skill_root / "ENGINEERING_DESIGN_GOALS.md").read_text(encoding="utf-8")  # 供模式复核使用的工程目标文档全文

    # 工程目标文档同样必须保留所有模式名称。
    for str_marker in source_audit_context.tuple_pattern_names:

        # 任一模式名缺失时，都说明工程目标文档与当前标准不一致。
        if str_marker not in str_goals_text:

            # 工程目标文档缺少模式名时，立即阻断一致性校验。
            raise AssertionError(
                f"> ERR: [Python] ENGINEERING_DESIGN_GOALS.md must preserve the {str_marker} pattern."
            )

# verify_skill_eval_assets 确认 skill 效果评估运行时资产齐全。
def verify_skill_eval_assets(source_audit_context: SourceAuditContext) -> None:
    """
    检查效果评估运行时资产是否完整存在。

    :param source_audit_context: 效果评估资产校验依赖的路径上下文。
    :return: 不返回业务值；通过时表示效果评估 helper 资产齐全。
    :raises AssertionError: 当必需评估资产缺失时抛出。
    """

    # path_evaluation 指向当前发布必须带上的 evaluation helper。
    path_evaluation = source_audit_context.path_skill_root / "scripts" / "python" / "validation" / "evaluation.py"  # 效果评估入口 helper 路径

    # path_eval_suite 指向批量评测套件实际调用的 eval_suite helper。
    path_eval_suite = source_audit_context.path_skill_root / "scripts" / "python" / "validation" / "eval_suite.py"  # 效果评估套件 helper 路径

    # list_required_eval_paths 记录当前 skill 发布要求的评估 helper 路径。
    list_required_eval_paths = [path_evaluation, path_eval_suite]  # 效果评估必需资产路径列表

    # list_missing_eval 累积缺失的效果评估资产相对路径。
    list_missing_eval: list[str] = []  # 缺失评估资产列表

    # 逐个确认效果评估资产是否实际落盘。
    for path_eval in list_required_eval_paths:

        # 缺失时把 skill 相对路径压进摘要，方便人工补齐。
        if not path_eval.exists():

            # 记录当前缺失的效果评估资产相对路径。
            list_missing_eval.append(path_eval.relative_to(source_audit_context.path_skill_root).as_posix())

    # 任一评估资产缺失都要阻断 validate 主链。
    if list_missing_eval:

        # 一旦存在缺失资产，就立即阻断效果评估资产校验。
        raise AssertionError("> ERR: [Python] Skill evaluation assets are missing: " + ", ".join(list_missing_eval))

# verify_legacy_terms 扫描旧领域词，只允许白名单路径保留。
def verify_legacy_terms(
    settings: dict[str, Any],
    source_audit_context: SourceAuditContext,
) -> None:
    """
    扫描 legacy 领域词，只允许白名单路径或特定行内解释性文本保留。

    :param settings: 当前 validate 链路加载出的治理配置字典。
    :param source_audit_context: legacy 词扫描依赖的路径与规则上下文。
    :return: 不返回业务值；通过时表示旧领域词未泄漏到禁止位置。
    :raises AssertionError: 当白名单外文件出现旧领域词时抛出。
    """

    # set_allowlist 保存允许保留 legacy 领域词的文件路径白名单。
    set_allowlist = set(settings.get("validation", {}).get("legacy_term_allowlist", []))  # legacy 词白名单路径集合

    # list_violations 累积命中禁止 legacy 领域词的路径与行号。
    list_violations: list[str] = []  # legacy 词违规列表

    # 沿当前源码审计文件集逐个扫描 legacy 词是否泄漏。
    for path_file in source_audit_context.func_iter_skill_files():

        # str_relative_path 用于白名单判断和错误摘要输出。
        str_relative_path = path_file.relative_to(source_audit_context.path_skill_root).as_posix()  # skill 相对文件路径

        # 白名单文件允许保留 legacy 词，直接跳过。
        if str_relative_path in set_allowlist:

            # legacy 白名单文件允许保留术语说明，当前文件直接跳过。
            continue

        # str_text 统一以忽略解码错误的方式读取，避免混合编码噪声中断 legacy 扫描。
        str_text = path_file.read_text(encoding="utf-8", errors="ignore")  # 当前文件全文

        # 逐行定位 legacy 词命中位置，并结合路径专属解释规则过滤合法文本。
        for int_line_number, str_line in enumerate(str_text.splitlines(), start=1):

            # bool_has_legacy_term 表示当前行命中了任一旧领域词片段。
            bool_has_legacy_term = any(str_term in str_line for str_term in source_audit_context.tuple_legacy_terms)  # 当前行是否命中任一 legacy 领域词片段

            # bool_line_allowed 表示当前路径与当前文本组合在解释性规则里被允许。
            bool_line_allowed = _allowed_dependency_term_line(str_relative_path, str_line)  # 当前行是否属于允许场景

            # 命中 legacy 词且不在允许场景内时，登记违规位置。
            if bool_has_legacy_term and not bool_line_allowed:

                # 记录当前 legacy 词泄漏的相对路径与行号。
                list_violations.append(f"{str_relative_path}:{int_line_number}")

    # 存在违规时，要把排序后的摘要显式抛给上层。
    if list_violations:

        # str_violation_summary 统一压成单行，方便人工逐项清理。
        str_violation_summary = ", ".join(sorted(list_violations))  # legacy 词违规摘要

        # 用统一 ERR 前缀抛出 legacy 词泄漏错误。
        raise AssertionError(
            "> ERR: [Python] Legacy generation terms found outside allowlist: " + str_violation_summary
        )

# verify_dependency_schema 校验 defaults 里的跨 skill 依赖与 FPGA developer 路由合同。
def verify_dependency_schema(settings: dict[str, Any]) -> None:
    """
    检查 defaults 中的依赖清单和 FPGA developer 路由是否符合约束。

    :param settings: 当前 validate 链路加载出的治理配置字典。
    :return: 不返回业务值；通过时表示依赖 URL、分组和路由策略全部符合合同。
    :raises AssertionError: 当依赖 URL、技能分组或 FPGA developer 路由配置偏离约束时抛出。
    """

    # 路由与依赖配置 helper 只在真正需要解析 defaults 时才延迟导入。
    from scripts.python.workflow.config import fpga_developer_routing_settings, skill_dependency_settings

    # dict_dependencies 保存当前 defaults 里的技能依赖配置。
    dict_dependencies = skill_dependency_settings(settings)  # 技能依赖配置字典

    # dict_routing 保存当前 defaults 里的 FPGA developer 路由配置。
    dict_routing = fpga_developer_routing_settings(settings)  # FPGA developer 路由配置字典

    # set_required_urls 汇总强制依赖组对外声明的全部 URL。
    set_required_urls = {item["url"] for item in dict_dependencies["required"]}  # required 依赖 URL 集合

    # set_recommended_urls 汇总可选增强能力的推荐依赖 URL。
    set_recommended_urls = {item["url"] for item in dict_dependencies["recommended"]}  # 推荐增强能力依赖 URL 的去重集合

    # set_manual_fallback_urls 汇总缺失开发者 skill 时的手动回退依赖 URL。
    set_manual_fallback_urls = {item["url"] for item in dict_dependencies["manual_fallback"]}  # 缺失开发者 skill 时的回退依赖 URL 去重集合

    # required 依赖只能包含 RemoteSSH 仓库。
    if set_required_urls != {"https://github.com/Eriemon/remote-ssh.git"}:

        # required 依赖 URL 集合一旦偏离约束，就立即阻断校验。
        raise AssertionError(f"> ERR: [Python] Unexpected required dependency URLs: {sorted(set_required_urls)}")

    # recommended 依赖只能包含 superpowers 与 context-engineering 两个仓库。
    if set_recommended_urls != {
        "https://github.com/obra/superpowers.git",
        "https://github.com/muratcankoylan/Agent-Skills-for-Context-Engineering.git",
    }:

        # 推荐依赖组一旦偏离增强能力清单，就立即阻断当前配置。
        raise AssertionError(
            f"> ERR: [Python] Unexpected recommended dependency URLs: {sorted(set_recommended_urls)}"
        )

    # manual fallback 依赖只能包含 FPGA-Agent-skills 单一仓库。
    if set_manual_fallback_urls != {"https://github.com/adeleempurpled290/FPGA-Agent-skills.git"}:

        # 手动回退依赖 URL 集合一旦偏离约束，就立即阻断校验。
        raise AssertionError(
            f"> ERR: [Python] Unexpected manual fallback dependency URLs: {sorted(set_manual_fallback_urls)}"
        )

    # dict_fpga_dependency 提取 manual_fallback 里 FPGA-Agent-skills 的配置项。
    dict_fpga_dependency = next(  # FPGA-Agent-skills 对应的手动回退依赖配置
        item  # manual_fallback 里的当前依赖项
        for item in dict_dependencies["manual_fallback"]  # 遍历全部手动回退依赖项
        if item["id"] == "fpga-agent-skills"  # 只保留 FPGA-Agent-skills 的依赖配置
    )

    # FPGA-Agent-skills 依赖必须覆盖全部 8 个 Vivado/Vitis 技能。
    if len(dict_fpga_dependency["skills"]) != 8:

        # 技能数量不足 8 个时，立即阻断手动回退依赖校验。
        raise AssertionError("> ERR: [Python] FPGA-Agent-skills dependency must include all 8 Vivado/Vitis skills.")

    # selection_policy 必须保持首次 FPGA workflow 询问策略。
    if dict_routing["selection_policy"] != "ask_on_first_fpga_workflow":

        # 路由策略偏离首次询问合同后，立即阻断配置校验。
        raise AssertionError("> ERR: [Python] FPGA developer routing must ask on first FPGA workflow.")

    # value_fpga_required 保存“开发者 skill 存在时是否仍强制 FPGA-Agent-skills”的当前配置。
    value_fpga_required = dict_routing["fpga_agent_required_when_developer_present"]  # FPGA-Agent-skills 强制开关

    # 当配置仍是布尔真值时，说明错误地把 FPGA-Agent-skills 设成了强制依赖。
    if isinstance(value_fpga_required, bool) and value_fpga_required:

        # 检测到错误的强制依赖语义时，立即阻断当前路由配置。
        raise AssertionError(
            "> ERR: [Python] FPGA-Agent-skills must not be required when a developer skill is installed."
        )

    # AMD-Xilinx 路由必须稳定识别 vivado-developer 和 vitis-developer。
    if dict_routing["vendors"]["amd_xilinx"]["skills"] != [
        "vivado-developer",
        "vitis-developer",
    ]:

        # AMD-Xilinx 路由技能集偏离约束时，立即阻断厂商路由校验。
        raise AssertionError(
            "> ERR: [Python] AMD-Xilinx developer routing must recognize vivado-developer and vitis-developer."
        )

    # Pangomicro 路由必须稳定识别板卡流程绑定的 pds-developer 技能。
    if dict_routing["vendors"]["pangomicro"]["skills"] != ["pds-developer"]:

        # PangoMicro 厂商路由缺少唯一开发者技能时，立即阻断配置校验。
        raise AssertionError("> ERR: [Python] PangoMicro developer routing must recognize pds-developer.")

# verify_hardcoded_paths 扫描硬编码绝对路径，只允许配置和集成文档保留。
def verify_hardcoded_paths(source_audit_context: SourceAuditContext) -> None:
    """
    扫描硬编码绝对路径，只允许配置或指定文档中的合法说明文本保留。

    :param source_audit_context: 硬编码路径扫描依赖的路径与模式上下文。
    :return: 不返回业务值；通过时表示没有私有绝对路径泄漏进禁止位置。
    :raises AssertionError: 当白名单外文件出现硬编码绝对路径时抛出。
    """

    # set_allowed 保存允许保留绝对路径说明文本的固定路径白名单。
    set_allowed = {"config/defaults.json", "references/integration/configuration.md"}  # 允许出现绝对路径说明文本的文件白名单

    # list_violations 累积所有命中硬编码绝对路径的相对文件路径。
    list_violations: list[str] = []  # 私有绝对路径违规文件列表

    # 沿当前源码审计文件集逐个扫描私有绝对路径泄漏。
    for path_file in source_audit_context.func_iter_skill_files():

        # str_relative_path 记录本轮硬编码路径扫描使用的 skill 相对文件路径。
        str_relative_path = path_file.relative_to(source_audit_context.path_skill_root).as_posix()  # 当前硬编码路径扫描目标的 skill 相对路径

        # 白名单文件允许保留绝对路径说明文本，直接跳过。
        if str_relative_path in set_allowed:

            # 白名单文档允许保留绝对路径说明文本，当前文件直接跳过。
            continue

        # str_text 统一以忽略解码错误的方式读取，避免混合编码阻断绝对路径扫描。
        str_text = path_file.read_text(encoding="utf-8", errors="ignore")  # 当前硬编码路径扫描目标的全文文本

        # 命中绝对路径正则时，把文件登记进违规列表。
        if source_audit_context.pattern_absolute_path.search(str_text):

            # 记录当前命中私有绝对路径的违规文件路径。
            list_violations.append(str_relative_path)

    # 存在违规文件时，要把排序后的摘要显式抛出。
    if list_violations:

        # str_violation_summary 把命中的绝对路径违规文件压缩成单行摘要。
        str_violation_summary = ", ".join(sorted(list_violations))  # 私有绝对路径违规摘要

        # 用统一 ERR 前缀暴露硬编码路径泄漏错误。
        raise AssertionError(
            "> ERR: [Python] Hardcoded absolute paths found outside config/docs: "
            + str_violation_summary
        )

# verify_no_ref_dependencies 扫描活跃文件和候选 release，确保不再依赖 ref 临时目录。
def verify_no_ref_dependencies(source_audit_context: SourceAuditContext) -> None:
    """
    扫描活跃文件与候选 release，确认不存在 ref 临时目录依赖。

    :param source_audit_context: ref 依赖审计依赖的路径与规则上下文。
    :return: 不返回业务值；通过时表示活跃文件与候选 release 未依赖 ref 临时目录。
    :raises AssertionError: 当活跃文件或候选 release 仍然引用 ref 临时目录时抛出。
    """

    # list_violations 累积所有命中 ref 临时目录依赖的文件。
    list_violations: list[str] = []  # ref 临时目录依赖违规列表

    # list_active_paths 先从 SKILL.md 开始收集必须扫描的活跃文件。
    list_active_paths = [source_audit_context.path_skill_root / "SKILL.md"]  # 活跃文件初始集合

    # 源码仓布局下，还要把仓库级治理文件和对应 release 目录纳入扫描。
    if source_audit_context.bool_source_repository_layout:

        # version 只在源码仓布局下才需要读取，用来定位候选 release 目录。
        from scripts.python.version import __version__

        # path_candidate_release 指向当前版本实际应检查的候选 release 目录。
        path_candidate_release = (  # 当前版本候选 release 目录
            source_audit_context.path_project_root / "dist" / f"readable-verilog-generator-v{__version__}"  # 当前版本号对应的 dist 目录
        )

        # 把仓库级治理与 smoke 入口一并纳入活跃文件扫描范围。
        list_active_paths.extend(
            [
                source_audit_context.path_project_root / "AGENTS.md",
                source_audit_context.path_project_root / "docs" / "development" / "DEVELOPMENT.md",
                source_audit_context.path_project_root / "docs" / "handoff" / "HANDOFF.md",
                source_audit_context.path_project_root / "docs" / "git_manager" / "CHANGELOG.md",
                source_audit_context.path_project_root / "tests" / "smoke" / "run_smoke.py",
            ]
        )

    # 非源码仓布局没有 dist 扫描目标，直接保留空 release 指针。
    else:

        # 非源码仓布局下没有 dist 目录需要继续扫描。
        path_candidate_release = None  # 非源码仓布局下没有候选 release 目录

    # 把 references 顶层资源加入活跃文件扫描列表。
    list_active_paths.extend(sorted((source_audit_context.path_skill_root / "references").glob("*")))

    # path_scripts_root 指向本轮 ref 依赖扫描要展开的 scripts 根目录。
    path_scripts_root = source_audit_context.path_skill_root / "scripts"  # 活跃文件扫描使用的 scripts 根目录

    # 把 scripts 根目录下的直接子资源并入活跃文件扫描列表。
    list_active_paths.extend(sorted(path_scripts_root.glob("*")))

    # path_python_scripts_root 锚定后续 rglob 展开的 Python helper 子树根。
    path_python_scripts_root = path_scripts_root / "python"  # 递归纳入活跃文件扫描的 Python helper 子树根目录

    # Python scripts 目录存在时，要递归纳入全部活跃 helper。
    if path_python_scripts_root.exists():

        # 把 Python helper 树完整并入活跃文件扫描范围。
        list_active_paths.extend(sorted(path_python_scripts_root.rglob("*")))

    # 逐个扫描活跃文件里的 ref 临时目录依赖。
    for path_file in list_active_paths:

        # 缺失路径或目录条目都不参与文本扫描。
        if not path_file.exists() or not path_file.is_file():

            # 目录或已消失条目不参与 ref 临时目录文本扫描。
            continue

        # str_relative_path 把当前活跃文件压成项目相对路径。
        str_relative_path = source_audit_context.func_project_relative(path_file)  # 当前活跃文件项目相对路径

        # str_text 统一以忽略解码错误的方式读取，避免混合编码阻断审计。
        str_text = path_file.read_text(encoding="utf-8", errors="ignore")  # 当前活跃文件全文

        # 命中 ref 依赖且当前路径不在允许白名单时，登记违规文件。
        if (
            source_audit_context.pattern_ref_dependency.search(str_text)
            and not allowed_ref_dependency_path(str_relative_path)
        ):

            # 记录当前活跃文件里命中的 ref 临时目录依赖。
            list_violations.append(source_audit_context.func_project_relative(path_file))

    # 候选 release 目录存在时，还要递归扫描其中的发布内容。
    if path_candidate_release is not None and path_candidate_release.exists():

        # 逐个扫描候选 release 中的文件，继续检查 ref 依赖泄漏。
        for path_file in path_candidate_release.rglob("*"):

            # 目录条目不参与文本扫描。
            if not path_file.is_file():

                # 目录节点不参与候选 release 文件内容扫描。
                continue

            # Python 缓存和编译产物不属于发布内容依赖检查范围。
            if "__pycache__" in path_file.parts or path_file.suffix.lower() in {".pyc", ".pyo"}:

                # 缓存或编译产物不代表真实发布内容，直接跳过。
                continue

            # 命中 ref 依赖时，把候选 release 文件登记为违规。
            if source_audit_context.pattern_ref_dependency.search(
                path_file.read_text(encoding="utf-8", errors="ignore")
            ):

                # 记录候选 release 中命中的 ref 临时目录依赖文件。
                list_violations.append(source_audit_context.func_project_relative(path_file))

    # 任一活跃文件或发布文件仍依赖 ref 时，都要阻断发布卫生通过。
    if list_violations:

        # 只要还有 ref 依赖留在活跃文件里，就立即阻断发布卫生校验。
        raise AssertionError("> ERR: [Python] Ref temporary directory dependencies remain in active files.")

# line_contains_any 统一判断当前行是否包含任一允许标记片段。
def line_contains_any(str_line: str, tuple_markers: tuple[str, ...]) -> bool:
    """
    判断单行文本是否包含任一允许标记片段。

    :param str_line: 当前待检查的单行文本。
    :param tuple_markers: 允许命中的标记片段集合。
    :return: 返回布尔值；True 表示当前行命中了至少一个标记片段。
    """

    # 把包含关系判定收束到单一 helper，避免各路径规则重复写 `any(...)`。
    return any(str_marker in str_line for str_marker in tuple_markers)

# _allowed_dependency_term_line 根据路径语义判断 legacy 词是否属于允许说明文本。
def _allowed_dependency_term_line(str_relative_path: str, str_line: str) -> bool:
    """
    根据相对路径判断 legacy 术语是否落在允许的说明文本里。

    :param str_relative_path: 当前文件的 skill 相对路径。
    :param str_line: 当前待检查的单行文本。
    :return: 返回布尔值；True 表示当前路径和文本组合属于允许场景。
    """

    # str_lower_line 统一小写后，用于大小写无关的说明文本匹配。
    str_lower_line = str_line.lower()  # 当前行的小写文本

    # defaults 配置允许保留依赖声明与开发者路由说明里的 legacy 词。
    if str_relative_path == "config/defaults.json":

        # defaults 命中依赖或路由白名单标记时，允许保留该 legacy 说明文本。
        return line_contains_any(
            str_line,
            (
                "fpga-agent-skills",
                "Vivado/Vitis",
                "vitis-hls-synthesis",
                "vitis-developer",
                '"skill": "vitis-',
                '"source_path": "vitis-',
            ),
        )

    # SKILL.md 允许保留 dependency 与 developer routing 说明文本。
    if str_relative_path == "SKILL.md":

        # SKILL.md 命中 dependency 或 developer routing 说明时允许保留。
        return (
            "dependency" in str_lower_line
            or "route to the installed FPGA" in str_line
            or "developer routing" in str_lower_line
            or "tb_language" in str_line
        )

    # host integration 文档允许保留验证 testbench 与 tb_language 说明。
    if str_relative_path == "references/integration/host-integration.md":

        # host integration 文档命中 testbench 说明时允许保留。
        return "verification testbench" in str_lower_line or "tb_language" in str_line

    # integration configuration 文档允许保留依赖、分组和路由相关说明文本。
    if str_relative_path == "references/integration/configuration.md":

        # integration configuration 命中依赖或分组说明时允许保留。
        return line_contains_any(
            str_line,
            (
                "dependency",
                "provides",
                "recommended groups",
                "required groups",
                "Vivado/Vitis",
                "Vitis/*/settings64.sh",
                "vitis-hls-synthesis",
                "vitis-developer",
                "developer routing",
            ),
        )

    # validate facade 允许保留 FPGA 依赖、工具链和 wrapper 相关说明文本。
    if str_relative_path == "scripts/python/validation/validate_verilog_skill.py":

        # validate facade 命中依赖、工具链或 wrapper 说明时允许保留。
        return line_contains_any(
            str_line,
            (
                "FPGA-Agent-skills dependency",
                "vitis-hls-synthesis",
                "vitis-developer",
                '"skill": "vitis-',
                '"source_path": "vitis-',
                "vitis_command",
                "VCS+Verdi",
                "/tools/Xilinx/Vitis/*/settings64.sh",
                "simulator_backend",
                "Vivado",
                "Vitis",
                "/tools/Xilinx/",
                "args.vitis_wrapper",
                "--vitis-wrapper",
            ),
        )

    # 依赖管理脚本允许保留 FPGA-Agent 与 Vivado/Vitis 依赖说明。
    if str_relative_path == "scripts/python/toolchain/manage_skill_dependencies.py":

        # 依赖管理脚本命中 FPGA 依赖说明时允许保留。
        return line_contains_any(
            str_line,
            (
                "FPGA-Agent",
                "Vivado/Vitis",
                "vitis-developer",
                "vitis-hls-synthesis",
                '"vivado-',
            ),
        )

    # testbench 生成脚本允许保留 `tb-language` 参数说明。
    if str_relative_path == "scripts/python/generation/tb_generator.py":

        # testbench 生成脚本命中 tb-language 说明时允许保留。
        return "tb-language" in str_lower_line

    # CLI generation commands 允许保留 vitis wrapper 参数绑定。
    if str_relative_path == "scripts/python/workflow/cli_generation_commands.py":

        # CLI generation commands 命中 vitis wrapper 参数绑定时允许保留。
        return "args.vitis_wrapper" in str_line

    # CLI parser 允许保留 vitis wrapper 选项文本。
    if str_relative_path == "scripts/python/workflow/cli_parser.py":

        # CLI parser 命中 vitis wrapper 选项文本时允许保留。
        return "--vitis-wrapper" in str_line

    # 远程验证脚本允许保留工具链路径模式和 simulator backend 说明。
    if str_relative_path == "scripts/python/remote/remote_validate_verilog_skill.py":

        # 远程验证脚本命中工具链路径或 simulator backend 说明时允许保留。
        return line_contains_any(
            str_line,
            (
                "/tools/Xilinx/Vitis/*/settings64.sh",
                "selected_backend",
                "simulator_backend",
            ),
        )

    # run_smoke 允许保留工具链选择和 settings64.sh 诊断文本。
    if str_relative_path == "tests/smoke/run_smoke.py":

        # run_smoke 命中工具链选择诊断文本时允许保留。
        return line_contains_any(
            str_line,
            (
                "vitis-hls-synthesis",
                "vitis-developer",
                "vitis_command",
                "/tools/Xilinx/Vitis/2022.2/settings64.sh",
                "/tools/Xilinx/Vitis/*/settings64.sh",
                "Configured Xilinx settings64.sh",
                "Multiple Xilinx toolchain settings64.sh candidates",
            ),
        )

    # dependency_gates 允许保留 FPGA 依赖与厂商路由诊断文本。
    if str_relative_path == "tests/smoke/dependency_gates.py":

        # dependency_gates 命中 FPGA 依赖或厂商路由诊断时允许保留。
        return line_contains_any(
            str_line,
            (
                "FPGA-Agent",
                "Vivado/Vitis",
                "vitis-developer",
                "vitis-hls-synthesis",
                '"vivado-',
                "AMD-Xilinx",
                "PangoMicro",
            ),
        )

    # toolchain_gates 允许保留工具链路径与 simulator backend 诊断文本。
    if str_relative_path == "tests/smoke/toolchain_gates.py":

        # toolchain_gates 命中工具链路径或 simulator backend 诊断时允许保留。
        return line_contains_any(
            str_line,
            (
                "vitis-hls-synthesis",
                "vitis-developer",
                "vitis_command",
                "/tools/Xilinx/Vitis/2022.2/settings64.sh",
                "/tools/Xilinx/Vitis/*/settings64.sh",
                "Configured Xilinx settings64.sh",
                "Multiple Xilinx toolchain settings64.sh candidates",
                "simulator_backend",
            ),
        )

    # verify_repair 允许保留 testbench 语言相关说明文本。
    if str_relative_path == "scripts/python/existing_rtl/verify_repair.py":

        # verify_repair 命中 testbench 语言说明时允许保留。
        return "tb_languages" in str_lower_line or "tb_language" in str_line

    # 其他 tests/ 文件允许保留 FPGA 工具链与 simulator backend 诊断文本。
    if str_relative_path.startswith("tests/"):

        # 其余 tests/ 文件命中 FPGA 诊断文本时允许保留。
        return line_contains_any(
            str_line,
            (
                "Vivado",
                "Vitis",
                "/tools/Xilinx/",
                "simulator_backend",
            ),
        )

    # 其余路径一律不允许保留 legacy 说明文本。
    # 其余路径没有白名单理由，统一返回不允许。
    return False

# allowed_ref_dependency_path 定义允许保留 ref 临时目录说明文本的极小白名单。
def allowed_ref_dependency_path(str_relative_path: str) -> bool:
    """
    判断当前路径是否允许出现临时参考目录说明文本。

    :param str_relative_path: 当前文件的项目相对路径。
    :return: 返回布尔值；True 表示当前路径属于允许保留 ref 说明的极小白名单。
    """

    # 当前只有根 AGENTS.md 允许保留对 ref 目录的治理性说明。
    return str_relative_path == "AGENTS.md"
