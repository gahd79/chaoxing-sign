# -*- coding: utf-8 -*-
"""学习通自动签到 - Python 精简版

用法:
    python chaoxing_sign.py <手机号> <密码>              # 手动模式: 检测+签到一次
    python chaoxing_sign.py <手机号> <密码> --monitor    # 监听模式: 定时检测, 发现签到自动签
    python chaoxing_sign.py <手机号> <密码> --monitor --interval 30 --location "113.516,34.817,河南科技大学"
    python chaoxing_sign.py <手机号> <密码> --debug      # 打印请求参数与原始响应

支持: 普通/手势/签到码(全自动), 位置(--location), 二维码(--enc), 拍照(云盘取图)

注意: --location 的顺序是「经度,纬度,地址」(经度在前, 范围±180), 写反会被本地拦下。
"""

import argparse
import base64
import hashlib
import json
import re
import secrets
import sys
import time
import urllib.parse
import uuid
from datetime import datetime

import requests
from pyDes import des, ECB, PAD_PKCS5

BASE = "https://mobilelearn.chaoxing.com"
DES_KEY = b"u2oh6Vu^"  # 前端 JS 硬编码的 16 字节密钥, DES 只取前 8 字节
LOG_FILE = "sign_log.txt"
PROFILE_FILE = "device_profile.json"  # 设备身份(UA 尾号 + deviceCode), 需跨次运行保持稳定
BROWSER_UA = (
    "Mozilla/5.0 (Linux; Android 13; SM-N9006; wv) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Version/4.0 Chrome/107.0.0.0 Mobile Safari/537.36"
)

# ---------------------------------------------------------------- App 客户端身份
# 服务端的定位授权链会校验「请求是不是学习通 App 发出来的」。浏览器 UA 会被判为
# 非 App 客户端, 位置签到直接返回 locationAuthError_LCR007。
# App 版 UA 的长这样(三段拼起来, 中间那段带 md5 签名 schild):
#   Dalvik/2.1.0 (Linux; U; Android 12; SM-N9006 Build/8aba9e4.0)
#   (schild:<md5("(schild:<盐>) " + 第三段)>)
#   (device:SM-N9006) Language/zh_CN com.chaoxing.mobile/ChaoXingStudy_3_6.7.5_android_phone_10941_314 (@Kalimdor)_<32位设备号>
# 算法已用参考实现(ChaoxingSignFaker)里硬编码的那条 UA 反推校验通过。
SCHILD_SALT = r"ipL$TkeiEmfy1gTXb2XHrdLN0a@7c^vu"
DEVICE_MODEL = "SM-N9006"
ANDROID_VER = "12"
ANDROID_BUILD = "8aba9e4.0"
CX_VER = "6.7.5"
CX_VER_CODE = "10941"
CX_API_VER = "314"


def build_app_ua(unique_id: str) -> str:
    """按官方算法拼一条签名合法的 App UA"""
    tail = (
        f"(device:{DEVICE_MODEL}) Language/zh_CN com.chaoxing.mobile/"
        f"ChaoXingStudy_3_{CX_VER}_android_phone_{CX_VER_CODE}_{CX_API_VER}"
        f" (@Kalimdor)_{unique_id}"
    )
    schild = md5_hex(f"(schild:{SCHILD_SALT}) {tail}")
    return (
        f"Dalvik/2.1.0 (Linux; U; Android {ANDROID_VER}; {DEVICE_MODEL} "
        f"Build/{ANDROID_BUILD}) (schild:{schild}) {tail}"
    )


def generate_device_code() -> str:
    """App 的设备指纹(参考实现同款: 两次随机 uuid 拼接取 sha256, 再复制一份 base64)"""
    raw = hashlib.sha256((uuid.uuid4().hex + uuid.uuid4().hex).encode()).digest()
    return base64.b64encode(raw + raw).decode()


