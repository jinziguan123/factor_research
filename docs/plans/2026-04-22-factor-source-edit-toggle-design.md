# 因子源码只读/编辑切换 + 自动备份 设计文档

> 日期：2026-04-22
> 状态：已与用户确认，等待进入实施
> 作者：Claude（与用户 jinziguan 共同设计）

## 0. 背景与动机

用户场景：**看业务因子源码是刚需，偶尔也想改**。

当前设计只允许 `backend/factors/llm_generated/` 下的因子通过前端编辑，`momentum/reversal/oscillator/…` 等业务目录永远不给编辑入口，后端 `PUT` / `DELETE` 还会用 403 兜底。这套边界的动机是**保护业务因子不被误覆盖**（因为热加载 = 物理覆写 git working tree，没有 undo）。

但代价是：用户现在想看一眼 `kdj_cross.py` 的实现都要跳到 IDE，体验割裂。

**本次目标**：在**保护代价尽量不升**的前提下放开"看"，同时允许"小心地改"。

## 1. 需求范围与非需求

**In scope**：
- 所有因子都能从前端查看源码（readonly）
- 所有因子都能从前端编辑源码（要手动切换到编辑态）
- 业务因子的编辑态有**更醒目的红色警示**
- `PUT` 前自动备份旧文件到 `.backup/`，保留最近 5 份
- 保存成功后前端 toast 附带展示备份路径

**Out of scope**（本次不做）：
- **删除业务因子**：删除风险远大于修改（文件物理删除 + 从 registry 摘除），本次保持仅 llm_generated 可删
- **从备份列表恢复**的前端 UI：YAGNI，真翻车用后端文件系统手动恢复
- **修改历史 / 版本对比**：现有只有 `version` 自增字段，没 diff，本次不引入

## 2. 前端 UI 交互流

### 2.1 入口按钮变化（`FactorDetail.vue` 右上角）

- **移除**：原有 `v-if="factor?.editable"` 下的「编辑源码」按钮
- **新增**：「源码」按钮对**所有因子**可见（无条件）
- **保留**：`editable === true` 下的「删除」按钮（删除不放开）

### 2.2 弹窗内部状态机

```
[打开] → ReadOnly 态
    ├─ 编辑器 readonly=true
    ├─ 标题："查看源码：<display_name>"
    ├─ 无 alert（纯查看无风险）
    └─ 按钮组：[关闭]  [编辑]

ReadOnly --[点击"编辑"]--> Editing 态
    ├─ 编辑器 readonly=false
    ├─ 标题："编辑源码：<display_name>"
    ├─ 顶部 alert 分级（见 §6 警示文案）：
    │    - 业务因子（editable=false）：红色 alert
    │    - llm_generated（editable=true）：黄色 alert
    └─ 按钮组：[取消编辑]  [保存]

Editing --[点击"取消编辑"]-->
    ├─ 有未保存变更 → 二次确认 dialog "放弃修改？"
    └─ 无变更 → 直接回 ReadOnly 态

Editing --[点击"保存"]-->
    ├─ 成功 → 刷新编辑器内容为新源码 → 自动回到 ReadOnly 态
    │         toast: "保存成功：<display_name>（vN）\n已备份至 <backup_path>"
    └─ 失败 → 顶部红色 error alert，停留在 Editing 态

[任意态] --[点击"关闭"或关闭 modal]--> 弹窗销毁
```

### 2.3 关键交互设计

- **默认只读**是软保护，防误触
- **保存成功后自动切回 ReadOnly**：防止用户保存后继续无意识修改
- **取消编辑时有未保存变更要二次确认**：防误关丢失修改

## 3. 后端权限边界调整（`backend/api/routers/factors.py`）

### 3.1 新增 helper：`_require_factor_path`

```python
_FACTORS_ROOT = (Path(__file__).resolve().parent.parent.parent / "factors").resolve()

def _require_factor_path(p: Path) -> None:
    """仅要求路径落在 backend/factors/ 下且是 .py。防御性边界。"""
    if not p.resolve().is_relative_to(_FACTORS_ROOT):
        raise HTTPException(
            status_code=403,
            detail="源码文件必须位于 backend/factors/ 下",
        )
    if p.suffix != ".py":
        raise HTTPException(
            status_code=400,
            detail="源码文件必须是 .py",
        )
```

### 3.2 改动：`PUT` 拆 403，`DELETE` 保持

- **`PUT /api/factors/{id}/code`**：从 `_require_editable_path(p)` **改调** `_require_factor_path(p)`。momentum/reversal/oscillator/llm_generated 全部放开写。
- **`DELETE /api/factors/{id}`**：**继续调** `_require_editable_path(p)`，仅 llm_generated 可删（本次不放开删除）。

