# HanLin-Notes UI 重构交接说明

> 这份文档写给接手 UI 重构的 Codex。它自包含：读完就能动手，不需要翻聊天记录。

## 1. 项目是什么

**HanLin-Notes** 是一个 Windows 本地笔记软件，PyQt5 实现，单机运行、不联网、不需要账号。

它的**数据格式与交互思路来自 Android 应用「Timo笔记」**（包名 `net.idik.timo`）：本项目的导入器能直接读 Timo 导出的 ZIP（`data/documents.json` + `content/*.md` + `accounts.csv` + `attachments/`），也能把本地库再导出成同样的结构，方便导回手机。

但**它是独立软件，不是 Timo 的移植版**：没有账号、没有云同步、没有内购，界面是桌面端的三栏布局，代码全部重写。README 里要写清楚这层关系。

## 2. 技术栈与目录

- Python 3.12 + PyQt5（不用 Qt6，环境里只有 PyQt5）
- 打包：PyInstaller onedir 主程序与 Inno Setup 安装向导。 [系列设计规范](HANLIN-SERIES.md)
- 仓库：`https://github.com/TangSong777/HanLin-Notes`（原 `Timo-For-Windows`）

```text
timodesk/
├── store.py        数据层：library.json + notes/<id>.md + attachments/
├── importer.py     导入 Timo ZIP / 文件夹（支持合并、替换、自动去重）
├── exporter.py     导出 Timo ZIP / Markdown 文件夹 / 单条 Markdown
├── backup.py       自动备份与恢复
├── settings.py     本地设置（窗口位置、排序、主题模式）
├── render.py       Markdown → HTML（预览用）
├── app.py          启动、欢迎页、异常兜底
└── ui/
    ├── theme.py    配色 + 全量 QSS（**UI 重构主要改这里**）
    ├── widgets.py  列表卡片、主题项、滚动条（自绘）
    ├── title_bar.py 无边框窗口标题栏
    ├── dialogs.py  自绘对话框（消息/进度/提醒/备份/关于/设置/去重…）
    ├── editor.py   编辑器（六种笔记类型各一页）
    └── main_window.py 三栏主窗口
tests/              59 个测试脚本，`python tests/run_all.py` 一次跑完
tools/              打包、发布、截图脚本
```

## 3. 现有功能（重构时不能丢）

| 类别 | 内容 |
| --- | --- |
| 笔记类型 | 便签、文章、图库、账号、链接、日子，各有专属编辑页与展示样式 |
| 编辑 | Markdown 编辑 + 实时预览、格式工具栏、粘贴/拖入图片、待办方框可点 |
| 组织 | 主题（增删改、拖拽排序、锁定、回收站）、置顶、8 档颜色标记、跨主题拖拽 |
| 查找 | 全库搜索、卡片与预览高亮命中词、三种排序（更新/创建/标题） |
| 导入导出 | Timo ZIP 往返、合并/替换、导入时自动去重、Markdown 文件夹、单条 md、PDF |
| 数据安全 | 自动备份（保留 7 份）、坏档容错、写盘失败提示、数据完整性检查 |
| 其他 | 提醒、深色模式、系统托盘、窗口位置记忆、偏好设置、未引用附件清理 |

## 4. 重构目标

把现在的**黑白灰**界面换成 **HanLin 系列**的暖色系，并重做图标。功能、数据格式、测试全部保持不变。

### 4.1 配色（来自 HanLin-ToDo / HanLin-IME，务必用这套）

| 用途 | 色值 | 说明 |
| --- | --- | --- |
| 画布 / 窗口底 | `#F4EFEA` | 比卡片深一档的暖灰 |
| 卡片 / 面板 | `#FFFCF9` | 暖白，主表面色 |
| 卡片次级 | `#FBF6F1` | 卡片内的小块 |
| 主文字 | `#292424` | 近黑，带一点暖 |
| 次文字 | `#736460` | 暖灰 |
| 弱文字 | `#8F7C76` | 更浅的暖灰 |
| 强调 / 主按钮 | `#E48887` | 珊瑚红（HanLin 的 Accent） |
| 强调深 | `#93444D` | 酒红，用于 hover / 选中文字 |
| 边框 | `#EADFD7` | 暖色描边 |
| 边框强 | `#DCCCC2` | 分隔线、卡片边 |
| 危险 | `#B4302A` | 删除类操作 |