def load_profile() -> dict:
    """读取(或首次生成)设备身份; 设备标识必须稳定, 换一次就可能被判定为新设备"""
    try:
        with open(PROFILE_FILE, encoding="utf-8") as f:
            p = json.load(f)
        if p.get("unique_id") and p.get("device_code"):
            return p
    except (OSError, ValueError):
        pass
    p = {"unique_id": uuid.uuid4().hex, "device_code": generate_device_code()}
    try:
        with open(PROFILE_FILE, "w", encoding="utf-8") as f:
            json.dump(p, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
    return p


TYPES = {0: "普通/拍照", 2: "二维码", 3: "手势", 4: "位置", 5: "签到码"}
DEBUG = False  # --debug 打开: 打印每个请求的完整参数, 排查接口问题用


def log(msg: str) -> None:
    ts = datetime.now().strftime("%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def dlog(msg: str) -> None:
    if DEBUG:
        log(f"[debug] {msg}")


def des_encrypt(pwd: str) -> str:
    k = des(DES_KEY, ECB, padmode=PAD_PKCS5)
    return k.encrypt(pwd.encode()).hex()


# ---------------------------------------------------------------- 位置授权链
# 2026-09 起超星给位置签到加了「定位授权链」校验: 请求必须带
#   locationResult = {"result":1,"latitude":..,"longitude":..,"mockData":{...},
#                     "locType":161,"address":"..",
#                     "signConfig":{"signToken":<md5>,"cxcid":<设备id>,"cxtime":<毫秒时间戳>}}
# 其中 signToken = md5("cxcid"+cid + "cxtime"+ts + "data"+数据 + sc)
# 的 cid/sc 是向超星注册设备后下发的一对设备标识, 靠下面这把 RSA 公钥换取:
# 先用公钥加密设备信息上传, 再用同一把公钥做一次模幂运算解出服务端返回的 clientId。
RSA_PUB = (
    "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC79d8Ot0hCbxxSISC6x8SCwTBspFSz"
    "lLKHJUYqoFNu1TSRaw4hEYkOnvEaL1VyoxV6HXcDrzwYvaFZaZaPQPFnfCHZy5dQw"
    "xcmifgSHqS+oKXw40Ys4cVIqnU5d90S7EWSRdBglX489jlqVaNcQSkDx2TYmC+Db"
    "Aq9FV/BU09ISQIDAQAB"
)


def _der_read(buf: bytes, i: int):
    """读一个 DER TLV, 返回 (tag, value, 下一个偏移)"""
    tag = buf[i]
    i += 1
    length = buf[i]
    i += 1
    if length & 0x80:
        n = length & 0x7F
        length = int.from_bytes(buf[i : i + n], "big")
        i += n
    return tag, buf[i : i + length], i + length


def _rsa_pubkey(b64: str):
    """从 SPKI base64 公钥里解析出 (n, e)"""
    _, spki, _ = _der_read(base64.b64decode(b64), 0)
    _, _, off = _der_read(spki, 0)  # AlgorithmIdentifier SEQUENCE
    _, bitstr, _ = _der_read(spki, off)  # BIT STRING
    _, rsa_seq, _ = _der_read(bitstr[1:], 0)  # 去掉 unused-bits 字节
    _, n_bytes, off = _der_read(rsa_seq, 0)
    _, e_bytes, _ = _der_read(rsa_seq, off)
    return int.from_bytes(n_bytes, "big"), int.from_bytes(e_bytes, "big")


def rsa_encrypt_pkcs1(text: str, b64_key: str = RSA_PUB) -> str:
    """RSA/PKCS#1 v1.5 分段加密, 返回 base64"""
    n, e = _rsa_pubkey(b64_key)
    k = (n.bit_length() + 7) // 8
    out = b""
    raw = text.encode()
    for i in range(0, len(raw), k - 11):
        chunk = raw[i : i + k - 11]
        ps = bytes(secrets.randbelow(255) + 1 for _ in range(k - 3 - len(chunk)))
        em = b"\x00\x02" + ps + b"\x00" + chunk
        out += pow(int.from_bytes(em, "big"), e, n).to_bytes(k, "big")
    return base64.b64encode(out).decode()


def _pkcs1_unpad(block: bytes) -> bytes:
    """剥掉 PKCS#1 v1.5 填充(兼容 block type 1/2), 失败返回空"""
    if len(block) < 3:
        return b""
    bt, start = (block[1], 2) if block[0] == 0 else (block[0], 1)
    if bt not in (1, 2):
        return b""
    sep = block.find(b"\x00", start)
    return b"" if sep == -1 else block[sep + 1 :]


def rsa_public_decrypt(b64_text: str, b64_key: str = RSA_PUB) -> str:
    """用公钥做模幂运算还原服务端签出的明文(即 clientId 的解密方式)"""
    if not b64_text:
        return ""
    n, e = _rsa_pubkey(b64_key)
    k = (n.bit_length() + 7) // 8
    raw = base64.b64decode(b64_text)
    out = b""
    for i in range(0, len(raw), k):
        em = pow(int.from_bytes(raw[i : i + k], "big"), e, n).to_bytes(k, "big")
        out += _pkcs1_unpad(em)
    return out.decode("utf-8", "replace")


def md5_hex(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


class Signer:
    def __init__(self, phone: str, password: str):
        self.s = requests.Session()
        # 关键: 必须用学习通 App 的 UA, 浏览器 UA 过不了位置签到的定位授权链
        profile = load_profile()
        self.unique_id = profile["unique_id"]
        self.device_code = profile["device_code"]
        self.app_ua = build_app_ua(self.unique_id)
        self.s.headers["User-Agent"] = self.app_ua
        dlog(f"App UA: {self.app_ua}")
        dlog(f"deviceCode: {self.device_code}")
        self._device_identity = None  # (cid, sc), 位置签到时才去换取
        self.login(phone, password)

    def login(self, phone: str, password: str) -> None:
        data = (
            f"uname={phone}&password={des_encrypt(password)}&fid=-1&t=true"
            "&refer=https%253A%252F%252Fi.chaoxing.com&forbidotherlogin=0&validate="
        )
        r = self.s.post(
            "https://passport2.chaoxing.com/fanyalogin",
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        res = r.json()
        if not res.get("status"):
            sys.exit(f"登录失败: {res.get('msg2', res)}")
        self._uid = self.s.cookies.get("_uid", "")
        self.fid = self.s.cookies.get("fid", "-1")  # 学校 ID, 签到参数需要
        self.name = res.get("name", "") or self._fetch_name()
        log(f"登录成功: {self.name}")

    def _fetch_name(self) -> str:
        """从 accountManage 页面提取真实姓名(网页端, 用浏览器 UA 才拿得到完整 HTML)"""
        r = self.s.get(
            "https://passport2.chaoxing.com/mooc/accountManage",
            headers={"User-Agent": BROWSER_UA},
        )
        m = re.search(r'class="fr colorBlue"[^>]*>\s*([^<\s]+)\s*</p>', r.text)
        return m.group(1).strip() if m else ""

    def get_courses(self):
        """backclazzdata 返回 JSON, 提取 (courseId, classId, chatid, 课程名, 班级名)"""
        r = self.s.get("https://mooc1-api.chaoxing.com/mycourse/backclazzdata?rss=1")
        courses, seen = [], set()
        for ch in r.json().get("channelList", []):
            cid = ch.get("content", {}).get("id")  # classId
            chatid = ch.get("content", {}).get("chatid", "")
            cls_name = ch.get("content", {}).get("name", "")
            for c in ch.get("content", {}).get("course", {}).get("data", []):
                key = (c["id"], cid)
                if key in seen:
                    continue
                seen.add(key)
                courses.append(
                    {
                        "courseId": str(c["id"]),
                        "classId": str(cid),
                        "chatid": str(chatid),
                        "courseName": c.get("name", ""),
                        "className": cls_name,
                    }
                )
        return courses

    def check_activity(self, course_id: str, class_id: str):
        """轮询 activelist, 返回进行中且 2 小时内的签到活动, 否则 None"""
        url = (
            f"{BASE}/v2/apis/active/student/activelist?fid=0&courseId={course_id}"
            f"&classId={class_id}&_={int(time.time() * 1000)}"
        )
        try:
            data = self.s.get(url).json()
        except (json.JSONDecodeError, ValueError):
            return None
        body = data.get("data") or {}
        lst = body.get("activeList") or []
        if not lst:
            return None
        a = lst[0]
        other_id = int(a.get("otherId", -1))
        if a.get("status") != 1 or not (0 <= other_id <= 5):
            return None
        if (
            time.time() - (a.get("startTime", 0) or 0) / 1000 > 7200
        ):  # 开始超过2小时忽略
            return None
        # data.ext 是服务端下发的签到凭证, 官方预签到必须原样带回
        ext = body.get("ext") or ""
        if not isinstance(ext, str):
            ext = json.dumps(ext, ensure_ascii=False)
        return {
            "activeId": str(a["id"]),
            "name": a.get("nameOne", ""),
            "otherId": other_id,
            "courseId": course_id,
            "classId": class_id,
            "ext": ext,
        }

    def pre_sign(self, act: dict) -> None:
        """模拟 app 打开签到页: 官方 preSign(POST ext) + analysis(抠code) + analysis2

        ext 取自 activelist 的 data.ext, 官方 app 是用 POST 表单提交的。
        """
        url = (
            f"{BASE}/newsign/preSign?courseId={act['courseId']}&classId={act['classId']}"
            f"&activePrimaryId={act['activeId']}&general=1&sys=1&ls=1&appType=15"
            f"&isTeacherViewOpen=0&uid={self._uid}"
        )
        self.s.post(
            url,
            data={"ext": act.get("ext", "")},
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
            },
        )
        time.sleep(0.5)
        r = self.s.get(
            f"{BASE}/pptSign/analysis?vs=1&DB_STRATEGY=RANDOM&aid={act['activeId']}"
        )
        m = re.search(r"code='\+'([^']*)", r.text)
        if m:
            self.s.get(f"{BASE}/pptSign/analysis2?DB_STRATEGY=RANDOM&code={m.group(1)}")
        time.sleep(0.5)

    def sign_general(self, act: dict) -> str:
        """普通签到"""
        url = (
            f"{BASE}/pptSign/stuSignajax?activeId={act['activeId']}&uid={self._uid}&clientip="
            f"&latitude=-1&longitude=-1&appType=15&fid={self.fid}&name={self.name}"
        )
        return self.s.get(url).text

    def sign_with_code(self, act: dict, sign_code: str = "") -> str:
        """手势/签到码: 先 checkSignCode 校验, 通过后提交"""
        r = self.s.get(
            f"https://mobilelearn.chaoxing.com/widget/sign/pcStuSignController/"
            f"checkSignCode?activeId={act['activeId']}&signCode={sign_code}"
        )
        try:
            d = r.json()
        except (json.JSONDecodeError, ValueError):
            return f"校验接口异常: {r.text[:100]}"
        if d.get("result") != 1:
            return f"码校验失败: {d.get('errorMsg', d)}"
        time.sleep(0.2)
        url = (
            f"{BASE}/pptSign/stuSignajax?activeId={act['activeId']}&uid={self._uid}&clientip="
            f"&latitude=&longitude=&appType=15&fid={self.fid}&name={self.name}&signCode={sign_code}"
        )
        return self.s.get(url).text

    def device_identity(self):
        """注册本机设备, 换取 (cid, sc); 位置签名的 signToken 需要它们

        注意: 设备信息里的 cdid/device_id 必须与 App UA 尾部的设备号一致,
        否则服务端会把这次请求看成"UA 与设备身份对不上"。
        """
        if self._device_identity:
            return self._device_identity
        info = {
            "app_name": "com.chaoxing.mobile",
            "app_ver": CX_VER,
            "board": "universal8895",
            "brand": "samsung",
            "cdid": self.unique_id,
            "cdtype": DEVICE_MODEL,
            "cpu_ar": "arm64-v8a,armeabi-v7a,armeabi",
            "device_id": self.unique_id,
            "dpi": "480",
            "hardware": "qcom",
            "mediaDrmId": "",
            "oaid": "1004",
            "os_lang": "",
            "os_name": "REL",
            "os_ver": ANDROID_VER,
            "platform": "android",
            "resolution": "1080*1920",
            "time_stamp": int(time.time() * 1000),
        }
        payload = rsa_encrypt_pkcs1(json.dumps(info, separators=(",", ":")))
        try:
            r = self.s.post(
                "https://sso.chaoxing.com/apis/login/userLogin4Uname.do",
                data={"data": payload},
            )
            j = r.json()
            dlog(f"设备注册响应: {str(j)[:300]}")
            msg = j.get("msg") if isinstance(j.get("msg"), dict) else {}
            client_id = msg.get("clientId") or ""
            if not client_id:
                log(f"设备注册未返回 clientId: {str(j)[:200]}")
            dev = json.loads(rsa_public_decrypt(client_id) or "{}")
            self._device_identity = (str(dev.get("cid", "")), str(dev.get("sc", "")))
            dlog(f"设备身份 cid={self._device_identity[0]} sc={self._device_identity[1]}")
        except Exception as e:  # 网络/接口变更时不让整个签到流程崩掉
            log(f"设备注册失败, 位置签名可能被服务端拒绝: {e}")
            self._device_identity = ("", "")
        if not self._device_identity[0]:
            log("未取到设备标识 cid, 位置授权很可能被服务端拒绝")
        return self._device_identity

    def build_location_result(self, lat: str, lon: str, address: str) -> str:
        """拼位置授权凭证 locationResult(带 signToken 签名)"""
        cid, sc = self.device_identity()
        ts = str(int(time.time() * 1000))
        lat_f, lon_f = float(lat), float(lon)
        data_str = '{"latitude":%s,"longitude":%s,"address":"%s"}' % (
            lat_f,
            lon_f,
            address,
        )
        # 键名按字典序拼接: cxcid / cxtime / data, 末尾接设备密钥 sc
        token = md5_hex(f"cxcid{cid}cxtime{ts}data{data_str}{sc}")
        return json.dumps(
            {
                "result": 1,
                "latitude": lat_f,
                "longitude": lon_f,
                "mockData": {"strategy": 0, "probability": -1},
                "locType": 161,
                "address": address,
                "signConfig": {"signToken": token, "cxcid": cid, "cxtime": ts},
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def _location_params(self, act: dict, lat: str, lon: str, address: str) -> dict:
        """位置签到的公共参数(两个变体共用)"""
        return {
            "name": self.name,
            "address": address,
            "activeId": act["activeId"],
            "courseId": act["courseId"],
            "uid": self._uid,
            "clientip": "",
            "latitude": f"{float(lat):.6f}",
            "longitude": f"{float(lon):.6f}",
            "fid": self.fid,
            "appType": "15",
            "ifTiJiao": "1",
            "validate": "",
            "deviceCode": self.device_code,
            "vpProbability": "-1",
            "vpStrategy": "",
            "currentFaceId": "",
            "ifCFP": "0",
            "faceEnc": "",
            "locationResult": self.build_location_result(lat, lon, address),
        }

    def sign_location(self, act: dict, lat: str, lon: str, address: str) -> str:
        """位置签到

        服务端这几年换过好几套实现, 各客户端发的也不一样, 所以这里依次试两个变体,
        哪个不被定位授权链拒绝就用哪个(命中成功/范围错误/已签/过期都算有效响应):
          变体1  GET      —— 活跃维护的 App 客户端(ChaoxingSignFaker)的写法
          变体2  POST 表单 —— Flutter 客户端(course_helper)2026-09 改版后的写法
        """
        params = self._location_params(act, lat, lon, address)
        dlog(f"位置签到参数: {json.dumps(params, ensure_ascii=False)}")

        r = self.s.get(f"{BASE}/pptSign/stuSignajax", params=params)
        dlog(f"变体1(GET) 响应: {r.text[:200]}")
        if not r.text.startswith("locationAuthError"):
            return r.text

        r = self.s.post(
            f"{BASE}/pptSign/stuSignajax",
            data=params,
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
            },
        )
        dlog(f"变体2(POST) 响应: {r.text[:200]}")
        return r.text

    def sign_photo(self, act: dict, object_id: str) -> str:
        url = (
            f"{BASE}/pptSign/stuSignajax?activeId={act['activeId']}&uid={self._uid}&clientip="
            f"&useragent=&latitude=-1&longitude=-1&appType=15&fid={self.fid}"
            f"&objectId={object_id}&name={self.name}"
        )
        return self.s.get(url).text

    def sign_qrcode(
        self,
        act: dict,
        enc: str,
        lat: str,
        lon: str,
        address: str,
        altitude: str = "100",
    ) -> str:
        loc = json.dumps(
            {
                "result": "1",
                "address": address,
                "latitude": float(lat),
                "longitude": float(lon),
                "altitude": float(altitude),
            },
            ensure_ascii=False,
        )
        url = (
            f"{BASE}/pptSign/stuSignajax?enc={enc}&name={self.name}&activeId={act['activeId']}"
            f"&uid={self._uid}&clientip=&location={urllib.parse.quote(loc)}&latitude=-1&longitude=-1"
            f"&fid={self.fid}&appType=15"
        )
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
        r = self.s.post(
            f"https://pan-yz.chaoxing.com/opt/listres?{params}", data=params
        )
        try:
            for f in r.json().get("list", []):
                if f.get("name") in ("0.jpg", "0.png"):
                    return f["objectId"]
        except (json.JSONDecodeError, ValueError):
            pass
        return None


def describe_result(msg: str) -> str:
    """把服务端返回码翻译成人话"""
    if msg == "success":
        return "签到成功"
    if msg.startswith("locationAuthError"):
        code = msg.split("_")[-1]
        return (
            f"位置授权校验未通过({code}): 服务端不认这次请求的客户端身份。"
            f"已用到 App 版 UA + 签名 locationResult, 若仍失败则是超星改了下发规则, "
            f"用 --debug 看请求详情再对"
        )
    if msg.startswith("errorLocation"):
        return f"位置不在签到范围内({msg}): 请填写教师设置的坐标"
    if msg == "validate":
        return "本次签到需要图形验证码(validate), 脚本无法自动完成"
    if msg == "success2":
        return "签到已过期"
    if msg == "您已签到过了":
        return "已签到过"
    return msg


def parse_location(text: str):
    """解析 --location, 格式是「经度,纬度,地址」, 返回 (lon, lat, address) 或 None

    与地图/其他项目的常见写法一致(经度在前)。顺序写反是最常见的错, 这里靠取值
    范围(纬度±90)直接拦下来, 免得把 113.516 当纬度发出去、只换回一句看不懂的服务端报错。
    """
    parts = [p.strip() for p in text.split(",", 2)]
    if len(parts) != 3 or not all(parts):
        log("--location 格式应为 '经度,纬度,地址', 如 113.516,34.817,河南科技大学")
        return None
    lon, lat, address = parts
    try:
        lon_f, lat_f = float(lon), float(lat)
    except ValueError:
        log(f"--location 里的经纬度不是数字: {lon},{lat}")
        return None
    if abs(lat_f) > 90 or abs(lon_f) > 180:
        log(
            f"经纬度超出取值范围(经度±180, 纬度±90): 解析到 经度={lon_f}, 纬度={lat_f}"
            " —— 注意顺序是「经度,纬度」, 别写成纬度在前"
        )
        return None
    return lon, lat, address


def safe_input(prompt: str = "") -> str:
    """input 封装: stdin 关闭(EOF)时返回空串而非抛异常"""
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def do_sign(
    signer: Signer, act: dict, location: str = "", enc: str = "", sign_code: str = ""
) -> bool:
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
            log(
                "位置签到需要 --location '经度,纬度,地址', 如 --location 113.516,34.817,河南科技大学"
            )
            lon = safe_input("经度: ")
            lat = safe_input("纬度: ")
            address = safe_input("详细地址: ")
            if not (lon and lat and address):
                log("位置参数不完整, 跳过")
                return False
        else:
            parsed = parse_location(location)
            if not parsed:
                return False
            lon, lat, address = parsed
        msg = signer.sign_location(act, lat, lon, address)
    elif act["otherId"] == 2:
        if not enc:
            log("二维码签到需要 --enc 参数(微信扫码抠出 enc)")
            enc = safe_input("enc: ")
        if not enc:
            log("未提供 enc, 跳过")
            return False
        if not location:
            lon, lat, address = "113.516", "34.817", "河南科技大学"
        else:
            parsed = parse_location(location)
            if not parsed:
                return False
            lon, lat, address = parsed
        msg = signer.sign_qrcode(act, enc, lat, lon, address)
    else:
        msg = signer.sign_general(act)

    ok = msg == "success"
    log("签到成功!" if ok else f"签到结果: {describe_result(msg)}")
    if ok:
        print("\a", end="", flush=True)  # 提示音
    return ok


def notify(title: str, text: str) -> None:
    """发送系统通知: Termux 环境用 termux-notification, 其他环境仅打印+响铃"""
    import shutil

    if shutil.which("termux-notification"):
        import subprocess

        try:
            subprocess.Popen(
                ["termux-notification", "-t", title, "-c", text],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass
    print("\a", end="", flush=True)


def monitor(
    signer: Signer,
    interval: int,
    location: str,
    enc: str,
    sign_code: str,
    confirm: bool = False,
) -> None:
    log(
        f"监听模式启动, 每 {interval}s 检测 {len(signer.get_courses())} 门课, Ctrl+C 退出"
        + (" (确认模式: 检测到签到需手动确认)" if confirm else "")
    )
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
    ap.add_argument("--location", default="", help="位置签到: '经度,纬度,地址'")
    ap.add_argument("--enc", default="", help="二维码签到 enc 参数")
    ap.add_argument("--signcode", default="", help="手势/签到码签到的码")
    ap.add_argument(
        "--confirm",
        action="store_true",
        help="确认模式: 检测到签到后需手动确认再签(适合手机Termux)",
    )
    ap.add_argument("--debug", action="store_true", help="打印请求参数与原始响应")
    args = ap.parse_args()

    global DEBUG
    DEBUG = args.debug

    signer = Signer(args.phone, args.password)
    if args.monitor:
        monitor(
            signer, args.interval, args.location, args.enc, args.signcode, args.confirm
        )
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