**`_require_editable_path` 不删除**：`DELETE` 仍需要它；且保留这个函数意图明确——"判断是否在沙盒目录"。

## 4. 自动备份机制

### 4.1 位置与命名

- 目录：`backend/factors/.backup/`
- 文件名：`<factor_id>.<yyyyMMdd-HHmmss>.py`（例：`kdj_cross.20260422-153012.py`）
- 时间戳用本地时间，格式化保证字典序 = 时间序（方便 `sorted(glob)` 找最新）

### 4.2 触发时机

`PUT /api/factors/{id}/code` 在 AST 白名单校验通过、**写入新内容前**执行。

**如果目标文件不存在**（新因子走 `POST` 落盘，不经过 `PUT`；但防御性考虑：llm_generated 下新因子首次 `PUT` 也可能发生）→ 返回 `None`，不生成备份。

### 4.3 保留策略

对每个 `factor_id` 只保留最近 **5 份**备份；旧的用 `sorted(glob(f"{factor_id}.*.py"))[:-5]` 批量删除。

选 5 的理由：够用于撤销近期误操作；再多让用户用 git。

### 4.4 实现草图

```python
def _save_backup(p: Path, factor_id: str) -> Path | None:
    """覆写前备份旧文件。返回备份路径；旧文件不存在时返回 None。"""
    if not p.exists():
        return None
    backup_dir = _FACTORS_ROOT / ".backup"
    backup_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = backup_dir / f"{factor_id}.{ts}.py"
    shutil.copy2(p, dest)  # 保留 mtime
    # 只保留最近 5 份
    olds = sorted(backup_dir.glob(f"{factor_id}.*.py"))
    for stale in olds[:-5]:
        stale.unlink(missing_ok=True)
    return dest
```

### 4.5 备份范围决策

**对所有 `PUT` 都做备份**，包括 llm_generated 下的因子。

理由：
- 保守一致，边界简单
- llm_generated 也是用户手工作品，误覆盖同样让人痛苦
- 磁盘占用可忽略（每因子 ≤ 5 × ~5KB = 25KB）

### 4.6 `.gitignore` 追加

```
backend/factors/.backup/
```

## 5. API 契约变化

**无破坏性变化**。

### 5.1 `GET /api/factors/{id}` 响应

`editable` 字段**语义不变**（= "在 `llm_generated/` 下"）。前端用它决定：
- 「删除」按钮是否显示
- Editing 态 alert 是黄色还是红色

### 5.2 `GET /api/factors/{id}/code` 响应

`editable` 字段语义不变。

### 5.3 `PUT /api/factors/{id}/code` 响应新增字段

```json
{
  "factor_id": "kdj_cross",
  "display_name": "KDJ 金叉强度",
  "version": 2,
  "backup_path": "backend/factors/.backup/kdj_cross.20260422-153012.py"
}
```

新因子首次 `PUT`（目标文件不存在）时 `backup_path: null`。

### 5.4 `DELETE /api/factors/{id}`

行为完全不变。

### 5.5 前端 TypeScript 类型（`frontend/src/api/factors.ts`）

`UpdateFactorCodeOut` 接口追加：
```ts
backup_path: string | null
```

## 6. 警示文案

### 6.1 ReadOnly 态

无 alert。

### 6.2 Editing 态 · llm_generated 因子（黄色 / `type="warning"`）

```
直接覆写 backend/factors/llm_generated/<factor_id>.py。
保存前后端做 AST 白名单校验 + 类属性 factor_id 必须等于 <factor_id>；
保存成功后自动备份旧版本到 .backup/（保留最近 5 份）、热加载生效。
```

### 6.3 Editing 态 · 业务因子（红色 / `type="error"`）

```
⚠️ 这是业务因子（位于 backend/factors/<category>/<factor_id>.py），
保存会直接覆写 git working tree 里的源码文件。

建议先 git commit 当前状态再修改，以便出错时能用 git checkout 回滚。
后端会在覆写前自动备份到 .backup/<factor_id>.<timestamp>.py（保留最近 5 份），
但这只是手滑兜底，不是正式版本管理手段。
```

### 6.4 保存成功 toast

现有（`FactorDetail.vue:95`）：
```ts
message.success(`保存成功：${res.display_name}（v${res.version}）`)
```

改为：
```ts
if (res.backup_path) {
  message.success(
    `保存成功：${res.display_name}（v${res.version}）\n已备份至 ${res.backup_path}`
  )
} else {
  message.success(`保存成功：${res.display_name}（v${res.version}）`)
}
```

