"""读取 readable Verilog 风格规则源。"""

# 延迟求值避免 dataclass 字段类型在导入期绑定过早。
from __future__ import annotations

# JSON 解析和路径对象负责把人工规则文件接入运行时门禁。
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

# validation 文件位于 skill/scripts/python/validation，向上三级回到 skill 包根。
PATH_SKILL_ROOT = Path(__file__).resolve().parents[3]  # skill 包根目录

# 默认 JSON 规则文件是 VG059 漂移检查的基准输入。
PATH_DEFAULT_RULES = PATH_SKILL_ROOT / "assets" / "verilog_style_rules.json"  # 内置风格规则文件

# 区域 key 不能散落在质量门里，统一在这里绑定到源码横幅标题。
DICT_REGION_LABELS = {  # 区域横幅查表字典，支撑规则源顺序和源码归属一致性检查
    "function_block": "函数区域",  # function 声明代码段横幅
    "task_block": "任务区域",  # task 过程块声明横幅
    "config_param": "配置参数区域",  # module 参数和普通 localparam 区域
    "state_param": "状态参数区域",  # FSM 状态 localparam 区域
    "instance_signal": "模块实例化信号",  # 子模块连接信号区域
    "counter_signal": "计数信号",  # 计数器信号区域
    "state_signal": "状态机信号",  # 状态机当前态和下一态信号区域
    "register_signal": "寄存器信号",  # 普通寄存器信号区域
    "flag_signal": "标志信号",  # valid/ready/done 等标志信号区域
    "encoder_signal": "编码信号",  # 编码中间信号区域
    "decoder_signal": "译码信号",  # 译码中间信号区域
    "other_signal": "其他信号",  # 其他内部信号区域
    "output_internal": "输出信号",  # 输出桥接内部信号区域
    "other_assign": "其他信号连线",  # 普通 assign 连线区域
    "output_assign": "输出信号连线",  # 输出端口桥接 assign 区域
    "output_always": "输出信号处理区域",  # 输出寄存处理 always 区域
    "state_machine": "状态机区域",  # FSM next-state 或状态寄存器区域
    "state_task": "状态任务处理区域",  # FSM 状态输出任务区域
    "main_task": "主要任务处理区域",  # 主业务逻辑区域
    "generate_block": "生成块区域",  # 生成语句块区域横幅
    "parameter_check": "参数检查区域",  # 参数合法性检查区域
    "initial_block": "初始化区域",  # 仿真初始化语句横幅
    "instance_block": "模块实例化区域",  # 子模块例化语句横幅
}

# 规则对象只暴露质量门实际需要的稳定字段。
@dataclass(frozen=True)
class VerilogRulebook:
    """表示从 JSON 规则源读取出的 Verilog 风格规则。"""

    # path 让 VG059 报告能指向实际读取的规则文件。
    path: Path  # 本次读取的 JSON 规则源文件

    # raw 保留尚未被当前质量门消费的扩展字段。
    raw: dict[str, Any]  # 未裁剪的规则源内容

    # region_keys 用来检测 JSON 区域顺序是否发生漂移。
    region_keys: tuple[str, ...]  # JSON 内声明的区域 key 顺序

    # region_labels 是源码横幅扫描时直接匹配的标题序列。
    region_labels: tuple[str, ...]  # 质量门用于匹配源码横幅的中文标题顺序

    # fallback_comments 支撑 VG056 拦截 formatter 残留占位注释。
    fallback_comments: tuple[str, ...]  # 最终 RTL 中禁止残留的 formatter 兜底注释

    # reset_polarity 让 reset 深语义规则和命名规则共享同一约定。
    reset_polarity: str  # 命名规则里声明的复位极性约定

# 公开入口带缓存，避免每个文件重复解析同一个 JSON 规则源。
@lru_cache(maxsize=8)

# 加载函数把文件结构问题转换成可由质量门报告的异常。
def load_verilog_rulebook(path_rules: Path | None = None) -> VerilogRulebook:
    """读取 Verilog 风格规则源。

    参数:
        path_rules: 可选规则源路径；为空时使用 skill 内置 assets 规则。
    返回:
        返回可被质量门复用的不可变规则对象。
    异常:
        FileNotFoundError: 规则源文件不存在时抛出。
        ValueError: 规则源结构缺少必要字段时抛出。
    """

    # 规则路径先规范化，确保 lru_cache 键稳定。
    path_effective = (path_rules or PATH_DEFAULT_RULES).resolve()  # 实际读取的规则源路径

    # 缺少规则源时阻断质量门，避免退回硬编码枚举。
    if not path_effective.is_file():

        # 错误文本使用项目统一前缀，便于 CLI 直接展示。
        raise FileNotFoundError(f"> ERR: [Python] Verilog style rulebook is missing: {path_effective}")

    # 读取 JSON 文本并解析为普通字典。
    dict_raw = json.loads(path_effective.read_text(encoding="utf-8"))  # 规则源 JSON 内容

    # regions 字段是区域顺序的唯一机器源。
    list_region_keys = list(dict_raw.get("regions") or [])  # JSON 中的区域枚举顺序

    # 缺少区域枚举会让区域归属检查失去依据。
    if not list_region_keys:

        # 直接阻断，避免质量门静默放弃区域检查。
        raise ValueError("> ERR: [Python] Verilog style rulebook must define non-empty regions.")

    # 所有 region key 都必须有中文横幅映射。
    list_missing_labels = [str_key for str_key in list_region_keys if str_key not in DICT_REGION_LABELS]  # 缺失中文横幅映射的区域 key

    # 映射缺失说明规则源和代码已经漂移。
    if list_missing_labels:

        # 错误信息列出缺口，便于维护者补齐映射。
        raise ValueError(
            "> ERR: [Python] Verilog region label mapping is missing: " + ", ".join(list_missing_labels)
        )

    # comments 分区承载 formatter 兜底注释清单。
    dict_comments = dict_raw.get("comments") or {}  # 注释规则配置字典

    # 兜底清单为空会让 VG056 失去判定依据。
    tuple_fallback_comments = tuple(str(item) for item in dict_comments.get("fallback_comments") or ())  # 禁止交付的 fallback 注释

    # naming 分区记录复位极性，后续 reset 语义规则按它解释命名。
    dict_naming = dict_raw.get("naming") or {}  # 命名规则配置字典

    # 复位极性缺失时保持空字符串，让质量门按保守方式处理。
    str_reset_polarity = str(dict_naming.get("reset_polarity") or "")  # 项目复位极性

    # 构造不可变规则对象。
    return VerilogRulebook(
        path=path_effective,
        raw=dict_raw,
        region_keys=tuple(str(item) for item in list_region_keys),
        region_labels=tuple(DICT_REGION_LABELS[str(item)] for item in list_region_keys),
        fallback_comments=tuple_fallback_comments,
        reset_polarity=str_reset_polarity,
    )
