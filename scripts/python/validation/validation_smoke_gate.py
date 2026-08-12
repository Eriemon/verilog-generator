"""validate_verilog_skill 的 CLI 冒烟与 existing RTL helper。"""

# future annotations 让 dataclass 和回调注解保持前向引用友好。
from __future__ import annotations

# Callable 用于约束外层注入的 CLI 执行器与路径转换器。
from collections.abc import Callable

# json 负责读取与写出 smoke 场景中的协议文件。
import json

# dataclass 用来收束 verify-existing 与 patch fixture 的结构化输入。
from dataclasses import dataclass

# pathlib 提供 smoke 目录、fixture 文件和报告路径表达能力。
from pathlib import Path

# Any 用于描述 workflow JSON 的动态载荷结构。
from typing import Any

# VerifyExistingRequest 收束一次 verify-existing CLI 需要的全部输入。
@dataclass(frozen=True)
class VerifyExistingRequest:
    """
    描述一次 verify-existing CLI 调用需要的路径与模式。

    :param path_source: 待检查的 existing RTL 源文件路径。
    :param path_out_dir: 当前调用写出报告与中间产物的目录。
    :param path_spec_source: verify-existing 使用的规格文档路径。
    :param str_automation_mode: verify-existing 自动化模式。
    :param str_tb_mode: verify-existing 的 testbench 处理模式。
    :param path_testbench_source: augment 模式下可选的显式 testbench 来源。
    :param path_decision_source: 恢复已确认 patch 时可选的决策 JSON。
    :param bool_allow_strict_exit_failure: 是否允许预期内 strict 非零退出。
    :return: 不返回业务值；实例化完成即表示请求载荷可供 helper 复用。
    """

    # path_source 锚定本次 verify-existing 真正检查的 RTL 文件。
    path_source: Path  # 本次 verify-existing 直接读取的 RTL 文件

    # path_out_dir 收纳报告、patch 计划与恢复决策等中间产物。
    path_out_dir: Path  # verify-existing 输出目录

    # path_spec_source 提供 verify-existing 对 RTL 的自然语言约束。
    path_spec_source: Path  # verify-existing 规格文件

    # str_automation_mode 直接映射外层 CLI 的自动化策略。
    str_automation_mode: str  # verify-existing 自动化模式

    # str_tb_mode 决定 testbench 是生成还是 augment。
    str_tb_mode: str  # 控制 testbench 生成还是增强的执行模式

    # path_testbench_source 只在 augment 流程下才会真正使用。
    path_testbench_source: Path | None = None  # augment 模式 testbench 来源

    # path_decision_source 只在恢复已确认 patch 时才参与命令拼装。
    path_decision_source: Path | None = None  # 已确认 patch 的决策 JSON

    # bool_allow_strict_exit_failure 保留旧 smoke 对人工确认边界的验证语义。
    bool_allow_strict_exit_failure: bool = False  # 是否接受预期内 strict 非零退出

# ExistingPatchCase 描述单个 existing RTL patch fixture 的静态配置。
@dataclass(frozen=True)
class ExistingPatchCase:
    """
    描述一个 existing RTL 修复确认流程 fixture。

    :param str_case_dir: smoke 目录下的场景子目录名称。
    :param str_source_name: existing_rtl 示例目录中的 RTL 文件名。
    :param str_spec_name: existing_rtl 示例目录中的规格文件名。
    :param str_copy_name: 复制到 smoke 目录后的 RTL 文件名。
    :param str_automation_mode: 首次 verify-existing 使用的自动化策略。
    :param str_decision_evidence: 恢复 patch 时写入决策文件的证据文本。
    :param str_expected_category: 可选的预期 patch 分类。
    :param str_error_label: 场景断言消息中保留的人类可读标签。
    :return: 不返回业务值；实例化完成即表示 patch 场景配置可供复用。
    """

    # str_case_dir 同时决定 smoke 输出目录和测试报告定位。
    str_case_dir: str  # patch 场景 smoke 子目录名

    # str_source_name 指向待复制的 existing RTL fixture 文件。
    str_source_name: str  # existing RTL 源文件名

    # str_spec_name 锚定该 fixture 对应的规格说明文档。
    str_spec_name: str  # existing RTL 规格文件名

    # str_copy_name 保持 smoke 目录里的 RTL 副本命名兼容旧流程。
    str_copy_name: str  # smoke 目录中的 RTL 副本名

    # str_automation_mode 决定首次 verify-existing 使用的自动化级别。
    str_automation_mode: str  # patch 场景自动化模式

    # str_decision_evidence 会被写进恢复 patch 的决策 JSON。
    str_decision_evidence: str  # patch 决策证据文本

    # str_expected_category 为空时表示当前场景只检查 patch 产物是否存在。
    str_expected_category: str | None = None  # 预期 patch 分类

    # str_error_label 保留旧断言文本里的人类可读场景称呼。
    str_error_label: str = "RTL fix"  # 断言使用的场景标签

