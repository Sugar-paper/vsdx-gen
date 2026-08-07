---
name: vsdx-gen
description: Use when users need Mermaid, structured workflow or architecture descriptions, or manually reconstructed image content converted to editable Visio .vsdx; need offline or batch VSDX generation; or need draw.io import validation.
---

# VSDX 生成器

将图描述（JSON）生成为 Open Packaging 格式的单页 `.vsdx` 文件（ZIP + XML 部件），
同时满足 Microsoft Visio 与 draw.io 的 VSDX 导入器，保留支持范围内的节点、连接和样式。
生成器是纯 Python 标准库。已实测：Visio 2016 桌面版可通过无头 COM 门禁打开生成文件
（1 页/39 形状，源哈希未变）；draw.io 31.1.5 四案例导入全部通过。Office 365 网页版、
其他 Visio 版本与所有 draw.io 版本仍未承诺全兼容。

图片/手绘输入由模型先重建为本技能的 JSON 契约；脚本本身不执行 OCR 或图像矢量化，
也不解析图片内容。模糊文字、连线关系或无法直接重画的位图/图标，应先向用户确认是近似重画还是省略。

## 技能根目录与命令

执行任何脚本前，先把加载到的 `SKILL.md` 所在目录记为 `<skill-dir>`；不要假定当前
工作目录就是技能目录。下面的 `<skill-dir>` 是占位符，实际执行时替换为绝对路径。

```
① 源材料 → JSON 契约（本文件的规则）
② python "<skill-dir>\scripts\vsdx_gen.py" "<input.json>" "<output.vsdx>"
③ （可选）python "<skill-dir>\scripts\test_import.py" "<output.vsdx>" "<result.drawio>"
```

脚本位置：`<skill-dir>\scripts\`；示例位于 `<skill-dir>\examples\`。

## JSON 契约（LLM 解析时的输出格式）

单位：**英寸**，Y 向上，`x`/`y` 是形状**中心**。页面默认 8.5×11。

```json
{
  "page": {"name": "Page-1", "title": "标题", "width": 8.5, "height": 11},
  "nodes": [
    {"id": "A", "text": "用户登录", "type": "rect", "x": 3.0, "y": 9.0,
     "w": 1.5, "h": 0.75, "fill": "#FFFFFF", "stroke": "#000000"},
    {"id": "B", "text": "验证完成", "type": "rect", "x": 3.0, "y": 7.5,
     "w": 1.5, "h": 0.75}
  ],
  "edges": [
    {"id": "e1", "from": "A", "to": "B", "label": "成功"}
  ]
}
```

顶层只能包含 `page`、`nodes`、`edges`。`nodes` 必须是非空数组；`edges` 省略时默认为空数组。
所有数字必须是有限 JSON 数字，不能使用 `NaN`、`Infinity` 或布尔值代替数字。边引用必须存在，
且不允许自环。可选字段要使用默认值时应省略，不能显式写 `null`（`page` 整体可为 `null`）。

### 节点字段

| 字段 | 说明 |
|------|------|
| id | 唯一标识（A/B/...） |
| text | 标签文本（支持 \n 多行） |
| type | 内置形状类型；使用 `geometry` 时可省略（默认 `rect`），轮廓由 `geometry` 决定 |
| x, y, w, h | 英寸；x/y 是中心点 |
| fill / stroke | `#RRGGBB`；fill 用 `"none"` 或 `"transparent"` 表示无填充 |
| strokeWidth | 线宽（英寸，默认 0.01） |
| dashed | true/false |
| opacity | 0–100 不透明度 |
| gradient | 渐变终点色 `#RRGGBB` |
| rotation | 角度（度，逆时针） |
| fontFamily | `Microsoft YaHei`/`SimSun`/`SimHei`/`KaiTi`/`Arial`/`MS Gothic` |
| fontSize | 磅（pt，默认 12） |
| fontColor | 字色 |
| bold / italic / underline | true/false |
| align | left / center / right |
| valign | top / middle / bottom |

### 形状库（17 种）

