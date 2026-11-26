# 待发布变更

> 每次改动（代码、配置、文档、脚本）完成后，在这里**追加**一条，5 行以内。
> 发版时整本读完，逐条核对是否已写进 `CHANGELOG.md`，然后清空本文件。
> 只写事实，不写推理过程。历史条目不要回头修改。

当前稳定版本：**1.0.0**

## 2026-11-26 — 打包后自动同步到桌面

- `tools/make-portable.py` 打完包自动把程序文件覆盖到桌面上的 `HanLin-Notes`（`Data/` 与 `backup/` 一律不动），路径从注册表的桌面位置读，可用 `--desktop` 覆盖、`--no-sync` 关闭。
- 程序正在运行时只提示「先退出程序」，不中断退出码。

## 2026-11-26 — 动效统一

- 新增 `timodesk/ui/motion.py`：时长常量（FAST 130 / BASE 190 / SLOW 240）、`enabled()`（读 Windows「显示动画」开关）、`mix()`、`fade_in()`、`HoverTracker`。
- 笔记卡片与主题条目悬停改为渐变（原来瞬间变色）：底色、边框、投影随进度过渡；列表只追踪当前一行，滚动时不增加开销。
- 对话框弹出时淡入并上浮 8px。
- 选中一条笔记时列表**平滑滚动**过去（原来瞬移）。
- 编辑区切换到另一种笔记类型时，内容淡入一次。
- 页面切换、滚动条淡出、卡片揭示、提示条的时长统一走 `motion`，系统关掉动画时全部退化为瞬时。
- 新增 `tests/test_motion.py`（17 项）；全量 62 个脚本通过。

## 2026-11-26 — 笔记历史版本

- 新增 `timodesk/history.py`：每条笔记的旧正文存到 `Data/history/<笔记 id>/<时间戳>.json`（纯 JSON，一条一版，自足可读）。
  - 自动保存只在正文真的变了时留档，留的是**改动前**那一版；90 秒内的连续保存合并成一版，避免自动保存刷满；每条最多 50 版；空白笔记不留档。
  - 笔记被彻底删除时连同历史一起清掉。
- `store.set_content()` 里留档；`delete_note(hard=True)` / `delete_notes(hard=True)` 里清理历史。
- 新增 `dialogs.HistoryDialog`（780x540）：左侧版本列表（本地时间 · 字数），右侧渲染那一版 Markdown；「恢复这一版」写回正文与字段，并且**可 Ctrl+Z 撤回**。
- 入口：笔记右键菜单与文档页「⋯」→「历史版本…」（`main_window.open_history`）。
- 卡片日期改用**创建日期**（原来用更新日期），底部仍同时显示「创建 / 更新」。
- 备份包含 `history/`，回滚时一并还原。
- 新增 `tests/test_history.py`（18 项）；全量 61 个脚本通过。

## 2026-11-26 — 侧栏改版：全部笔记只按时间、归档与回收站收进角落

- 「全部笔记」固定按最近更新倒序，排序菜单在这个视图里置灰并说明原因（进主题后仍可切换）；列表本来就只列使用中的笔记，归档笔记不再出现在这里。
- 侧栏主导航只剩「全部笔记」；「归档」「回收站」收进左下角 footer，做成两个小字入口，当前视图对应项高亮。
- 搜索只搜使用中的笔记（原来会把归档笔记也搜出来）；按 Esc 清空搜索时列表立刻回到当前视图，不再停在搜索结果上。
- `tests/test_sort.py` 改为验证「全部笔记不吃排序 / 主题视图吃排序」。
- 60/60 测试通过。

## 2026-11-26 — 修复排序 / 归档 / 回收站 / 字体的操作逻辑

- 排序：标题排序改为**中文拼音序**（借 Windows `CompareStringW`，`store.compare_zh`），原来按 Unicode 码位排，中文等于乱序；`sort_notes()` 抽出来，列表、主题、搜索结果共用。
- 归档：新建笔记跟随主题状态；`restore_note` 保留归档身份；拖到归档主题/拖回自动同步；删除主题时归档笔记也进回收站；**恢复主题后仍是归档主题**（不再一律变正常），并排到列表末尾避免 order 冲突。
- 回收站：新增 `Library.empty_trash()`；「清空回收站」一次写盘；回收站右键加「彻底删除」需二次确认；批量菜单按视图显示「取消归档 / 彻底删除」。
- 字体：默认改**宋体**；`render._css()` 用 `C['font_text']`（原来硬编码雅黑，预览不跟随）；启动时先定字体再生成 QSS；切字体沿用各控件原字号；笔记卡片列表也跟随正文字体。
- 新增 `diag.py`（排序/归档/回收站/字体四项体检，35 项全过）、`shot_default.py`；`tests/test_bulk.py` 每次重建临时库。
- 59/59 测试通过。