# read_json_file 统一负责验证 helper 中的 UTF-8 JSON 读取。
def read_json_file(path_json: Path) -> dict[str, Any]:
    """
    按 UTF-8 读取 JSON 文件并返回字典载荷。

    :param path_json: 需要读取的 JSON 文件路径。
    :return: 返回 JSON 反序列化后的字典载荷。
    """

    # 先按 UTF-8 读出原始文本，避免系统默认编码污染 smoke 结果。
    str_json_text = path_json.read_text(encoding="utf-8")  # 原始 JSON 文本

    # 再把原始文本反序列化成字典对象，供后续断言直接读取字段。
    dict_payload = json.loads(str_json_text)  # JSON 字典载荷

    # 把字典载荷返回给上层 helper 继续做协议断言。
    return dict_payload

# run_canonical_cli_flow 负责覆盖 canonical scaffold 到 validate 的最小链路。
def run_canonical_cli_flow(
    path_example_spec: Path,
    path_smoke_dir: Path,
    *,
    func_run_verilog_cli: Callable[..., None],
) -> None:
    """
    运行 canonical scaffold / prompt / workflow / validate 链。

    :param path_example_spec: canonical 示例规格文件路径。
    :param path_smoke_dir: 当前 smoke 运行目录根。
    :param func_run_verilog_cli: 外层注入的 workflow CLI 执行回调。
    :return: 不返回业务值；通过时表示 canonical CLI 链未发现阻断。
    :raises AssertionError: 当 validate 报告仍含 warnings 时抛出。
    """

    # 先把 canonical CLI 专属产物目录锚定到本轮 smoke 根下。
    path_cli_dir = path_smoke_dir / "cli"  # canonical CLI 产物目录

    # workflow 尝试链单独落在子目录中，便于直接定位 generated RTL 结果。
    path_workflow_dir = path_smoke_dir / "workflow"  # canonical workflow 尝试链目录

    # validate JSON 报告保持固定文件名，便于后续统一读取。
    path_canonical_report = path_cli_dir / "validation-report.json"  # canonical validate 报告路径

    # scaffold 步骤先验证 spec 模板可以正常落盘。
    func_run_verilog_cli(
        "scaffold",
        "--name",
        "erie_adapter",
        "--out",
        str(path_cli_dir / "spec.json"),
        "--no-state",
    )

    # prompt 步骤继续验证 canonical spec 能生成最终提示词。
    func_run_verilog_cli(
        "prompt",
        "--spec",
        str(path_example_spec),
        "--out",
        str(path_cli_dir / "prompt.md"),
        "--no-state",
    )

    # run-workflow 步骤覆盖 requirements 到 generated RTL 的基线路径。
    func_run_verilog_cli(
        "run-workflow",
        "--spec",
        str(path_example_spec),
        "--out-dir",
        str(path_workflow_dir),
        "--model-provider",
        "mock",
        "--no-external",
    )

    # validate 步骤确认 workflow 生成目录能被本地交付门禁接受。
    func_run_verilog_cli(
        "validate",
        "--spec",
        str(path_example_spec),
        "--path",
        str(path_workflow_dir / "attempt-001" / "rtl" / "generated"),
        "--no-external",
        "--report-json",
        str(path_canonical_report),
        "--no-state",
    )

    # 读取 validate 报告，检查 canonical 路径是否仍残留 warnings。
    dict_canonical_report = read_json_file(path_canonical_report)  # canonical validate 报告载荷

    # 只要 validate 还给 warnings，就说明 canonical 冒烟链尚未达到基线。
    if dict_canonical_report.get("warnings") != 0:

        # 用统一 ERR 前缀把 canonical validate 的失败语义抛回主流程。
        raise AssertionError("> ERR: [Python] Canonical validate emitted warnings.")