`rect`(矩形) `diamond`(菱形) `ellipse`(椭圆) `process`(圆角矩形) `cylinder`(圆柱/数据库) `document`(文档) `note`(便签) `triangle`(三角) `pentagon`(五边形) `hexagon`(六边形) `parallelogram`(平行四边形) `trapezoid`(梯形) `arrow`(右箭头) `leftArrow` `upArrow` `downArrow` `star`(五角星)

### 自定义几何（15 种行类型）

```json
{"id": "X", "type": "rect", "x": 1, "y": 1, "w": 1.3, "h": 0.8,
 "geometry": [
   ["MoveTo",  {"x": 0.2, "y": 0.1}],
   ["LineTo",  {"x": 0.8, "y": 0.1}],
   ["ArcTo",   {"x": 1.1, "y": 0.4, "a": 0.3}],
   ["RelCubBezTo", {"x": 0, "y": 0.3, "a": 0, "b": 0.3, "c": 0.3, "d": 0}],
   ["Ellipse", {"x": 0.5, "y": 0.5, "a": 1, "b": 0.5, "c": 0.5, "d": 1}],
   ["EllipticalArcTo", {"x": 1, "y": 0.5, "a": 0.5, "b": 0.5, "c": 0.5, "d": 1}],
   ["InfiniteLine", {"x": 0, "y": 0, "a": 1, "b": 1}],
   ["NURBSTo", {"x": 1, "y": 0, "a": 0.5, "b": 0.5, "c": 0.5, "d": 0.5, "e": 1}],
   ["PolylineTo", {"x": 1, "y": 0.5, "a": "1 0 1 1"}],
   ["RelQuadBezTo", {"x": 0.5, "y": 1, "a": 0.5, "b": 0.5}],
   ["RelLineTo", {"x": 1, "y": 0}],
   ["RelMoveTo", {"x": 1, "y": 1}],
   ["RelEllipticalArcTo", {"x": 0.5, "y": 1, "a": 0.5, "b": 0.5, "c": 0.5, "d": 1}],
   ["SplineStart", {"x": 0, "y": 1, "a": 1, "b": 0, "c": 0, "d": 0}],
   ["SplineKnot",  {"x": 1, "y": 1, "a": 0}]
 ]}
```

规则：序列第一个必须是 `MoveTo`；坐标是局部坐标（原点在形状左下角，范围 0..w × 0..h）。
`Rel*` 行的 X/Y 范围是 0..1；控制单元格必须是有限数字；`PolylineTo.A` 是空格分隔的有限数字字符串。

### 边字段

| 字段 | 说明 |
|------|------|
| from / to | 节点 id |
| label | 边标签（可选） |
| fromSide / toSide | bottom/top/left/right；省略时按相对位置自动判断 |
| lineColor / strokeWidth / dashed | 线样式 |
| startArrow / endArrow | `none` `open` `block` `classic` `oval` `diamond` `blockThin` `dash`（默认 endArrow=block） |
| points | 仅表示中间路由点 `[[x,y],...]`（英寸，页面坐标）；源/目标锚点由 `from`/`to` 和边自动生成 |
| routing | `auto`（默认）/ `straight` / `elbow`；`auto` 让斜向边自动生成正交折线，若折线会穿过其他节点则退回直线；`straight` 强制直线；`elbow` 强制折线；水平/垂直对齐的边始终直线；显式 `points` 优先于 `routing` |

## Mermaid → JSON 转换规则（模型遵循；脚本不解析 Mermaid）

