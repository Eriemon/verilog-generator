"""把远程 pytest 末行转换为可审计的阶段化结构化证据。"""

# argparse 让远端 shell 能显式绑定阶段、日志和命令摘要。
import argparse

# JSON 负责写出稳定的 retained 证据协议。
import json
import os
import sys

# datetime 为每一个阶段摘要写入带时区的生成时间。
from datetime import datetime, timezone

# 正则表达式从 pytest 成功末行提取计数和耗时。
import re

# Path 保持日志与摘要位置显式且可复核。
from pathlib import Path

# pytest 日志和摘要使用当前 retained run 内的相对位置。
PATH_PYTEST_LOG = Path("remote_pytest.log")  # pytest 原始输出相对路径

# 摘要路径由 report-runs 下载合同消费，不能与日志路径混用。
PATH_PYTEST_SUMMARY = Path("remote_pytest_summary.json")  # pytest 摘要 JSON 相对路径

# smoke_run_root 只接受远程编排显式导出的本轮目录，不回退到旧路径。
def smoke_run_root() -> Path:
    """返回远程编排显式指定的 retained smoke 运行目录。

    参数:
        本函数不接收外部业务参数，目录由环境变量提供。
    返回:
        返回当前远程 smoke 运行目录路径。
    异常:
        RuntimeError: 远程编排未导出本轮目录时抛出。
    """

    # 缺失运行目录变量时拒绝猜测路径，避免证据写入错误位置。
    str_smoke_run_dir = os.environ.get("VERILOG_GENERATOR_SMOKE_RUN_DIR", "")  # 当前远程 retained 目录文本

    # 空变量不能建立可复核的远程证据边界。
    if not str_smoke_run_dir:

        # 结构化错误提示调用方先建立远程运行目录合同。
        raise RuntimeError("> ERR: [Python] VERILOG_GENERATOR_SMOKE_RUN_DIR is required")

    # 返回调用时解析出的路径，避免模块导入阶段绑定陈旧环境状态。
    return Path(str_smoke_run_dir)

# 结果类别必须与 pytest 摘要中的公开名称保持一致。
STR_RESULT_PATTERN = r"(\d+)\s+(failed|passed|skipped|xfailed|xpassed|deselected)"  # pytest 结果类别模式

# 耗时模式读取 pytest 自报秒数，不使用本地重新计时。
STR_DURATION_PATTERN = r"in\s+([0-9]+(?:\.[0-9]+)?)s"  # pytest 成功摘要耗时模式

