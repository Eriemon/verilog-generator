"""集中承载 validate facade 的公开兼容门禁入口。"""

# dataclass 用于把 facade 依赖的 loader 与 context builder 收束到一个对象里。
from dataclasses import dataclass

# pathlib 与 typing 提供路径签名、回调类型和绑定命名空间的标注。
from pathlib import Path
from typing import Any, Callable

# FacadeLoader 统一表示延迟导入 helper 模块的回调签名。
FacadeLoader = Callable[[], Any]  # facade helper loader 类型别名

# ContextBuilder 统一表示复用现有 gate 上下文拼装函数的回调签名。
ContextBuilder = Callable[[], Any]  # 现有 gate 上下文 builder 类型别名

# ValidationGateFacade 聚合 validate_verilog_skill.py 需要继续公开的兼容门禁函数。
@dataclass(frozen=True)
class ValidationGateFacade:
    """桥接治理、源码审计与工作区 helper 的公开兼容入口。"""

    # 这一个 loader 负责工作区 helper 的延迟导入。
    func_load_workspace_gate_module: FacadeLoader  # 工作区 helper 模块 loader

    # 这一个 loader 负责源码审计 helper 的延迟导入。
    func_load_source_audit_module: FacadeLoader  # 源码审计 helper 模块 loader

    # 这一个 loader 负责治理 helper 的延迟导入。
    func_load_governance_gate_module: FacadeLoader  # 治理 helper 模块 loader

    # 这一个 builder 复用工作区 gate 上下文拼装逻辑。
    func_build_workspace_gate_context: ContextBuilder  # 工作区 gate 上下文 builder

    # 这一个 builder 复用源码审计上下文拼装逻辑。
    func_build_source_audit_context: ContextBuilder  # 源码审计上下文 builder

    # 这一个 builder 复用治理 gate 上下文拼装逻辑。
    func_build_governance_gate_context: ContextBuilder  # 治理 gate 上下文 builder

    # build_remote_validation_command 保持远程 gate 子进程命令的兼容拼装顺序。
    def build_remote_validation_command(
        self,
        settings_path: Path,
        remote_server: str | None,
        *,
        report_runs: bool = False,
        run_id: str | None = None,
    ) -> list[str]:
        """组装远程验证脚本命令。

        :param settings_path: 当前 validate 运行使用的 settings 文件路径。
        :param remote_server: 可选的显式远程服务器标识；为空时回落到本地已确认选择。
        :param report_runs: 是否切换到只读取最近一次远端运行证据的报告模式。
        :param run_id: report-runs 需要精确读取的 outer retained run 标识。
        :return: 返回命令列表；保持 facade 对远程验证子进程 argv 顺序的兼容封装。
        """

        # 直接把 settings、服务器选择与治理上下文交给远程命令拼装 helper。
        return self.func_load_governance_gate_module().build_remote_validation_command(
            settings_path,
            remote_server,
            self.func_build_governance_gate_context(),
            report_runs=report_runs,
            run_id=run_id,
        )

    # resolve_remote_server 只提取已确认选择中的 server_id。
    def resolve_remote_server(self, settings: dict[str, Any]) -> str | None:
        """读取项目已确认远程服务器。

        :param settings: Verilog skill 治理配置字典，提供远程集成与选择文件路径。
        :return: 返回服务器标识或 None；供远程 gate 决定是否继续向下执行。
        """

        # 直接委托治理 helper 解析当前项目已经确认的远程服务器选择。
        return self.func_load_governance_gate_module().resolve_remote_server(settings)

    # resolve_required_remote_validation_state 负责远程 gate 的前置状态校验。
    def resolve_required_remote_validation_state(
        self,
        settings: dict[str, Any],
        *,
        explicit_server: str | None = None,
    ) -> dict[str, Any]:
        """校验远程验证的本地选择状态，并返回远程运行所需的最小上下文。

        :param settings: Verilog skill 治理配置字典，提供远程集成与本地选择文件路径。
        :param explicit_server: 可选的显式服务器标识；提供时优先覆盖本地已确认选择。
        :return: 返回远程运行上下文字典；供远程 gate 继续拼装命令与断言状态。
        """

        # 直接委托治理 helper 校验远程选择状态，并回收运行所需的最小上下文。
        return self.func_load_governance_gate_module().resolve_required_remote_validation_state(
            settings,
            explicit_server=explicit_server,
        )

    # run_work_folder_gate 桥接 agents-md-generator 文档治理脚本。
    def run_work_folder_gate(self, *, require_external: bool = True) -> dict[str, Any]:
        """运行 AGENTS 文档治理 gate。

        :param require_external: 是否要求外部治理依赖齐备；为 False 时允许按策略跳过。
        :return: 返回治理结果字典；供调用方判断 AGENTS/docs gate 的后续状态。
        """

        # 直接把治理上下文和外部依赖策略转交给 docs gate helper。
        return self.func_load_governance_gate_module().run_work_folder_gate(
            self.func_build_governance_gate_context(),
            require_external=require_external,
        )

    # _is_advisory_work_folder_gate_failure 只放行当前开发期脏树提示。
    def _is_advisory_work_folder_gate_failure(self, payload: dict[str, Any]) -> bool:
        """识别仅由 dirty worktree 触发的分支治理失败。

        :param payload: work-folder gate 返回的结构化治理结果字典。
        :return: 返回布尔值；True 表示当前失败只属于可提示但不阻断的脏树分支状态。
        """

        # 直接委托治理 helper 判断该结果是否只是 dirty worktree advisory。
        return self.func_load_governance_gate_module()._is_advisory_work_folder_gate_failure(payload)

    # _has_transient_artifact_marker 统一识别 validate 会主动清理的局部运行产物路径。
    def _has_transient_artifact_marker(self, text: str) -> bool:
        """判断文本是否命中 validate 可自动清理的瞬时产物路径。

        :param text: 待扫描的日志文本或错误消息。
        :return: 返回布尔值；True 表示文本中出现了 validate 可安全清理的瞬态产物标记。
        """

        # 直接委托治理 helper 识别日志中的瞬态运行产物路径标记。
        return self.func_load_governance_gate_module()._has_transient_artifact_marker(text)

    # _is_dirty_worktree_branch_gate_message 统一匹配开发期允许 advisory 的脏树分支治理文案。
    def _is_dirty_worktree_branch_gate_message(self, message: str) -> bool:
        """判断错误消息是否对应 dirty worktree 的 branch-gate advisory。

        :param message: work-folder gate 报出的原始错误消息文本。
        :return: 返回布尔值；True 表示该消息属于开发期允许 advisory 的脏树提示。
        """

        # 直接委托治理 helper 匹配 dirty worktree 分支门禁的提示文案。
        return self.func_load_governance_gate_module()._is_dirty_worktree_branch_gate_message(message)

    # _payload_has_only_transient_artifact_errors 判断 JSON 载荷是否只包含可安全重试的瞬态工件错误。
    def _payload_has_only_transient_artifact_errors(self, payload: dict[str, Any]) -> bool:
        """判断 JSON 诊断是否只包含瞬态运行产物错误与 dirty worktree advisory。

        :param payload: audit 或 docs gate 返回的结构化诊断载荷。
        :return: 返回布尔值；True 表示当前错误只属于可清理后重试的瞬态工件集合。
        """

        # 直接委托治理 helper 判断该 JSON 载荷是否仅含瞬态工件错误。
        return self.func_load_governance_gate_module()._payload_has_only_transient_artifact_errors(payload)

    # _is_transient_work_folder_gate_failure 只识别可安全重试的治理脚本瞬时产物缺失。
    def _is_transient_work_folder_gate_failure(self, output: str) -> bool:
        """判断 work-folder-gate 失败是否属于瞬时运行产物缺失。

        :param output: work-folder gate 的原始输出文本。
        :return: 返回布尔值；True 表示当前失败只需清理瞬态工件后重试即可。
        """

        # 直接委托治理 helper 判断该 docs gate 失败是否属于瞬态工件缺失。
        return self.func_load_governance_gate_module()._is_transient_work_folder_gate_failure(output)

    # verify_skill_effectiveness 检查 skill-effectiveness JSON 摘要是否通过。
    def verify_skill_effectiveness(self, report_path: Path) -> None:
        """读取 effectiveness 报告并在 summary.ok 非 True 时失败。

        :param report_path: skill-effectiveness JSON 报告路径。
        :return: 不返回业务值；执行完成即表示效果评估摘要满足本地信心门禁要求。
        """

        # 直接委托治理 helper 校验效果评估报告中的 summary.ok 结论。
        self.func_load_governance_gate_module().verify_skill_effectiveness(report_path)

    # verify_audit_skill_report 检查 skill audit JSON 是否包含阻塞错误。
    def verify_audit_skill_report(self, output: str) -> None:
        """解析 audit 输出中的 JSON 对象，并在 errors 非空时失败。

        :param output: skill audit 子进程输出的原始文本。
        :return: 不返回业务值；执行完成即表示 audit JSON 未报告阻塞错误。
        """

        # 直接委托治理 helper 提取 audit JSON，并验证其中没有阻塞错误。
        self.func_load_governance_gate_module().verify_audit_skill_report(output)

    # run_audit_skill 执行 skill audit，并处理 smoke 目录瞬时残留导致的一次重试。
    def run_audit_skill(self, settings: dict[str, Any], smoke_dir: Path) -> None:
        """运行 skill audit；遇到 smoke 报告目录瞬时缺失时重试一次。

        :param settings: Verilog skill 治理配置字典，提供 audit 所需路径和依赖开关。
        :param smoke_dir: 本轮 validate 使用的 smoke 运行目录。
        :return: 不返回业务值；执行完成即表示 skill audit 已通过或按约定完成一次重试。
        """

        # 直接把 audit 参数与治理上下文交给治理 helper 处理一次重试逻辑。
        self.func_load_governance_gate_module().run_audit_skill(
            settings,
            smoke_dir,
            self.func_build_governance_gate_context(),
        )

    # parse_json_object 从混合日志尾部提取最后一个 JSON 对象。
    def parse_json_object(self, output: str) -> dict[str, Any]:
        """从命令输出中向后搜索并解析 JSON object。

        :param output: 可能混有日志和 JSON 的命令输出文本。
        :return: 返回字典对象；供调用方继续读取最后一个 JSON 结构化结果。
        """

        # 直接委托治理 helper 从混合日志尾部提取最后一个 JSON 对象。
        return self.func_load_governance_gate_module().parse_json_object(output)

    # verify_markdown_ascii 防止 Markdown 文档无边界地引入安装环境不稳定的非 ASCII 字符。
    def verify_markdown_ascii(self, settings: dict[str, Any] | None = None) -> None:
        """确认 skill 包内 Markdown 文件默认保持 ASCII-only，仅允许精确白名单例外。

        :param settings: validate 加载后的 settings 字典；当缺省时按空配置处理。
        :return: 不返回业务值；执行完成即表示 Markdown ASCII 门禁与白名单约束通过。
        """

        # 直接委托源码审计 helper 校验 Markdown ASCII 约束及精确白名单例外。
        self.func_load_source_audit_module().verify_markdown_ascii(
            settings,
            self.func_build_source_audit_context(),
        )

    # verify_skill_standards 串联发布前的 skill 元数据和资源约束。
    def verify_skill_standards(self) -> None:
        """检查 SKILL.md 和配套标准资源是否满足发布约束。

        :param: 此函数不接收外部业务参数。
        :return: 不返回业务值；执行完成即表示 SKILL.md 与标准资源门禁通过。
        """

        # 直接委托源码审计 helper 检查 SKILL.md 与标准资源约束。
        self.func_load_source_audit_module().verify_skill_standards(self.func_build_source_audit_context())

    # verify_legacy_terms 防止旧版依赖术语泄漏到未豁免文件。
    def verify_legacy_terms(self, settings: dict[str, Any]) -> None:
        """扫描 skill 文件中的 legacy 术语，并应用配置化 allowlist。

        :param settings: Verilog skill 治理配置字典，提供路径、依赖和验证开关。
        :return: 不返回业务值；执行完成即表示 legacy 术语扫描门禁通过。
        """

        # 直接委托源码审计 helper 扫描 legacy 术语并应用 allowlist。
        self.func_load_source_audit_module().verify_legacy_terms(
            settings,
            self.func_build_source_audit_context(),
        )

    # verify_dependency_schema 校验 defaults 中跨 skill 依赖和 FPGA 路由约束。
    def verify_dependency_schema(self, settings: dict[str, Any]) -> None:
        """确认依赖 schema、推荐项和 FPGA developer routing 没有漂移。

        :param settings: Verilog skill 治理配置字典，提供路径、依赖和验证开关。
        :return: 不返回业务值；执行完成即表示依赖 schema 与路由配置门禁通过。
        """

        # 直接委托源码审计 helper 校验 defaults 中的依赖 schema 与 FPGA 路由配置。
        self.func_load_source_audit_module().verify_dependency_schema(settings)

    # verify_hardcoded_paths 防止 skill 源文件携带本机绝对路径。
    def verify_hardcoded_paths(self) -> None:
        """扫描 skill 源文件中的硬编码绝对路径，允许配置文档中的示例除外。

        :param: 此函数不接收外部业务参数。
        :return: 不返回业务值；执行完成即表示硬编码绝对路径扫描门禁通过。
        """

        # 直接委托源码审计 helper 检查 skill 活动文件中的硬编码绝对路径。
        self.func_load_source_audit_module().verify_hardcoded_paths(self.func_build_source_audit_context())

    # verify_no_ref_dependencies 防止临时 ref 输入泄漏到活动文档或候选发布目录。
    def verify_no_ref_dependencies(self) -> None:
        """扫描活动文件和候选 release，确认没有依赖 ref 临时目录。

        :param: 此函数不接收外部业务参数。
        :return: 不返回业务值；执行完成即表示 ref 目录依赖扫描门禁通过。
        """

        # 直接委托源码审计 helper 检查活动文件和候选 release 的 ref 目录依赖。
        self.func_load_source_audit_module().verify_no_ref_dependencies(self.func_build_source_audit_context())

    # verify_no_residuals 确认 validate 结束后没有禁止的临时产物残留。
    def verify_no_residuals(self, settings: dict[str, Any], smoke_dir: Path) -> None:
        """检查 smoke 目录和 skill 根下的禁止残留文件。

        :param settings: Verilog skill 治理配置字典，提供路径、依赖和验证开关。
        :param smoke_dir: 本轮验证使用的临时 smoke 目录。
        :return: 不返回业务值；执行完成即表示禁止残留检查通过。
        """

        # 直接委托工作区 helper 校验 smoke 目录与 skill 根下的禁止残留。
        self.func_load_workspace_gate_module().verify_no_residuals(
            settings,
            smoke_dir,
            self.func_build_workspace_gate_context(),
        )

    # cleanup_residuals 清除 validate 过程中允许自动删除的本地残留。
    def cleanup_residuals(self, settings: dict[str, Any], smoke_dir: Path) -> None:
        """删除 smoke 目录、workflow-state 和 Python 缓存目录。

        :param settings: Verilog skill 治理配置字典，提供路径、依赖和验证开关。
        :param smoke_dir: 本轮验证使用的临时 smoke 目录。
        :return: 不返回业务值；执行完成即表示可清理残留已被处理。
        """

        # 直接委托工作区 helper 清理 smoke、workflow-state 与 Python 缓存目录。
        self.func_load_workspace_gate_module().cleanup_residuals(
            settings,
            smoke_dir,
            self.func_build_workspace_gate_context(),
        )

    # cleanup_audit_retry_local_artifacts 清理 audit 瞬态重试仅归属当前进程的本地产物。
    def cleanup_audit_retry_local_artifacts(self, settings: dict[str, Any], smoke_dir: Path) -> None:
        """清理 audit 瞬态重试场景下仅归属当前 validate 进程的本地产物。

        :param settings: Verilog skill 治理配置字典，提供路径、依赖和验证开关。
        :param smoke_dir: 本轮验证使用的临时 smoke 目录。
        :return: 不返回业务值；执行完成即表示 audit 重试残留已被处理。
        """

        # 直接委托工作区 helper 清理 audit 重试场景下归属当前进程的本地产物。
        self.func_load_workspace_gate_module().cleanup_audit_retry_local_artifacts(
            settings,
            smoke_dir,
            self.func_build_workspace_gate_context(),
        )

    # cleanup_audit_runtime_artifacts 清空审计运行后允许重建的本地运行产物。
    def cleanup_audit_runtime_artifacts(self, settings: dict[str, Any], smoke_dir: Path) -> None:
        """清空审计运行后允许重建的本地运行产物。

        :param settings: Verilog skill 治理配置字典，提供路径、依赖和验证开关。
        :param smoke_dir: 本轮验证使用的临时 smoke 目录。
        :return: 不返回业务值；执行完成即表示 audit runtime 工件已被处理。
        """

        # 直接委托工作区 helper 清空审计运行后允许重建的本地运行产物。
        self.func_load_workspace_gate_module().cleanup_audit_runtime_artifacts(
            settings,
            smoke_dir,
            self.func_build_workspace_gate_context(),
        )

    # _prune_empty_smoke_root 只在 smoke 根无内容时移除目录壳。
    def _prune_empty_smoke_root(self, settings: dict[str, Any]) -> None:
        """在 smoke 根清空后移除目录壳。

        :param settings: Verilog skill 治理配置字典，提供路径、依赖和验证开关。
        :return: 不返回业务值；执行完成即表示空的 smoke 根目录壳已按规则处理。
        """

        # 直接委托工作区 helper 在 smoke 根清空后移除目录壳。
        self.func_load_workspace_gate_module().prune_empty_smoke_root(settings)

    # remove_inside_skill 删除 skill 根内的临时文件，并拒绝越界路径。
    def remove_inside_skill(self, path: Path) -> None:
        """在 skill 根目录边界内安全删除文件或目录。

        :param path: 待解析、删除或展示的路径。
        :return: 不返回业务值；执行完成即表示 skill 根边界内目标已按规则处理。
        """

        # 直接委托工作区 helper 在 skill 根边界内安全删除临时文件或目录。
        self.func_load_workspace_gate_module().remove_inside_skill(path, self.func_build_workspace_gate_context())

    # remove_inside_smoke_root 删除 smoke 运行根内的临时文件，并拒绝越界路径。
    def remove_inside_smoke_root(self, settings: dict[str, Any], path: Path) -> None:
        """在 smoke 根目录边界内安全删除文件或目录。

        :param settings: Verilog skill 治理配置字典，提供路径、依赖和验证开关。
        :param path: 待解析、删除或展示的路径。
        :return: 不返回业务值；执行完成即表示 smoke 根边界内目标已按规则处理。
        """

        # 直接委托工作区 helper 在 smoke 根边界内安全删除目标路径。
        self.func_load_workspace_gate_module().remove_inside_smoke_root(settings, path)

    # _remove_tree_with_retry 缓解 Windows 文件句柄短暂占用导致的 rmtree 失败。
    def _remove_tree_with_retry(self, path: Path, *, attempts: int = 5, delay_s: float = 0.1) -> None:
        """带短暂重试地删除目录树，保留最终 OSError 供调用者诊断。

        :param path: 待解析、删除或展示的路径。
        :param attempts: 目录删除最多重试次数。
        :param delay_s: 两次删除重试之间的等待秒数。
        :return: 不返回业务值；执行完成即表示目标目录树已按短暂重试策略处理。
        """

        # 直接委托工作区 helper 按短暂重试策略删除目录树。
        self.func_load_workspace_gate_module().remove_tree_with_retry(
            path,
            attempts=attempts,
            delay_s=delay_s,
        )

    # iter_skill_files 枚举可参与发布/残留检查的 skill 源文件。
    def iter_skill_files(self) -> list[Path]:
        """列出 skill 根下除缓存和临时报告外的普通文件。

        :param: 此函数不接收外部业务参数。
        :return: 返回文件路径列表；供发布与残留检查 helper 复用。
        """

        # 直接委托工作区 helper 枚举可参与发布与残留检查的 skill 源文件。
        return self.func_load_workspace_gate_module().iter_skill_files(self.func_build_workspace_gate_context())

    # _project_relative 统一把路径呈现为项目相对形式。
    def _project_relative(self, path: Path) -> str:
        """返回用于错误信息和摘要的项目相对路径。

        :param path: 待解析、删除或展示的路径。
        :return: 返回字符串结果；供错误信息、报告和日志复用。
        """

        # 直接委托工作区 helper 把路径转换成项目相对展示形式。
        return self.func_load_workspace_gate_module().project_relative(
            path,
            self.func_build_workspace_gate_context(),
        )

    # project_artifact_path 解析命令行或配置中出现的项目工件路径。
    def project_artifact_path(self, path: str | Path) -> Path:
        """把相对路径锚定到项目根，绝对路径保持原样。

        :param path: 待解析、删除或展示的路径。
        :return: 返回路径对象；供 smoke、报告与发布卫生流程继续使用。
        """

        # 直接委托工作区 helper 解析命令行或配置中出现的项目工件路径。
        return self.func_load_workspace_gate_module().project_artifact_path(
            path,
            self.func_build_workspace_gate_context(),
        )

