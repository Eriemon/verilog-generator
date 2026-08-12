"""工作流 mock provider 的内容组装辅助逻辑。"""

# future annotations 让内容 helper 只在运行时解析必要类型
from __future__ import annotations

# JSON 负载文本仍然通过标准库序列化
import json

# Path 后缀判断仍然依赖 pathlib
from pathlib import Path

# 内容组装 helper 只需要最小类型集合
from typing import Any

# provider 上下文类型
from .model_provider import GenerationContext

# review 文本补注释 helper
from .model_provider_mock_comments import _add_mock_line_comments

# RTL 文本生成 helper
from .model_provider_mock_rtl import (
    _build_mock_port_layout,
    _layout_has_sequential_controls,
    _mock_erie_comb_source_text,
    _mock_erie_rtl_source_text,
)

# 时序与组合 testbench 渲染入口
from .model_provider_mock_testbench import (
    _mock_erie_comb_testbench_text,
    _mock_erie_rtl_testbench_text,
)

# mock stage 内容在这里统一装配
def _mock_mode(context: GenerationContext, config: dict[str, Any]) -> str:
    """
    根据 provider 配置与 spec.workflow 决定 mock 模式。

    :param context: 提供 stage 与 spec 的阶段上下文。
    :param config: provider 级 mock 行为配置。
    :return: success、invalid_response 或 spec_issue 等模式名。
    """

    # 读取 provider 级 mock 行为配置
    raw_behavior = config.get("mock_behavior")  # provider 级优先采用的 mock 行为配置

    # 在 provider 配置缺失时回退 spec.workflow 配置
    if raw_behavior is None:

        # 使用 spec.workflow 中的 mock 行为覆盖
        raw_behavior = (context.spec.get("workflow") or {}).get("mock_behavior")  # spec.workflow 中声明的回退行为配置

    # 直接返回字符串模式
    if isinstance(raw_behavior, str):

        # 使用显式声明的行为模式
        return raw_behavior

    # 从字典模式中按 stage 解析行为
    if isinstance(raw_behavior, dict):

        # 先准备当前 stage 未命中时的统一回退行为。
        raw_stage_behavior_fallback = raw_behavior.get("*", raw_behavior.get("default", "success"))  # stage 缺失时复用的回退行为配置

        # 先取当前 stage，再回退到 * 或 default。
        raw_stage_behavior = raw_behavior.get(context.stage, raw_stage_behavior_fallback)  # 当前 stage 命中的行为配置

        # 支持 {"mode": "..."} 形态
        if isinstance(raw_stage_behavior, dict):

            # 返回嵌套字典中的 mode 字段
            return str(raw_stage_behavior.get("mode", "success"))

        # 返回可转换为字符串的标量模式
        if raw_stage_behavior:

            # 使用当前阶段覆盖值
            return str(raw_stage_behavior)

    # 默认返回成功模式
    return "success"

# 生成 stage 对应的 mock 文件内容
def _mock_file_contents(context: GenerationContext, files: list[dict[str, Any]]) -> dict[str, str]:
    """
    根据 stage 生成各目标文件的 mock 文本。

    :param context: 提供 stage、spec 与向量契约的阶段上下文。
    :param files: 当前阶段需要回填内容的文件清单。
    :return: 以相对路径为键、文本内容为值的映射。
    """

    # 提取当前 stage 名称
    str_stage = context.stage  # 决定 mock 产物形态的阶段标识

    # 提取当前规范化 spec
    dict_spec = context.spec  # 生成 mock 文件时使用的 spec 快照

    # 获取用于 mock 的测试向量
    list_vectors = _mock_vectors(dict_spec)  # 生成 Python/RTL/tests 内容的向量列表

    # 读取向量契约摘要哈希
    str_vector_hash = str((context.vector_contract or {}).get("sha256") or "")  # testbench 注入的契约哈希

    # 生成 Python 阶段产物
    if str_stage == "python":

        # Python 阶段委托专用 helper 填充参考模型和向量文件。
        return _mock_python_stage_contents(files, list_vectors)

    # RTL 阶段要把同一份规格拆成设计源文件和仿真文件两类交付物。
    if str_stage == "rtl":

        # RTL 阶段委托专用 helper 区分 DUT、testbench 和占位文件。
        return _mock_rtl_stage_contents(dict_spec, files, list_vectors, str_vector_hash)

    # review 阶段只负责为每个目标文件落一份可审阅的 Markdown 文本。
    if str_stage == "review":

        # review 阶段委托专用 helper 复用同一份报告正文。
        return _mock_review_stage_contents(dict_spec, files)

    # tests 阶段要把同一份向量清单投递到每个测试载荷文件。
    if str_stage == "tests":

        # tests 阶段委托专用 helper 序列化统一向量载荷。
        return _mock_tests_stage_contents(files, list_vectors)

    # 未知阶段委托兜底 helper 保留 manifest 文件键。
    return _mock_unknown_stage_contents(files)

