# -*- coding: utf-8 -*-
"""学习通自动签到 - Python 精简版

用法:
    python chaoxing_sign.py <手机号> <密码>              # 手动模式: 检测+签到一次
    python chaoxing_sign.py <手机号> <密码> --monitor    # 监听模式: 定时检测, 发现签到自动签
    python chaoxing_sign.py <手机号> <密码> --monitor --interval 30 --location "34.817,113.516,河南科技大学"

支持: 普通/手势/签到码(全自动), 位置(--location), 二维码(--enc), 拍照(云盘取图)
"""
import argparse
import json
import re
import sys
import time
import urllib.parse
from datetime import datetime

import requests
from pyDes import des, ECB, PAD_PKCS5

BASE = "https://mobilelearn.chaoxing.com"
DES_KEY = b"u2oh6Vu^"  # 前端 JS 硬编码的 16 字节密钥, DES 只取前 8 字节
UA = ("Mozilla/5.0 (Linux; Android 13; 22081212C Build/TKQ1.220829.002; wv) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/107.0.0.0 Mobile Safari/537.36")
LOG_FILE = "sign_log.txt"

TYPES = {0: "普通/拍照", 2: "二维码", 3: "手势", 4: "位置", 5: "签到码"}


def log(msg: str) -> None:
    ts = datetime.now().strftime("%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def des_encrypt(pwd: str) -> str:
    k = des(DES_KEY, ECB, padmode=PAD_PKCS5)
    return k.encrypt(pwd.encode()).hex()


class Signer:
    def __init__(self, phone: str, password: str):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self.login(phone, password)

    def login(self, phone: str, password: str) -> None:
        data = (f"uname={phone}&password={des_encrypt(password)}&fid=-1&t=true"
                "&refer=https%253A%252F%252Fi.chaoxing.com&forbidotherlogin=0&validate=")
        r = self.s.post("https://passport2.chaoxing.com/fanyalogin", data=data,
                        headers={"Content-Type": "application/x-www-form-urlencoded",
                                 "X-Requested-With": "XMLHttpRequest"})
        res = r.json()
        if not res.get("status"):
            sys.exit(f"登录失败: {res.get('msg2', res)}")
        self._uid = self.s.cookies.get("_uid", "")
        self.fid = self.s.cookies.get("fid", "-1")  # 学校 ID, 签到参数需要
        self.name = res.get("name", "") or self._fetch_name()
        log(f"登录成功: {self.name}")

    def _fetch_name(self) -> str:
        """从 accountManage 页面提取真实姓名"""
        r = self.s.get("https://passport2.chaoxing.com/mooc/accountManage")
        m = re.search(r'class="fr colorBlue"[^>]*>\s*([^<\s]+)\s*</p>', r.text)
        return m.group(1).strip() if m else ""

    def get_courses(self):
        """backclazzdata 返回 JSON, 提取 (courseId, classId, chatid, 课程名, 班级名)"""
        r = self.s.get("https://mooc1-api.chaoxing.com/mycourse/backclazzdata?rss=1")
        courses, seen = [], set()
        for ch in r.json().get("channelList", []):
            cid = ch.get("content", {}).get("id")            # classId
            chatid = ch.get("content", {}).get("chatid", "")
            cls_name = ch.get("content", {}).get("name", "")
            for c in ch.get("content", {}).get("course", {}).get("data", []):
                key = (c["id"], cid)
                if key in seen:
                    continue
                seen.add(key)
                courses.append({"courseId": str(c["id"]), "classId": str(cid),
                                "chatid": str(chatid), "courseName": c.get("name", ""),
                                "className": cls_name})
        return courses

    def check_activity(self, course_id: str, class_id: str):
        """轮询 activelist, 返回进行中且 2 小时内的签到活动, 否则 None"""
        url = (f"{BASE}/v2/apis/active/student/activelist?fid=0&courseId={course_id}"
               f"&classId={class_id}&_={int(time.time() * 1000)}")
        try:
            data = self.s.get(url).json()
        except (json.JSONDecodeError, ValueError):
            return None
        lst = (data.get("data") or {}).get("activeList") or []
        if not lst:
            return None
        a = lst[0]
        other_id = int(a.get("otherId", -1))
        if a.get("status") != 1 or not (0 <= other_id <= 5):
            return None
        if time.time() - (a.get("startTime", 0) or 0) / 1000 > 7200:  # 开始超过2小时忽略
            return None
        return {"activeId": str(a["id"]), "name": a.get("nameOne", ""),
                "otherId": other_id, "courseId": course_id, "classId": class_id}

    def pre_sign(self, act: dict) -> None:
        """模拟 app 打开签到页: preSign + analysis(抠code) + analysis2"""
        self.s.get(f"{BASE}/newsign/preSign?courseId={act['courseId']}&classId={act['classId']}"
                   f"&activePrimaryId={act['activeId']}&general=1&sys=1&ls=1&appType=15&&tid=&uid={self._uid}&ut=s")
        r = self.s.get(f"{BASE}/pptSign/analysis?vs=1&DB_STRATEGY=RANDOM&aid={act['activeId']}")
        m = re.search(r"code='\+'([^']*)", r.text)
        if m:
            self.s.get(f"{BASE}/pptSign/analysis2?DB_STRATEGY=RANDOM&code={m.group(1)}")
        time.sleep(0.5)

    def sign_general(self, act: dict) -> str:
        """普通签到"""
        url = (f"{BASE}/pptSign/stuSignajax?activeId={act['activeId']}&uid={self._uid}&clientip="
               f"&latitude=-1&longitude=-1&appType=15&fid={self.fid}&name={self.name}")
        return self.s.get(url).text

    def sign_with_code(self, act: dict, sign_code: str = "") -> str:
        """手势/签到码: 先 checkSignCode 校验, 通过后提交"""
        r = self.s.get(f"https://mobilelearn.chaoxing.com/widget/sign/pcStuSignController/"
                       f"checkSignCode?activeId={act['activeId']}&signCode={sign_code}")
        try:
            d = r.json()
        except (json.JSONDecodeError, ValueError):
            return f"校验接口异常: {r.text[:100]}"
        if d.get("result") != 1:
            return f"码校验失败: {d.get('errorMsg', d)}"
        time.sleep(0.2)
        url = (f"{BASE}/pptSign/stuSignajax?activeId={act['activeId']}&uid={self._uid}&clientip="
               f"&latitude=&longitude=&appType=15&fid={self.fid}&name={self.name}&signCode={sign_code}")
        return self.s.get(url).text

    def sign_location(self, act: dict, lat: str, lon: str, address: str) -> str:
        url = (f"{BASE}/pptSign/stuSignajax?name={self.name}&address={urllib.parse.quote(address)}"
               f"&activeId={act['activeId']}&uid={self._uid}&clientip=&latitude={lat}&longitude={lon}"
               f"&fid={self.fid}&appType=15&ifTiJiao=1")
        return self.s.get(url).text

    def sign_photo(self, act: dict, object_id: str) -> str:
        url = (f"{BASE}/pptSign/stuSignajax?activeId={act['activeId']}&uid={self._uid}&clientip="
               f"&useragent=&latitude=-1&longitude=-1&appType=15&fid={self.fid}"
               f"&objectId={object_id}&name={self.name}")
        return self.s.get(url).text

    def sign_qrcode(self, act: dict, enc: str, lat: str, lon: str, address: str, altitude: str = "100") -> str:
        loc = json.dumps({"result": "1", "address": address, "latitude": float(lat),
                          "longitude": float(lon), "altitude": float(altitude)}, ensure_ascii=False)
        url = (f"{BASE}/pptSign/stuSignajax?enc={enc}&name={self.name}&activeId={act['activeId']}"
               f"&uid={self._uid}&clientip=&location={urllib.parse.quote(loc)}&latitude=-1&longitude=-1"
               f"&fid={self.fid}&appType=15")
        return self.s.get(url).text

    def is_photo_sign(self, active_id: str) -> bool:
        """otherId==0 时再查活动详情判断是否拍照签到"""
        r = self.s.get(f"{BASE}/v2/apis/active/getPPTActiveInfo?activeId={active_id}")
        try:
            return (r.json().get("data") or {}).get("ifphoto") == 1
        except (json.JSONDecodeError, ValueError):
            return False

    def get_object_id(self):
        """从超星云盘根目录找 0.jpg/0.png, 返回 objectId"""
        r = self.s.get("https://pan-yz.chaoxing.com")
        m_enc = re.search(r'enc ="(.*?)"', r.text)
        m_root = re.search(r'_rootdir = "(.*?)"', r.text)
        if not m_enc or not m_root:
            return None
        params = f"puid=0&shareid=0&parentId={m_root.group(1)}&page=1&size=50&enc={m_enc.group(1)}"
        r = self.s.post(f"https://pan-yz.chaoxing.com/opt/listres?{params}", data=params)
        try:
            for f in r.json().get("list", []):
                if f.get("name") in ("0.jpg", "0.png"):
                    return f["objectId"]
        except (json.JSONDecodeError, ValueError):
            pass
        return None


def safe_input(prompt: str = "") -> str:
    """input 封装: stdin 关闭(EOF)时返回空串而非抛异常"""
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def do_sign(signer: Signer, act: dict, location: str = "", enc: str = "", sign_code: str = "") -> bool:
    """预签到 + 按类型签到, 返回是否成功"""
    t = TYPES.get(act["otherId"], "未知")
    log(f"检测到活动: [{t}] {act['name']}")
    signer.pre_sign(act)

    if act["otherId"] == 0:
        if signer.is_photo_sign(act["activeId"]):
            log("拍照签到: 需要云盘根目录有 0.jpg/0.png")
            oid = signer.get_object_id()
            if not oid:
                log("云盘未找到图片, 请上传后手动重跑")
                return False
            msg = signer.sign_photo(act, oid)
        else:
            msg = signer.sign_general(act)
    elif act["otherId"] in (3, 5):
        # 手势(3)/签到码(5): 需要正确的码, 校验通过后提交
        if not sign_code:
            log("手势/签到码签到需要 --signcode 参数(老师公布的码/手势轨迹编码)")
            sign_code = safe_input("签到码/手势码: ")
        if not sign_code:
            log("未提供签到码, 跳过")
            return False
        msg = signer.sign_with_code(act, sign_code)
    elif act["otherId"] == 4:
        if not location:
            log("位置签到需要 --location '纬度,经度,地址', 如 --location 34.817,113.516,河南科技大学")
            lat = safe_input("纬度: ")
            lon = safe_input("经度: ")
            address = safe_input("详细地址: ")
            if not (lat and lon and address):
                log("位置参数不完整, 跳过")
                return False
        else:
            lat, lon, address = location.split(",", 2)
        msg = signer.sign_location(act, lat, lon, address)
    elif act["otherId"] == 2:
        if not enc:
            log("二维码签到需要 --enc 参数(微信扫码抠出 enc)")
            enc = safe_input("enc: ")
        if not enc:
            log("未提供 enc, 跳过")
            return False
        lat, lon, address = ("34.817", "113.516", "河南科技大学") if not location \
            else location.split(",", 2)
        msg = signer.sign_qrcode(act, enc, lat, lon, address)
    else:
        msg = signer.sign_general(act)

    ok = msg == "success"
    log("签到成功!" if ok else f"签到结果: {msg}")
    if ok:
        print("\a", end="", flush=True)  # 提示音
    return ok


def notify(title: str, text: str) -> None:
    """发送系统通知: Termux 环境用 termux-notification, 其他环境仅打印+响铃"""
    import shutil
    if shutil.which("termux-notification"):
        import subprocess
        try:
            subprocess.Popen(["termux-notification", "-t", title, "-c", text],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
    print("\a", end="", flush=True)


def monitor(signer: Signer, interval: int, location: str, enc: str, sign_code: str,
            confirm: bool = False) -> None:
    log(f"监听模式启动, 每 {interval}s 检测 {len(signer.get_courses())} 门课, Ctrl+C 退出"
        + (" (确认模式: 检测到签到需手动确认)" if confirm else ""))
    signed = set()  # 记录已签活动, 避免重复
    while True:
        try:
            act = None
            for c in signer.get_courses():
                a = signer.check_activity(c["courseId"], c["classId"])
                if a:
                    act = a
                    break
            if act and act["activeId"] not in signed:
                t = TYPES.get(act["otherId"], "未知")
                if confirm:
                    notify("学习通签到", f"检测到[{t}] {act['name']}，请在 Termux 确认")
                    log(f"检测到活动: [{t}] {act['name']} —— 回车确认签到, 输入 n 跳过")
                    try:
                        ans = input().strip().lower()
                    except EOFError:
                        ans = ""
                    if ans == "n":
                        log("已跳过该签到")
                        signed.add(act["activeId"])
                        continue
                ok = do_sign(signer, act, location, enc, sign_code)
                if ok:
                    signed.add(act["activeId"])
            else:
                log("无进行中签到活动")
        except KeyboardInterrupt:
            log("已退出")
            break
        except Exception as e:
            log(f"检测出错: {e}")
        time.sleep(interval)


def main() -> None:
    ap = argparse.ArgumentParser(description="学习通自动签到")
    ap.add_argument("phone", help="手机号")
    ap.add_argument("password", help="密码")
    ap.add_argument("--monitor", action="store_true", help="监听模式: 定时检测自动签到")
    ap.add_argument("--interval", type=int, default=60, help="监听间隔秒数(默认60)")
    ap.add_argument("--location", default="", help="位置签到: '纬度,经度,地址'")
    ap.add_argument("--enc", default="", help="二维码签到 enc 参数")
    ap.add_argument("--signcode", default="", help="手势/签到码签到的码")
    ap.add_argument("--confirm", action="store_true", help="确认模式: 检测到签到后需手动确认再签(适合手机Termux)")
    args = ap.parse_args()

    signer = Signer(args.phone, args.password)
    if args.monitor:
        monitor(signer, args.interval, args.location, args.enc, args.signcode, args.confirm)
        return

    # 手动模式: 检测一次并签到
    courses = signer.get_courses()
    if not courses:
        log("未找到课程")
        return
    log(f"共 {len(courses)} 门课, 检测签到活动中...")
    act = None
    for c in courses:
        a = signer.check_activity(c["courseId"], c["classId"])
        if a:
            act = a
            log(f"[{c['courseName']} / {c['className']}] 有活动")
            break
    if not act:
        log("未检测到有效签到活动")
        return
    do_sign(signer, act, args.location, args.enc, args.signcode)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    main()
