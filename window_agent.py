"""住在自己視窗裡的 agent（目前是 Gemini，使用者 11-18 20:11 移交到 agy 視窗）。
中樞不再用 agy -p 在背景叫它（官方 FAQ：消費方案不支援把 agy 包進應用程式），改成留單：
每個房間一個檔 H:\\Gemini用\\群組\\待回\\<房間id>.md，有人叫它就把最近的訊息附上去；
視窗裡的 Gemini 用 群組.py 看群組、回完把單移到 待回\\已處理\\。
"""
import os, time

import agents as rt

TICKETS = r"H:\Gemini用\群組\待回"
HOMES = {"Gemini": r"H:\Gemini用", "輝達": r"H:\免費模型用"}  # 輝達 11-18 21:2x 也搬進自己的視窗（OpenCode）


class WindowAgent(rt.Agent):
    """run() 不呼叫模型，只留單；回傳空字串，中樞就不會替它發言。"""

    def __init__(self, name, room_id="", room_name=""):
        super().__init__(name)
        self.room_id, self.room_name = room_id, room_name

    @property
    def write(self):
        return False

    @write.setter
    def write(self, v):
        pass

    def build_prompt(self, transcript):
        new = [m for m in transcript[self.seen:] if m["who"] != self.name][-8:]
        return "\n\n".join(f"【{m['who']}｜{m.get('time', '')}】{m['text'][:1500]}" for m in new)

    def run(self, prompt):
        self.steps, self.started, self.said = [], time.time(), []
        self.step("留單給視窗")
        home = HOMES.get(self.name)
        tickets = os.path.join(home, "群組", "待回") if home else TICKETS
        os.makedirs(tickets, exist_ok=True)
        p = os.path.join(tickets, f"{self.room_id or 'main'}.md")
        head = "" if os.path.exists(p) else (f"# 待回：{self.room_name or self.room_id}（房間 id：{self.room_id}）\n"
                                             f"看完整對話：PY {home or 'H:'}\\群組\\群組.py 看 {self.room_id}；回完把這個檔移到 已處理\\\n")
        with open(p, "a", encoding="utf-8") as f:
            f.write(head + f"\n## {time.strftime('%m-%d %H:%M')} 有人找你\n{prompt}\n")
        return ""
