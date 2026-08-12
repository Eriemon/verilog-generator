# Verilog 代码规范、注释规范与命名规范详细说明

> 适用范围：本规范依据 `verilog-formatter.zip` 的源码、配置、参考文档与用户提供的两份历史约束附件交叉整理。本文档给出“项目生成/交付 RTL 应遵守的最终规范”，并单独标注 formatter 当前实际自动化能力与实现差异，避免把“工具自动能修”误认为“规范允许”。
>
> 规范用语：**必须** 表示交付代码硬约束；**建议** 表示强推荐但特殊场景可说明原因；**formatter 自动** 表示当前工具可识别/改写；**人工/生成器负责** 表示项目规范存在但 formatter 不一定自动完整识别。

## 当前门禁对齐状态

以下对齐说明以 `scripts/python/quality/quality_gate.py`、`assets/verilog_style_rules.json`、`tests/validation/test_deliverable_gate.py` 为真源；当本文档正文与运行时实现冲突时，以这些真源为准。

- `VG001`、`VG003`、`VG004`、`VG005`、`VG025`：覆盖 ``timescale 1ns / 1ps``、行尾空白、Tab 缩进、文件末尾换行、控制语句显式 `begin/end`。
- `VG010`、`VG011`、`VG012`、`VG013`、`VG014`、`VG024`：覆盖端口方向前缀、ANSI header 禁止 `wire/reg/logic` 与 `output reg`、`C_`/`ST_`/内部语义前缀、内部输出 `_o` 约束、输出桥接存在性、实例命名语义。
- `VG021`、`VG023`、`VG053`、`VG054`、`VG057`：覆盖时钟/复位命名与低有效复位结构、FSM next-state 的 default、默认保持、`if / else if` 显式闭合，以及协议端口 section 顺序。
- `VG144`：FSM 必须严格使用三个独立过程；下一状态必须在组合过程内用阻塞赋值 `=` 计算，严禁 `assign state_next = ...`。
- `VG145`：连续赋值与组合 `always` 共用完整 module 局部依赖锥，展开后的运行时来源最多三个；搬入组合 `always` 不能规避。只有输出端口直接连接时序驱动 `_o` `reg` 的单信号 bridge 可豁免。
- `VG146`/`VG147`：每个静态目标的完整组合操作锥最多使用目录配置的操作数；普通逻辑由 VG146 负责，包含 `for` 展开克隆操作的目标由 VG147 负责。长表达式直接搬入时序 D 输入仍会检查，优先用流水寄存、注册预译码或多周期 FSM 降低组合链路。
- `VG031`、`VG052`、`VG061`：覆盖固定区域 banner、output bridge 所属区域、参数/声明/过程块/实例的区域归属。
- `VG040`、`VG041`、`VG055`、`VG056`、`VG060`、`VG066`：覆盖中文优先注释、占位注释、同线注释覆盖、尾随注释对齐、重复/近似重复注释。
- `VG063`：覆盖过程块、实例以及 `case(state_current)` 下 `ST_*:begin`、`default:begin` 的前导纯注释贴邻、左对齐和空行布局。
- `VG067`：覆盖 assign 纯分组注释的空行布局；写了分组注释就必须满足“上一段代码后恰好一行空行或区域横幅直连”。
- `JSON/配置已覆盖`：`assets/verilog_style_rules.json` 中的 `profiles`、`files`、`comments`、`statements`、`protocols`、`known_differences` 已是现行机器真源；本文档只做解释，不再重复声明为独立 `VGxxx`。
- `说明性规则`：注释增强双 `verify_comment_only` 流水线、版本号升级时机、人工评审输出要求、profile 选型建议、历史示例和最终检查清单仍是流程/治理规则，不是单个 `VGxxx`。

## 目录