# parse_pytest_summary_line 只负责解析 pytest 末行，文件系统副作用留在 main。
def parse_pytest_summary_line(
    str_summary_line: str,
    *,
    phase: str = "",
    log_path: str = "",
    output_path: str = "",
    command_hash: str = "",
    exit_code: int = 0,
    timestamp: str = "",
) -> dict[str, object]:
    """解析 pytest 摘要中的精确计数和耗时。

    参数:
        str_summary_line: pytest 控制台输出中的最终摘要行。
        phase: 当前 targeted、regression 或 full 阶段名称。
        log_path: 当前阶段原始日志路径。
        output_path: 当前阶段 JSON 摘要路径。
        command_hash: 实际执行命令文本的 SHA-256。
        exit_code: 当前 pytest 进程退出码。
        timestamp: 当前阶段摘要生成时间；为空时由本函数生成。
    返回:
        返回包含状态、分类计数、耗时和原始摘要的字典。
    异常:
        AssertionError: 摘要为空、成功阶段没有通过用例或耗时缺失时抛出。
    """

    # 空摘要不能证明 pytest 真实执行并完成。
    if not str_summary_line:

        # 未观察到 pytest 末行时拒绝生成任何阶段证据。
        raise AssertionError("> ERR: [Python] pytest summary line was not found")

    # 计数字典保留每类执行结果，不合并跳过与通过项。
    dict_counts = {  # pytest 各结果类别的精确数量
        str_name: int(str_value)  # 把十进制文本转换为数量
        for str_value, str_name in re.findall(  # 提取摘要中的分类计数
            STR_RESULT_PATTERN,  # 允许的 pytest 结果类别集合
            str_summary_line,  # 只解析调用方已定位的 pytest 末行
        )
    }

    # 耗时匹配对象保留 pytest 自报秒数。
    obj_duration_match = re.search(  # pytest 摘要中的耗时捕获结果
        STR_DURATION_PATTERN,  # 支持整数或小数秒
        str_summary_line,  # 耗时来源限定为同一条成功摘要
    )

    # 成功阶段至少一个 passed 用例才能证明本轮实际执行了回归。
    if exit_code == 0 and dict_counts.get("passed", 0) <= 0:

        # 零通过意味着没有形成可接受的权威覆盖。
        raise AssertionError(f"> ERR: [Python] remote pytest passed count is not positive: {dict_counts}")

    # 缺失耗时说明 pytest 摘要格式不完整。
    if obj_duration_match is None:

        # 没有自报耗时就无法识别异常短跑。
        raise AssertionError(f"> ERR: [Python] pytest duration is missing: {str_summary_line}")

    # 阶段摘要同时保留通过、跳过和失败计数，避免把 skipped 隐含到 passed。
    dict_payload: dict[str, object] = {  # 当前远程 pytest 阶段的结构化证据
        "status": "passed" if exit_code == 0 else "failed",  # 阶段状态
        "passed": dict_counts.get("passed", 0),  # 通过用例数
        "failed": dict_counts.get("failed", 0),  # 失败用例数
        "skipped": dict_counts.get("skipped", 0),  # 跳过用例数
        "xfailed": dict_counts.get("xfailed", 0),  # 预期失败用例数
        "xpassed": dict_counts.get("xpassed", 0),  # 意外通过用例数
        "deselected": dict_counts.get("deselected", 0),  # 被筛除用例数
        "duration_seconds": float(obj_duration_match.group(1)),  # pytest 自报耗时
        "summary_line": str_summary_line,  # 原始 pytest 末行
        "phase": phase or "full",  # 当前阶段名称
        "log_path": log_path,  # 阶段日志相对路径
        "output_path": output_path,  # 阶段摘要相对路径
        "command_hash": command_hash,  # 阶段命令文本哈希
        "exit_code": int(exit_code),  # pytest 进程退出码
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),  # 阶段完成时间
    }

    # count 绑定 pytest 已报告的 passed+skipped，供 release receipt 做阶段覆盖比较。
    dict_payload["count"] = int(dict_payload["passed"]) + int(dict_payload["skipped"])  # 实际执行与跳过合计数

    # 返回稳定阶段载荷，调用方负责 JSON 文件副作用。
    return dict_payload

# find_pytest_summary_line 从完整日志中定位最终摘要。
def find_pytest_summary_line(list_log_lines: list[str]) -> str:
    """从 pytest 日志行中定位最终摘要。

    参数:
        list_log_lines: pytest 完整控制台输出按行拆分后的列表。
    返回:
        返回最后一条包含结果类别和耗时的摘要；找不到时返回空字符串。
    """

    # 从日志末尾向前搜索，跳过进度行和可选警告段。
    return next(
        (
            str_line.strip()
            for str_line in reversed(list_log_lines)
            if " in " in str_line
            and re.search(r"\b(?:failed|passed|skipped|xfailed|xpassed|deselected)\b", str_line)
        ),
        "",
    )

# _resolve_run_path 把阶段路径限制在当前 retained smoke 运行目录。
def _resolve_run_path(path_smoke_run_root: Path, str_path: str, path_default: Path) -> Path:
    """解析阶段日志或摘要路径。

    :param path_smoke_run_root: 当前 retained smoke 运行目录。
    :param str_path: shell 传入的相对或绝对路径文本。
    :param path_default: 未传路径时使用的兼容默认文件名。
    :return: 归属于当前运行目录的证据路径。
    """

    # 缺省值沿用旧 remote_pytest.log/summary.json 合同。
    path_candidate = Path(str_path) if str_path else path_default  # shell 提供的阶段路径候选

    # shell 传入的相对路径可能已经带有 smoke 根，不能再次重复拼接。
    if path_candidate.is_absolute():

        # 绝对路径由远程编排明确提供，直接保留。
        return path_candidate

    # 当前 smoke 根转换为 POSIX 文本，供阶段路径前缀比较。
    str_root = path_smoke_run_root.as_posix().rstrip("/")  # 当前 smoke 根的 POSIX 文本

    # shell 候选统一转成 POSIX 形式后再比较根目录前缀。
    str_candidate = path_candidate.as_posix()  # 当前阶段候选的 POSIX 文本

    # 已经带 smoke 根的相对路径不能再次前缀拼接。
    if str_candidate == str_root or str_candidate.startswith(str_root + "/"):

        # 返回 shell 已经解析好的路径。
        return path_candidate

    # 其他相对路径只能落在当前 retained 运行目录，归一化后防止证据漂移。
    return path_smoke_run_root / path_candidate