# run_use_case_cli_flows 逐个遍历 use-case 示例并验证模板 id 贯穿。
def run_use_case_cli_flows(
    path_use_case_examples_dir: Path,
    path_smoke_dir: Path,
    *,
    func_run_verilog_cli: Callable[..., None],
    func_project_artifact_path: Callable[[str | Path], Path],
) -> None:
    """
    遍历所有 use-case 示例并完成 prompt / workflow / validate 检查。

    :param path_use_case_examples_dir: use-case 示例规格目录。
    :param path_smoke_dir: 当前 smoke 运行目录根。
    :param func_run_verilog_cli: 外层注入的 workflow CLI 执行回调。
    :param func_project_artifact_path: 外层注入的项目工件路径解析回调。
    :return: 不返回业务值；通过时表示全部 use-case 示例未发现阻断。
    """

    # 先按稳定顺序枚举所有 use-case spec，避免不同平台扫描顺序漂移。
    for path_example_spec in sorted(path_use_case_examples_dir.glob("*.json")):

        # family 名称直接来自 spec 文件名，用于目录、报告和断言定位。
        str_family = path_example_spec.stem  # 当前 use-case family 标识

        # 当前 family 的 smoke 根目录单独隔离，便于排查失败样本。
        path_family_dir = path_smoke_dir / "cli-use-case" / str_family  # 当前 use-case 的专属运行目录

        # validate 报告固定写到 family 目录下，方便后续读取 warnings。
        path_family_report = path_family_dir / "validation-report.json"  # 当前 use-case 的 validate 报告文件

        # 把单个 family 的 prompt/workflow/validate 验证交给专用 helper 处理。
        run_single_use_case_flow(
            path_example_spec,
            str_family,
            path_family_dir,
            path_family_report,
            func_run_verilog_cli=func_run_verilog_cli,
            func_project_artifact_path=func_project_artifact_path,
        )

# run_single_use_case_flow 负责单个 use-case family 的完整 CLI 验证。
def run_single_use_case_flow(
    path_example_spec: Path,
    str_family: str,
    path_family_dir: Path, path_family_report: Path,
    *,
    func_run_verilog_cli: Callable[..., None],
    func_project_artifact_path: Callable[[str | Path], Path],
) -> None:
    """
    运行单个 use-case，并检查模板 id 在产物中的贯穿。

    :param path_example_spec: 当前 use-case 的 spec 文件路径。
    :param str_family: 当前 use-case family 标识。
    :param path_family_dir: 当前 use-case 的 smoke 输出目录。
    :param path_family_report: 当前 use-case 的 validate 报告路径。
    :param func_run_verilog_cli: 外层注入的 workflow CLI 执行回调。
    :param func_project_artifact_path: 外层注入的项目工件路径解析回调。
    :return: 不返回业务值；通过时表示当前 use-case 未发现阻断。
    :raises AssertionError: 当 prompt、workflow 或 validate 结果不满足合同要求时抛出。
    """

    # prompt 产物固定写在 family 目录内，后续会直接检查其模板片段。
    path_prompt = path_family_dir / "prompt.md"  # family prompt 文件路径

    # 当前 workflow 子目录只服务这个 family，用来固定最后一次 attempt 的落点。
    path_workflow_dir = path_family_dir / "workflow"  # 当前 family 的 workflow 尝试目录

    # 先生成 prompt，确认当前 family 能被模板路由正确识别。
    func_run_verilog_cli(
        "prompt",
        "--spec",
        str(path_example_spec),
        "--out",
        str(path_prompt),
        "--no-state",
    )

    # prompt 文件落盘后立即读取文本，用于检查模板章节与 family 透传。
    str_prompt_text = path_prompt.read_text(encoding="utf-8")  # 用于模板核验的 prompt 完整文本

    # 缺少模板章节或 family 标识时，说明 use-case 选择没有贯穿到 prompt。
    if "## Use-case template" not in str_prompt_text or str_family not in str_prompt_text:

        # 用 family 名称明确指出是哪一个用例的 prompt 结构失真。
        raise AssertionError(f"> ERR: [Python] Prompt missing use-case template section for {str_family}.")

    # 再运行 workflow，验证选中的 use-case 能推进到 requirements 与 plan 产物。
    func_run_verilog_cli(
        "run-workflow",
        "--spec",
        str(path_example_spec),
        "--out-dir",
        str(path_workflow_dir),
        "--model-provider",
        "mock",
        "--no-external",
    )

    # 先读取 workflow 总结果，用于解析最后一次 attempt 的工件路径。
    dict_workflow_result = read_json_file(path_workflow_dir / "workflow_result.json")  # workflow 结果载荷

    # 只检查最后一次 attempt，保持和既有 smoke 语义一致。
    dict_attempt = dict_workflow_result["attempts"][-1]  # 最后一次 workflow attempt

    # stage_outputs 子树提供 requirements 与 codegen_plan 的相对工件路径。
    dict_stage_outputs = dict_attempt["stage_outputs"]  # workflow 阶段输出映射

    # requirements 工件路径会被解析回项目工作目录中的真实文件位置。
    path_requirements = func_project_artifact_path(dict_stage_outputs["requirements"]["artifact_path"])  # requirements 产物在工作区中的实际路径

    # codegen_plan 工件也要映射回真实文件，后续要检查模板选择是否继续贯穿。
    path_plan = func_project_artifact_path(dict_stage_outputs["codegen_plan"]["artifact_path"])  # codegen_plan 产物在工作区中的真实落点

    # artifact_dir 指向 validate 需要检查的生成 RTL 目录。
    path_generated_dir = func_project_artifact_path(dict_attempt["artifact_dir"])  # 生成 RTL 目录绝对路径

    # requirements 载荷必须保留被选择的 use-case template id。
    assert_selected_use_case_id(read_json_file(path_requirements), str_family, "Requirements")

    # codegen plan 载荷同样必须保留同一个 use-case template id。
    assert_selected_use_case_id(read_json_file(path_plan), str_family, "Codegen plan")

    # validate 步骤最后确认该 family 的生成目录在本地校验链上无额外告警。
    func_run_verilog_cli(
        "validate",
        "--spec",
        str(path_example_spec),
        "--path",
        str(path_generated_dir),
        "--no-external",
        "--report-json",
        str(path_family_report),
        "--no-state",
    )

    # 读取 family validate 报告，准备确认该模板场景是否已经没有额外告警。
    dict_family_report = read_json_file(path_family_report)  # 当前 family 的 validate 状态载荷

    # family validate 一旦仍有 warnings，就说明该模板场景尚未满足交付合同。
    if dict_family_report.get("warnings") != 0:

        # 用 family 名称标出具体失败用例，便于后续定位。
        raise AssertionError(f"> ERR: [Python] Validate emitted warnings for {str_family}.")