1. **节点映射**：`A[文本]`→rect；`A{文本}`→diamond；`A((文本))`→ellipse；`A[(文本)]`→cylinder；`A[/文本/]`、`A[\文本\]`→parallelogram；`A>文本]`→process。其余语法→rect
2. **布局**：自上而下排版。节点从上到下排列，x 居中（约 3.0），节点间距 1.2–2.0 英寸；同一层的分支节点左右排开（±1.5 英寸）；尺寸默认 1.5×0.75（diamond 1.5×1.0）
3. **边映射**：`A --> B` → `{from, to}`；`-->|标签|` → 带 label；`-.->`、`==>` 也统一为普通边
4. **样式推断**：决策节点（diamond）默认 `fill:#FFF2CC stroke:#D6B656`；普通节点白底黑框；`-->|是/成功|` 绿色 `#D5E8D4/#82B366`，`-->|否/失败|` 红色 `#F8CECC/#B85450`
5. **子图（subgraph）**：不支持泳道，展开为普通节点即可
6. **循环回边**：目标在上方时自动取 `fromSide/top → toSide/bottom` 相反方向即可，或显式指定
7. **方向**：`graph TD` 按自上而下布局；`graph LR` 改自左而右（层级按 x 递增、分支上下排开，y 保持居中对称）
8. **折返边/跨图长连线**：蛇形或跳回式布局会产生穿节点的斜线——用 `points` 给边加正交路径点（先水平进空隙列 → 竖直 → 再水平），或用 `fromSide/toSide` 指定侧面；`verify_layout.py` 能查出这类问题
9. **中文**：所有文本原样保留；`fontFamily` 默认 `Microsoft YaHei`

## 验证

**结构验证**（快，无浏览器）：
```bash
python "<skill-dir>\scripts\vsdx_gen.py" "<input.json>" "<output.vsdx>"
```

生成器在写入最终路径前会执行输入校验、ZIP/XML 结构校验和边端点语义校验；失败时不替换已有目标文件。

**导入验证**（真实 draw.io UI）：
`<skill-dir>\scripts\test_import.py` 用 Playwright + headless Firefox：导入 VSDX → 导出回 XML，
校验 `mxGraphModel` 以及每条边的 `source`/`target`；传入任一 `--expect-*` 选项时还会校验
相应的精确节点或边数量。前置依赖：本地 draw.io webapp
服务、Playwright 和 Firefox 浏览器。

首次使用时先安装可选依赖：

```powershell
python -m pip install playwright
python -m playwright install firefox
```

在终端 A 启动服务并保持运行：

```powershell
Set-Location "<drawio-webapp-dir>"
python -m http.server 8080 --bind 127.0.0.1
```

在终端 B 执行导入：

```powershell
python "<skill-dir>\scripts\test_import.py" "<output.vsdx>" "<result.drawio>" --expect-nodes 2 --expect-edges 1
```

导入工具默认访问 `http://127.0.0.1:8080`，并把截图写到输出 XML 同目录；可用
`--url`、`--timeout`、`--screenshot` 覆盖。

Windows 上若 Playwright 报 `Browser.new_page: Cannot read properties of undefined
(reading '_page')`（Firefox 标签子进程启动失败），工具会自动重试两次，并在最后一次
给 Firefox 进程设置 `MOZ_DISABLE_CONTENT_SANDBOX=1`；无关错误不会重试。

**批量导入验证**：
```powershell
python "<skill-dir>\scripts\batch_import.py" "<one.vsdx>" "<two.vsdx>" --output-dir "<result-dir>"
```

**布局验证**（在导入验证成功得到 `result.drawio` 后执行；几何级，推荐给复杂图）：
`<skill-dir>\scripts\verify_layout.py <imported.drawio>` 使用 ElementTree 检查：节点两两不重叠、边线段不穿过
其他节点、边未绑定、边标签中心不落在节点框内，并检查节点/边/标签中心是否落在页面内。
传入 `--expect-page-width-in W --expect-page-height-in H`（默认严格匹配）时按源 JSON 页面尺寸校验；
显式加 `--allow-tiled-paper` 才允许 draw.io 默认纸张与源尺寸不一致（边界仍按源尺寸检查）。
边界接触按 0.5px epsilon 处理，输出每个节点的英寸坐标表。
```bash
python "<skill-dir>\scripts\test_import.py" "<output.vsdx>" "<result.drawio>"
python "<skill-dir>\scripts\verify_layout.py" "<result.drawio>" --expect-nodes 2 --expect-edges 1 --expect-page-width-in 8.5 --expect-page-height-in 11
```

