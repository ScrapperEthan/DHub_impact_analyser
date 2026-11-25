# Decisioning Process（演示）

本页为 DHub PoC 的合成资料。步骤名称用于演示证据定位。

## Decision Process Baseline

| 阶段 | 用途 |
| --- | --- |
| P1 Initialization | 加载并按渠道选取活跃 Campaign |
| P2 Multi-Layered Eligibility Filtering | 针对 Campaign、Channel 和 Communication Strategy 应用资格条件 |
| P3 Business Value Calculation | 计算候选 Campaign 的 Arbitration Score |
| P4 Contact Permissions | 应用 Contact Policy 和 Suppression Rules |
| P5 Final Arbitration & Fatigue Management | 排序、处理冲突并检查 Frequency Cap 和 Quiet Time |
| P6 Delivery | 将符合条件的 Campaign 候选交给对应渠道 |

## Phase 5

### Step 18

Frequency Cap（频控）检查本次拟议联络后的触达次数。如果次数超过上限，则拦截该 Campaign。

当前演示规则对 Inbound 和 Outbound 生效。若 Campaign 类型是 MANDATORY，且 Global Settings 的 Allow mandate campaign override 开启，则可以绕过 Frequency Cap，超过上限时也通过。Override 关闭或类型为 MARKETING 时按通常上限检查。

配置字段见 DEMO-SETTINGS v1 的 Frequency Cap 小节。Quiet Time 另有配置项。

## Phase 6

### Step 21

Outbound Delivery 接收经过资格和 Frequency Cap 筛选的候选 ID，再执行渠道投递。被频控拦截的 Campaign 不进入投递候选集合。最终发送结果仍取决于渠道处理。