- [1. 总体原则](#1-总体原则)
- [2. 文件、目录、运行 profile 规范](#2-文件目录运行-profile-规范)
- [3. 文件头、版本与历史记录规范](#3-文件头版本与历史记录规范)
- [4. 基础格式规范](#4-基础格式规范)
- [5. Module header 与端口区规范](#5-module-header-与端口区规范)
- [6. 命名规范](#6-命名规范)
- [7. 模块内部区域顺序规范](#7-模块内部区域顺序规范)
- [8. 参数与信号声明规范](#8-参数与信号声明规范)
- [9. assign 与输出连线规范](#9-assign-与输出连线规范)
- [10. always 块规范](#10-always-块规范)
- [11. 状态机规范](#11-状态机规范)
- [12. if/case/loop/generate/function/task/initial 规范](#12-ifcaseloopgeneratefunctiontaskinitial-规范)
- [13. 注释规范](#13-注释规范)
- [14. Preprocessor 与 include 规范](#14-preprocessor-与-include-规范)
- [15. 安全、限制与人工评审规则](#15-安全限制与人工评审规则)
- [16. 交付模板骨架](#16-交付模板骨架)
- [17. Formatter 当前实现差异与注意点](#17-formatter-当前实现差异与注意点)
- [18. 最终交付检查清单](#18-最终交付检查清单)
- [19. 推荐命令](#19-推荐命令)

---

## 1. 总体原则

1. **代码风格统一为 Erie/VerilogFormatter 风格。** 默认输出包含标准双语文件头、ANSI-style module header、Tab 缩进、固定区域 banner、尾随注释对齐和严格命名。
2. **默认面向单个可综合 RTL 模块。** 普通 normalize 路径按单模块处理；多模块、vendor/IP、复杂 generate/preprocessor、复杂 lvalue、难拆分 always 等复杂输入，应先人工审查。
3. **安全优先。** formatter 使用确定性 profile：保守保留、强制 normalize 或只检查；禁止在复杂场景下直接猜测改写。
4. **注释不能改变 RTL token。** 添加/优化注释必须走 `format baseline -> comment draft -> verify -> format final -> verify` 流水线，只允许 `//` 注释变化。
5. **历史约束优先成为项目交付规范。** 当历史约束比 formatter 自动规则更严格时，交付代码按历史约束执行；formatter 未完全覆盖的部分由生成器/人工评审补足。

---

## 2. 文件、目录、运行 profile 规范

### 2.1 文件类型与扫描范围

- 默认识别扩展名：`.v`。
- 默认递归扫描；默认排除：`.git`、`__pycache__`、`.pytest_cache`、`dist`、`out`、`ref`、`tests/verilog-formatter/baselines`。
- `.vh` include fragment 默认不结构化格式化：`format_include_fragments = false`。include 片段中宏、条件编译、局部 declarations 不应被当作完整模块强行 normalize。
- 文本 I/O 默认保留 encoding、EOL、BOM、final newline 策略；微格式化内部会使用 LF 进行处理并确保最终换行，交付时应按项目仓库行尾策略统一。

### 2.2 Profile 使用规范

| Profile | 用途 | 改写策略 | 关键行为 |
|---|---|---|---|
| `formatter-preserve` | 默认保守入口 | `preserve` | 不排序 ports/params，不自动修 prefix，不重排区域，不拆 always，不改 inline wire assign。 |
| `formatter-normalize` | 强制结构化 | `normalize` | 强制渲染标准模板，可能重命名、重排区域、拆 always、补 output bridge、改 header；仅用于确认可控代码或生成器输出。 |
| `formatter-lint` | 只检查 | `never` | dry-run/lint；不写出格式化结果，用于 CI 或人工审查。 |
| `vitis-wrapper` | AMD/Vitis ABI wrapper 专用 | format | 保留 `ap_clk`、`ap_rst_n`、`interrupt`、`s_axi_control_*`、`m_axi_*_*` 顶层 ABI 端口名；仅在明确是 Vitis wrapper 时使用。 |

### 2.3 Rewrite 行为

- `formatter-preserve` 保留原文，不做结构化重排或自动写回。
- `formatter-normalize` 才允许结构化渲染，且必须配合 review/quality gate。
- `formatter-lint` 用于只检查流程，不产生格式化输出。
- 结构化改写包括：端口排序、参数排序、信号重命名、区域重排、always 拆分、inline wire assign 拆分、header 重建、output bridge、reset/header 综合等。结构化改写只能在 normalize 模式下发生。
- 硬门禁失败必须停止，不应写文件。常见硬门禁：无法识别 module、preprocessor/module 名丢失风险、strict mode 下缺失时钟/复位、unsupported syntax、FSM 不满足强约束、实例化块不完整等。

---

## 3. 文件头、版本与历史记录规范

### 3.1 标准文件头

交付文件必须使用标准双语文件头；标准 preamble 包含 ``timescale 1ns / 1ps`。formatter 当前会重建如下字段：

- 英文段：`Company`、`Engineer`、`Create Date`、`Design Name`、`Module Name`、`Description`、`Simulations`、`Referrences`、`Dependencies`、`Version`、`Revision Date`、`History`。
- 中文段：`版权归属`、`开发人员`、`创建日期`、`设计名称`、`模块名称`、`模块说明`、`仿真工程`、`参考资料`、`依赖文件`、`当前版本`、`修订日期`、`修订历史`。
- 默认身份字段：`Erie`。
- 默认版本：`V1.0`。
- 英文默认说明路径：`description/<module>_Design.pdf`。
- 英文默认仿真路径：`testbench/vivado/2021.1/<module>`。
- 中文默认说明路径：`Description/<module>_Design.pdf`。
- 中文默认仿真路径：`TestBench/Vivado/2021.1/<module>`。
- formatter 当前英文 header 中固定使用拼写 `Referrences`。为了与工具输出一致，不要在被 formatter 接管的文件中手动改成 `References`。

### 3.2 版本号规范

- 版本号形式为 `Vx.y`。
- **不得自动升级版本号。** 默认配置 `allow_auto_version_bump = false`，历史附件也要求：只有用户明确要求升级版本时才修改版本号。
- 小版本升级用于局部修正/兼容性变更；大版本升级用于接口、行为或架构重大变化。具体是否升级由用户/项目负责人确认。
- 修改既有文件时，应保留已有历史记录；不得因为格式化重建 header 而丢失有价值的版本历史。
- 新建文件可使用 `V1.0` 和 “Create file/创建文件” 初始记录。

### 3.3 Header 与普通注释的边界

- 模块名、功能摘要、设计说明路径、仿真路径等属于 header 字段，不要在 `module` 前另写零散说明注释。
- 注释增强流水线中，文件开头/模块前的自由注释不算有效 RTL 注释增量，因为 header 可能被重建并丢弃。
- 普通 RTL 注释必须贴近 port、parameter、signal、assign、always、generate、function/task 等结构。

---

## 4. 基础格式规范

### 4.1 缩进、空白和换行

- 缩进必须使用 **Tab**；每一层 block 缩进一个 Tab。
- 行尾禁止多余空格。
- 文件末尾必须有换行。
- 连续空行最多保留两行；一般区域之间空一行。
- 运算符两侧加空格：`a + b`、`x == 1'b0`、`C_WIDTH - 1`。
- comma 后加空格；端口/参数渲染中通常一行一个条目。
- packed range 内冒号两侧不额外加空格：`[C_WIDTH - 1:0]`。

### 4.2 关键字/括号风格（formatter 当前实现）

formatter 结构渲染采用紧凑 RTL 样式：

```verilog
always@(posedge i_clk or negedge i_rstn)begin
    if(i_rstn == 1'b0)begin
        reg_data <= 0;
    end else begin
        reg_data <= reg_data + 1'b1;
    end
end
```

- `always` 与 `@` 之间无空格：`always@(...)begin`。
- `if`/`case`/`for`/`while` 与 `(` 之间无空格：`if(...)begin`、`case(...)`、`for(...)begin`。
- `end else begin` 写在同一行。
- `case` item 使用 `LABEL:begin`，再以 `end` 结束该 item。
- 实例化无参数时：`submodule inst_name(`。
- 实例化带参数时：

```verilog
submodule
#(
    .PARAM_A(VALUE_A),
    .PARAM_B(VALUE_B)
)inst_name(
    .i_clk(i_clk),
    .i_rstn(i_rstn)
);
```

注意：formatter 当前实现中 `)inst_name(` 之间没有空格。若要完全匹配工具输出，应保持该风格。

### 4.3 位宽与声明风格

formatter 当前渲染声明时，packed width 与标识符之间可能无空格，例如：

```verilog
output [C_WIDTH - 1:0]o_data                // port signal
reg [C_WIDTH - 1:0]data_o = 0;              // signal
```

这是当前实现事实，不是 Verilog 语义问题。项目交付如果由该 formatter 接管，应按工具输出为准；若未来希望改成 `output [C_WIDTH - 1:0] o_data`，需要同步修改 formatter 渲染逻辑并更新规范。

---

## 5. Module header 与端口区规范

### 5.1 Module 声明形式

- 必须使用 ANSI-style module header。
- `module` 名单独成行；有参数时 `#(` 单独一行；端口区 `(` 单独一行；最后 `);` 单独一行。
- 参数一行一个，尾随注释对齐。
- 端口一行一个，端口区必须按方向、协议/功能分组排序。
- 端口区不得保留 `wire`/`reg`/`logic` 类型关键字作为交付目标风格。formatter 能解析 legacy `input wire`、`output reg`，但标准输出应只保留 direction、signed/width、name。

推荐结构：

```verilog
module module_name
#(
    parameter C_DATA_WIDTH = 32             // parameter
)
(
    //---------------全局信号---------------//
    input i_clk,                            // 时钟信号
    input i_rstn,                           // 复位信号,低电平复位

    //---------------用户接口---------------//
    input i_start,                          // 起始信号
    output o_done                           // 结束信号
);
```

### 5.2 端口排序

默认端口方向排序：`input` -> `output` -> `inout`。在每个方向内，再按协议/功能分组。

通用端口分区标题包括：

- `全局信号`：clock/reset。
- `AXI接口`、`AXIS接口`、`APB接口`、`Wishbone接口`。
- `UART接口`、`SPI接口`、`I2C接口`、`GMII接口`、`RGMII接口`。
- `用户接口` 或其他由信号族推断出的接口组。

### 5.3 协议端口分组顺序

#### AXI

- section 顺序：`clock_reset` -> `aw` -> `w` -> `b` -> `ar` -> `r` -> `other`。
- AW/AR member 顺序：`addr`、`prot`、`len`、`size`、`burst`、`lock`、`cache`、`qos`、`region`、`user`、`valid`、`ready`。
- W member 顺序：`data`、`strb`、`last`、`user`、`valid`、`ready`。
- B member 顺序：`resp`、`user`、`valid`、`ready`。
- R member 顺序：`data`、`resp`、`last`、`user`、`valid`、`ready`。

#### AXIS

- 当前状态：`VG057` 读取 `assets/verilog_style_rules.json.protocols.axis_sections`，并以现有回归测试允许 `slave/master` 端点成组顺序。
- section 顺序：`clock_reset` -> `slave` -> `master` -> `control` -> `data` -> `other`。
- data member：`data`、`keep`、`strb`、`user`、`id`、`dest`。
- control member：`valid`、`ready`、`last`。

#### APB

- section 顺序：`clock_reset` -> `request` -> `response`。
- request：`paddr`、`pprot`、`psel`、`penable`、`pwrite`、`pstrb`、`pwdata`。
- response：`prdata`、`pready`、`pslverr`。

#### Wishbone

- request：`adr`、`dat_w`、`we`、`sel`、`cyc`、`stb`、`cti`、`bte`、`lock`。
- response：`dat_r`、`ack`、`stall`、`err`、`rty`。

#### UART/SPI/I2C/GMII/RGMII

- UART 按 `rx`、`tx`、`baud`、`status/control` 类分组。
- SPI 按 `sclk`、`mosi`、`miso`、`cs/ss`、`mode/status` 类分组。
- I2C 按 `scl`、`sda`、`control/status` 类分组；双向 SDA/SCL 注意 inout 方向和 `io_` 前缀。
- GMII/RGMII 按 RX、TX、clock/control/status 类分组。

### 5.4 Vitis wrapper 特例

- 只有在明确使用 `vitis-wrapper` profile 或用户明确说明是 Vitis/AMD ABI wrapper 时，才保留 Vitis ABI 顶层端口名。
- 保留端口：`ap_clk`、`ap_rst_n`、`interrupt`、`s_axi_control_*`、`m_axi_*_*`。
- wrapper 内部普通 RTL 仍按 Erie 命名和区域规则执行。

---

## 6. 命名规范

### 6.1 总体命名原则

- 标识符使用小写英文、数字和下划线；parameter/localparam/state parameter 使用大写英文、数字和下划线。
- 前缀/后缀必须表达方向、类型或角色。
- 禁止重复前缀：`i_i_data`、`C_C_DATA_WIDTH`、`ST_ST_IDLE`、`reg_reg_data`、`cnt_cnt_pixel`、`flag_flag_done`、`enc_enc_addr`、`dec_dec_sel`、`state_state_current` 等必须去重。
- legacy 端口尾缀 `_i`/`_o` 会被剥离后转换为标准方向前缀。例如 `start_i` -> `i_start`，`done_o` -> `o_done` 或内部 `done_o`。
- 不要依赖缩写造成歧义；保持语义可读。

### 6.2 端口命名

| 方向 | 必须格式 | 示例 |
|---|---|---|
| input | `i_<name>` | `i_start`、`i_data_valid` |
| output | `o_<name>` | `o_done`、`o_data` |
| inout | `io_<name>` | `io_i2c_sda` |

- clock/reset 作为特殊 input 处理。
- 顶层 output 端口必须使用 `o_` 前缀。
- 内部 output 代理信号不得使用 `o_` 前缀；必须使用 `_o` 后缀，见 6.8。

### 6.3 时钟与复位命名

默认：

- 主时钟：`i_clk`。
- 主复位：`i_rstn`。
- 复位必须为低有效；复位敏感列表使用 `negedge i_rstn`。

协议场景建议：

| 场景 | 时钟 | 复位 |
|---|---|---|
| 通用模块 | `i_clk` | `i_rstn` |
| AXI | `i_axi_aclk` | `i_axi_arstn` |
| AXIS | `i_axis_aclk` | `i_axis_arstn` |
| AHB | `i_ahb_hclk` | `i_ahb_hrstn` |
| APB | `i_apb_pclk` | `i_apb_prstn` |

formatter 当前自动把常见 `clk/clock` 归一为 `i_clk`，把常见低有效 reset alias（`rstn`、`rst_n`、`resetn`、`reset_n`、`aresetn`、`areset_n`、`arstn`、`arst_n`）归一为 `i_rstn`。协议专用时钟/复位名更多属于项目/生成器约束，不能完全依赖 formatter 自动推断。

### 6.4 Parameter/localparam/state parameter 命名

| 类型 | 格式 | 示例 | 说明 |
|---|---|---|---|
| module parameter | `C_<UPPER_NAME>` | `C_DATA_WIDTH` | formatter 会给 header parameter 加 `C_` 并大写。 |
| localparam 普通参数 | `<UPPER_NAME>` | `ADDR_WIDTH` | 非 state localparam 默认大写，不强加 `C_`。 |
| state parameter | `ST_<UPPER_NAME>` | `ST_IDLE` | FSM state 编码必须 `ST_`。 |

- 参数表达式要显式、可综合，必要时用 parameter 化位宽。
- 用户历史约束要求：根据 C model/range 推导位宽并参数化。formatter 本身不会读取 C model，生成器必须在生成 RTL 时完成位宽推导。
- 已有 `C_`、`ST_` 前缀不得重复。
- 交付门禁使用 `VG012` 阻断 `C_C_`、`ST_ST_` 这类参数前缀重复；module `parameter` 缺少 `C_` 会失败，普通非 state `localparam` 使用 `C_` 也会失败，state localparam 则继续要求 `ST_`。

### 6.5 内部寄存器、计数器、状态、编码译码、标志

| 类别 | 格式 | 示例 | 识别/要求 |
|---|---|---|---|
| 普通寄存器 | `reg_<name>` | `reg_data` | `reg`/`logic` 类内部变量应加 `reg_`；formatter 会把 `logic` 渲染为 `reg`。 |
| 计数器 | `cnt_<name>` | `cnt_pixel` | 含 `cnt/counter/count` 语义的信号必须使用 `cnt_`。 |
| 状态机信号 | `state_<name>` | `state_current`、`state_next` | 当前/下一状态统一使用 `state_current`/`state_next`。 |
| 编码信号 | `enc_<name>` | `enc_symbol` | encoder/encode/enc 语义。 |
| 译码信号 | `dec_<name>` | `dec_opcode` | decoder/decode/dec 语义。 |
| 标志信号 | `flag_<name>` | `flag_ready`、`flag_done` | flag/flg/end/req/ack/done/valid 类控制标志按项目规范使用 `flag_`。 |

formatter 当前自动后缀识别最稳定的是 `_counter/_count/_cnt`、`_flag/_flg`、`_encode/_enc`、`_decode/_dec`。历史约束中 `end/req/ack` 也应归为 `flag_`，但 formatter 不一定自动覆盖所有语义词，生成器/人工必须主动命名。

交付门禁使用 `VG013` 阻断 `reg_reg_`、`cnt_cnt_`、`flag_flag_`、`enc_enc_`、`dec_dec_`、`state_state_` 等重复语义前缀；输出端口误用内部 `_o` 后缀仍按端口命名规则处理，内部输出逻辑信号则必须使用 `_o` 后缀而不是 `o_` 前缀。

### 6.6 State 命名

- state 参数必须 `ST_` 前缀并大写：`ST_IDLE`、`ST_RUN`、`ST_DONE`。
- state 信号必须 `state_` 前缀；推荐固定名：`state_current`、`state_next`。
- 不建议使用 `cur_state`、`next_state` 作为最终交付名；formatter 会倾向映射到 `state_current`/`state_next`。
- 状态机相关任务/输出处理应进入 `状态机区域` 与 `状态任务处理区域`，不要散落在主要任务区域。

### 6.7 内部输出信号与 output bridge

这是历史约束中的关键规则：

- 当前状态：顶层 `output reg` 禁用、内部 `_o` 命名、bridge 存在性与 bridge 区域归属分别由 `VG011`、`VG013`、`VG014`、`VG052` 覆盖。

- 顶层输出端口：必须 `o_<name>`。
- 与顶层输出对应的内部驱动信号：必须 `<name>_o`，不得使用 `o_` 前缀。
- 输出端口不得在主要逻辑里直接使用 `o_` 作为寄存器目标；应通过 output bridge：

```verilog
reg done_o = 0;                              // internal output signal
assign o_done = done_o;                      // output bridge
```

- 如果 output 是纯组合直连，且 formatter 判断它是 direct output assign，可直接放在输出连线区域，不一定生成内部 `_o` 代理。
- 对时序输出、复杂输出或 always 中赋值的 output，必须使用内部 `_o` 信号并 bridge 到 `o_` 端口。

### 6.8 实例化命名

- 历史模板要求实例名采用“被实例化模块英文名 + `_Inst_` + 实例语义名”的形式，例如：`uart_rx_Inst_core`。
- 当前状态：`VG024` 已阻断 `u0/u1/inst` 一类无语义实例名；`_Inst_` 形态目前仍是 warning/说明性约束，不是强制自动重命名。
- 项目交付应使用清晰英文实例名，避免 `u0/u1/tmp/inst` 等无语义名称，除非是局部 generate 自动实例并有清晰标签。

---

## 7. 模块内部区域顺序规范

### 7.1 交付代码区域顺序

formatter 默认启用 `enforce_region_order = true`。综合 `defaults.json`、源码 banner 与历史模板，模块内部应按以下顺序组织；当存在 `参数检查区域` 时，它必须成为模块内部最后一区并紧贴 `endmodule` 之前：

1. `函数定义区域`（function；若存在，formatter 会放在普通区域前）
2. `任务定义区域`（task；若存在，formatter 会放在普通区域前）
3. `配置参数区域`
4. `状态参数区域`
5. `模块实例化信号`
6. `计数信号`
7. `状态机信号`
8. `寄存器信号`
9. `标志信号`
10. `编码信号`
11. `译码信号`
12. `其他信号`
13. `输出信号`
14. `其他信号连线`
15. `输出信号连线`
16. `输出信号处理区域`
17. `状态机区域`
18. `状态任务处理区域`
19. `主要任务处理区域`
20. `生成块区域`
21. `初始化区域`
22. `模块实例化区域`
23. `参数检查区域`

历史附件列出的 18 个核心区域是交付模板主干；formatter 额外支持 function、task、generate、parameter_check、initial 等区域。若这些结构存在，必须按上述扩展顺序放置。`参数检查区域` 只在存在可证明的参数约束时出现，禁止空壳检查区。

### 7.2 区域 banner 格式

区域使用固定注释 banner：

```verilog
    //-------------配置参数区域-------------//
    //-------------状态参数区域-------------//
    //------------模块实例化信号------------//
    //---------------计数信号---------------//
    //--------------状态机信号--------------//
    //--------------寄存器信号--------------//
    //---------------标志信号---------------//
    //---------------编码信号---------------//
    //---------------译码信号---------------//
    //---------------其他信号---------------//
    //---------------输出信号---------------//
    //-------------其他信号连线-------------//
    //-------------输出信号连线-------------//
    //-----------输出信号处理区域-----------//
    //--------------状态机区域--------------//
    //-----------状态任务处理区域-----------//
    //-----------主要任务处理区域-----------//
	//--------------生成块区域--------------//
	//--------------初始化区域--------------//
    //------------模块实例化区域------------//
	//-------------参数检查区域-------------//
```

实际横线数量由 formatter 根据中文显示宽度自动生成，上例用于说明形式。不要手写风格不同的 banner。

### 7.3 区域内排序与归类

- 参数区：module parameter 与部分 top-level localparam 会被合并/排序；state parameter 单独进入状态参数区。
- 信号区：按命名/用途归类。`cnt_` 进入计数信号，`state_` 进入状态机信号，`reg_` 进入寄存器信号，`flag_` 进入标志信号，`enc_`/`dec_` 分别进入编码/译码信号，`*_o` 进入输出信号，其余进入其他信号。
- 连线区：非 output bridge 进入其他信号连线，output port 连接进入输出信号连线。
- always 区：输出内部信号目标进入输出信号处理区域，状态寄存器/next-state 进入状态机区域，引用 state 的任务处理进入状态任务处理区域，其他进入主要任务处理区域。
- 常规情况下实例化位于模块尾部；若存在 `参数检查区域`，则实例化区域位于其前，`参数检查区域` 作为最终内部区域收尾。

### 7.4 参数检查与运行时消息合同

- `参数检查区域` 统一使用 `initial begin ... end` 承载参数合法性检查，并放在 `endmodule` 前的最后一个模块内部区域。
- 仅当规格或已验证约束能给出明确检查条件时才生成 `参数检查区域`；禁止为了模板完整性输出空壳检查块。
- 使用 `$display` 输出人类可读参数检查信息时，前缀必须是 ` > INFO: [Verilog]`、` > WARNING: [Verilog]` 或 ` > ERR: [Verilog]`。
- 机器可读 transcript（如 `VERILOG-GEN-RESULT ...`）不受上述人类可读前缀限制。

---

## 8. 参数与信号声明规范

### 8.1 参数声明

- `parameter` 放 module header；`localparam` 放模块内部参数区。
- 一行一个 parameter/localparam；必须有对齐注释。
- module parameter 名使用 `C_` + 大写；state parameter 使用 `ST_`。
- 参数族可用小分组注释说明，例如 `// DDR参数`、`// MVM参数` 等。
- 已知配置参数族包括：`BASE_ADDR_`、`LUT_THETA_`、`QGEN_`、`MVM_`、`DDR_`、`AA_`、`EV_`、`H1_` 等。

### 8.2 信号声明

- 内部声明允许 `wire`、`reg`、`integer`、`real`、`genvar`；`logic` 会被标准化为 `reg`。
- 每行只声明一个主要信号，避免多信号合并声明。
- `VG015`：模块内部 `reg` 标量/向量声明（非 unpacked array）必须显式初始化；若原始声明缺少初值，规范化/修复时必须补成精确 ` = 0;`。
- `wire` 不能 inline assign：禁止 `wire x = expr;`，必须拆成：

```verilog
wire x;                                     // signal
assign x = expr;                            // assign
```

- unpacked array、复杂类型或超出 Verilog-2001 的高级结构不应期待 formatter 完整结构化改写；必要时放入保守路径并人工评审。

### 8.3 端口声明中的类型

历史约束明确要求：端口区不要写 `wire` 或 `reg`。示例：

```verilog
input i_start,                              // port signal
output [C_WIDTH - 1:0]o_data                // port signal
```

而不是：

```verilog
input wire i_start,
output reg [C_WIDTH - 1:0] o_data
```

formatter 可吸收旧代码中的 `input wire` / `output reg`，但最终规范输出不应保留这些关键字。

---

## 9. assign 与输出连线规范

### 9.1 普通 assign

- 所有连续赋值使用显式 `assign lhs = rhs;`。
- `assign` 一行一个；每个连续或过程组合目标都必须展开完整依赖锥，运行时来源最多三个。
- 中间组合信号不会重置 VG145 的计数；跨 `assign` 与组合 `always` 的传递依赖仍属于同一组合锥。
- 超过三个来源时必须用时序 `reg` 隔断，禁止把逻辑改写进组合 `always` 规避门禁。
- assign 区按功能/来源分组，可有小组注释。
- 若写纯分组注释引入后续 `assign` 小组，则该注释上方必须满足唯一空行，或直接紧跟区域横幅；不能直接贴在上一条代码后面。
- `assign` 区不承载时序逻辑，不写阻塞/非阻塞赋值。

### 9.2 output assign 与 output bridge

- output bridge 必须放在 `输出信号连线` 区域。
- bridge 注释推荐 `// output bridge`，若无语义注释 formatter 会补 fallback。
- 示例：

```verilog
    //-------------输出信号连线-------------//
    //用户接口
    assign o_done = done_o;                 // output bridge
    assign o_data = data_o;                 // output bridge
```

- 不能把 output bridge 混入其他信号连线区域。
- 时序 output 的赋值逻辑应在 `输出信号处理区域` 中驱动内部 `_o` 信号，再 bridge 到端口。
- 输出优先使用 `assign o_xxx = xxx_o;`，其中 `xxx_o` 必须声明为 `reg` 并由时序过程驱动。
- 只有上述单信号直接 bridge 豁免 VG145；取反、拼接、算术、逻辑或多信号输出表达式仍按完整组合锥检查。

---

## 10. always 块规范

### 10.1 一个 always 只赋值一个 reg

历史约束与 formatter 默认配置一致：

- 一个 `always` block 只负责一个主要寄存器/目标信号。
- 如果一个 always 同时赋值多个可安全隔离的目标，formatter 可自动拆分。
- 如果存在复杂 lvalue、case 分支目标不一致、组合依赖复杂等无法安全拆分情况，strict mode 应停止并要求人工处理。
- 拆分后每个 always 必须保留正确 reset/default/self-hold 行为。

### 10.2 时序 always

- 必须使用低有效复位：`always@(posedge i_clk or negedge i_rstn)begin`。
- reset 分支必须检查 `i_rstn == 1'b0` 或等价低有效条件。
- 时序赋值使用非阻塞赋值 `<=`。
- reset 分支给目标寄存器明确初值，常用 `0` 或 state 初始值 `ST_IDLE`。
- 非 reset 分支只处理该 always 对应目标信号。

示例：

```verilog
always@(posedge i_clk or negedge i_rstn)begin
    if(i_rstn == 1'b0)begin
        reg_data <= 0;
    end else begin
        reg_data <= next_data;
    end
end
```

### 10.3 组合 always

- 组合逻辑使用 `always@(*)begin`。
- 组合 always 内使用阻塞赋值 `=`。
- 必须覆盖所有分支，避免锁存器；必要时先给默认值。
- 三段式 FSM 的 next-state 组合段必须使用阻塞赋值 `state_next = ...;`，不得使用非阻塞赋值或 continuous assign。
- formatter 对简单条件会标准化：
  - `if(flag)` -> `if(flag == 1'b1)`。
  - `if(!flag)`/`if(~flag)` -> `if(flag == 1'b0)`。
  - 多位信号 truthy -> `> 0`，否定 -> `== 0`。

### 10.4 always 分区

- output internal 信号目标：进入 `输出信号处理区域`。
- `state_current`/`state_next` 等状态目标：进入 `状态机区域`。
- 引用 state 但不直接是状态寄存器的任务逻辑：进入 `状态任务处理区域`。
- 其他寄存器逻辑：进入 `主要任务处理区域`。

---

## 11. 状态机规范

### 11.1 三段式 FSM 为项目硬约束

历史约束要求状态机必须三段式。交付 RTL 应采用：

1. **状态寄存器段**：时序 always，`state_current <= state_next`，reset 到初始状态。
2. **下一状态组合段**：组合 always，根据 `state_current`、输入和条件计算 `state_next`。
3. **状态输出/任务段**：根据状态执行输出或任务处理；若输出是寄存器输出，进入输出信号处理区域或状态任务处理区域。

VG144 自动验证三个独立过程、状态角色 `reg` 声明，并阻断任何 continuous next-state assign；生成器和人工评审必须使用同一严格三段式合同。

### 11.2 FSM 命名与结构

- state parameter：`ST_IDLE`、`ST_RUN`、`ST_DONE`。
- state signal：`state_current`、`state_next`。
- reset 初始状态必须明确，例如 `state_current <= ST_IDLE;`。
- `case(state_current)` 必须覆盖所有状态；必须有 `default`。
- `state_next` 组合 always 要先设置默认值，通常写成 `state_next = state_current;`。
- next-state 组合逻辑中的 `if / else if` 链必须显式闭合到最终 `else`。
- `case(state_current)` 下的 `ST_*:begin` 与 `default:begin` 上方必须有纯注释，且该注释与分支标签左侧对齐。
- 输出逻辑不要在 next-state 组合逻辑里混杂大量非状态赋值。

推荐骨架：

```verilog
    //-------------状态参数区域-------------//
    localparam ST_IDLE = 0;                 // 起始状态
    localparam ST_RUN  = 1;                 // 执行状态

    //--------------状态机信号--------------//
    reg state_current = ST_IDLE;            // signal
    reg state_next = ST_IDLE;               // signal

    //--------------状态机区域--------------//
    //状态转换
    always@(posedge i_clk or negedge i_rstn)begin
        if(i_rstn == 1'b0)begin
            state_current <= ST_IDLE;
        end else begin
            state_current <= state_next;
        end
    end
    
    //主状态机
    always@(*)begin
        state_next = state_current;
        case(state_current)
            //空闲状态转移分支
            ST_IDLE:begin
                if(i_start == 1'b1)begin
                    state_next = ST_RUN;
                end else begin
                    state_next = ST_IDLE;
                end
            end
            //运行状态转移分支
            ST_RUN:begin
                if(i_start == 1'b0)begin
                    state_next = ST_IDLE;
                end else begin
                    state_next = ST_RUN;
                end
            end
            //默认状态转移分支
            default:begin
                state_next = ST_IDLE;
            end
        endcase
    end
```

---

## 12. if/case/loop/generate/function/task/initial 规范

### 12.1 if/else

- 必须使用显式 `begin/end`，即使分支只有一条语句。
- 条件表达式要明确比较，不建议隐式 truthy。
- `else if` 链保持 `end else if(...)begin` 风格。
- next-state 组合逻辑中的 `if / else if` 链必须显式闭合到最终 `else`。
- reset 条件统一低有效写法。

### 12.2 case

- `case(expr)` 独立一行。
- 每个 case item 使用 `LABEL:begin`，item 内缩进一级，最后 `end`。
- 必须有 `default`，尤其是 FSM next-state。
- 在 `case(state_current)` 下，`ST_*:begin` 与 `default:begin` 必须有正上方纯注释，且注释与标签左对齐。
- 不要在 case item 中跨多个目标信号写复杂混合逻辑；必要时拆到不同 always 或任务区域。

### 12.3 for/while/generate

- loop 必须显式 `begin/end`。
- generate block 放入 `生成块区域`。
- generate 中可包含 `if`/`case`/`for` generate、nested always、initial、function/task raw block、实例化等，但复杂结构会提高风险；normalize 前应先做人工审查。
- `genvar` 声明应放入合适信号/生成区域，不要与业务信号混杂。

### 12.4 function/task

- top-level function 放 `函数定义区域`，task 放 `任务定义区域`。
- function/task 内部当前主要按 raw block 保留并参与必要的标识符重命名；formatter 不会像 always 那样完整语义分解。
- function/task 注释必须说明用途、输入输出或关键行为；不要只重复函数名。

### 12.5 initial

- 可综合 RTL 中应避免使用 `initial`，除非目标 FPGA/工具链和项目规范明确允许。
- 若存在 initial，formatter 支持 `初始化区域`，通常排在实例化区域前。
- testbench 不适用本 RTL 规范时应放在独立目录/后缀并采用对应 testbench 规范。

---

## 13. 注释规范

### 13.1 注释类型

- 只使用 `//` 单行注释。
- 禁止保留或新增 `/* ... */` block comment；如遇旧代码应改写为多行 `//`。
- 注释中文优先；信号名、模块名、协议名、timing 术语、RTL 术语可保持英文。
- 注释必须贴近代码实体，不要在远处写大段描述。
- 删除过时、误导、与代码不一致的注释。
- 参数、端口、信号、assign、always 内赋值、实例映射等实体说明注释必须针对当前实体；不能把同一句、只改编号的一句或模板化换皮句批量复用。

### 13.2 文件头注释

- 文件头由标准模板负责，不要在 header 外额外写公司/作者/日期/模块功能等重复信息。
- 修改文件时保持版本历史准确；不要让 formatter 重建 header 丢掉历史信息。

### 13.3 区域 banner

- 模块内部主要区域必须有固定 banner。
- banner 用于结构，不用于解释业务逻辑；业务说明放在具体声明/语句附近。
- 手写 banner 要与 formatter 形式一致；不要混用 `// =====`、`/* ---- */` 等其它风格。

### 13.4 尾随注释

- 默认尾随注释按第 46 列对齐。
- 一行声明/assign 尽量有简短尾随注释。
- 无注释时 formatter fallback 可能补：
  - `parameter`
  - `port signal`
  - `signal`
  - `assign`
  - `output bridge`
  - `internal output signal`
- 更好的交付标准是用有意义中文注释替代 fallback，例如：

```verilog
parameter C_DATA_WIDTH = 32                 // 数据位宽
input i_start,                              // 启动脉冲
reg flag_ready = 0;                         // 数据准备完成标志
assign o_done = done_o;                     // 输出完成标志
```

### 13.5 leading 注释

- 对复杂 always、case、generate、function/task，可在结构前用一行 leading 注释说明用途。
- leading 注释不应脱离结构，也不应成为唯一功能描述。
- 对多协议端口/参数/信号组，可使用小组注释，例如 `//用户接口`、`//AXI写地址通道`。

### 13.6 注释重复与模板复用门禁

- 当前状态：`VG066` 负责重复/近似重复语义注释；`VG041`、`VG055`、`VG056` 负责占位文本、弱语义和覆盖缺失。
- 标准区域 banner 仅作为导航注释，不参与重复注释阻断，也不能替代实体说明。
- 参数、localparam、端口、内部信号、assign、过程赋值、实例参数映射和实例端口映射的同线注释必须有独立语义。
- always、initial、generate、function/task 和实例前导注释必须说明当前块或实例的作用，不能用“数据处理逻辑”“模块逻辑”等套话复用。
- 交付门禁会在去除装饰符、编号、标识符噪声、零宽字符和占位外壳后检测精确重复与近似重复；命中 `VG066` 时 strict 模式阻断，非 strict 模式降为 warning。
- 对同一类信号可以保持相似的句式，但必须说明不同方向、来源、目标、时序条件、协议通道或取值语义；只改 `01/02`、`A/B`、信号名或前后缀不算有效差异。

### 13.7 注释覆盖范围

添加注释时应覆盖：

- module parameter / localparam / state parameter。
- 端口信号。
- 内部 `reg`、`wire`、`integer`、`genvar`。
- assign 逻辑。
- always block 的关键行为。
- FSM 的状态含义、跳转条件和输出任务。
- generate/function/task 的用途和关键参数。
- 实例化模块的功能和连接语义。

### 13.8 注释增强流水线硬规则

当任务是“添加注释/优化注释/AI 添加注释”时，必须执行：

- 当前状态：这是交付流程门禁和人工操作约束，不是单个 `VGxxx`；机器检查点是两次 `python -m scripts.python.quality.verify_verilog_comment_only ...` 与最终交付门禁的组合。

1. 先格式化得到 immutable `baseline.v`。
2. 只在 `baseline.v` 的副本上添加/修改 `//` 注释，得到 `annotated.v`。
3. 用 `python -m scripts.python.quality.verify_verilog_comment_only baseline.v annotated.v --require-comment-delta` 检查：只能有注释变化，且必须有有效 RTL 注释增量。
4. 再对 `annotated.v` 运行 formatter，得到 `final.v`。
5. 再次用 `python -m scripts.python.quality.verify_verilog_comment_only baseline.v final.v --require-comment-delta` 检查。
6. 只有两次检查通过后才能交付 final。

禁止事项：

- 不得在第一次格式化前添加注释。
- 不得覆盖 immutable baseline。
- 不得把第二次格式化输入指向原始文件或 baseline。
- 不得把 pre-header 注释作为唯一注释增量。
- 不得用注释掩盖代码 token 变化。

---

## 14. Preprocessor 与 include 规范

- 宏名、条件编译指令、module 名不得在格式化中丢失。
- formatter guard 会检查 preprocessor directives 和 module names；guard 失败不得写出。
- 复杂 `ifdef/ifndef/elsif/else/endif`、macro-generated module/port、include 片段等高风险代码应使用 `formatter-lint` 或 `formatter-preserve` 先评估。
- 不要在宏中拼接关键 module 结构后再期待 normalize 完全识别。
- include header 文件默认不做完整结构化格式化；若要格式化 include fragment，必须显式配置并人工确认。

---

## 15. 安全、限制与人工评审规则

### 15.1 高风险输入

以下情况需要先做人工审查，不应直接强制 normalize：

- 一个文件多个 module。
- vendor/IP wrapper 或 ABI 必须保留端口名的模块。
- 大量条件编译、宏生成端口/实例、include fragment。
- generate 嵌套复杂，或 generate 内含多层 always/instance/function/task。
- always 同时赋值多个目标且无法安全拆分。
- concat/part-select/array select 等复杂 lvalue 作为拆分目标。
- 非 Verilog-2001 interface/modport/class/package 等 formatter 未明确支持的高级结构。
- latch、异步置位、高有效复位、无复位时序 always。
- FSM 命名/结构不清晰但含 state 语义。

### 15.2 strict mode 约束

默认 strict mode 开启：

- 不支持的语法必须失败，不猜测。
- 缺失 clock/reset 或不满足低有效 reset 要失败。
- output signal/bridge 不变量不满足要失败。
- state parameter/signal 触发 FSM 检查，不满足要失败。

### 15.3 人工评审输出要求

格式化或生成后至少检查：

- module 名和端口方向/位宽是否被正确保留。
- 时钟/复位名和复位极性是否符合项目。
- output port 与内部 `_o` bridge 是否正确。
- `always` 是否一块一目标；拆分后功能是否等价。
- FSM 是否三段式，state 参数/信号命名是否正确。
- inline wire assign 是否拆分。
- 区域顺序和 banner 是否符合规范。
- 注释是否中文优先、有意义、无误导。
- preprocessor directives 是否完整保留。
- 对 Vitis wrapper 是否使用专用 profile 并保留 ABI 端口。

---

## 16. 交付模板骨架

```verilog
`timescale 1ns / 1ps

////////////////////////////////////English///////////////////////////////////////
// Company:         Erie
// Engineer:        Erie
// 
// Create Date:     YYYY/MM/DD HH:MM:SS
// Design Name:     module_name
// Module Name:     module_name
// Description:     description/module_name_Design.pdf
// Simulations:     testbench/vivado/2021.1/module_name
// 
// Referrences:     None
//
// Dependencies:    None
//
// Version:         V1.0
// Revision Date:   YYYY/MM/DD HH:MM:SS
// History:
// Time             Version     Revised by        Contents
// YYYY/MM/DD       V1.0        Erie              Create file.
///////////////////////////////////Chinese////////////////////////////////////////
// 版权归属:        Erie
// 开发人员:        Erie
// 
// 创建日期:        YYYY年MM月DD日
// 设计名称:        module_name
// 模块名称:        module_name
// 模块说明:        Description/module_name_Design.pdf
// 仿真工程:        TestBench/Vivado/2021.1/module_name
//    
// 参考资料:        None
//
// 依赖文件:        None
//
// 当前版本:        V1.0
// 修订日期:        YYYY年MM月DD日
// 修订历史:
// 时间             版本        修订人            修订内容
// YYYY年MM月DD日   V1.0        Erie              创建文件

//xxx模块
module module_name
#(
    parameter C_DATA_WIDTH = 32             // 数据位宽
)
(
    //---------------全局信号---------------//
    input i_clk,                            // 工作时钟
    input i_rstn,                           // 低有效复位

    //---------------用户接口---------------//
    input i_start,                          // 启动信号
    output o_done                           // 完成标志
);

    //-------------配置参数区域-------------//

    //-------------状态参数区域-------------//

    //------------模块实例化信号------------//

    //---------------计数信号---------------//

    //--------------状态机信号--------------//

    //--------------寄存器信号--------------//

    //---------------标志信号---------------//
    reg flag_done = 0;                      // 完成标志寄存器

    //---------------编码信号---------------//

    //---------------译码信号---------------//

    //---------------其他信号---------------//

    //---------------输出信号---------------//
    reg done_o = 0;                         // 完成标志输出缓存

    //-------------其他信号连线-------------//

    //-------------输出信号连线-------------//
    assign o_done = done_o;                 // 输出完成标志
    
    //-----------输出信号处理区域-----------//
    always@(posedge i_clk or negedge i_rstn)begin
        if(i_rstn == 1'b0)begin
            done_o <= 0;
        end else begin
            done_o <= flag_done;
        end
    end

    //--------------状态机区域--------------//
    
    //-----------状态任务处理区域-----------//
    
    //-----------主要任务处理区域-----------//
	//完成标志寄存器
    always@(posedge i_clk or negedge i_rstn)begin
        if(i_rstn == 1'b0)begin
            flag_done <= 0;
        end else begin
            flag_done <= i_start;
        end
    end
    
    //------------模块实例化区域------------//

endmodule
```

---

## 17. Formatter 当前实现差异与注意点

这些是“读源码后确认的现状”，交付和后续维护时要特别注意：

1. **端口/信号 packed width 与 name 当前可能无空格。** 这是渲染实现事实；不影响语义，但会影响人工期待。
2. **`Referrences` 拼写按实现输出。** 这是当前字面合同；不要在 formatter 接管文件中手动改回 `References`。
3. **VG144 强制三段式 FSM。** 工具检查三个独立过程并禁止 continuous next-state assign；人工仍需复核状态转移语义是否正确。
4. **`end/req/ack/done/valid` 等 flag 语义不一定全部自动加 `flag_`。** 生成器必须主动遵守。
5. **协议专用 clock/reset 名不一定由 formatter 自动推断。** `i_axi_aclk`、`i_axis_arstn` 等要由生成器或人工指定。
6. **实例名规范主要靠项目模板。** formatter 保证实例语法和位置，但不强制实例名语义化。
7. **function/task 内部是 raw-block 级处理。** 可参与标识符重命名，但不会完整拆解语义。
8. **复杂非 Verilog-2001 结构不应直接 normalize。** interface/package/class/modport 等需保守处理。
9. **output direct assign 与 output bridge 有区别。** 优先使用时序 `_o` reg 的单信号直接 bridge；复杂输出 assign 继续接受 VG145 完整组合锥检查。
10. **默认 auto 不等于强制 normalize。** auto 可能保留源文件，这是设计上的安全策略。

---

## 18. 最终交付检查清单

### 18.1 命名检查

- [ ] input 为 `i_`，output 为 `o_`，inout 为 `io_`。
- [ ] clock/reset 使用低有效复位命名，默认 `i_clk/i_rstn`。
- [ ] parameter 为 `C_` 大写；localparam 大写；state parameter 为 `ST_`。
- [ ] state signal 为 `state_current/state_next` 或 `state_` 前缀。
- [ ] reg/cnt/flag/enc/dec 命名符合前缀。
- [ ] 内部 output 为 `_o`，顶层 output 为 `o_`，二者不混用。
- [ ] 无 `C_C_`、`ST_ST_`、`reg_reg_`、`cnt_cnt_`、`flag_flag_`、`enc_enc_`、`dec_dec_`、`state_state_` 等重复前缀，无 legacy `_i/_o` 端口尾缀残留。

### 18.2 结构检查

- [ ] ANSI-style module header。
- [ ] 端口区无 `wire/reg/logic`。
- [ ] 区域 banner 完整，顺序正确。
- [ ] 参数、信号、assign、always、instance 位于正确区域。
- [ ] 无参数检查区时 instance 位于模块末尾；有参数检查区时 `参数检查区域` 为最终内部区域。
- [ ] 人类可读 `$display` 使用 ` > INFO/WARNING/ERR: [Verilog]` 前缀；机器 transcript 单独豁免。
- [ ] inline wire assign 已拆分。
- [ ] output bridge 在输出信号连线区域。

### 18.3 行为检查

- [ ] 每个 always 只赋值一个主要寄存器/目标。
- [ ] 时序 always 使用 `posedge clk or negedge rstn`。
- [ ] reset 为低有效，reset 分支初值明确。
- [ ] 组合 always 有默认值，避免 latch。
- [ ] FSM 为三段式，有 default 状态。
- [ ] 复杂 generate/preprocessor 未被误改。

### 18.4 注释检查

- [ ] 只使用 `//` 注释。
- [ ] 注释中文优先，贴近代码。
- [ ] 文件头信息准确，版本历史不乱改。
- [ ] port/parameter/signal/assign/always/FSM/instance 均有必要注释。
- [ ] 无过时、误导、纯重复语法的注释。
- [ ] 无重复注释、近似重复注释、只改编号/信号名的模板化复用注释。
- [ ] 尾随注释尽量按第 46 列对齐。

### 18.5 工具与安全检查

- [ ] 对未知/高风险代码先跑 `formatter-lint` 或人工审查。
- [ ] 对 Vitis wrapper 使用 `vitis-wrapper` profile。
- [ ] normalize 前确认不是 vendor/IP 或 ABI-sensitive 顶层。
- [ ] guard 不通过时不写文件。
- [ ] 注释任务走双 verify comment-only 流水线。

---

## 19. 推荐命令

统一运行合同：

- 命令从 skill root 运行。
- 参数路径必须对当前 Python 运行宿主可见。
- 不做 Windows 盘符到 POSIX 路径的自动转换。

Review：

```bash
python -m scripts.python.workflow.cli review --target <input.v> --report-json review.json
```

聚焦 VG 诊断：

```bash
python -m scripts.python.quality.verilog_quality_gate <input.v> --json quality_gate.json --markdown quality_gate.md
```

最终交付门禁：

```bash
python -m scripts.python.validation.generated_deliverable_gate <input.v> --json deliverable_gate.json --markdown deliverable_gate.md
```

注释验证：

```bash
python -m scripts.python.quality.verify_verilog_comment_only baseline.v annotated.v --require-comment-delta
python -m scripts.python.quality.verify_verilog_comment_only baseline.v final.v --require-comment-delta
```