# 治理入口绑定 helper 隔离远程、工作区门禁与效果评估导出表。
def _bind_governance_exports(
    module_globals: dict[str, Any],
    facade: ValidationGateFacade,
) -> None:
    """把治理相关兼容入口回填到调用模块命名空间。

    :param module_globals: 调用方模块的 globals 字典。
    :param facade: 已绑定 loader 与上下文 builder 的门禁 facade。
    :return: 不返回业务值；导出入口原地写入 module_globals。
    """

    # 键名保持 validate_verilog_skill 的既有公开与私有兼容面。
    module_globals.update(
        {
            "build_remote_validation_command": facade.build_remote_validation_command,
            "resolve_remote_server": facade.resolve_remote_server,
            "resolve_required_remote_validation_state": facade.resolve_required_remote_validation_state,
            "run_work_folder_gate": facade.run_work_folder_gate,
            "_is_advisory_work_folder_gate_failure": facade._is_advisory_work_folder_gate_failure,
            "_has_transient_artifact_marker": facade._has_transient_artifact_marker,
            "_is_dirty_worktree_branch_gate_message": facade._is_dirty_worktree_branch_gate_message,
            "_payload_has_only_transient_artifact_errors": facade._payload_has_only_transient_artifact_errors,
            "_is_transient_work_folder_gate_failure": facade._is_transient_work_folder_gate_failure,
            "verify_skill_effectiveness": facade.verify_skill_effectiveness,
            "verify_audit_skill_report": facade.verify_audit_skill_report,
            "run_audit_skill": facade.run_audit_skill,
            "parse_json_object": facade.parse_json_object,
        }
    )

