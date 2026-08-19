"""生成远端验证包解包和逐文件完整性校验的 shell 片段。"""

# build_package_integrity_snippet 保留兼容符号，但拒绝恢复上传归档。
def build_package_integrity_snippet(
    str_py: str,
    str_archive_name_quoted: str,
    str_source_digest_quoted: str,
) -> str:
    """拒绝生成归档解包脚本。

    :param str_py: 已完成 shell quoting 的远端 Python 命令。
    :param str_archive_name_quoted: 已完成 shell quoting 的归档文件名。
    :param str_source_digest_quoted: 历史 staging 摘要参数，仅用于保持调用签名。
    :return: 不返回；当前实现始终抛出归档禁用异常。
    :raises RuntimeError: 任意调用都表示试图绕过 manifest-only 合同。
    """

    # 兼容入口必须 fail-closed，不能生成任何 archive extraction shell 片段。
    raise RuntimeError(
        "> ERR: [Python] archive upload is disabled; use manifest-bound directory upload"
    )
