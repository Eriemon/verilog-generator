"""Semantic comment-placement validation for generated Verilog."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


GENERIC_COMMENT_PATTERNS = (
    re.compile(r"泛泛"),
    re.compile(r"逐行中文注释"),
    re.compile(r"占位"),
    re.compile(r"\bplaceholder\b", re.IGNORECASE),
    re.compile(r"这里处理逻辑"),
    re.compile(r"模块结束\s*$"),
    re.compile(r"//"),
)


def validate_comment_placement(root: Path, comment_language: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {
        "scanned_files": 0,
        "code_lines": 0,
        "same_line_comment_lines": 0,
        "violations": 0,
        "by_construct": {},
    }
    for path in sorted(root.glob("**/*.v")):
        rel = path.relative_to(root).as_posix()
        is_tb = _is_testbench(path)
        infos = _verilog_line_infos(path.read_text(encoding="utf-8", errors="ignore").splitlines())
        metrics["scanned_files"] += 1
        in_multiline_macro = False
        for index, info in enumerate(infos):
            if not info["has_code"]:
                continue
            code = str(info["code"]).strip()
            comment = str(info["comment"]).strip()
            construct = _classify_construct(code, is_tb)
            counted_construct = _metric_construct(construct)
            metrics["code_lines"] += 1
            _bump(metrics, counted_construct, "checked")

            if in_multiline_macro:
                if comment:
                    _add_issue(
                        issues,
                        metrics,
                        counted_construct,
                        "Multiline macro continuation must not carry an inline comment; bind one pure comment before the macro.",
                        rel,
                        info["line_no"],
                    )
                if not code.endswith("\\"):
                    in_multiline_macro = False
                continue

            if code.startswith("`define") and code.endswith("\\"):
                if comment:
                    _add_issue(
                        issues,
                        metrics,
                        counted_construct,
                        "Multiline macro `define line must use a pure leading comment, not an inline continuation comment.",
                        rel,
                        info["line_no"],
                    )
                if not _valid_leading_comment(infos, index, comment_language, keyword="宏"):
                    _add_issue(
                        issues,
                        metrics,
                        counted_construct,
                        "Multiline macro must have a pure explanatory comment immediately before the `define line.",
                        rel,
                        info["line_no"],
                    )
                in_multiline_macro = True
                continue

            if _comment_satisfies_language(comment, comment_language):
                metrics["same_line_comment_lines"] += 1
            else:
                _add_issue(
                    issues,
                    metrics,
                    counted_construct,
                    "Verilog code line must use a same-line explanatory comment in the requested language.",
                    rel,
                    info["line_no"],
                )
                continue

            if _comment_is_generic(comment):
                _add_issue(
                    issues,
                    metrics,
                    counted_construct,
                    "Generic Verilog comment is not allowed; describe the construct, signal, condition, or verification purpose.",
                    rel,
                    info["line_no"],
                )

            if construct in {"module_end", "task_end", "function_end", "generate_end"} and not _valid_end_comment(comment):
                _add_issue(
                    issues,
                    metrics,
                    counted_construct,
                    "End construct comment must name the construct being closed and start with an end/结束 phrase.",
                    rel,
                    info["line_no"],
                )
            if construct == "module" and not _module_comment_valid(comment):
                _add_issue(
                    issues,
                    metrics,
                    counted_construct,
                    "Module declaration comment must identify the module or testbench purpose.",
                    rel,
                    info["line_no"],
                )
            if construct == "generate" and "begin:" in code and "gen_" not in code:
                _add_issue(
                    issues,
                    metrics,
                    counted_construct,
                    "Generate branch labels must begin with `gen_` and the same line must explain the branch.",
                    rel,
                    info["line_no"],
                )
            if construct in {"task", "function"}:
                if not is_tb:
                    _add_issue(
                        issues,
                        metrics,
                        counted_construct,
                        "RTL task/function blocks are not allowed; only testbenches may use documented helpers.",
                        rel,
                        info["line_no"],
                    )
                elif not _valid_leading_comment(infos, index, comment_language, keyword="任务" if construct == "task" else "函数"):
                    _add_issue(
                        issues,
                        metrics,
                        counted_construct,
                        "Testbench task/function declarations must have a pure leading purpose comment.",
                        rel,
                        info["line_no"],
                    )
    return issues, metrics


def _add_issue(
    issues: list[dict[str, Any]],
    metrics: dict[str, Any],
    construct: str,
    message: str,
    rel: str,
    line_no: int,
) -> None:
    issues.append(
        {
            "severity": "error",
            "message": message,
            "path": f"{rel}:{line_no}",
            "stage": "static",
            "source": "current_module_issue",
            "detail": f"COMMENT_PLACEMENT construct={construct} line={line_no}",
        }
    )
    metrics["violations"] += 1
    _bump(metrics, construct, "violations")


def _bump(metrics: dict[str, Any], construct: str, key: str) -> None:
    by_construct = metrics.setdefault("by_construct", {})
    bucket = by_construct.setdefault(construct, {"checked": 0, "violations": 0})
    bucket[key] = int(bucket.get(key, 0)) + 1


def _classify_construct(code: str, is_tb: bool) -> str:
    del is_tb
    stripped = code.strip()
    if stripped.startswith("endmodule"):
        return "module_end"
    if stripped.startswith("endtask"):
        return "task_end"
    if stripped.startswith("endfunction"):
        return "function_end"
    if stripped.startswith("endgenerate"):
        return "generate_end"
    if re.match(r"^module\b", stripped):
        return "module"
    if stripped.startswith("`define"):
        return "macro"
    if stripped.startswith("`"):
        return "directive"
    if re.match(r"^(parameter|localparam)\b", stripped):
        return "parameter"
    if re.match(r"^(input|output|inout)\b", stripped):
        return "port"
    if re.match(r"^(reg|wire|integer|genvar)\b", stripped):
        return "signal"
    if re.match(r"^assign\b", stripped):
        return "assign"
    if re.match(r"^(task)\b", stripped):
        return "task"
    if re.match(r"^(function)\b", stripped):
        return "function"
    if stripped.startswith("generate") or "begin:" in stripped and "gen_" in stripped:
        return "generate"
    if re.match(r"^(always|initial)\b", stripped):
        return "always"
    if re.match(r"^(case|endcase)\b", stripped):
        return "case"
    if re.match(r"^(if|else|default)\b", stripped) or re.match(r"^[A-Z][A-Z0-9_]*\s*:", stripped):
        return "branch"
    if _looks_like_instance(stripped):
        return "instance"
    return "statement"


def _metric_construct(construct: str) -> str:
    if construct in {"module_end"}:
        return "module"
    if construct in {"task", "task_end", "function", "function_end"}:
        return "testbench_task"
    if construct in {"generate_end"}:
        return "generate"
    return construct


def _looks_like_instance(code: str) -> bool:
    if re.match(r"^(if|for|case|assign|always|initial|else|begin|end)\b", code):
        return False
    return bool(
        re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s+[A-Za-z_][A-Za-z0-9_]*\s*\(", code)
        or re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*#\s*\(", code)
    )


def _valid_leading_comment(infos: list[dict[str, Any]], index: int, comment_language: str, *, keyword: str) -> bool:
    previous = index - 1
    if previous < 0:
        return False
    info = infos[previous]
    comment = str(info["comment"]).strip()
    if not info["pure_comment"] or not _comment_satisfies_language(comment, comment_language):
        return False
    if _comment_is_generic(comment):
        return False
    return keyword in comment or comment_language != "zh"


def _module_comment_valid(comment: str) -> bool:
    return any(token in comment for token in ("模块", "测试平台", "module", "testbench"))


def _valid_end_comment(comment: str) -> bool:
    stripped = comment.strip()
    return stripped.startswith("结束") or stripped.lower().startswith(("end ", "end:"))


def _comment_is_generic(comment: str) -> bool:
    stripped = comment.strip()
    return any(pattern.search(stripped) for pattern in GENERIC_COMMENT_PATTERNS)


def _comment_satisfies_language(comment: str, comment_language: str) -> bool:
    normalized = comment.strip()
    if not normalized:
        return False
    if comment_language == "zh":
        return any("\u4e00" <= char <= "\u9fff" for char in normalized)
    return True


def _verilog_line_infos(lines: list[str]) -> list[dict[str, Any]]:
    infos: list[dict[str, Any]] = []
    in_block_comment = False
    for line_no, line in enumerate(lines, start=1):
        code, comment, in_block_comment = _split_verilog_code_and_comment(line, in_block_comment)
        has_code = bool(code.strip())
        has_comment = bool(comment.strip())
        infos.append(
            {
                "line_no": line_no,
                "code": code,
                "has_code": has_code,
                "comment": comment.strip(),
                "pure_comment": has_comment and not has_code,
            }
        )
    return infos


def _split_verilog_code_and_comment(line: str, in_block_comment: bool) -> tuple[str, str, bool]:
    code_parts: list[str] = []
    comment_parts: list[str] = []
    index = 0
    while index < len(line):
        if in_block_comment:
            end_index = line.find("*/", index)
            if end_index == -1:
                comment_parts.append(line[index:])
                return "".join(code_parts), " ".join(comment_parts), True
            comment_parts.append(line[index:end_index])
            index = end_index + 2
            in_block_comment = False
            continue
        if line.startswith("//", index):
            comment_parts.append(line[index + 2 :])
            break
        if line.startswith("/*", index):
            end_index = line.find("*/", index + 2)
            if end_index == -1:
                comment_parts.append(line[index + 2 :])
                return "".join(code_parts), " ".join(comment_parts), True
            comment_parts.append(line[index + 2 : end_index])
            index = end_index + 2
            continue
        code_parts.append(line[index])
        index += 1
    return "".join(code_parts), " ".join(comment_parts), False


def _is_testbench(path: Path) -> bool:
    stem = path.stem.lower()
    return stem.endswith("_tb") or stem.startswith("tb_") or "testbench" in stem
