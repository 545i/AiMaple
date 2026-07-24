# maple 開發日誌與架構（供接續）

手機遠端遊玩 + 自動巡邏掛機系統。**自動化違反遊戲 TOS，封號風險自負**。

## 架構
- **後端** FastAPI（`server/`），token=`***REMOVED***`（MAPLE_TOKEN）。訪客短密碼限 4/←/→。
- **輸入**：Arduino HID 鍵盤（`press`=key_down→hold~60ms→key_up，**不用 tap，遊戲會漏讀**）+ km.dll 滑鼠。
- **畫面**：WGC 視窗捕獲 maplestory.exe（只讀屏、不碰遊戲記憶體/不注入 → GameGuard 難測；行為層仍像 bot）。
- **重啟**：`restart-admin.bat`（UAC 靜默提權，載入最新碼）。改後端必重啟；改前端硬刷新。
- 主要檔案：`main.py`(端點/lifespan)、`navigator.py`(導航/巡邏)、`pathgraph.py`(路徑圖)、`mapdata.py`(座標/平台/繩索/profile)、`minimap.py`(小地圖偵測)、`idle_mode.py`(隨機AFK)、`web/index.html`(中控頁,單檔)。

## 前端結構（index.html）
儀表板 4 分頁 tab：🏠出租 / 🤖閒置 / 🗺巡邏 / 🎮遠端。共用底部=解除卡死+登出。
- 巡邏 tab：頂部=開始/停止巡邏+狀態(phase)+放置技能倒數監控；下方「巡邏設定」=方向/小地圖預覽/地圖座標/地形/平A/放置技能。
- 遙控漢堡「操作」區有「停止巡邏」。
- 浮動 toast（`toast(msg,ok)`，頂部居中，2.5s）：凡走 mapAct 或巡邏開關都彈綠(成功)/紅(失敗)。
- 按鍵一律虛擬鍵盤 `openKeyPicker` 點選（#picker，已移到 body 頂層避 stacking context 被 dashView 蓋）。
- 小地圖預覽疊加：平台=藍線、繩索=黃虛線、記錄點=橘點+青圈(攻擊範圍)、**當前導航路線=青虛線+節點圓點**(每秒讀 /nav/status 的 path)。

## 移動模型（實測標定，關鍵資產）
- **黃點靜止 0px 抖動**（40/40 全同）→ 判定要 `_settle` 等停穩再讀，移動中瞬時值不可靠。
- **二段跳**：同層水平**精確 30px**（16組±1，左右對稱）；落更低平台時抛物線飛更遠（實測 37px @ 下降 8px）。X→interval0.2→P。
- **走路脈衝**：0.05~0.08s→1px、0.10s→0~2px、0.13s→3px、0.20s→4px（小地圖解析度粗、慣性極小、松鍵即停）。
- **繩索**：站到繩索 x 按 C 一路上到頂平台（如 x28：y68→y34，dy-34）；不在繩索 x 原地按 C 無效。上到頂再下跳修正到目標層。
- **下跳**：垂直落到正下方平台（走出/跳出邊緣也會掉落，可當下降手段）。
- 座標系=小地圖黃點 (x,y)，記錄點/平台/繩索/紫標同一系。

## pathgraph（路徑規劃）
`build_physics(points, platforms, jump=30, jump_up=11)`：只用平台+落點幾何建圖。
- 節點=記錄點+連接點；邊：walk(同平台)、jump(端點朝左右飛30落到落點平台)、fall(正下方平台垂直落)、rope(大落差>jump_up 上升)。
- `shortest_path` Dijkstra、`nearest_node` 當前位置對應節點。
- 繩索零標記——靠平台重疊/幾何推，繩索確切 x 執行時探。
- 舊版 `build_overlap`（重疊+接近跨間隙）保留。

## navigator（按鍵流程）
- `_goto_sync(tx,ty,skills,precise)`：有平台(terrain_fn)→`_goto_via_graph`(圖導航)；無→簡單導航(fallback)。
- `_goto_via_graph`：沿 path 分段 walk/jump/rope/fall + 黃點閉環。**jump 段算精確起跳點**（使落點=起跳±30 落在目標平台內、取範圍中間避邊緣，`_walk_to` 精確走到再二段跳）。
- `_fine_tune_x`：精確點停穩(`_settle`)+分檔脈衝回正到 ±1px（實測偏移8px→4~5步收斂）。
- **移動攻擊**(平A mode="move")：走路(walk)`_same_skills` 按住攻擊鍵；二段跳/下跳/繩索/到點 `_release_atk` 放開。勾選「二段跳保持攻擊」`_jump_hold_atk`、「下跳保持攻擊」`_fall_hold_atk`(平A設定,存 attack.jump_atk/fall_atk)。
- terrain 於導航啟動(靜止)取一次(current_map_id 移動中偶爾 None)；`_state["terr_n"]` 診斷。
- **移動數據記錄** `nav_moves.jsonl`：每段 {t,mode,act,start,target,end}，供訓練/調參。

