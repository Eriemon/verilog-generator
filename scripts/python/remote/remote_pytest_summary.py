"""把远程 pytest 成功末行转换为发布门禁可读取的结构化证据。"""

# JSON 负责写出稳定的 retained 证据协议。
import json

# 正则表达式从 pytest 成功末行提取计数和耗时。
import re

# Path 保持日志与摘要位置显式且可复核。
from pathlib import Path

# pytest 日志和摘要固定在当前 retained run 的 smoke 目录。
PATH_PYTEST_LOG = Path("_smoke_runs/remote_pytest.log")  # pytest 原始输出路径

# 摘要路径由 report-runs 下载合同消费，不能与日志路径混用。
PATH_PYTEST_SUMMARY = Path("_smoke_runs/remote_pytest_summary.json")  # pytest 摘要 JSON 路径

# 结果类别必须与 pytest 成功摘要中的公开名称保持一致。
STR_RESULT_PATTERN = r"(\d+)\s+(passed|skipped|xfailed|xpassed|deselected)"  # pytest 结果类别模式

# 耗时模式读取 pytest 自报秒数，不使用本地重新计时。
STR_DURATION_PATTERN = r"in\s+([0-9]+(?:\.[0-9]+)?)s"  # pytest 成功摘要耗时模式

# parse_pytest_summary_line 只负责解析成功末行，文件系统副作用留在 main。
def parse_pytest_summary_line(str_summary_line: str) -> dict[str, object]:
    """解析 pytest 成功摘要中的精确计数和耗时。

    参数:
        str_summary_line: pytest 控制台输出中的最终成功摘要行。
    返回:
        返回包含状态、分类计数、耗时和原始摘要的字典。
    异常:
        AssertionError: 摘要为空、通过数不是正数或耗时缺失时抛出。
    """

    # 空摘要不能证明 pytest 真实执行并完成。
    if not str_summary_line:

        # 未观察到成功末行时拒绝生成任何通过证据。
        raise AssertionError("> ERR: [Python] pytest success summary line was not found")

    # 计数字典保留每类执行结果，不合并跳过与通过项。
    dict_counts = {  # pytest 各结果类别的精确数量
        str_name: int(str_value)  # 把十进制文本转换为数量
        for str_value, str_name in re.findall(  # 提取成功摘要中的分类计数
            STR_RESULT_PATTERN,  # 允许的 pytest 结果类别集合
            str_summary_line,  # 只解析调用方已定位的成功末行
        )
    }

    # 耗时匹配对象保留 pytest 自报秒数。
    obj_duration_match = re.search(  # pytest 成功摘要中的耗时捕获结果
        STR_DURATION_PATTERN,  # 支持整数或小数秒
        str_summary_line,  # 耗时来源限定为同一条成功摘要
    )

    # 至少一个 passed 用例才能证明本轮实际执行了回归。
    if dict_counts.get("passed", 0) <= 0:

        # 零通过意味着没有形成可接受的权威覆盖。
        raise AssertionError(f"> ERR: [Python] remote pytest passed count is not positive: {dict_counts}")

    # 缺失耗时说明 pytest 摘要格式不完整。
    if obj_duration_match is None:

        # 没有自报耗时就无法识别异常短跑。
        raise AssertionError(f"> ERR: [Python] pytest duration is missing: {str_summary_line}")

    # 载荷保留通过、跳过、扩展状态、耗时和原始摘要。
    return {
        "status": "passed",
        "passed": dict_counts["passed"],
        "skipped": dict_counts.get("skipped", 0),
        "xfailed": dict_counts.get("xfailed", 0),
        "xpassed": dict_counts.get("xpassed", 0),
        "deselected": dict_counts.get("deselected", 0),
        "duration_seconds": float(obj_duration_match.group(1)),
        "summary_line": str_summary_line,
    }

# find_pytest_summary_line 从完整日志中定位最终成功摘要。
def find_pytest_summary_line(list_log_lines: list[str]) -> str:
    """从 pytest 日志行中定位最终成功摘要。

    参数:
        list_log_lines: pytest 完整控制台输出按行拆分后的列表。
    返回:
        返回最后一条同时包含 passed 和耗时的摘要；找不到时返回空字符串。
    """

    # 从日志末尾向前搜索，跳过进度行和可选警告段。
    return next(
        (
            str_line.strip()
            for str_line in reversed(list_log_lines)
            if " passed" in str_line and " in " in str_line
        ),
        "",
    )

# main 隔离文件读取和摘要写入，导入模块时不产生运行工件。
def main() -> None:
    """读取 pytest 日志并写出 retained 结构化摘要。

    参数:
        本函数不接收外部业务参数，输入路径由远程验证目录合同固定。
    返回:
        本函数只写出摘要文件，不返回业务值。
    异常:
        AssertionError: pytest 成功摘要、正数通过计数或耗时缺失时抛出。
    """

    # 按行读取日志，替换非法工具输出字节后仍保留诊断文本。
    list_log_lines = PATH_PYTEST_LOG.read_text(  # pytest 控制台输出行
        encoding="utf-8",  # 远程验证包统一使用 UTF-8
        errors="replace",  # 非法字节替换后继续解析成功末行
    ).splitlines()

    # 最后一条成功摘要是精确计数和耗时的唯一输入。
    str_summary_line = find_pytest_summary_line(list_log_lines)  # pytest 成功摘要原文

    # 解析函数在写文件前验证通过数与耗时，失败时不产生伪证据。
    dict_payload = parse_pytest_summary_line(str_summary_line)  # report-runs 下载的 pytest 载荷

    # retained JSON 使用稳定键序和 UTF-8，供机器与人工共同复核。
    PATH_PYTEST_SUMMARY.write_text(
        json.dumps(  # 生成稳定键序的 JSON 文本
            dict_payload,  # 只写入已验证的 pytest 统计载荷
            indent=2,  # 保持 retained 文件可人工审阅
            sort_keys=True,  # 跨运行保持字段顺序稳定
        )
        + "\n",  # 文本文件以换行结束
        encoding="utf-8",  # retained JSON 使用 UTF-8
    )

# 仅在远程验证命令直接执行模块时生成 pytest 摘要。
if __name__ == "__main__":

    # 入口不接收参数，所有输入均来自同一 retained run。
    main()
