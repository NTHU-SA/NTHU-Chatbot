"""
隱私權政策版本：後端要求同意的版本與 `frontend/privacy.html` 顯示的版本都以這裡為準。

改版時同時修改政策內容並遞增這個值；`infra/build_frontend.py` 會把它寫進 privacy.html 與 config.json，
前端送出同意時帶的是畫面上顯示的版本，和後端不一致（前後端尚未同步部署）時後端回 409，不會記錯版本。
"""

PRIVACY_POLICY_VERSION = "3"