# assert_selected_use_case_id 验证 workflow 产物是否保留了模板选择结果。
def assert_selected_use_case_id(
    dict_payload: dict[str, Any],
    str_family: str,
    str_label: str,
) -> None:
    """
    确认 workflow 阶段产物保留了 use-case 模板 id。

    :param dict_payload: 待检查的 JSON 载荷。
    :param str_family: 当前 use-case family 标识。
    :param str_label: 当前断言所对应的逻辑阶段标签。
    :return: 不返回业务值；通过时表示 template id 贯穿正常。
    :raises AssertionError: 当载荷缺失预期的 template id 时抛出。
    """

    # str_label 继续保留在签名里，确保外层旧调用面无需同步改写。
    _ = str_label  # 兼容旧签名的阶段标签

    # 选中的 template id 必须与当前 family 完全一致，不能在阶段间丢失。
    if dict_payload.get("selected_use_case_template_id") != str_family:

        # 用固定错误文本维持既有 smoke 断言的失败语义。
        raise AssertionError("> ERR: [Python] use-case workflow did not preserve selected template id.")

# run_existing_rtl_boundary_flows 覆盖 semi_auto 边界与 augment testbench 产物合同。
def run_existing_rtl_boundary_flows(
    path_smoke_dir: Path,
    *,
    path_skill_root: Path,
    func_run_verilog_cli: Callable[..., None],
) -> None:
    """
    覆盖 semi_auto 边界和 augment testbench 产物合同。

    :param path_smoke_dir: 当前 smoke 运行目录根。
    :param path_skill_root: readable-verilog-generator skill 根目录。
    :param func_run_verilog_cli: 外层注入的 workflow CLI 执行回调。
    :return: 不返回业务值；通过时表示 existing RTL 边界场景未发现阻断。
    :raises AssertionError: 当 semi_auto 边界或 augment 合同被破坏时抛出。
    """

    # existing RTL 示例统一位于 skill 资产目录下，供 verify-existing 直接复用。
    path_existing_examples_dir = path_skill_root / "assets" / "examples" / "existing_rtl"  # 边界场景共享的 existing RTL 样例目录

    # ready_valid_slice fixture 作为 semi_auto 边界验证的固定 RTL 输入。
    path_existing_fixture = path_existing_examples_dir / "ready_valid_slice.v"  # 半自动确认边界用的 RTL 样例

    # ready_valid_slice_spec 为 verify-existing 提供对应的规格文档。
    path_existing_spec = path_existing_examples_dir / "ready_valid_slice_spec.md"  # 半自动确认边界对应的规格文档

    # augment 场景显式提供一个现成 testbench，验证原路径能否被保留。
    path_existing_tb = path_existing_examples_dir / "ready_valid_slice_tb.v"  # augment 场景显式传入的 testbench

    # semi_auto 路径单独落在专属目录里，便于读取确认边界结果。
    path_verify_existing_dir = path_smoke_dir / "cli-verify-existing"  # 半自动确认边界场景的运行目录

    # 先执行 semi_auto 模式，验证 strict 非零是否仍能被视为确认边界的一部分。
    run_verify_existing(
        VerifyExistingRequest(
            path_source=path_existing_fixture,
            path_out_dir=path_verify_existing_dir,
            path_spec_source=path_existing_spec,
            str_automation_mode="semi_auto",
            str_tb_mode="generate",
            bool_allow_strict_exit_failure=True,
        ),
        func_run_verilog_cli=func_run_verilog_cli,
    )

    # verification_result 会记录是否正确保留了 source_mutation 确认边界。
    dict_verification_result = read_json_file(path_verify_existing_dir / "verification_result.json")  # 半自动确认边界的验证结果载荷

    # semi_auto 必须要求人工确认，不能在 smoke 回归里悄悄自动修改原 RTL。
    if not dict_verification_result.get("source_mutation", {}).get("confirmation_required"):

        # 一旦确认边界被破坏，就用统一错误文本阻断整个 validation 链。
        raise AssertionError("> ERR: [Python] verify-existing did not preserve semi-auto confirmation boundary.")

    # augment 流程单独使用一个目录，避免覆盖前面的 semi_auto 报告。
    path_augment_dir = path_smoke_dir / "cli-verify-existing-augment"  # testbench 增强场景的独立运行目录

    # 再执行 augment 模式，确认显式 testbench 路径可以透传到产物合同。
    run_verify_existing(
        VerifyExistingRequest(
            path_source=path_existing_fixture,
            path_out_dir=path_augment_dir,
            path_spec_source=path_existing_spec,
            str_automation_mode="conservative",
            str_tb_mode="augment",
            path_testbench_source=path_existing_tb,
            bool_allow_strict_exit_failure=True,
        ),
        func_run_verilog_cli=func_run_verilog_cli,
    )

    # augment 模式必须落出 plan 与 diff，证明 testbench 扩展流程已真正运行。
    assert_files_exist(
        [
            path_augment_dir / "tb_augment_plan.json",
            path_augment_dir / "tb_augment_diff.txt",
        ],
        "verify-existing augment did not emit plan and diff artifacts.",
    )

    # tb_contract 会回显原始 testbench 路径，供显式来源断言使用。
    dict_augment_contract = read_json_file(path_augment_dir / "tb_contract.json")  # augment 合同载荷

    # 合同里的 original_testbench_path 必须和显式输入保持逐字一致。
    if dict_augment_contract.get("original_testbench_path") != str(path_existing_tb):

        # testbench 来源一旦漂移，就阻断 existing RTL augment 场景。
        raise AssertionError("> ERR: [Python] verify-existing augment did not preserve explicit testbench source.")

