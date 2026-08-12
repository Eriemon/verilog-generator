"""生成远端验证包解包和逐文件完整性校验的 shell 片段。"""

# build_package_integrity_snippet 把上传归档恢复为 workspace 内容并 fail-closed。
def build_package_integrity_snippet(
    str_py: str,
    str_archive_name_quoted: str,
    str_source_digest_quoted: str,
) -> str:
    """构造远端归档解包、清单校验和整体摘要校验脚本。

    :param str_py: 已完成 shell quoting 的远端 Python 命令。
    :param str_archive_name_quoted: 已完成 shell quoting 的归档文件名。
    :param str_source_digest_quoted: 已完成 shell quoting 的 staging 摘要。
    :return: 可插入远端 bash gate 的完整归档完整性片段。
    """

    # 归档先解包到 workspace 父目录，再以清单和整体摘要双重校验内容。
    return f"""
package_archive_path="$PWD"/{str_archive_name_quoted}
package_root_path="$PWD/.."
{str_py} - "$package_archive_path" "$package_root_path" {str_source_digest_quoted} <<'PY'
import hashlib
import json
import sys
import tarfile
from pathlib import Path, PurePosixPath

archive_path = Path(sys.argv[1]).resolve()
package_root = Path(sys.argv[2]).resolve()
expected_digest = sys.argv[3]
manifest_path = package_root / "package-manifest.json"

if not archive_path.is_file():
    raise SystemExit(f"Package archive is missing: {{archive_path}}")

with tarfile.open(archive_path, mode="r:gz") as archive:
    list_safe_members = []
    for member in archive.getmembers():
        member_path = (package_root / member.name).resolve()
        if member.issym() or member.islnk():
            raise SystemExit(f"Package archive contains link: {{member.name}}")
        if member_path != package_root and package_root not in member_path.parents:
            raise SystemExit(f"Package archive escapes workspace: {{member.name}}")
        list_safe_members.append(member)
    archive.extractall(package_root, members=list_safe_members)

if not manifest_path.is_file():
    raise SystemExit(f"Package manifest is missing: {{manifest_path}}")

manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
records = manifest.get("files") if isinstance(manifest, dict) else None
if not isinstance(manifest, dict) or manifest.get("schema") != 1 or not isinstance(records, list):
    raise SystemExit("Package manifest schema is invalid")

expected_paths = []
record_by_path = {{}}
for record in records:
    if not isinstance(record, dict):
        raise SystemExit("Package manifest contains a non-object record")
    relative_path = str(record.get("path", ""))
    relative_parts = PurePosixPath(relative_path).parts
    if not relative_path or PurePosixPath(relative_path).is_absolute() or ".." in relative_parts:
        raise SystemExit(f"Package manifest contains unsafe path: {{relative_path}}")
    if relative_path in record_by_path:
        raise SystemExit(f"Package manifest contains duplicate path: {{relative_path}}")
    expected_paths.append(relative_path)
    record_by_path[relative_path] = record

actual_paths = []
for path_file in package_root.rglob("*"):
    if path_file.is_file() and path_file.resolve() != archive_path:
        actual_paths.append(path_file.relative_to(package_root).as_posix())

expected_with_manifest = set(expected_paths) | {{"package-manifest.json"}}
actual_path_set = set(actual_paths)
list_missing = sorted(expected_with_manifest - actual_path_set)
list_unexpected = sorted(actual_path_set - expected_with_manifest)
list_mismatched = []
for relative_path, record in record_by_path.items():
    path_file = package_root / relative_path
    if not path_file.is_file():
        continue
    bytes_file = path_file.read_bytes()
    if record.get("size") != len(bytes_file) or record.get("sha256") != hashlib.sha256(bytes_file).hexdigest():
        list_mismatched.append(relative_path)

digest = hashlib.sha256()
for relative_path in sorted(actual_paths):
    digest.update(relative_path.encode("utf-8"))
    digest.update(b"\\0")
    digest.update((package_root / relative_path).read_bytes())
    digest.update(b"\\0")
actual_digest = digest.hexdigest()
if list_missing or list_unexpected or list_mismatched or actual_digest != expected_digest:
    details = {{
        "missing": list_missing,
        "unexpected": list_unexpected,
        "mismatched": list_mismatched,
        "expected_digest": expected_digest,
        "actual_digest": actual_digest,
    }}
    raise SystemExit("Package integrity check failed: " + json.dumps(details, sort_keys=True))
PY
""".strip()
