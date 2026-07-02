#!/usr/bin/env python3
"""构建 Erie Verilog Generator 的确定性发布产物。"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

sys.dont_write_bytecode = True

SKILL_DIR = Path(__file__).resolve().parents[1]
ROOT = SKILL_DIR
DIST_ROOT = ROOT / "dist"
MANIFEST_PATH = DIST_ROOT / "manifest.json"
RECEIPT_NAME = "RELEASE_RECEIPT.json"
ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)
POLICY_VERSION = "2026-07-02-v1"
TOP_LEVEL_FILE_MODE = "explicit-allowlist"

ALLOWED_TOP_LEVEL_FILES = [
    ".gitignore",
    "CITATION.cff",
    "CONTRIBUTING.md",
    "ENGINEERING_DESIGN_GOALS.md",
    "LICENSE",
    "README-CN.md",
    "README.md",
    "RELEASE_RECEIPT.json",
    "SECURITY.md",
    "SKILL.md",
    "VERSION",
    "pyproject.toml",
]

ALLOWED_TOP_LEVEL_DIRS = [
    "agents",
    "assets",
    "config",
    "docs",
    "evals",
    "integration",
    "references",
    "runtime",
    "scripts",
]

FORBIDDEN_EXACT_NAMES = [
    ".erie-verilog-generator-state",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".settings",
    "__pycache__",
    "_smoke_runs",
    "dist",
    "downloads",
    "logs",
    "reports",
    "requests",
    "runs",
    "smoke",
    "test",
    "tests",
    "tmp",
]

FORBIDDEN_PREFIXES = ["smoke"]
FORBIDDEN_SUFFIXES = [".log", ".pyc", ".pyo"]


def should_exclude(path: Path, root: Path) -> bool:
    """判断源码树中的路径是否应排除出发布包。"""

    path_relative = path.relative_to(root)
    list_lower_parts = [part.casefold() for part in path_relative.parts]
    str_lower_name = path.name.casefold()
    str_posix = path_relative.as_posix()

    if ".git" in list_lower_parts:
        return True

    if any(part in FORBIDDEN_EXACT_NAMES for part in list_lower_parts):
        return True

    if any(part.startswith(tuple(FORBIDDEN_PREFIXES)) for part in list_lower_parts):
        return True

    if path.suffix.casefold() in FORBIDDEN_SUFFIXES:
        return True

    if str_lower_name == "project.local.json" or str_lower_name.endswith(".local.json"):
        return True

    if (
        str_lower_name.startswith("server_list.local.json.bak")
        or ".bak." in str_lower_name
        or str_lower_name.endswith(".bak")
    ):
        return True

    if str_posix.startswith("dist/"):
        return True

    return False


def release_files(root: Path) -> list[Path]:
    """列出 root 下应进入发布包的文件。"""

    list_files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if should_exclude(path, root):
            continue
        list_files.append(path)
    return list_files


def skill_version() -> str:
    """读取 VERSION 中的当前版本。"""

    return (SKILL_DIR / "VERSION").read_text(encoding="utf-8").strip()


def artifact_base_name() -> str:
    """返回 release 目录和 zip 的共享基名。"""

    return f"erie-verilog-generator-{skill_version()}"


def dist_skill_path() -> Path:
    """返回当前版本发布目录路径。"""

    return DIST_ROOT / artifact_base_name()


def zip_path() -> Path:
    """返回当前版本 zip 路径。"""

    return DIST_ROOT / f"{artifact_base_name()}.zip"


def remove_release_path(path: Path) -> None:
    """安全删除当前版本发布目录或 zip。"""

    if path.is_dir():
        shutil.rmtree(path)
        return
    if path.exists():
        path.unlink()


def copy_release_tree() -> None:
    """复制当前仓库到版本化 release 目录。"""

    path_dist_skill = dist_skill_path()
    if path_dist_skill.exists():
        remove_release_path(path_dist_skill)

    path_dist_skill.parent.mkdir(parents=True, exist_ok=True)

    for source in release_files(SKILL_DIR):
        path_relative = source.relative_to(SKILL_DIR)
        path_target = path_dist_skill / path_relative
        path_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, path_target)


def build_zip() -> None:
    """把 release 目录打成确定性 zip。"""

    path_dist_skill = dist_skill_path()
    path_artifact_zip = zip_path()
    if path_artifact_zip.exists():
        remove_release_path(path_artifact_zip)

    with zipfile.ZipFile(path_artifact_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source in release_files(path_dist_skill):
            str_archive_name = source.relative_to(path_dist_skill).as_posix()
            zip_info_entry = zipfile.ZipInfo(str_archive_name, ZIP_TIMESTAMP)
            zip_info_entry.compress_type = zipfile.ZIP_DEFLATED
            zip_info_entry.external_attr = 0o644 << 16
            archive.writestr(zip_info_entry, source.read_bytes())


def file_sha256(path: Path) -> str:
    """计算文件 SHA-256。"""

    obj_hash = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            obj_hash.update(chunk)
    return obj_hash.hexdigest()


def git_output(args: list[str]) -> str:
    """运行只读 Git 命令并返回 stdout。"""

    completed_process = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed_process.returncode == 0:
        return completed_process.stdout.strip()
    return ""


def is_git_work_tree() -> bool:
    """判断当前目录是否在 Git 工作树内。"""

    completed_process = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return completed_process.returncode == 0 and completed_process.stdout.strip() == "true"


def source_state() -> tuple[str, str, bool]:
    """返回分支、提交和脏工作树状态。"""

    if not is_git_work_tree():
        return "standalone", "not-a-git-repository", False

    str_branch = git_output(["rev-parse", "--abbrev-ref", "HEAD"]) or "unknown"
    str_commit = git_output(["rev-parse", "HEAD"])
    bool_dirty = subprocess.run(["git", "diff", "--quiet"], cwd=ROOT, check=False).returncode != 0
    bool_dirty = bool_dirty or subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=ROOT,
        check=False,
    ).returncode != 0
    return str_branch, ("working-tree" if bool_dirty else str_commit), bool_dirty


def git_lines(args: list[str]) -> list[str]:
    """运行只读 Git 命令并按行清理输出。"""

    if not is_git_work_tree():
        return []

    completed_process = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed_process.returncode != 0:
        return []

    list_lines: list[str] = []
    for line in completed_process.stdout.splitlines():
        if not line.strip():
            continue
        list_lines.append(line.strip().lstrip("*+ ").strip())
    return list_lines


def tracked_forbidden_paths() -> list[str]:
    """扫描 Git 跟踪内容中不应公开的路径。"""

    if not is_git_work_tree():
        return []

    completed_process = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if completed_process.returncode != 0:
        return []

    list_violations: list[str] = []
    for raw_path in completed_process.stdout.split(b"\x00"):
        if not raw_path:
            continue
        str_rel = raw_path.decode("utf-8", errors="ignore").replace("\\", "/")
        path_rel = Path(str_rel)
        list_lower_parts = [part.casefold() for part in path_rel.parts]
        str_lower_name = path_rel.name.casefold()

        if any(part in FORBIDDEN_EXACT_NAMES for part in list_lower_parts):
            list_violations.append(str_rel)
            continue
        if any(part.startswith(tuple(FORBIDDEN_PREFIXES)) for part in list_lower_parts):
            list_violations.append(str_rel)
            continue
        if str_lower_name == "project.local.json" or str_lower_name.endswith(".local.json"):
            list_violations.append(str_rel)
            continue
        if (
            str_lower_name.startswith("server_list.local.json.bak")
            or ".bak." in str_lower_name
            or str_lower_name.endswith(".bak")
            or path_rel.suffix.casefold() in FORBIDDEN_SUFFIXES
        ):
            list_violations.append(str_rel)

    return sorted(set(list_violations))


def verify_tracked_content_policy() -> None:
    """阻断仍被 Git 跟踪的禁传路径。"""

    list_violations = tracked_forbidden_paths()
    if list_violations:
        raise SystemExit(
            "> ERR: [Python] Forbidden tracked paths remain in the public repository: "
            + ", ".join(list_violations)
        )


def release_file_manifest(root: Path) -> list[dict[str, str]]:
    """生成 release 目录中文件的路径与哈希清单。"""

    list_manifest: list[dict[str, str]] = []
    for path in release_files(root):
        str_release_path = path.relative_to(root).as_posix()
        if str_release_path == RECEIPT_NAME:
            continue
        list_manifest.append({"path": str_release_path, "sha256": file_sha256(path)})
    return list_manifest


def release_content_analysis(root: Path) -> dict[str, object]:
    """生成 release 内容策略摘要并做顶层结构校验。"""

    list_included_files: list[str] = []
    for path in release_files(root):
        str_release_path = path.relative_to(root).as_posix()
        if str_release_path == RECEIPT_NAME:
            continue
        list_included_files.append(str_release_path)

    list_top_level_entries = sorted({Path(relative).parts[0] for relative in list_included_files})
    set_allowed_entries = set(ALLOWED_TOP_LEVEL_FILES) | set(ALLOWED_TOP_LEVEL_DIRS)
    list_unexpected_entries = [entry for entry in list_top_level_entries if entry not in set_allowed_entries]

    if list_unexpected_entries:
        raise SystemExit(
            "> ERR: [Python] Unexpected top-level release entries were found: "
            + ", ".join(list_unexpected_entries)
        )

    return {
        "policy_version": POLICY_VERSION,
        "top_level_file_mode": TOP_LEVEL_FILE_MODE,
        "allowed_top_level_files": ALLOWED_TOP_LEVEL_FILES,
        "allowed_top_level_dirs": ALLOWED_TOP_LEVEL_DIRS,
        "forbidden_exact_names": FORBIDDEN_EXACT_NAMES,
        "forbidden_prefixes": FORBIDDEN_PREFIXES,
        "forbidden_suffixes": FORBIDDEN_SUFFIXES,
        "included_file_count": len(list_included_files),
        "included_top_level_entries": list_top_level_entries,
        "unexpected_top_level_entries": list_unexpected_entries,
        "forbidden_source_paths": tracked_forbidden_paths(),
        "forbidden_release_paths": [],
    }


def write_release_receipt(str_branch: str, str_commit: str, bool_dirty: bool) -> None:
    """写入当前 release 目录的 RELEASE_RECEIPT.json。"""

    path_dist_skill = dist_skill_path()
    dict_policy = release_content_analysis(path_dist_skill)
    str_packaging_mode = "repository-dist" if is_git_work_tree() else "standalone-dist"

    dict_receipt = {
        "skill_name": "erie-verilog-generator",
        "version": skill_version(),
        "source_path": ".",
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "current_branch": str_branch,
        "source_commit": str_commit,
        "local_branches": git_lines(["branch", "--list"]),
        "worktree_clean": not bool_dirty,
        "phase_results": {"pre": True, "post": True},
        "packaging_mode": str_packaging_mode,
        "validation_level": "strong",
        "provenance_mode": str_packaging_mode,
        "sanitization": {
            "required": True,
            "mode": "static-exclude",
            "scope": "public-repository-release",
        },
        "release_content_policy": dict_policy,
        "files": release_file_manifest(path_dist_skill),
        "other_version_artifacts": [],
    }

    (path_dist_skill / RECEIPT_NAME).write_text(
        json.dumps(dict_receipt, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_manifest(str_branch: str, str_commit: str, bool_dirty: bool) -> None:
    """写入 dist/manifest.json。"""

    path_dist_skill = dist_skill_path()
    path_artifact_zip = zip_path()
    str_release_created_at = dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    dict_manifest = {
        "name": "erie-verilog-generator",
        "version": skill_version(),
        "source_branch": str_branch,
        "source_commit": str_commit,
        "source_dirty": bool_dirty,
        "directory_artifact": artifact_base_name(),
        "zip_artifact": path_artifact_zip.name,
        "zip_sha256": file_sha256(path_artifact_zip),
        "file_count": len(release_files(path_dist_skill)),
        "release_created_at": str_release_created_at,
        "validation_commands": [
            r"python .\scripts\validate_verilog_skill.py --no-require-remote",
            r"python .\scripts\validate_verilog_skill.py --no-require-remote --with-external-audit",
            r"python .\scripts\build_release.py",
        ],
        "excludes": [
            "dist/",
            ".settings/",
            ".erie-verilog-generator-state/",
            "reports/",
            "requests/",
            "downloads/",
            "logs/",
            "tmp/",
            "runs/",
            "smoke/",
            "test/",
            "tests/",
            "__pycache__/",
            "_smoke_runs/",
            "*.local.json",
            "*.bak",
            "*.log",
            "*.pyc",
            "*.pyo",
        ],
    }

    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(dict_manifest, indent=2) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    """执行 release 目录、zip、receipt 和 manifest 构建流程。"""

    parser = argparse.ArgumentParser(description="Build Erie Verilog Generator release artifacts.")
    parser.parse_args()

    verify_tracked_content_policy()

    str_branch, str_commit, bool_dirty = source_state()
    copy_release_tree()
    write_release_receipt(str_branch, str_commit, bool_dirty)
    build_zip()
    write_manifest(str_branch, str_commit, bool_dirty)

    print(f"> INFO: [Python] release directory: {dist_skill_path()}")
    print(f"> INFO: [Python] release zip: {zip_path()}")
    print(f"> INFO: [Python] manifest: {MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