## 巡邏（_patrol_run）
- 每輪 `points_fn()` 重讀最新點（運行中改設定即時生效）。**順序完全隨機、不連號**。
- 選點優先序：①冷卻好的「導航略過」放置技能點→優先去放 ②普通點(+非略過技能點)不連號循環 ③只剩冷卻中略過點→等待。
- 到點：先平A(確保落地)→放置技能(冷卻好才放、可精確、可略過；安全停攻擊0.45s再放)。**未到達(arrived=False)不施放、重試**。
- 放置技能：每點 {skill,cd,precise,skip}；`_place` 冷卻表，/nav/status 回 placements 倒數。
- **紫標(rune)出現→自動暫停巡邏**(minimap 事件 hook + 每輪兜底) + Telegram。**下一步改成自動解除(見下)**。

## mapdata / 端點
- point={x,y,skill,cd,precise,skip}；平台={y,xA,xB}；繩索={x}；平A attack={key,mode,jump_atk,fall_atk}(存 layouts/_attack.json)。
- profile(具名設置「北方」等)存 profiles/<name>.json：points+platforms+ropes+attack。**load_profile 已按尺寸縮放座標**(當前map_id÷來源尺寸)。
- 端點：/map/{status,record,remove_last,clear,attack,point_skill,platform/*,rope/*,profile/*}、/nav/{move_to,patrol,status,stop}、/idle/*。

## ⚠️ 已知重大問題
1. **小地圖偵測尺寸不穩定**（同地圖出 165x84 / 170x111 / 330x168）。165→330 是精確2倍(解析度/DPI)，170x111 是框選含邊框差異(比例不一致)。導致 map_id 變、座標錯位。load_profile 縮放只救等比；**根治要小地圖偵測每次框選同一區域(邊框配對在不同解析度/DPI 邊界不一致)**。minimap 記憶：邊框線配對法可靠、底色/輪廓不可靠。
2. 閒置模式曾誤加 has_layout 檢查(已移除，閒置=純隨機不需座標)。

## 🔜 進行中：符文(紫方塊 rune)自動解除
沿用 auto-maple 模型。**模型已下載到 `dev/maple/rune_model_rnn_filtered_cannied/`**(4.7M，saved_model.pb+variables+assets)。auto-maple 在 `dev/auto-maple`(resources submodule 只有配置、無模型；模型 README 指向 Google Drive，已下)。
- 識別法(`auto-maple/src/detection/detection.py`)：
  - 預處理：`cropped = image[120:h//2, w//4:3*w//4]`(畫面上半中間) → `filter_color`(HSV 橙~綠 inRange(1,100,100)~(75,255,255)) → `canny`(200,300)。
  - `run_inference_for_single_image`：TF SavedModel `signatures['serving_default']`，輸出 detection_boxes/scores/classes。
  - `get_boxes`：score>0.5 取 top4 bbox。
  - `merge_detection`：跑正立 + 旋轉90° 兩次推理合併(垂直箭頭+旋轉後的水平箭頭)，直到兩次一致。label_map={1:up,2:down,3:left,4:right}，rotated converter={up:right,down:left}。
- 待做：
  1. venv 裝 `tensorflow`(重依賴~500MB) + opencv(已有)。
  2. 新模組 `server/rune.py`：載入模型(路徑 `rune_model_rnn_filtered_cannied/saved_model`)、移植 filter_color/canny/推理/merge_detection → 回 4 方向序列。輸入=WGC 全畫面幀(video_pipeline/screen 抓)。
  3. rune solver 流程(navigator 或新模組)：紫標偵測→中斷巡邏→`move_to(紫標小地圖座標)`→按上(up)激活→截全畫面→rune.solve()識別→依序按方向鍵→驗證(rune_buff_template 或紫標消失)→續巡邏。
  4. main 把 minimap 紫標 hook 從 pause_purple 改成觸發 solve_rune（或加開關）。
  5. 需符文實際出現實測(截圖區域/座標對位/成功驗證)。
- 注意：auto-maple 截的是它自己的遊戲畫面座標(image[120:h//2...])，本專案 WGC 幀解析度可能不同，crop 區域要對齊實測。

## 下一步優先序
1. 符文自動解除(裝TF+移植 rune.py+solver 流程)——用戶已備模型。
2. 小地圖偵測尺寸穩定化(根治座標錯位)。
3. 巡邏參數/落點微調(nav_moves.jsonl 累積數據)。