# 源码审计入口绑定 helper 保留技能正文与依赖边界检查。
def _bind_source_audit_exports(
    module_globals: dict[str, Any],
    facade: ValidationGateFacade,
) -> None:
    """把源码审计兼容入口回填到调用模块命名空间。

    :param module_globals: 调用方模块的 globals 字典。
    :param facade: 已绑定 loader 与上下文 builder 的门禁 facade。
    :return: 不返回业务值；导出入口原地写入 module_globals。
    """

    # 六个入口共同依赖同一 source-audit 上下文 builder。
    module_globals.update(
        {
            "verify_markdown_ascii": facade.verify_markdown_ascii,
            "verify_skill_standards": facade.verify_skill_standards,
            "verify_legacy_terms": facade.verify_legacy_terms,
            "verify_dependency_schema": facade.verify_dependency_schema,
            "verify_hardcoded_paths": facade.verify_hardcoded_paths,
            "verify_no_ref_dependencies": facade.verify_no_ref_dependencies,
        }
    )

# 工作区入口绑定 helper 保留清理、路径和残留检查兼容面。
def _bind_workspace_exports(
    module_globals: dict[str, Any],
    facade: ValidationGateFacade,
) -> None:
    """把工作区兼容入口回填到调用模块命名空间。

    :param module_globals: 调用方模块的 globals 字典。
    :param facade: 已绑定 loader 与上下文 builder 的门禁 facade。
    :return: 不返回业务值；导出入口原地写入 module_globals。
    """

    # 清理与路径入口必须共享同一个 workspace gate 上下文。
    module_globals.update(
        {
            "verify_no_residuals": facade.verify_no_residuals,
            "cleanup_residuals": facade.cleanup_residuals,
            "cleanup_audit_retry_local_artifacts": facade.cleanup_audit_retry_local_artifacts,
            "cleanup_audit_runtime_artifacts": facade.cleanup_audit_runtime_artifacts,
            "_prune_empty_smoke_root": facade._prune_empty_smoke_root,
            "remove_inside_skill": facade.remove_inside_skill,
            "remove_inside_smoke_root": facade.remove_inside_smoke_root,
            "_remove_tree_with_retry": facade._remove_tree_with_retry,
            "iter_skill_files": facade.iter_skill_files,
            "_project_relative": facade._project_relative,
            "project_artifact_path": facade.project_artifact_path,
        }
    )

