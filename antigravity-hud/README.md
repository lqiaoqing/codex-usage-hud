# Antigravity 用量

从仓库根目录双击 `Start-CodexUsageHud.bat`，切换到 **Antigravity**。顶部的用量页和刷新按钮作用于当前标签。

- 额度通过官方 Antigravity CLI 的 `agy -p /usage --output-format json` 获取。请用与桌面版相同的 Google 账号在 CLI 登录一次。HUD 按顶部共用间隔在后台读取；关闭桌面版或用量页也可刷新。「打开用量页」仍可按需打开桌面版设置。
- Antigravity CLI 查询复用 HUD 的 HTTP 代理设置。双击启动时也会将 HUD 发现的代理传给 CLI。
- 本地任务统计可独立使用。任务总计包含主会话和能确认归属的子代理。缓存命中率是缓存读取 token 除以未缓存输入、缓存读取和缓存写入的总和。API 估价按当前公开标准价折算，包含推理输出。缺少模型价格或缓存写入费率时，只合计可计价部分。
- 可选择最近的会话。桌面会话无法识别时，以最近活动的会话作为默认值。

## 构建额度组件

额度需要支持 JSON 用量输出的 [Antigravity CLI](https://antigravity.google/download)，最低版本 1.2.15。HUD 会查找官方用户安装路径、`antigravity-hud/runtime/agy.exe` 和 PATH。

在仓库根目录执行以下构建命令可启用桌面会话跟随和「打开用量页」。运行主面板需要 Python 和 tkinter，该桌面辅助组件另需 .NET 10 Desktop Runtime。构建需要 .NET 10 SDK。

```powershell
dotnet publish antigravity-hud/AntigravityUsageHud.csproj -c Release -o antigravity-hud/publish
```

`publish` 中的程序由主面板后台调用以识别当前桌面会话。任务统计来自本地数据库。
