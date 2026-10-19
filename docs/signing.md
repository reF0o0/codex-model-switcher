# 更新时保留文件授权

macOS 用代码签名身份识别应用。之前的发布使用临时签名（ad-hoc），每次重新构建都会产生不同的身份，所以只保留应用名称、Bundle ID 和路径不足以保证授权延续。依据：[Apple TN3127](https://developer.apple.com/documentation/technotes/tn3127-inside-code-signing-requirements)。

2026-10-19 已完成本机一次性设置，从临时签名的 1.5.0 迁移到固定签名的 1.5.1，并继续以同一身份更新到 1.6.1。证书仅对“代码签名”设为始终信任，其他用途保持默认；窗口实测显示“文件读写检查通过”。旧应用和迁移前偏好已分别保留在本机 `Previous Apps/` 与 `Migrations/` 目录。

实测两份内容不同、代码散列不同的签名应用具有相同的固定身份，普通更新身份检查通过，钥匙串搜索列表恢复原样。迁移前没有已保存的目录书签，因此本次没有验证已有真实书签跨版本延续；书签恢复由独立 Swift 检查覆盖。未来系统隐私策略变化仍需按实际提示处理。

## 一次性设置（本机已完成，保留操作步骤）

固定本机证书已准备在：

`~/Library/Application Support/Codex Model Switcher/Signing/identity.cer`

1. 在用户能操作窗口时，打开此证书并导入本人的登录钥匙串。
2. 在“钥匙串访问”找到 `Codex Model Switcher Local Signing`，展开“信任”，仅将“代码签名”设为“始终信任”。系统可能要求本人的登录密码；其他用途保持系统默认。
3. 运行 `python3 scripts/signing.py status`，应返回 `ready: true`。准备脚本不修改系统信任或隐私权限，也不会自动打开上述窗口。
4. 构建后执行首次安装：`python3 scripts/install.py --migrate-legacy`。旧临时身份与新固定身份不同，macOS 可能要求最后一次文件权限确认；软件不能代替用户授权。
5. 此后更新只运行构建和 `python3 scripts/install.py`，不再使用迁移参数。更新前会检查签名身份一致；不一致就停止并保留旧版。

固定签名可消除因每次构建更换身份而重复授权的问题。用户手动撤销权限、重装系统或系统隐私策略变化仍可能要求重新确认，不能承诺所有情况下永不提示。

## 后续更新规则

- 保持 Bundle ID `io.github.ref0o0.codex-model-switcher`、安装位置和同一证书不变；不再使用 `codesign --sign -` 发布。
- `scripts/build.sh` 在固定身份未就绪时直接停止；不会生成新的临时签名安装包覆盖原包。
- 证书和私钥放在专用本机钥匙串中，目录仅本人可访问，不放入仓库、应用或 ZIP；私钥只允许签名工具使用。准备时恢复原钥匙串搜索列表，不改系统信任。
- 根据本机 `codesign(1)` 的 `SIGNING IDENTITIES` 说明，签名身份所在钥匙串还必须出现在用户搜索列表；签名脚本只在签名期间临时加入，使用 `finally` 在成功或失败后恢复原列表。
- 不删除 `~/Library/Application Support/Codex Model Switcher/Signing/`；丢失签名材料时停止构建，恢复原备份，不自动生成另一个身份。
- 系统目录授权仍使用原来的 `directory-access-bookmarks-v1` 设置键；启动时恢复，过期但可用的记录自动刷新。失败时保留旧记录，不因为更新清空设置。
- `scripts/install.py` 在替换前核对证书身份，保留旧应用，失败则恢复；不清空偏好、目录授权、Codex 配置或聊天记录。

此证书仅供这台 Mac 的本机版本使用；未来公开分发应使用 Apple Developer ID 与公证，并单独处理身份迁移，不能将私钥打包给其他人。
