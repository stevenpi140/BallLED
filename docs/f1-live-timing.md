# F1 官方即時計時設定

BallLED 的 F1 名次預設來自 ESPN，只有「誰第幾名、跑到第幾圈」。照這份說明設定之後，伺服器會在 F1 賽段進行中，用**你自己的 F1 帳號**連 F1 官方即時計時，看板會多出：

- 和第一名的時間差，例如 `+1.234`（原本這個位置顯示車隊）
- 官方的車隊顏色
- 官方的總圈數

**免費的 F1 帳號就可以用**，不需要 F1 TV 訂閱。沒設定或設定失效時，看板會自動改回 ESPN 的名次，不會斷。

---

## 先看這裡：帳號安全

- **只在你自己的瀏覽器裡登入 formula1.com。** BallLED 不需要、也不會用到你的帳號密碼，只用登入後瀏覽器裡的一段憑證（token）。
- **密碼和 token 都不要給任何人**，包括論壇、群組、AI 助手。token 等於你帳號的登入狀態，拿到的人可以用你的身分存取 F1 網站。
- **如果密碼曾經貼到別的地方，請先到 formula1.com 改密碼**，再繼續下面的步驟。
- token 只會存在你電腦上的 `server/config.yaml`。這個檔案已經設定成不會傳上 GitHub。

---

## 需要準備

- 一個 F1 帳號（formula1.com 免費註冊）
- 電腦上的 Chrome 或 Edge（Firefox、Safari 也可以，步驟 2 有寫）
- BallLED 伺服器已經裝好（組裝手冊第 03 階段）

---

## 步驟 1：在瀏覽器登入

打開 <https://www.formula1.com>，按右上角登入你的 F1 帳號。登入後重新整理一次頁面。

## 步驟 2：複製 `login-session` cookie 的值

**Chrome / Edge**

1. 在 formula1.com 的頁面上按 **F12**，打開開發者工具。
2. 上方分頁選 **Application**（中文介面是「應用程式」）。看不到的話，按分頁列最右邊的 `»` 找。
3. 左邊欄位：**Storage** → **Cookies** → 點 `https://www.formula1.com`。
4. 右邊表格上方的篩選框輸入 `login-session`，點那一列。
5. 表格下方會出現這個 cookie 的值（Cookie Value）。點進去按 **Ctrl+A** 全選、**Ctrl+C** 複製。值很長，是正常的。

**Firefox**：F12 → **儲存空間（Storage）** → **Cookies** → `https://www.formula1.com` → 找 `login-session`，複製「值」。

**Safari**：先在「設定 → 進階」打開「顯示網頁開發者功能」，再用「開發 → 顯示網頁檢閱器 → 儲存空間 → Cookie」找 `login-session`。

> 複製整段值就好，不用自己找裡面的 token。下一步的指令會自動抽出 `subscriptionToken`。

## 步驟 3：寫進設定檔

在伺服器那台電腦開「命令提示字元」（cmd）：

```
cd BallLED\server
.venv\Scripts\activate
python -m scoreboard f1-token
```

畫面出現 `>` 時，貼上剛剛複製的值（cmd 裡按右鍵或 Ctrl+V），再按 Enter。為了安全，貼上的內容**不會顯示在畫面上**，這是正常的。

成功時會看到：

```
token 有效，到期時間 2026-11-04 12:09（還有 3.0 天）。
已寫入 config.yaml 的 leagues.f1.live_token。重開伺服器後生效。
```

**貼上沒反應或被截斷**：把值貼進記事本，存成 `cookie.txt`（放在 `server` 資料夾），改用：

```
python -m scoreboard f1-token --from-file cookie.txt
```

寫入成功後**把 `cookie.txt` 刪掉**。

## 步驟 4：測試連線

```
python -m scoreboard probe f1 --diagnose
```

在輸出裡找「F1 官方即時計時」那一段：

```
F1 官方即時計時（BallLED 自己的 User-Agent，不偽裝）：
  使用 config.yaml 的 live_token，到期時間： 2026-11-04 12:09:00
  連線成功，快照大小： {...}
  SessionInfo： {"Meeting": {"Name": "Mexico City Grand Prix", ...}, "Name": "Race", ...}
  前五名： [{'pos': 1, 'tla': 'NOR', 'gap': '', ...}, {'pos': 2, 'tla': 'VER', 'gap': '+1.234', ...}, ...]
```