# 生成 Python 阶段 mock 文件内容。
def _mock_python_stage_contents(files: list[dict[str, Any]], vectors: list[dict[str, Any]]) -> dict[str, str]:
    """
    填充 Python 阶段的参考模型和向量文件。

    :param files: 当前阶段 manifest 声明的文件条目。
    :param vectors: 需要写入 Python 参考模型和 JSON 文件的测试向量。
    :return: 以 manifest 相对路径为键的文本内容映射。
    """

    # dict_contents 收集 Python 阶段所有目标文件文本。
    dict_contents: dict[str, str] = {}  # Python 阶段输出文本映射

    # 逐个填充 Python 阶段文件内容。
    for dict_file_entry in files:

        # 读取相对路径。
        str_relative_path = str(dict_file_entry["path"])  # 输出映射使用的相对路径

        # 提取文件后缀。
        str_suffix = Path(str_relative_path).suffix.lower()  # 决定内容模板的后缀

        # 根据后缀选择当前文件内容。
        dict_contents[str_relative_path] = _mock_python_stage_file_text(str_suffix, vectors)  # 当前 Python 阶段文件文本

    # 返回 Python 阶段文件映射。
    return dict_contents

# 生成单个 Python 阶段文件的文本。
def _mock_python_stage_file_text(suffix: str, vectors: list[dict[str, Any]]) -> str:
    """
    根据文件后缀返回 Python 阶段的单文件文本。

    :param suffix: 当前文件的小写后缀。
    :param vectors: 需要写入参考模型或 JSON 的测试向量。
    :return: 当前文件应写入的 mock 文本。
    """

    # Python 参考模型文件承载可执行的 run_case 示例。
    if suffix == ".py":

        # 写入用于 Python 阶段的参考模型实现。
        return _mock_python_model_text(vectors)

    # JSON 文件承载 Python 阶段复用的测试向量。
    if suffix == ".json":

        # 以可读 JSON 文本序列化当前向量清单。
        return json.dumps({"cases": vectors}, indent=2, ensure_ascii=False) + "\n"

    # 其他文件默认留空，等待后续阶段决定内容。
    return "\n"

# RTL manifest 路径在这里分流到 DUT、testbench 与占位产物。
def _mock_rtl_stage_contents(
    spec: dict[str, Any],
    files: list[dict[str, Any]],
    vectors: list[dict[str, Any]],
    vector_hash: str,
) -> dict[str, str]:
    """
    按 manifest 路径生成 RTL 阶段的 DUT、testbench 或占位文本。

    :param spec: 当前生成任务的规范化规格。
    :param files: 当前阶段 manifest 声明的文件条目。
    :param vectors: testbench 需要使用的测试向量。
    :param vector_hash: 向量契约哈希。
    :return: 以 manifest 相对路径为键的文本内容映射。
    """

    # dict_contents 保存硬件阶段 manifest 路径到源码文本的对应关系。
    dict_contents: dict[str, str] = {}  # RTL 文件路径到生成正文的映射

    # 逐个消费 manifest 中声明的硬件输出路径。
    for dict_file_entry in files:

        # 固定当前输出条目的目标相对路径。
        str_relative_path = str(dict_file_entry["path"])  # 当前交付文件写回时使用的 manifest 相对路径

        # 当前 manifest 条目只写入自己路径对应的硬件文本。
        dict_contents[str_relative_path] = _mock_rtl_stage_file_text(spec, str_relative_path, vectors, vector_hash)  # 当前 RTL 目标文件正文

    # RTL 阶段生成完毕后，把整批文件内容映射交回上层。
    return dict_contents