## 2026-11-26 — 全局撤销 / 重做（Ctrl+Z 能撤回排序、归档、回收站等操作）

- 新增 `timodesk/undo.py`：`UndoStack` + `Command`，最多 60 步；只快照受影响的笔记/主题与正文，不整库拷贝；空操作（比如对话框里点了取消）不占格子。
- `store.py` 新增 `Snapshot`、`Library.snapshot()`、`Library.restore()`；恢复时按 id 覆盖/重建/删除，正文用原子写回。
- `main_window.py` 新增 `undoable()` 上下文管理器，把切换排序、主题排序、新建/重命名/删除/拖动主题、新建笔记、置顶、归档、删除到回收站、恢复、彻底删除、复制、颜色、移动、清空回收站、整理已删除主题全部纳入撤销栈。
- 快捷键：`Ctrl+Z` 撤销、`Ctrl+Shift+Z` / `Ctrl+Y` 重做；文本框内仍由控件自己撤销文字（QShortcut 不抢编辑快捷键）。
- 数据与设置菜单顶部新增「撤销 <上一步>」「重做」，菜单弹出时刷新文案与可用状态。
- 修掉一个崩溃：早前用 `QApplication.installEventFilter` 接管快捷键，窗口销毁后 Qt 仍持有过滤器，进程退出时 `0xC0000005` 访问违例（`tests/test_window.py`、`test_view_restore.py` 显示 PASS 但退出码非 0）。改用 QShortcut。
- `tests/run_all.py` 的判定改为「非零失败数才算失败」，之前 `30 ok / 0 FAIL` 被误判成失败。
- 新增 `tests/test_undo.py`（34 项，含真实按键：编辑器里 Ctrl+Z 只撤文字、列表焦点下 Ctrl+Z 撤排序）。
- 60/60 测试通过。

## 2026-11-26 — 去掉第三方痕迹：新应用图标、文案与内部命名中性化

- 应用图标重做：`tools/make-icon.py` 生成「珊瑚红圆角外框 + 三条递减横线」的几何徽章（不再用「记」字），16/20/24 小尺寸单独优化，看不出笔画粘连；`assets/hanlin-notes.ico`、`assets/hanlin-notes.png`、`docs/icon.png` 已重出。
- 界面文案：「Timo 导出包（ZIP）…」「Timo 导出文件夹…」「Timo ZIP…」→「笔记包（ZIP）…」「笔记包（解压文件夹）…」「笔记包（ZIP）…」；欢迎页、导入/导出对话框标题与进度框同步。
- 内部命名：备份前缀 `timo-backup-` → `hanlin-backup-`（`backup.list_all()` 同时认旧前缀，历史备份仍可见）；拖拽 MIME `application/x-timo-note` → `application/x-hanlin-note`；导入临时目录前缀、导出默认文件名 `HanLin_Notes_Export_*.zip`；QSettings 组织名 `HanLin-Notes`。
- 数据目录：`%LOCALAPPDATA%\TimoDesktop` → `HanLinNotes`，首次启动若只发现旧目录会自动改名迁移；环境变量支持 `HANLIN_DATA_DIR`（旧的 `TIMO_DATA_DIR` 仍认）。
- 代码注释：把「Timo 导出的…」统一改成「外部导出格式的…」；`timo://file-store/` 这个 URI 前缀是 ZIP 格式约定，保留并加注释说明。
- 新增 `SetCurrentProcessExplicitAppUserModelID("HanLin.Notes.Desktop")`：任务栏与通知按自己的应用标识分组，不再受旧的图标缓存影响。
- README：开头改成「独立软件，只是数据格式兼容」，删掉「沿用 Timo 思路」的从属表述；操作步骤与功能表里的旧菜单名同步。
- `tests/test_drag.py` 的 MIME 断言同步；60/60 测试通过。