看到「連線成功」就完成了。沒有比賽的時候也能測：官方會送出最近一個賽段的資料。

## 步驟 5：重開伺服器

在伺服器視窗按 **Ctrl+C** 停止，再執行 `python -m scoreboard serve --port 8080`。之後每逢 F1 賽段進行中（或 20 分鐘內開始），伺服器會自己連線，平常不會連。

---

## 自動更新（Chrome 136+）

Chrome 136 之後不能用 CDP 讀你平常的 Chrome 個人檔案。BallLED 用**專用 profile**（只讀 formula1 的 `login-session`）：

```powershell
# 只需做一次：登入公式1，讓登入狀態存在這個 profile 裡
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=9222 `
  --user-data-dir="$env:LOCALAPPDATA\BallLED\ChromeF1Debug" `
  --no-first-run --no-default-browser-check https://www.formula1.com/
# 在裡面登入後可關掉視窗

cd BallLED\server
.venv\Scripts\activate
python -m scoreboard f1-token --from-chrome
```

之後**不必一直開著**：F1 賽段快開始／進行中時，若 token 已過期或 10 分鐘內到期（或連線遇到 401/403），伺服器會自己：

1. 短暫開啟專用 debug Chrome  
2. 只讀 `www.formula1.com` 的 `login-session`  
3. 寫回 `config.yaml`  
4. **用完自動關閉該 Chrome**（只關這次由 BallLED 開的那個；本來就在跑的不會強制關）

`python -m scoreboard f1-token --from-chrome` 同樣預設用完就關；加 `--keep-open` 才會留著。

- 你平常的用瀏覽不受影響；不會讀密碼、歷史或其他網站。
- 專用 profile 的登入狀態會保留在 `%LOCALAPPDATA%\BallLED\ChromeF1Debug`，下次重開通常不用再手動登入。
- 停用自動開關：`BALLLED_F1_AUTO_REFRESH=0`；port：`BALLLED_F1_CDP_PORT`（預設 9222）。


## token 過期了怎麼辦

token 大約幾天就會過期。過期後，看板的 F1 會自動改回 ESPN 的名次（沒有時間差），其他都正常。

若已啟用 session：賽段快開始／進行中且 token 快過期或已過期，伺服器會短暫開啟、更新、關閉專用 debug Chrome；或遇到 401/403 時同樣處理。否則重做登入後執行一次 `f1-token --from-chrome`。比賽週末前確認專用 profile 仍能登入即可。

---

## 疑難排解

| 狀況 | 原因與處理 |
|---|---|
| 找不到 `login-session` | 確認已經登入，而且網址是 `www.formula1.com`；登入後重新整理頁面再找。真的沒有這個 cookie，可能是官網改了名稱，請回報 |
| `f1-token` 說「找不到 token」 | 複製不完整。回到步驟 2，在 Cookie Value 裡用 Ctrl+A 全選後再複製 |
| `f1-token` 說「已經過期」 | 重新登入 formula1.com，再複製一次 |
| `f1-token` 說「沒辦法自動寫入」 | 還沒有 `config.yaml`：先執行 `copy config.example.yaml config.yaml` |
| probe 顯示 `negotiate HTTP 401` 或 `403` | token 過期、貼錯，或官方不接受這個帳號。重新取得 token 再試；還是一樣，把**錯誤訊息**貼出來回報（不要貼 token） |
| probe 連線成功，但看板沒有時間差 | 只有 F1 賽段**進行中**才會用到即時計時；也確認 `config.yaml` 的 `leagues.f1.sessions` 有包含這個賽段 |
| 本來有時間差，比賽到一半不見了 | 多半是 token 剛好過期，或官方連線中斷。伺服器會每 10 秒重試；token 過期就要重新取得 |

---

## 運作方式與限制

- 資料來自 F1 官網 Live Timing 背後的即時資料流（`livetiming.formula1.com`），連線方式參考 [julesr0y/f1-livetiming-api](https://github.com/julesr0y/f1-livetiming-api)。
- 連線時用 BallLED 自己的名稱（User-Agent），**不偽裝成官方 App 或網站**；帳號身分只透過你提供的 token 證明。
- F1 從 2026 年 10 月起要求登入才給資料，沒有 token 會被拒絕（HTTP 403）。
- 這是官網沒有公開說明的介面，F1 隨時可能更改。改了之後看板會退回 ESPN 的名次，等程式更新即可。
- 只在賽段進行中連線，不會整天佔用官方伺服器。