# main 隔离命令行元数据、日志读取和摘要写入。
def main(argv: list[str] | None = None) -> None:
    """读取 pytest 日志并写出 retained 结构化摘要。

    参数:
        argv: 可选的阶段摘要参数；缺省时由 argparse 读取进程参数。
    返回:
        本函数只写出摘要文件，不返回业务值。
    异常:
        AssertionError: pytest 摘要、成功阶段正数通过计数或耗时缺失时抛出。
    """

    # 解析阶段参数，同时让被测试的 Python 调用只使用环境变量默认值。
    parser = argparse.ArgumentParser(description="Write one retained remote pytest phase summary.")  # 阶段摘要 CLI 解析器

    # phase 标识决定摘要文件和 release receipt 的绑定阶段。
    parser.add_argument("--phase", default=os.environ.get("VERILOG_GENERATOR_PYTEST_PHASE", "full"))

    # log-path 指向当前阶段的完整 pytest 控制台输出。
    parser.add_argument("--log-path", default=os.environ.get("VERILOG_GENERATOR_PYTEST_LOG", ""))

    # output-path 决定阶段 JSON 摘要的 retained 落点。
    parser.add_argument("--output-path", default=os.environ.get("VERILOG_GENERATOR_PYTEST_OUTPUT", ""))

    # command-hash 绑定 shell runner 实际执行的命令文本。
    parser.add_argument("--command-hash", default=os.environ.get("VERILOG_GENERATOR_PYTEST_COMMAND_HASH", ""))

    # exit-code 保留 pytest 原始退出状态，失败阶段仍可记录结构化摘要。
    parser.add_argument("--exit-code", type=int, default=int(os.environ.get("VERILOG_GENERATOR_PYTEST_EXIT_CODE", "0")))

    # timestamp 由远程 shell 生成，避免本地时钟替代远端事实。
    parser.add_argument("--timestamp", default=os.environ.get("VERILOG_GENERATOR_PYTEST_TIMESTAMP", ""))

    # 导入方调用 main() 时不应把宿主 pytest 参数误当成当前摘要参数。
    list_argv = [] if argv is None else argv  # 嵌入式调用默认不继承宿主进程参数

    # argparse 结果供路径、日志和摘要写入阶段复用。
    namespace_args: argparse.Namespace = parser.parse_args(list_argv)  # 解析后的阶段摘要参数

    # 先解析本轮目录，后续日志与摘要必须共享同一证据边界。
    path_smoke_run_root = smoke_run_root()  # 当前远程 retained 运行目录

    # 日志路径按本轮运行目录与稳定相对名称组合。
    path_pytest_log = _resolve_run_path(path_smoke_run_root, namespace_args.log_path, PATH_PYTEST_LOG)  # 当前 pytest 控制台日志

    # 摘要路径与日志归属于同一 retained 运行目录。
    path_pytest_summary = _resolve_run_path(path_smoke_run_root, namespace_args.output_path, PATH_PYTEST_SUMMARY)  # 当前 pytest 结构化摘要

    # 按行读取日志，替换非法工具输出字节后仍保留诊断文本。
    list_log_lines = path_pytest_log.read_text(  # pytest 控制台输出行
        encoding="utf-8",  # 远程验证包统一使用 UTF-8
        errors="replace",  # 非法字节替换后继续解析成功末行
    ).splitlines()

    # 最后一条成功摘要是精确计数和耗时的唯一输入。
    str_summary_line = find_pytest_summary_line(list_log_lines)  # pytest 成功摘要原文

    # 解析函数在写文件前验证通过数与耗时，失败时不产生伪证据。
    dict_payload = parse_pytest_summary_line(  # report-runs 下载的 pytest 载荷
        str_summary_line,  # 末行中的精确计数和耗时来源
        phase=namespace_args.phase,  # 当前 targeted/regression/full 阶段
        log_path=str(path_pytest_log),  # 当前阶段日志路径
        output_path=str(path_pytest_summary),  # 当前阶段摘要路径

        # 以下字段把阶段命令和完成时刻绑定到同一摘要。
        command_hash=namespace_args.command_hash,  # 实际命令文本哈希
        exit_code=namespace_args.exit_code,  # pytest 原始退出码
        timestamp=namespace_args.timestamp,  # 远程阶段完成时间
    )

    # retained JSON 使用稳定键序和 UTF-8，供机器与人工共同复核。
    path_pytest_summary.parent.mkdir(parents=True, exist_ok=True)

    # write_text 负责以 UTF-8 固化可下载的阶段摘要，stdout 仍只输出短状态。
    path_pytest_summary.write_text(
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

    # 直接执行模块时显式传播 CLI 参数，区别于被导入方的无参数调用。
    main(sys.argv[1:])