## 7. 测试策略

### 7.1 后端 pytest 扩展（`backend/tests/test_api_factor_crud.py`）

| 测试名 | 验证 |
|---|---|
| `test_put_business_factor_succeeds` | PUT 一个业务因子（如 momentum/），返回 200 + `backup_path` 非 null |
| `test_put_business_factor_creates_backup` | 备份文件真实落在 `.backup/<factor_id>.*.py` |
| `test_put_business_factor_keeps_only_5_backups` | 连续 PUT 6 次，`.backup/` 下只剩 5 份 |
| `test_delete_business_factor_still_403` | DELETE 业务因子仍 403（边界不变，防回归） |
| `test_put_new_llm_factor_no_backup` | llm_generated 新因子首次 PUT，`backup_path=null` |
| `test_put_existing_llm_factor_has_backup` | llm_generated 已有因子再 PUT，有备份（备份对所有 PUT 生效） |
| `test_put_path_outside_factors_root_rejected` | 通过构造恶意 factor_id 让路径逃出 `backend/factors/`（理论上不可能，防御性测试） |

**保留**现有 `test_put_llm_generated_factor` 等测试确保向前兼容。

### 7.2 前端不新增测试

项目前端本身没有 E2E / 组件测试基础设施；状态机逻辑变动轻量（readonly ref 切换），手测即可覆盖。

### 7.3 手测场景清单

在实施 PR 提交前跑一遍：
- [ ] 查看业务因子（KDJ/momentum/reversal）源码 → readonly 编辑器正常显示
- [ ] 编辑业务因子 → 红色 alert 显示 → 保存 → toast 显示备份路径 → `.backup/` 下文件存在
- [ ] 编辑 llm_generated 因子 → 黄色 alert → 保存成功
- [ ] 连续编辑同一业务因子 6 次 → `.backup/` 下只剩 5 份
- [ ] 编辑态未保存时点"取消编辑" → 弹出二次确认 dialog
- [ ] 尝试删除业务因子（通过 API 直接 PUT `DELETE`）→ 仍返回 403
- [ ] 保存失败（故意写坏语法）→ 红色 error alert，编辑器停留在编辑态

## 8. 实施顺序

1. **后端**
   - 新增 `_require_factor_path` helper + 单元测试
   - 新增 `_save_backup` helper + 单元测试
   - 改 `PUT /api/factors/{id}/code`：调新 helper + 调备份 + 返回 `backup_path`
   - `DELETE` 不动，仅加回归测试
   - `.gitignore` 追加 `backend/factors/.backup/`
2. **前端**
   - 更新 `frontend/src/api/factors.ts` 中 `UpdateFactorCodeOut` 类型追加 `backup_path`
   - 改造 `FactorDetail.vue`：
     - 按钮逻辑：源码按钮无条件显示，移除旧"编辑源码"按钮
     - Modal 内状态机：`readonly` ref + "编辑"/"取消编辑"/"保存"按钮组
     - 警示 alert 分级（基于 `factor.editable`）
     - 取消编辑的二次确认 dialog
     - 保存成功 toast 带 `backup_path`
3. **手测** 按 §7.3 清单跑一遍

## 9. 风险与已知局限

### 9.1 git working tree 脏

业务因子被前端覆写后 `git status` 会显示文件改动。用户需要自己 decide：commit、revert、还是 stash。红色 alert 文案已显式提醒这一点。

### 9.2 备份目录可能变大

每个因子最多 5 份 × ~5KB ≈ 25KB；库里因子规模 ~50 → 最坏情况 1.25MB。可忽略。如果将来因子数爆炸，可以加 cron 清理 30 天前的备份。

### 9.3 并发保存

没有做锁。同一因子被两个浏览器同时 `PUT` 时，后写的覆盖先写的，但两份备份都在 `.backup/`。对单人研究平台来说可接受。

### 9.4 热加载延迟

保存成功后依赖 watchdog 扫描 + 进程池重置。业务因子覆写后新评估/回测立即用新代码的前提是热加载路径对**整个 `backend/factors/`** 而不仅是 `llm_generated/`。需要在实施时验证（如果热加载只监听 `llm_generated/`，得一起扩展）。

## 10. 后续扩展方向（不在本次交付中）

- 从备份列表恢复的前端 UI（一键 revert 到任意历史版本）
- diff 视图：当前代码 vs 最近一次备份的 diff
- 删除业务因子的放开（需要配套更强的确认流程：输入因子名二次确认）
- 版本历史上推到 `version` 字段管理（目前只是自增计数）