# run_existing_rtl_patch_flows 负责三类 patch 恢复场景的顺序覆盖。
def run_existing_rtl_patch_flows(
    path_smoke_dir: Path,
    *,
    path_skill_root: Path,
    func_run_verilog_cli: Callable[..., None],
) -> None:
    """
    覆盖 reset、control、timing 三类 existing RTL patch 恢复流程。

    :param path_smoke_dir: 当前 smoke 运行目录根。
    :param path_skill_root: readable-verilog-generator skill 根目录。
    :param func_run_verilog_cli: 外层注入的 workflow CLI 执行回调。
    :return: 不返回业务值；通过时表示三类 patch 恢复场景未发现阻断。
    """

    # reset 场景验证 conservative 模式下的 reset 缺口修复恢复流程。
    patch_case_reset = ExistingPatchCase(  # reset 缺口恢复场景对象
        "cli-verify-existing-rtl-fix",  # 复位缺口场景使用的 smoke 目录名
        "reset_gap_counter.v",  # 指向缺少 reset 收尾逻辑的原始 RTL
        "reset_gap_counter_spec.md",  # 约束复位补丁行为的规格文件
        "reset_gap_counter.v",  # 落入 smoke 目录后等待修复的 RTL 副本
        "conservative",  # 先要求人工确认的保守自动化模式
        "approved low-risk reset patch",  # 恢复 apply 流程时写入的批准结论
    )

    # control 场景验证 auto_apply 高风险时能否降级为 confirm_before_apply。
    patch_case_control = ExistingPatchCase(  # 缺省分支补全场景对象
        "cli-verify-existing-rtl-control",  # 缺省分支场景使用的 smoke 目录名
        "fsm_without_default.v",  # 锁定没有 default 分支的状态机 RTL
        "fsm_without_default_spec.md",  # 描述 default 补全约束的规格文件
        "fsm_without_default.v",  # 复制到 smoke 目录中的状态机副本
        "auto_apply",  # 先触发高风险自动应用的降级判断
        "approved control logic patch",  # 恢复执行时提交的控制补丁批准文本
        "case_default_completion",  # 期望识别出的缺省分支 patch 分类
        "control logic patch",  # 失败提示里使用的 control 标签
    )

    # 最后这个场景专门检查缺少输出寄存器时，auto_apply 入口是否仍会先停在确认边界。
    patch_case_timing = ExistingPatchCase(  # 输出寄存器补全场景对象
        "cli-verify-existing-rtl-timing",  # 输出寄存场景使用的 smoke 目录名
        "missing_output_register.v",  # 选取缺少输出寄存器的输入 RTL
        "missing_output_register_spec.md",  # 约束输出寄存补丁的规格文件
        "missing_output_register.v",  # 复制到 smoke 目录后的时序 RTL 副本
        "auto_apply",  # 先验证高风险自动应用是否被拦截
        "approved timing register patch",  # 恢复执行时提交的时序补丁批准文本
        "output_register_completion",  # 期望识别出的输出寄存 patch 分类
        "timing patch",  # 让失败断言明确指向时序补丁分支
    )

    # 三个 patch 场景按固定顺序执行，保证回归输出稳定可复现。
    tuple_patch_cases = (patch_case_reset, patch_case_control, patch_case_timing)  # 按 reset/control/timing 顺序执行的 patch 场景集合

    # 逐个执行 patch fixture，确保每条恢复路径都经过同样的确认流程。
    for patch_case in tuple_patch_cases:

        # 当前 patch fixture 的首次检查与 decision 恢复交给专用 helper 处理。
        run_existing_rtl_patch_case(
            path_smoke_dir,
            patch_case,
            path_skill_root=path_skill_root,
            func_run_verilog_cli=func_run_verilog_cli,
        )