# 单个 RTL 目标文件在这里选择主模块、仿真平台或占位内容。
def _mock_rtl_stage_file_text(
    spec: dict[str, Any],
    relative_path: str,
    vectors: list[dict[str, Any]],
    vector_hash: str,
) -> str:
    """
    根据 RTL 阶段目标路径选择 DUT、testbench 或占位文本。

    :param spec: 当前生成任务的规范化规格。
    :param relative_path: manifest 中声明的相对输出路径。
    :param vectors: testbench 需要使用的测试向量。
    :param vector_hash: 向量契约哈希。
    :return: 当前 RTL 阶段文件应写入的文本。
    """

    # str_suffix 用来区分 DUT 主模块和 testbench。
    str_suffix = Path(relative_path).suffix.lower()  # 决定生成 RTL 或 testbench

    # 文件名词干同时承担新旧 testbench 命名的兼容识别。
    str_stem = Path(relative_path).stem.lower()  # 兼容识别新旧 testbench 文件名

    # VG149 前缀用于正常输出，旧后缀仅保留给负向样例识别。
    bool_testbench_name = str_stem.startswith("tb_") or "_tb" in str_stem  # VG149 前缀与旧负向样例

    # 主模块文件只承载 DUT 骨架，不能混入仿真逻辑。
    if str_suffix == ".v" and not bool_testbench_name:

        # 返回 DUT Verilog 源码文本。
        return _mock_erie_rtl_source_text(spec)

    # 仿真文件统一生成自检 testbench，供后续 smoke 流程直接消费。
    if str_suffix == ".v":

        # 当前接口场景对应的 testbench 需要携带向量驱动和自检断言。
        return _mock_erie_rtl_testbench_text(spec, vectors, vector_hash)

    # manifest 中的辅助文件暂不生成内容，先保留空文本占位。
    return "\n"

# review 阶段只把同一份审查摘要投递到 manifest 声明的报告路径。
def _mock_review_stage_contents(spec: dict[str, Any], files: list[dict[str, Any]]) -> dict[str, str]:
    """
    为 review 阶段的每个目标文件写入同一份摘要文本。

    :param spec: 当前生成任务的规范化规格。
    :param files: 当前阶段 manifest 声明的文件条目。
    :return: review 文件路径到 Markdown 文本的映射。
    """

    # 生成 review 阶段统一复用的 Markdown 正文。
    str_review_text = _mock_review_text(spec)  # review 报告正文

    # 返回每个报告路径到统一正文的映射。
    return {str(dict_file_entry["path"]): str_review_text for dict_file_entry in files}

# tests 阶段把向量负载复制到每个测试数据目标。
def _mock_tests_stage_contents(files: list[dict[str, Any]], vectors: list[dict[str, Any]]) -> dict[str, str]:
    """
    为 tests 阶段的每个目标文件写入统一 JSON 向量载荷。

    :param files: 当前阶段 manifest 声明的文件条目。
    :param vectors: tests 阶段共享的测试向量。
    :return: tests 文件路径到 JSON 文本的映射。
    """

    # 准备 tests 阶段统一复用的结构化向量载荷。
    dict_payload = {"version": 1, "cases": vectors}  # tests 阶段共享向量负载

    # str_payload_text 是每个 tests 文件复用的 JSON 正文。
    str_payload_text = json.dumps(dict_payload, indent=2, ensure_ascii=False) + "\n"  # tests 阶段 JSON 负载文本

    # 返回每个测试文件到统一载荷的映射。
    return {str(dict_file_entry["path"]): str_payload_text for dict_file_entry in files}

# 生成未知阶段 mock 文件内容。
def _mock_unknown_stage_contents(files: list[dict[str, Any]]) -> dict[str, str]:
    """
    为未知阶段保留 manifest 文件键并写入空对象占位。

    :param files: 当前阶段 manifest 声明的文件条目。
    :return: 未知阶段文件路径到占位文本的映射。
    """

    # 返回未知阶段的保底占位文件映射。
    return {str(dict_file_entry["path"]): "{}\n" for dict_file_entry in files}

