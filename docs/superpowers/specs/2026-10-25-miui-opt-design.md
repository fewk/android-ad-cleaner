# 小米专属优化与遥测管理模块（miui-opt）设计规范

## 1. 背景与目标

在小米及 Redmi 设备（MIUI / HyperOS）上，除了常规的应用内开关和广告核心组件（`com.miui.systemAdSolution`）外，系统还存在：
1. **遥测与日志上报**：安全中心报告、崩溃错误报告、Activity 启动日志打点，后台持续进行数据收集与网络传输。
2. **触控预加载优化（Touch Prestart）**：按压屏幕瞬间后台预启动 App，容易在滑动浏览时误触发拉起后台进程，增加发热并降低续航。
3. **监控与守护组件**：`com.miui.analytics`（打点统计）、`com.miui.daemon`（守护进程）。

本项目遵循 `AGENTS.md` 规范：优先 App 内开关、动包使用 `pm suspend`（冻结）、严格备份原值、保证改动可逆。
本设计在 `tool/run.py` 中引入专属于小米家族机型的模块化命令 `miui-opt`，并在 `check` 检查阶段自动感知并呈现状态。

---

## 2. 管理项与规范定义

### 2.1 系统设置项（4 项）

| 设置项 Key | 命名空间 | 目标值 (off) | 默认/原厂行为 | 作用与效益 |
|---|---|---|---|---|
| `send_security_reports` | `system` | `0` | `1` (开) | 关闭安全中心自动向云端上传安全检测和病毒扫描报告 |
| `send_action_app_error` | `secure` | `0` | `1` (开) | 关闭应用发生崩溃（Crash）或 ANR 时向小米服务器回传错误报告 |
| `activity_starts_logging_enabled` | `global` | `0` | `1` (开) | 关闭 AOSP / 系统级 Activity 启动追踪打点日志，降低后台 I/O |
| `touch_prestart_opt_config` | `system` | `"{featureDisable:true}"` | 未配置或 `{featureDisable:false,...}` | 彻底禁用触控预启动功能，避免滑动误触唤醒后台应用，降低功耗 |

### 2.2 监控与常驻组件（2 项）

* `com.miui.analytics`（小米打点与统计分析服务）
* `com.miui.daemon`（小米性能与监控守护进程）

状态判定标准：
* `已挂起`：`dumpsys package <pkg>` 包含 `suspended=true`。
* `未安装`：`pm path <pkg>` 为空，或 `dumpsys package <pkg>` 显示 `installed=false`（即已被机主 `uninstall --user 0`）。
* `未挂起`：已安装但未挂起。

---

## 3. 架构设计与命令行为

### 3.1 品牌识别
使用现有的 `FAMILY.get(brand.lower()) == "xiaomi"` 进行家族识别。
仅当机型品牌属于 `Xiaomi`、`Redmi`、`POCO` 时激活小米专属检测。非小米机型调用 `miui-opt` 时输出提示并优雅退出。

### 3.2 备份与恢复存储格式
备份文件保存于当次操作的备份目录下：
`backups/<型号>-<时间戳>/miui-opt-before.json`

JSON 格式规范：
```json
{
  "serial": "192.168.31.128:5555",
  "saved_at": "2026-10-25T23:45:00",
  "settings": {
    "system/send_security_reports": "1",
    "secure/send_action_app_error": "1",
    "global/activity_starts_logging_enabled": "1",
    "system/touch_prestart_opt_config": "{featureDisable:false,bgExceptionInterceptDisable:false}"
  }
}
```
* **备份幂等性保护**：若备份文件已存在（例如用户连续多次执行 `off`），绝对不能把已经修改为 `0` 的中间值覆盖进最初备份。
* **空值处理**：如果读取原值返回 `null` 或空，记录为 `null`；在 `miui-opt on` 还原时，对应调用 `settings delete <namespace> <key>` 彻底恢复系统默认。

### 3.3 命令行调用接口

#### (1) `run.py -s <序列号> check`
在现有输出（锁屏、机型脚本、广告包、快应用）后，若是小米家族机型，追加输出：
```text
小米专属优化（遥测与触控省电）：
  - 安全报告上传 (send_security_reports)：已关闭 (0)
  - 崩溃错误上传 (send_action_app_error)：未关闭 (1)
  - Activity 启动打点 (activity_starts_logging_enabled)：已关闭 (0)
  - 触控预加载 (touch_prestart_opt_config)：未关闭
  - 统计分析服务 (com.miui.analytics)：已挂起
  - 性能守护进程 (com.miui.daemon)：未挂起
提示：一键优化遥测与触控：python3 tool/run.py -s S miui-opt off（可随时用 miui-opt on 还原）
```

#### (2) `run.py -s <序列号> miui-opt [check]`
* 只读查看详细状态，并附带每个设置项的详细说明和当前数值。

#### (3) `run.py -s <序列号> miui-opt off`
* 步骤 1：先检查并生成原值备份 `miui-opt-before.json`。
* 步骤 2：依次调用 `settings put` 修改 4 项配置。
* 步骤 3：逐项回读校验；校验失败报错退出并保留备份。
* 步骤 4：检查 `analytics` 与 `daemon`，若已安装且未挂起，提示用户可按需执行 `run.py -s S suspend <包名>` 冻结。

#### (4) `run.py -s <序列号> miui-opt on`
* 步骤 1：从最近的备份目录中加载 `miui-opt-before.json`。
* 步骤 2：逐项恢复原值（非空值执行 `settings put`，空值执行 `settings delete`）。
* 步骤 3：逐项回读校验，全部恢复完成后清理或标记备份已还原。

#### (5) `run.py -s <序列号> status-json`
* 在返回的 JSON 对象中增加可选字段 `"miui_opt"`，提供给 GUI 或前端消费。

---

## 4. 错误处理与安全性考量

1. **非小米品牌保护**：在非小米机型上运行 `miui-opt` 时报错提示并退出，不写入任何 settings。
2. **原子性与回读验证**：每一条 `settings put` 之后，必须紧跟 `settings get` 回读。若回读值不符合预期目标，抛出错误，明确告知用户哪一项设置被系统拒绝。
3. **已卸载包容错**：对于此前已被用户 `uninstall --user 0` 卸载的包，显示为 `已卸载`，不尝试对其进行 `pm suspend`。

---

## 5. 测试与验证规划

1. **单元测试（`tests/test_miui_opt.py`）**：
   * 测试非小米设备拒绝执行逻辑。
   * 测试首次备份生成与重复执行防覆盖机制。
   * 测试 `off` 流程的命令调用序列与回读校验。
   * 测试 `on` 流程的恢复逻辑（包括正常值恢复与 `null` 值的 `settings delete`）。
   * 测试 `check` 命令对小米与非小米设备输出内容的差异。
2. **现有回归测试**：
   * 确保现有 78 个自动化测试 100% 保持通过。
3. **实机验证**：
   * 在已连接的 Redmi Note 12 Turbo (`192.168.31.128:5555`) 上运行 `run.py check` 与 `miui-opt` 系列命令进行真机端到端验收。
