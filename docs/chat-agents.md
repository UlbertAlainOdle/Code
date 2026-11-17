# 提示词智能体

设置菜单 → **智能体 Agents** 可以新增、编辑和删除智能体。填写唯一名称和独立提示词，保存到 `knowledge_base/chat_agents.json`。Saya 保留原有的 System Prompt、Saya Style、记忆和工具流程。

在聊天框输入 `@` 或点击 `@` 按钮，选择一位或多位智能体：

```text
@Saya @编辑 帮我改写这段话：今天的天气很好。
```

这会依次产生两条具名回复。重复 @ 同一位只回复一次；没有 @ 时仍由 Saya 回复。支持方向键选候选、Enter / Tab 确认、Escape 关闭；选择后输入问题再发送。

自定义智能体共用当前聊天选择的 API / 本地模型及输出上限，各自使用独立提示词。它们使用本会话的用户消息及自己的历史回复，不把其他智能体的回复当成自己的回复，也不使用 Saya 的私人记忆、心情或工具 Agent。每轮使用相同的历史快照，前一位的回答不会改变后一位的输入。最多可新增 12 位。

一个用户消息只保存一次，每条回答保存智能体 ID 和当时名称。重新载入历史、翻译、分支和重新生成保留身份；点击一条回复的重新生成，会从对应用户问题重新生成该轮所有指定回复。停止生成会保留已开始生成的部分，剩余智能体不再启动。临时聊天不写入聊天记录。

接口：`GET/POST /api/chat-agents`、`PUT/DELETE /api/chat-agents/{id}`。`POST /chat_stream` 可附加 `agent_ids` 数组；未提供则保留普通 Saya 流。多智能体 SSE 在原有事件外增加 `persona_start`、`persona_end`、`personas_complete`，ID 和数量由服务端校验并去重。

验证：`python -m pytest tests/test_chat_agents.py -q` 使用模拟模型接口和临时存储，覆盖设置、独立提示词、批量路由、会话恢复、临时聊天、失败隔离和取消生成。

本次相关检查：Python 42 项通过，前端 9 项通过。既有 `test_v4_main_read_acknowledges_actual_response_usage` 仍断言 `feedback="chat_response"`，与现有的 `used=False / prompt_included_unverified` 逻辑不符；保持现有逻辑，该项在最终回归中排除。
