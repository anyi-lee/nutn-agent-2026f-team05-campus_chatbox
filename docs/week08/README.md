# Week 08｜B 路線：中央分工

第五組「南大校務通」。同一句話問「許世昌老師的信箱和電機系辦電話」，由召集人分派師資與行政專家，各查一項官方事實再回報。使用原聊天畫面與 `POST /api/chat`；不增加寄信或記憶寫入權限。

- **基線：** `4764e4c2a0733cbaead59b28494376b26adc8cff`。
- **候選程式 SHA：** `637c839ada2939a7ab09fffef68cfb77e53c981e`（`week08-mas` 分支）。測試在提交前執行；已核對比較報告列出的程式與資料檔案雜湊皆與候選一致。文件與證據另行提交，不變更候選程式。
- **提交分支：** `week08-mas`；不合併到期末專案的 `main`。
- [角色契約、流程圖與採用決策](architecture.md)
- [測試矩陣、同條件比較與待辦](validation.md)
- [同資料快照的原版／B 比較（4 案例，離線）](../../evidence/week08_mas_comparison_latest.json)
- 瀏覽器證據（離線替身）：[正常畫面](../../evidence/week08_browser_latest/normal.png)、[邊界畫面](../../evidence/week08_browser_latest/unknown_teacher.png)、[同一輪完整軌跡](../../evidence/week08_browser_latest/browser_trace.json)。
- [處室信箱覆蓋盤點](../office_email_coverage.md) 為另外的 Email 助手修正，不列入中央分工的驗收。

## 啟動與操作

離線可重現，不需 Key。從 repo 根目錄執行：

```bash
python3 scripts/run_week08_comparison.py --serve
```

開啟 [本機測試頁](http://127.0.0.1:8088/)，按「同時查老師與系辦」，展開「查看召集人與兩位專家的分工紀錄」。正常範例回傳兩筆資料與兩個來源；問「不存在老師的信箱和電機系辦電話」時只保留已查證的系辦電話。頁面標「離線測試替身」，真實模型呼叫為 0。

要驗證真實 Gemini，先關閉占用 8000 的舊開發伺服器：

```bash
CHAT_WORKFLOW_BACKEND=mas bash scripts/run_with_gemini.sh
```

在終端機輸入 Key；不要貼到聊天、寫入檔案或提交。開啟 [原本的問答頁](http://127.0.0.1:8000/)，執行相同步驟。這是既有 API Key transport，不代表 ADC 已完成。沒設定模型時，複合查詢回 `MAS_NOT_CONFIGURED` 並停止。單一查詢、寫信與記憶不變；將 `CHAT_WORKFLOW_BACKEND` 改回 `baseline` 可回原版。

## 重跑

```bash
python3 scripts/run_week08_comparison.py
python3 -m unittest discover -s tests -p 'test_week08_mas.py' -v
```

HTTP 測試需要能綁定本機臨時埠。完整回歸包含 LangChain，使用已裝 `requirements-week05.txt` 的環境執行。

若本機的 Node 可載入 Playwright 且有 Google Chrome，可執行 `node scripts/capture_week08_browser.cjs <新輸出目錄>`，產生訪客畫面及對應 JSON。加上 `--live` 會改用已啟動在 `127.0.0.1:8000` 的 Gemini MAS；先確認後端已重啟載入候選版本。腳本只測公開查詢，不寄信、不讀私人帳號資料。
