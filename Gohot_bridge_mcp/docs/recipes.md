# 实战配方 / Recipes

每个配方都是可以直接粘给 AI 的一句话，后面列出它应该调用的工具链，方便你判断 AI 是否走对了路。

## 1. 从零搭一个可运行的 2D 角色

> 新建 `res://scenes/player.tscn`，根节点 CharacterBody2D 命名 Player，加 Sprite2D 和
> CollisionShape2D（胶囊 16×32），挂一个 `player.gd`，实现左右移动和跳跃，速度 300，
> 跳跃初速 -450，最后保存并运行给我看日志。

工具链：`godot_create_scene` → `godot_add_node` ×3 → `godot_set_properties` →
`godot_create_script` → `godot_attach_script` → `godot_save_scene` → `godot_run_scene` →
`godot_get_game_logs`。

要点：让 AI 先 `godot_list_properties` 看 `CollisionShape2D.shape` 的当前类型，再写
`{"__gd":"Object","class":"CapsuleShape2D","path":""}` 这类引用值；直接写裸字符串会得到 1004。

## 2. 批量铺 50 个道具，且一把撤销

> 在当前场景的 Props 节点下创建 50 个 Sprite2D，命名 Coin_01…Coin_50，按 10×5 网格摆放，
> 间距 64，全部加到 group "coins"。做完先别保存。

工具链：`godot_find_nodes` 确认 `Props` 存在 → `godot_add_node` ×50（每次一个 undo step）。

注意：当前版本没有「批量单 action」工具，50 次调用 = 50 次 `Ctrl+Z`。如果不满意，
直接让 AI 调 `godot_find_nodes(group="coins")` 再逐个 `godot_delete_node`，或者用
`godot_execute_script` 一次性清理（该操作不可撤销）。

## 3. 让 AI 自己定位并修复运行时报错

> 运行当前场景，读到第一条报错后，定位对应脚本和行号，修掉它，再运行一次确认没有新报错。

工具链：`godot_run_scene` → `godot_get_game_logs(level="error")` →
`godot_read_script` / `godot_search_files` → `godot_check_script` → `godot_write_script` →
`godot_stop_scene` → `godot_run_scene`。

要点：`godot_write_script` 会触发编辑器重载；改完先 `godot_check_script` 再写，能避免把
工程改成编译不过的状态。

## 4. UI 改完截图验收

> 把 HUD 的血条改成红色圆角、宽度 240，锚点右上角，边距 24，然后运行游戏截图给我。

工具链：`godot_find_nodes(type="ProgressBar")` → `godot_set_properties` →
`godot_run_scene` → `godot_take_screenshot(max_size=1280)`。

要点：截图必须在有渲染的环境运行；headless 会返回 `1008`，提示「no frames」。

## 5. 查 Godot API 再动手

> CharacterBody2D 的 move_and_slide 在 4.7 里签名是什么？floor_snap_length 默认值多少？

工具链：`godot_get_class_info("CharacterBody2D")` → 读 `methods` / `properties`。

比让模型凭记忆写 API 更可靠，尤其是 4.5→4.7 之间改过默认值的属性。

## 6. 安全地改项目设置

> 把物理引擎切到 Jolt，重力改成 980，改完告诉我改了哪些 key。

工具链：`godot_get_project_settings(prefix="physics")` → `godot_set_project_setting`。

要点：写设置会 `ProjectSettings.save()`，属于全局副作用，建议先读再写；这类操作不进 undo 栈。

## 7. 多客户端协作

> （两个人同时连着同一个编辑器）

- 每个客户端独立连接、独立 `client_id`，日志里能区分。
- 但 undo 历史是编辑器全局的：A 的撤销可能撤掉 B 刚做的操作。多人同时改同一场景时，
  建议约定「一人写、一人读」。