# run_existing_rtl_patch_case 负责单个 patch fixture 的完整确认恢复链。
def run_existing_rtl_patch_case(
    path_smoke_dir: Path,
    patch_case: ExistingPatchCase,
    *,
    path_skill_root: Path,
    func_run_verilog_cli: Callable[..., None],
) -> None:
    """
    执行单个 patch fixture 的首次检查与 decision 恢复。

    :param path_smoke_dir: 当前 smoke 运行目录根。
    :param patch_case: 当前 patch 场景配置对象。
    :param path_skill_root: readable-verilog-generator skill 根目录。
    :param func_run_verilog_cli: 外层注入的 workflow CLI 执行回调。
    :return: 不返回业务值；通过时表示当前 patch 场景未发现阻断。
    :raises AssertionError: 当 patch 计划、恢复决策或最终应用语义不符合预期时抛出。
    """

    # existing RTL 根目录统一承载所有 patch 场景需要的 RTL 与规格 fixture。
    path_existing_root = path_skill_root / "assets" / "examples" / "existing_rtl"  # 当前 patch 场景共享的 existing RTL 样例根目录

    # source fixture 指向当前 patch 场景真正要复制的 RTL 文件。
    path_source_fixture = path_existing_root / patch_case.str_source_name  # patch 场景 RTL fixture 路径

    # spec fixture 描述本次 patch 期望满足的行为约束。
    path_spec_fixture = path_existing_root / patch_case.str_spec_name  # patch 场景规格文档路径

    # case 目录承载首次检查、patch 计划和恢复执行的全部中间产物。
    path_case_dir = path_smoke_dir / patch_case.str_case_dir  # patch 场景 smoke 目录

    # source copy 保持旧流程的副本写法，确保不会直接改动原始 fixture。
    path_source_copy = path_case_dir / patch_case.str_copy_name  # patch 场景 RTL 副本路径

    # RTL 副本父目录需要先落盘，后续复制动作才能稳定执行。
    path_source_copy.parent.mkdir(parents=True, exist_ok=True)

    # 当前场景先复制原始 RTL fixture，再让 verify-existing 在副本上运行。
    path_source_copy.write_text(path_source_fixture.read_text(encoding="utf-8"), encoding="utf-8")

    # 第一次 verify-existing 负责生成 patch 计划并验证是否需要人工确认。
    run_verify_existing(
        VerifyExistingRequest(
            path_source=path_source_copy,
            path_out_dir=path_case_dir,
            path_spec_source=path_spec_fixture,
            str_automation_mode=patch_case.str_automation_mode,
            str_tb_mode="generate",
            bool_allow_strict_exit_failure=True,
        ),
        func_run_verilog_cli=func_run_verilog_cli,
    )

    # 第一次执行后必须已经写出 patch 计划与 diff，证明修复分析真实发生。
    assert_files_exist(
        [
            path_case_dir / "rtl_patch_plan.json",
            path_case_dir / "rtl_patch_diff.txt",
        ],
        "verify-existing RTL fix did not emit patch plan/diff artifacts.",
    )

    # intervention 文件也必须存在，证明当前流程仍保留人工确认边界。
    assert_files_exist(
        [
            path_case_dir / "rtl_intervention.json",
        ],
        "verify-existing RTL fix did not emit intervention before apply.",
    )

    # 高风险 auto_apply 场景还需要额外确认分类与降级策略没有漂移。
    assert_patch_category_when_expected(path_case_dir, patch_case)

    # decision.json 是恢复应用 patch 时的唯一显式用户确认输入。
    path_decision = path_case_dir / "decision.json"  # patch 恢复决策文件

    # 先写入用户确认证据，再恢复 verify-existing 的 apply 路径。
    write_patch_decision(path_decision, patch_case.str_decision_evidence)

    # 第二次 verify-existing 会消费决策 JSON，并真正尝试把 patch 应用到副本。
    run_verify_existing(
        VerifyExistingRequest(
            path_source=path_source_copy,
            path_out_dir=path_case_dir,
            path_spec_source=path_spec_fixture,
            str_automation_mode=patch_case.str_automation_mode,
            str_tb_mode="generate",
            path_decision_source=path_decision,
            bool_allow_strict_exit_failure=True,
        ),
        func_run_verilog_cli=func_run_verilog_cli,
    )

    # 恢复执行后的 verification_result 会标出 RTL patch 是否已经真正落地。
    dict_resumed_result = read_json_file(path_case_dir / "verification_result.json")  # patch 恢复结果载荷

    # 若 applied 仍为假，说明决策恢复并没有真正把 patch 应用到 RTL 副本。
    if not dict_resumed_result.get("rtl_mutation", {}).get("applied"):

        # 直接阻断场景，避免主流程误判 patch 恢复能力已经闭环。
        raise AssertionError("> ERR: [Python] RTL patch did not apply after decision resume.")