**规则**：
- 珊瑚红是**点缀**，不是大面积底色。主按钮、选中指示、图标外框、链接用它可以；整块面板不要刷成珊瑚红。
- 卡片之间靠**暖白 + 极淡边框 + 柔和投影**分层，不要用重色块。
- 深色模式要同步做一套：画布 `#1A1614`、卡片 `#221D1A`、主文字 `#F2EAE4`、次文字 `#B5A49C`、强调仍用 `#E48887`（深色下可提亮到 `#F09A99`）、边框 `#332B27`。

### 4.2 图标

- 中间的字：**「记」**
- 字体：**造字工房悦黑常规体**，本机路径
  `C:\Users\<用户名>\AppData\Local\Microsoft\Windows\Fonts\ZaoZiGongFangYueHeiChangGuiTi-1.otf`
- 外框：**珊瑚红圆角矩形描边**，色值 `#CC575C`（比 Accent 深一点，小尺寸下更清楚）
- 底色：`#FFFCFA`
- 字形色：`#292424`

**直接复用现成脚本**：`D:\WorkSpace\HanLin-ToDo\tools\New-TaskbarIcon.py` 就是干这个的（它画的是「办」）。复制过来，把 `glyph = "办"` 改成 `"记"`，其余参数（圆角比例 0.115、描边宽度 0.088、字号比例 0.71/0.65、小尺寸加粗）原样保留，输出多尺寸 ICO（16/20/24/32/40/48/64/96/128/256）。

产物放 `assets/`：
- `hanlin-notes.ico`（窗口/任务栏/托盘）
- `hanlin-notes.png`（256px，README 用）

### 4.3 界面语言

参考 HanLin-ToDo 的观感：**暖白纸面、细边框、圆角克制（8–12px）、留白充足、字号层级清楚**。不要玻璃拟态、不要渐变、不要彩色阴影。

## 5. 约束

1. **不要改数据层**（`store.py` / `importer.py` / `exporter.py` 的对外行为），数据格式必须与 Timo ZIP 保持兼容。
2. **不要删功能**。重构前后功能表要一一对应。
3. **测试必须全绿**：`python tests/run_all.py`，59 个脚本。UI 改动若导致断言里的颜色值变化，改断言而不是改行为。
4. **分支纪律**：日常提交只推 `snapshot` 分支；`master` 只在发版时 `git merge --ff-only snapshot`。发版流程见 `C:\Users\<用户名>\.dsh\skills\timo-release-discipline\SKILL.md`（仓库改名后 skill 里的坐标要同步更新）。
5. **打包**：`python tools/make-portable.py` 生成 `dist/HanLin-Notes-Setup-<版本>.exe`。
6. 改完 UI 要**截图核对**：`python tools/shot-docs.py` 生成 `docs/screenshot-main.png` 等，浅色与深色各一张。

## 6. 验收

- [ ] 浅色 / 深色两套配色都按 4.1 的表实现，切换后所有控件（含对话框、菜单、滚动条）都跟着变
- [ ] 图标是「记」+ 珊瑚红外框，任务栏、托盘、窗口标题栏、安装包 exe 四处都换掉
- [ ] `python tests/run_all.py` 59/59 通过
- [ ] `python tools/make-portable.py` 打包成功，exe 启动正常
- [ ] README 更新：说明基于 Timo 的数据格式与思路、但为独立软件；截图换成新配色
- [ ] 版本号按 SemVer 判断（纯 UI 改版属于 MINOR）

## 7. 参考

- HanLin-ToDo（配色与图标脚本的来源）：`D:\WorkSpace\HanLin-ToDo\`
- HanLin-IME：`https://github.com/TangSong777/HanLin-IME`
- 本项目仓库：`https://github.com/TangSong777/HanLin-Notes`
- Timo 官方导出格式说明：`docs/` 下的导入导出文档