# bind_validation_gate_exports 把公开兼容门禁函数回填到 validate facade 模块命名空间。
def bind_validation_gate_exports(
    module_globals: dict[str, Any],
    *,
    func_load_workspace_gate_module: FacadeLoader, func_load_source_audit_module: FacadeLoader,
    func_load_governance_gate_module: FacadeLoader, func_build_workspace_gate_context: ContextBuilder,
    func_build_source_audit_context: ContextBuilder, func_build_governance_gate_context: ContextBuilder,
) -> ValidationGateFacade:
    """把公开兼容门禁函数绑定到 validate_verilog_skill 模块。

    :param module_globals: 调用方模块的 `globals()` 字典，用于回填兼容函数导出面。
    :param func_load_workspace_gate_module: 工作区 helper 模块 loader。
    :param func_load_source_audit_module: 源码审计 helper 模块 loader。
    :param func_load_governance_gate_module: 治理 helper 模块 loader。
    :param func_build_workspace_gate_context: 工作区 gate 上下文 builder。
    :param func_build_source_audit_context: 源码审计上下文 builder。
    :param func_build_governance_gate_context: 治理 gate 上下文 builder。
    :return: 返回 facade 对象；供调用方在调试或扩展场景保留绑定句柄。
    """

    # 先构造统一的兼容 facade 对象，后续公开入口共享同一组 loader 与上下文 builder。
    validation_gate_facade_binding: ValidationGateFacade = ValidationGateFacade(  # 当前兼容门禁 facade 对象
        func_load_workspace_gate_module=func_load_workspace_gate_module,  # 绑定工作区 helper 延迟导入回调
        func_load_source_audit_module=func_load_source_audit_module,  # 绑定源码审计 helper 导入回调
        func_load_governance_gate_module=func_load_governance_gate_module,  # 绑定治理 helper 导入回调
        func_build_workspace_gate_context=func_build_workspace_gate_context,  # 绑定工作区 gate 上下文 builder
        func_build_source_audit_context=func_build_source_audit_context,  # 绑定源码审计上下文 builder
        func_build_governance_gate_context=func_build_governance_gate_context,  # 绑定治理 gate 上下文 builder
    )

    # 三个低复杂度 helper 分别绑定治理、源码审计和工作区入口。
    _bind_governance_exports(module_globals, validation_gate_facade_binding)

    # 源码审计入口复用同一 facade 中的 source-audit loader。
    _bind_source_audit_exports(module_globals, validation_gate_facade_binding)

    # 工作区入口复用同一 facade 中的 workspace loader。
    _bind_workspace_exports(module_globals, validation_gate_facade_binding)

    # 最后把 facade 对象返回给调用方，便于调试或后续扩展保留句柄。
    return validation_gate_facade_binding
