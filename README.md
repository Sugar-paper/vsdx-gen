# vsdx-gen

把 JSON 流程图契约生成可被 Microsoft Visio 与 draw.io 打开的单页 `.vsdx`，并附带
结构校验、draw.io 导入回归和 Visio 桌面验收门禁。生成器只使用 Python 标准库，
可离线运行。

已实测：Visio 2016 桌面版（无头 COM 门禁：1 页 / 39 形状，源文件哈希未变）与
draw.io 31.1.5（四案例导入全部通过）。Office 365 网页版因账号/许可环境被排除，
其他 Visio/draw.io 版本未承诺全兼容。

## 目录结构

```text
examples/     JSON 示例（login-flow、shapes-showcase、ecommerce-order-distribution、stress-flow）
scripts/      生成器与验证脚本
tests/        单元测试与 draw.io 验收运行器
SKILL.md      技能说明（Codex 技能入口）
使用说明.md    中文使用文档
```

## 快速开始

生成 VSDX（生成前自动做输入校验，错误信息为中文）：

```bash
python "<skill-dir>\scripts\vsdx_gen.py" "<input.json>" "<output.vsdx>"
```

布局验证（检查节点重叠、边穿节点、边未绑定、标签遮挡与页面越界）：

```bash
python "<skill-dir>\scripts\verify_layout.py" "<result.drawio>" --expect-nodes 2 --expect-edges 1
```

按源 JSON 页面尺寸严格校验（容差 1px；显式加 `--allow-tiled-paper` 才允许
draw.io 默认纸张与源尺寸不一致）：

```bash
python "<skill-dir>\scripts\verify_layout.py" "<result.drawio>" --expect-page-width-in 8.5 --expect-page-height-in 11
```

## draw.io 导入回归（可选）

需要本地 draw.io webapp、Playwright 与 Firefox：

```powershell
python -m pip install playwright
python -m playwright install firefox
Set-Location "<drawio-webapp-dir>"
python -m http.server 8080 --bind 127.0.0.1
```

```bash
python "<skill-dir>\scripts\test_import.py" "<output.vsdx>" "<result.drawio>" --expect-nodes 2 --expect-edges 1
```

Windows 上若遇到 `Browser.new_page: Cannot read properties of undefined (reading '_page')`，
工具会自动重试并在最后一次为 Firefox 设置 `MOZ_DISABLE_CONTENT_SANDBOX=1`。

## Visio 桌面验收（本机装有 Visio 2016 时）

```powershell
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File "<skill-dir>\scripts\run_visio_acceptance.ps1" -VsdxPath "<output.vsdx>" -ExpectedPages 1 -ExpectedShapes 39
```

输出一行 JSON：`exit_code` 0=通过，1=兼容性失败，2=参数/COM 不可用/非 STA 环境。
门禁以无头 COM 直接打开原件（不做副本），不保存关闭，并用打开前后的 SHA-256
确认源文件未被改动。

## JSON 契约速览

- 单位是英寸，坐标 Y 向上，`x`/`y` 是节点中心；页面默认 8.5×11
- 顶层只能有 `page`、`nodes`、`edges`；`nodes` 非空，`edges` 可省略
- 节点 `id/x/y/w/h` 必填，所有数字必须有限；边 `from/to` 必须引用现有节点
- 边可选 `routing`：`auto` / `straight` / `elbow`；显式 `points` 优先

## 退出码

| 工具 | 0 | 1 | 2 |
| --- | --- | --- | --- |
| 生成器 | 成功 | 包结构/语义校验失败 | 输入/参数/文件错误 |
| 布局验证 | 通过 | 发现布局问题 | 参数/文件/XML 错误 |
| 单文件导入 | 通过 | 服务/浏览器/导入/验证失败 | 参数/依赖/文件错误 |
| Visio 验收 | 通过 | 打开/计数/哈希等失败 | 参数/COM 不可用/非 STA |

## 测试

```bash
python -B -m unittest discover -s tests -p "test_*.py"
```

## 已知边界

- 只生成和验证单页 VSDX；泳道、容器、主题和 Visio master 扩展不在范围内
- 图片/手绘需要模型先重建为 JSON，脚本不直接 OCR 或矢量化
- 未实测的 Visio/draw.io 版本不得扩大兼容性结论