# run_verify_existing 统一组装 verify-existing 命令并交给外层回调执行。
def run_verify_existing(
    request: VerifyExistingRequest,
    *,
    func_run_verilog_cli: Callable[..., None],
) -> None:
    """
    按请求对象运行 verify-existing CLI。

    :param request: verify-existing CLI 请求对象。
    :param func_run_verilog_cli: 外层注入的 workflow CLI 执行回调。
    :return: 不返回业务值；通过时表示命令已按既有参数顺序发出。
    """

    # verify-existing 命令参数保持旧顺序，避免外层 smoke 语义发生漂移。
    list_command_args = [
        "verify-existing",  # 触发 verify-existing 子命令
        "--source",  # 声明 RTL 输入路径参数
        str(request.path_source),  # 指向当前要检查的 RTL 文件
        "--out-dir",  # 声明结果输出目录参数
        str(request.path_out_dir),  # 指向当前 verify-existing 结果目录
        "--spec-source",  # 声明规格文档路径参数
        str(request.path_spec_source),  # 指向本轮约束使用的规格文件
        "--automation-mode",  # 声明自动化模式参数
        request.str_automation_mode,  # 当前 verify-existing 的自动化策略值
        "--tb-mode",  # 声明 testbench 处理模式参数
        request.str_tb_mode,  # 当前 verify-existing 的 testbench 模式值
        "--tb-language",  # 声明 testbench 语言参数
        "verilog",  # 固定使用的 testbench 语言值
        "--no-external",  # 禁用外部依赖探测
        "--no-state",  # 禁止写入持久状态
    ]  # 不含可选 testbench 与 decision 的基础命令参数

    # augment 模式若显式提供 testbench，则需要把该来源加回命令行。
    if request.path_testbench_source is not None:

        # testbench 来源参数追加后，verify-existing 才能保留原始 testbench 路径。
        list_command_args.extend(
            [
                "--testbench-source",  # 声明显式 testbench 来源参数
                str(request.path_testbench_source),  # 当前 augment 使用的 testbench 文件
            ]
        )

    # 恢复已确认 patch 时，还需要把决策 JSON 明确拼进命令参数。
    if request.path_decision_source is not None:

        # 决策文件参数会驱动 verify-existing 进入 patch 恢复路径。
        list_command_args.extend(
            [
                "--decision-source",  # 声明决策文件来源参数
                str(request.path_decision_source),  # 当前 patch 恢复使用的决策文件
            ]
        )

    # 最终命令仍交给外层统一的 workflow CLI 执行器处理。
    func_run_verilog_cli(
        *list_command_args,
        allow_failure=request.bool_allow_strict_exit_failure,
    )

