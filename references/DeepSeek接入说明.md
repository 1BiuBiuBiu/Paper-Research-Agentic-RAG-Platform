# DeepSeek Flash / Pro 接入说明

核对日期：2026-10-21（Asia/Shanghai），以当前官方文档为准。

| UI 选择 | API model | 服务端地址 |
| --- | --- | --- |
| DeepSeek Flash | deepseek-flash | https://api.deepseek.com/chat/completions |
| DeepSeek Pro | deepseek-v4-pro | https://api.deepseek.com/chat/completions |

依据：[官方首次调用](https://api-docs.deepseek.com/zh-cn/)、[Chat Completions 参数](https://api-docs.deepseek.com/api/create-chat-completion/)、[JSON 输出](https://api-docs.deepseek.com/guides/json_mode/)。当前官方日志说明 Flash 别名会跟随模型更新，Pro 仍提供服务；旧文章的下线计划不作为当前配置依据。[官方更新日志](https://api-docs.deepseek.com/zh-cn/updates/)

本项目只通过后端 httpx 调用官方兼容接口，不依赖浏览器持有凭据。Key 只放本机 .env 的 DEEPSEEK_API_KEY 或进程环境变量，模板中留空，不自动传到客户端/日志/解析或向量 worker。

S1 使用非流式 JSON、thinking=disabled、reasoning_effort=none、输出上限默认 2,048 tokens、总调用期限 90 秒，单次问答最多一次外部模型调用；没有付费自动重试、自动换模型或隐式联网。提供 UTF-8 字节硬预算，实际 token 用量以 API usage 为准，不把字符估算冒充精确 token。参考：[官方 token 说明](https://api-docs.deepseek.com/quick_start/token_usage/)。

错误状态分别处理 401/402/403/429、网络与总超时、JSON 无效/截断/引用无效；不回传上游原始错误正文。重定向默认拒绝，TLS 验证保持开启。模型返回的实际 model 和真实 usage 会返回给用户，缺失用量是 null。

本轮曾对无凭据的官方 /models 发起连接探测，得到预期 401，只证明地址可达；这不等同 Key 有效，也不等同真实问答通过。真实 Flash/Pro 冒烟须在本机填好 Key 后针对自建样本运行，禁止把替身的 token 数或回答当作线上结果。