# 构造默认 mock 向量列表
def _mock_vectors(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """
    返回 spec 声明的 mock 向量或默认样例。

    :param spec: 可能内含 workflow.mock_vectors 的规范化规格。
    :return: 非空 mock 向量列表。
    """

    # 读取 workflow 中配置的 mock 向量
    list_configured_vectors = (spec.get("workflow") or {}).get("mock_vectors")  # 用户覆盖的 mock 向量

    # 优先返回显式配置的向量列表
    if isinstance(list_configured_vectors, list) and list_configured_vectors:

        # 直接使用调用方提供的向量
        return list_configured_vectors

    # 返回最小默认向量样例
    return [
        {
            "id": "case_1",
            "inputs": {"value": 1},
            "expected_outputs": {"value": 1},
            "checkpoints": {"value": 1},
        }
    ]

# 生成 mock Python 参考实现文本
def _mock_python_model_text(vectors: list[dict[str, Any]]) -> str:
    """
    生成可执行的最小 Python 参考模型文本。

    :param vectors: mock 测试向量列表。
    :return: 供 workflow tests 使用的 Python 源码字符串。
    """

    # 把向量列表渲染成稳定 repr 文本
    str_payload = repr(vectors)  # 嵌入源码的参考向量文本

    # 返回最小参考模型源码
    return f"""REFERENCE_VECTORS = {str_payload}

def run_case(case):
    if "expected_outputs" in case:
        return case["expected_outputs"]
    if "expected" in case:
        return case["expected"]
    if "outputs" in case:
        return case["outputs"]
    inputs = case.get("inputs", {{}})
    if isinstance(inputs, dict):
        return inputs
    return {{"result": inputs}}

def collect_checkpoints(case):
    if "checkpoints" in case:
        return case["checkpoints"]
    return {{"observed": run_case(case)}}

def run_tests():
    for case in REFERENCE_VECTORS:
        expected = case.get("expected_outputs", run_case(case))
        if run_case(case) != expected:
            print(f"FAIL {{case.get('id', 'case')}}")
            return False
    print("PASS")
    return True

if __name__ == "__main__":
    raise SystemExit(0 if run_tests() else 1)
"""

# review 阶段 Markdown 摘要在这里统一生成
def _mock_review_text(spec: dict[str, Any]) -> str:
    """
    生成供 review 阶段使用的 Markdown 摘要。

    :param spec: 提供顶层模块名的规范化规格。
    :return: Markdown 格式的 mock review 文本。
    """

    # 读取 review 标题中使用的模块名。
    str_top = str(spec.get("name") or "rtl_module")  # review 标题模块名

    # 返回固定 review 模板。
    return f"""# {str_top} Plan Review

## Interface
The interface maps the declared clock, reset, data, and valid ports directly into the generated RTL and testbench.

## Reset
The reset path drives all state-holding registers to deterministic zero values before stimulus begins.

## Timing And Pipeline
The timing structure uses one visible sequential sampling stage and names the data/valid pipeline registers for review.

## Handshake And FSM
The ready/valid handshake is represented without a hidden FSM; valid propagation is checked in the testbench.

## Width
The data width is parameterized from the spec and compared against the expected value in the testbench checkpoint.

## Synthesis
The RTL avoids simulation-only constructs in synthesizable source files.
It keeps testbench constructs isolated under tb/.

## Testbench Coverage
The testbench instantiates the DUT, drives reset and input stimulus.
It compares value against EXPECTED_VALUE and has $fatal failure paths.

## Risk
The remaining risk is mock-level functional simplicity.
Any real release must compile and simulate the generated artifact when claiming execute readiness.
"""

# 为 mock 响应构造带 checks 的 manifest 正文
def _build_mock_response_manifest(
    context: GenerationContext,
    files: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    生成 mock 响应顶层 manifest。

    :param context: 提供 stage 与 manifest 的阶段上下文。
    :param files: 当前阶段准备返回的文件清单。
    :return: 带 checks 结构的 manifest 字典。
    """

    # 返回带 checks 的 manifest 副本。
    return {
        **context.manifest,
        "files": files,
        "checks": {
            "spec_coverage": [f"Mock provider generated stage {context.stage} artifacts."],
            "verification_plan": ["Mock response includes deterministic verification hooks."],
            "execution_plan": ["Mock response is intended for local workflow tests."],
            "implementation_assessment": ["Mock artifacts satisfy structural contracts for the workflow runner."],
            "reviewability_assessment": ["Mock artifacts keep comments and markers for validation."],
            "assumptions": [],
            "known_limitations": ["Mock provider prioritizes workflow determinism over hardware fidelity."],
        },
    }

# 把 manifest 渲染成 mock 回复头部的 fenced block 行序列。
def _mock_manifest_block_lines(response_manifest: dict[str, Any]) -> list[str]:
    """
    把 manifest 字典渲染成 mock 回复头部的 fenced block 行序列。

    :param response_manifest: 当前阶段准备返回给解析器的 manifest 字典。
    :return: 供最终响应文本直接拼接的 manifest 代码块行列表。
    """

    # 返回 mock 回复头部固定使用的 manifest fenced block。
    return [
        "```json",  # manifest 代码块起始标记
        json.dumps(response_manifest, indent=2, ensure_ascii=False),  # manifest 的 JSON 正文
        "```",  # manifest 代码块结束标记
    ]

# 构造 mock 端口块文本