**Visio 桌面验收**（本机装有 Visio 2016 时执行；无头 COM，打开后不保存）：
```powershell
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File "<skill-dir>\scripts\run_visio_acceptance.ps1" -VsdxPath "<output.vsdx>" -ExpectedPages 1 -ExpectedShapes 39
```
脚本输出一行 JSON：`exit_code` 0=通过（打开、页数/形状数一致、关闭退出、源 SHA-256 未变），
1=兼容性失败，2=参数/无 COM/非 STA 环境失败。门禁直接打开原件而不是副本：本机 Visio 2016
打开字节级副本会卡死（与目录和打开标志无关），原件则立即打开，故用前后哈希保证源文件未改动。

**交付前检查清单**：
1. `vsdx_gen.py` 输出 `structure OK`（含大小写几何单元格防回归检查）
2. 若执行 draw.io 集成验证：先确认 `test_import.py` 成功，再运行 `verify_layout.py` 并确认零问题（重叠/穿节点/标签遮挡）；未执行时明确标注未验证
3. （若环境有 Visio）运行 `run_visio_acceptance.ps1` 并确认 `exit_code` 为 0；退出码 2 时
   必须如实标注“环境不可用”，不得当作兼容性通过。当前技能已用 Visio 2016 桌面版 COM
   打开验证（1 页/39 形状）；Office 365 网页上传不在验收范围内

## 关键实现细节与坑（维护时必读）

1. **document.xml 不能含 `<Pages>` 段**：draw.io 导入器的 `importNodes()` 用活 NodeList（`getElementsByTagName("Rel")`）遍历 document.xml，有 `Pages > Page > Rel` 会反复匹配自己追加的节点 → 无限循环卡死。页面内容靠 `pages.xml → pages.xml.rels → page1.xml` 链发现（`initPages → parseNodes → resolveRel`）
2. **FillForegndTrans 存 0–1 分数**：导入器算 `opacity = 100 - trans×100`；要 50% 透明度就写 `0.5`
3. **Char.Style 位标志**：1=粗体，2=斜体，4=下划线，可组合
4. **颜色表**：document.xml `<Colors>` 的 ColorEntry（IX 0–55 是 Visio 默认调色板，自定义色从 56 起追加）
5. **Y 翻转**：draw.io 导入时 `y = 页面高 - y`，再用 101.6（英寸→像素）缩放——坐标契约是英寸 Y 向上的原因
6. **边=1D 形状**：靠 `BeginX/BeginY/EndX/EndY` 单元格识别；边标签是 Shape 的 `<Text>` 子元素
7. **命名空间**：ElementTree 的 `register_namespace('', ...)` 多个 URI 时会互相覆盖（后注册的删除先注册的），所以统一用 `_serialize()` 里正则把 ET 自动生成的 `nsN:` 前缀改写为默认命名空间
8. **ArcTo 圆弧方向**：在本技能的 Y 向上坐标中，正 `A` 弓高凸向行进方向右侧；圆柱上下盖的弦线必须内缩，弧线才不会越界
9. **几何行单元格名必须大写**（X/Y/A/B/C/D/E）：draw.io 导入器的 RowFactory 用大小写敏感的 switch 匹配，小写名导致整行数值丢失、形状塌缩成竖线（x 全部为 0）——本项目最严重的坑，`validate()` 已加防回归检查
10. **内置形状行数据是点元组列表** `[(x, y)]`，`_geom_section` 会摊平后与 `_ROW_CELLS` 对齐；直接 zip 会把整个元组序列化成 `V="(x, y)"`
11. **必须输出 LocPinX/LocPinY 单元格**（= w/2, h/2）：draw.io 导入器用 `x = PinX - LocPinX`、`y = pageH - (PinY + h - LocPinY)` 把 Pin（中心）换算成左上角。缺失时按 0 处理 → 形状整体偏右 w/2、偏上 h/2
12. **验证脚本陷阱**：不能用正则从 mxCell 文本中提取坐标；`verify_layout.py` 使用 ElementTree，只从 `<mxGeometry>` 及结构化点元素读取
13. **`visio/windows.xml` 是 Microsoft Visio 的必需部件**：缺失时 Visio 报“某些部分丢失或无效”。
    生成器必须输出该部件、在 `[Content_Types].xml` 声明 `application/vnd.ms-visio.windows+xml`，
    并在 `visio/_rels/document.xml.rels` 增加 rId2（类型 .../relationships/windows）。参考项目
    IoTServ/visioeditor 的最小结构漏掉此部件，其自产文件同样打不开；`validate()` 已加防回归检查
