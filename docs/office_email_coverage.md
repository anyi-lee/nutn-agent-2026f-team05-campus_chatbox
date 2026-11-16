# 郵件助手｜處室收件人盤點

2026-11-16 盤點既有行政名錄的 51 個處室／組別。原先 11 個有可用收件人（包含學籍成績組）；本次從官網的成員職掌與各業務聯絡頁補齊 32 個，合計 43 個。這個數字是名錄覆蓋，不代表實際試寄；系統不寄真實信件。

- [總務處出納組成員職掌](https://oga.nutn.edu.tw/mode02.asp?m=20180508120051474&t=list)：日間大學部學雜費選林芸安；日間研究所及成績單、學生證補發繳費可選陳依吟。另列薪津、入帳及專案款項窗口。
- [教務處教學業務組](https://academic.nutn.edu.tw/index.php?lang=cht&temp=office2)、[研究生教務組](https://academic.nutn.edu.tw/index.php?lang=cht&temp=office4)、[圖資處工作職掌](https://lis.nutn.edu.tw/p/406-1001-1813,r56.php?Lang=zh-tw)：以組別與職掌對應承辦窗口；不能把處室信箱算成每一組的信箱。
- 每個新增窗口可在 `data/campus_catalog.json` 的 `email_windows` 與 `email_windows_source` 核對。前端資產 `ui/email_directory.json` 只保留公開名稱、分組、業務及聯絡頁等資訊，不含信箱。建立草稿時，由後端解析使用者明確選定的窗口。

## 尚未接入的 8 個組別／窗口

| 所屬處室 | 待確認 | 官方查詢入口 |
| --- | --- | --- |
| 總務處 | 事務組、營繕組、文書組、保管組 | [總務處網站導覽](https://oga.nutn.edu.tw/sitemap.asp) |
| 圖資處 | 格致樓辦公室、文薈樓管理室 | [圖資處聯絡與職掌](https://lis.nutn.edu.tw/p/406-1001-1813,r56.php?Lang=zh-tw) |
| 國際事務處 | 語文中心、國際長 | [國際事務處](https://oia.nutn.edu.tw/) |

「待確認」表示這次未找到足以核對該窗口及其信箱的資料，不是宣稱學校沒有信箱。這些選項保留官方電話或連結，不自動改用其他處室、其他組別或舊公告的信箱。