# assert_files_exist 负责确认关键 smoke 产物已经落盘。
def assert_files_exist(list_paths: list[Path], str_message: str) -> None:
    """
    确认一组关键产物已经写出。

    :param list_paths: 必须存在的路径列表。
    :param str_message: 兼容旧调用面的错误消息参数。
    :return: 不返回业务值；通过时表示全部路径都已存在。
    :raises AssertionError: 当任意关键产物缺失时抛出。
    """

    # str_message 继续保留在签名里，确保旧调用方不需要同步改写。
    _ = str_message  # 兼容旧调用面的错误消息占位参数

    # 只要有任何关键产物缺失，就说明当前 smoke 场景并未完整落盘。
    if not all(path_item.exists() for path_item in list_paths):

        # 继续保留统一错误文本，避免测试基线因消息变化而漂移。
        raise AssertionError("> ERR: [Python] required validation artifacts are missing.")

# assert_patch_category_when_expected 检查高风险 patch 场景的降级与分类。
def assert_patch_category_when_expected(
    path_case_dir: Path,
    patch_case: ExistingPatchCase,
) -> None:
    """
    检查高风险 auto_apply 场景是否降级为确认并标出预期分类。

    :param path_case_dir: 当前 patch 场景的 smoke 目录。
    :param patch_case: 当前 patch 场景配置对象。
    :return: 不返回业务值；通过时表示降级与分类都符合预期。
    :raises AssertionError: 当 patch 策略或 patch_category 漂移时抛出。
    """

    # 没有预期分类的场景只检查产物存在性，不在这里做额外分类断言。
    if patch_case.str_expected_category is None:

        # 当前场景不需要分类核验时，直接结束即可。
        return

    # verification_result 记录本次 patch 是否被降级为确认后应用。
    dict_fix_result = read_json_file(path_case_dir / "verification_result.json")  # 高风险 patch 的验证状态载荷

    # rtl_patch_plan 提供 patch_category，供高风险场景做额外分类比对。
    dict_patch_plan = read_json_file(path_case_dir / "rtl_patch_plan.json")  # 高风险 patch 的计划说明载荷

    # rtl_mutation 子树汇总策略与 applied 状态，是降级断言的核心字段。
    dict_mutation = dict_fix_result.get("rtl_mutation", {})  # patch 变更状态子载荷

    # 高风险 auto_apply 场景必须先降级为确认，且首次执行不得直接 applied。
    if dict_mutation.get("policy") != "confirm_before_apply" or dict_mutation.get("applied"):

        # 一旦策略没有降级成功，就阻断高风险 patch 场景通过。
        raise AssertionError("> ERR: [Python] high-risk RTL patch bypassed confirmation policy.")

    # patch_category 还必须与场景预期完全一致，防止分类器悄悄漂移。
    if dict_patch_plan.get("patch_category") != patch_case.str_expected_category:

        # 用固定错误文本标记 patch 分类没有命中预期场景。
        raise AssertionError("> ERR: [Python] expected RTL patch category was not detected.")

# write_patch_decision 生成恢复已确认 patch 所需的决策 JSON。
def write_patch_decision(path_decision: Path, str_evidence: str) -> None:
    """
    写入 verify-existing 恢复流程需要的决策 JSON。

    :param path_decision: 目标决策文件路径。
    :param str_evidence: 写入决策文件的用户确认证据文本。
    :return: 不返回业务值；通过时表示决策文件已按既有格式写出。
    """

    # 先准备一个空载荷，再逐项填入旧流程要求的字段。
    dict_decision: dict[str, Any] = {}  # patch 决策 JSON 载荷

    # version 字段声明当前决策文件遵循的最小协议版本。
    dict_decision["version"] = 1  # 决策协议版本

    # status 保持 resolved，表示用户已经完成本轮 patch 决策。
    dict_decision["status"] = "resolved"  # 决策状态

    # decision 固定为 apply_rtl_patch，驱动 verify-existing 进入恢复应用路径。
    dict_decision["decision"] = "apply_rtl_patch"  # 决策动作

    # evidence 数组收纳当前用户确认该 patch 的人类可读证据。
    dict_decision["evidence"] = [str_evidence]  # 决策证据列表

    # constraints 保持 preserve interface，延续旧场景的接口保护语义。
    dict_decision["constraints"] = ["preserve interface"]  # patch 约束列表

    # affected_subfunctions 继续使用通配标记，保持旧 smoke 兼容输出。
    dict_decision["affected_subfunctions"] = ["*"]  # 受影响子功能列表

    # 决策文件按两空格缩进写出，并保留 UTF-8 中文内容。
    path_decision.write_text(
        json.dumps(dict_decision, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