14. **连接线文本块不能写零宽**：`TxtWidth/TxtHeight=0` 会让 Visio 2016 把每个字符换行成垂直堆叠
    （看起来像竖排文字，切换文字方向又变成镜像横排）。必须仿照官方 Dynamic Connector 写
    `TxtWidth=TEXTWIDTH(TheText)`、`TxtHeight=TEXTHEIGHT(TheText,TxtWidth)`、居中 LocPin
    （`TxtLocPinX/Y=Txt*0.5`）与 `TxtAngle=0`；draw.io 只读 V 属性，所以 V 要写合理估算值。
    生成器的 `_estimate_label_width()` 与相关测试已锁定该契约
15. **节点文本块要显式钉在图形边界内**：节点必须写 `TxtPinX/Y=Width*0.5/Height*0.5`、
    `TxtWidth/Height=Width*1/Height*1`、居中 LocPin 与 `TxtAngle=0`，否则 Visio 自动适配
    文本块时可能让文字脱出图形、看起来像独立文本框，导致图形不可选中缩放。F 公式必须
    恰好是这四组 `Width*`/`Height*` 组合：draw.io 的 `isDisplacedLabel()` 据此判断是否把
    标签拆成独立子形状（写别的公式就会在 draw.io 里出现独立文本框）
16. **节点几何要用 Width/Height 公式并补齐原生单元格**：Visio 2016 对纯硬编码几何的图形
    可能不显示盒状缩放手柄（拖动只改文本框）。内置形状的 X/Y 坐标必须映射为
    `Width*0 / Width*0.5 / Width*1`（Y 对应 `Height*`），并写出 `Angle/FlipX/FlipY/
    ResizeMode` 与公式化 `LocPinX/Y`，与 Visio 自绘矩形结构一致；draw.io 几何行只读 V
    属性，加 F 公式不影响导入

## 使用示例

```bash
# 生成（生成前自动做输入校验，错误信息为中文）
python "<skill-dir>\scripts\vsdx_gen.py" "<skill-dir>\examples\login-flow.json" "<output.vsdx>"

# 验证（本地 draw.io 服务运行时；服务未启动会给出明确提示）
python "<skill-dir>\scripts\test_import.py" "<output.vsdx>" "<result.drawio>"
```

## 错误处理

- **输入校验**：生成前自动执行 `validate_input()`——未知形状/箭头/字体、悬空边引用、
  重复 id、缺字段、非法颜色、坏几何等都会以中文错误信息失败退出（exit 2），
  绝不静默产出错误文件
- **结构校验**：`validate()` 检查 zip 完整性、XML 格式、rels 目标存在、部件齐全、
  页面关系类型、PageSheet 尺寸、形状 ID、连接端点、颜色单元格（必须 `#RRGGBB`）、
  连接线 1D 属性和 `<Connects>` 语义
- **生成器退出码**：0=成功，1=生成包结构/语义校验失败，2=输入/参数/文件错误
- **布局验证退出码**：0=通过，1=发现布局问题，2=参数/文件/XML 输入错误
- **导入验证退出码**：0=通过，1=服务/浏览器/导入/导出/验证失败，2=参数/依赖/文件错误
- **批量导入退出码**：任一单文件失败时最终返回 1；参数或启动阶段文件错误返回 2

## 已知边界

- 只生成和验证单页 VSDX；泳道、容器、主题和 Visio master 扩展不在范围内
- 图片/手绘需要模型人工重建 JSON，不是脚本直接 OCR、识图或矢量化；契约也不包含位图/图标嵌入字段，无法重画的内容必须省略并说明
- 已实测：Visio 2016 桌面版（64 位）可打开生成文件（1 页/39 形状）；draw.io 31.1.5 四案例导入通过。
  未覆盖：其他 Visio 版本、Office 365 网页上传（账号/许可环境为已知排除项）、其他 draw.io 版本；
  未实测时不得扩大兼容性结论
