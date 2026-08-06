# 学习通自动签到 (Chaoxing Auto Sign)

基于超星学习通网页协议复现的 **Python 自动签到工具**。单文件、零框架、跨平台（Windows/macOS/Linux），支持全部 6 种签到类型，可挂机监听自动签到。

> ⚠️ 本项目仅用于学习网络协议与接口编程，请只签自己的课。代签、伪造位置等行为有账号风控风险，后果自负。

---

## 📋 功能总览

| 签到类型 | 自动化程度 | 需要准备 |
|---|---|---|
| 普通签到 | ✅ 全自动 | 无 |
| 拍照签到 | ✅ 全自动 | 超星云盘根目录放一张 `0.jpg` 或 `0.png` |
| 位置签到 | ⚠️ 半自动 | 经纬度 + 地址（`--location`） |
| 手势签到 | ⚠️ 半自动 | 手势码（`--signcode`，3×3 九宫格轨迹编码） |
| 签到码签到 | ⚠️ 半自动 | 老师公布的签到码（`--signcode`） |
| 二维码签到 | ⚠️ 半自动 | 二维码中的 enc 参数（`--enc`，微信扫码抠出） |

**监听模式**：后台轮询全部课程，老师一发签到，几十秒内自动完成签到（无需码的类型）。

---

## 🛠 环境准备

### 1. 安装 Python

- Windows: 去 [python.org](https://www.python.org/downloads/) 下载 3.8+ 安装，**安装时勾选 "Add Python to PATH"**
- macOS: `brew install python3`
- Linux: `sudo apt install python3 python3-pip`

验证安装：

```bash
python --version
```

### 2. 安装依赖（只需两个库）

```bash
pip install requests pyDes
```

### 3. 下载项目

```bash
git clone https://github.com/HEDBS/chaoxing-sign.git
cd chaoxing-sign
```

> 📱 想跑在**安卓手机**上后台监听？看 [TERMUX.md](TERMUX.md)（Termux 部署教程，支持检测到签到后通知确认）。

---

## 🚀 快速开始（手动签到）

```bash
python chaoxing_sign.py 你的手机号 你的密码
```

程序会自动：

1. 登录学习通（密码 DES 加密传输）
2. 拉取你的全部课程
3. 轮询检测进行中的签到活动
4. 检测到 → 预签到 → 按类型自动提交

```
[08-06 20:53:05] 登录成功: 王至
[08-06 20:53:05] 共 27 门课, 检测签到活动中...
[08-06 20:53:06] 检测到活动: [普通/拍照] 普通签到
[08-06 20:53:07] 签到成功!
```

---

## 🎓 六种签到类型详细教程

### 1. 普通签到（全自动）

最常见的类型，无需任何准备：

```bash
python chaoxing_sign.py 手机号 密码
```

### 2. 拍照签到（全自动）

需要先在**超星云盘**放一张照片：

1. 浏览器登录 [pan-yz.chaoxing.com](https://pan-yz.chaoxing.com)（用学习通账号）
2. 在**根目录**上传一张照片，命名为 `0.jpg` 或 `0.png`
3. 运行工具，检测到拍照签到时自动取图提交

> 不想用云盘？可以每次拍照签到前手动换图，或用 `--objectid` 指定云盘文件 ID（进阶用法）。

### 3. 位置签到（半自动）

服务端不校验真实位置，只需提供经纬度和地址文本：

1. 打开 [百度拾取坐标系统](https://api.map.baidu.com/lbsapi/getpoint/index.html)
2. 点击你教室的位置，右上角出现经纬度，复制
3. 运行：

```bash
# 格式: 纬度,经度,地址
python chaoxing_sign.py 手机号 密码 --location "34.817,113.516,河南科技大学"
```

> 教室位置固定的话，这个参数可以一直复用。

### 4. 手势签到（半自动）

需要手势码。手势是 3×3 九宫格轨迹，编码为数字序列：

```
1 2 3
4 5 6
7 8 9
```

比如画「Z」字 = `1235789`。老师投屏展示手势后，把轨迹编码传入：

```bash
python chaoxing_sign.py 手机号 密码 --signcode 1235789
```

### 5. 签到码签到（半自动）

老师公布签到码（通常 4 位数字），直接传入：

```bash
python chaoxing_sign.py 手机号 密码 --signcode 0721
```

### 6. 二维码签到（半自动）

1. 用微信扫老师投屏的二维码
2. 二维码内容是一串 URL，找到其中 `enc` 参数（形如 `1D0A628CK317F44CCC378M5KD92`）
3. 传入：

```bash
python chaoxing_sign.py 手机号 密码 --enc 1D0A628CK317F44CCC378M5KD92
```

> 动态二维码（10 秒刷新）需要快速操作；完全自动需要接 OCR，性价比低。

---

## 🔔 监听模式（开学挂机用）

每 N 秒轮询全部课程，检测到签到自动签：

```bash
# 电脑挂机：全自动（默认，检测到直接签，无需人工）
python chaoxing_sign.py 手机号 密码 --monitor

# 每 30 秒检测一次
python chaoxing_sign.py 手机号 密码 --monitor --interval 30

# 预置签到码 + 位置，手势/签到码/位置也能全自动
python chaoxing_sign.py 手机号 密码 --monitor --signcode 0721 --location "34.817,113.516,河南科技大学"
```

### 确认模式（适合手机，防止误签）

加 `--confirm` 后：检测到签到会**通知你 + 等待确认**，回车才签，输 `n` 跳过：

```bash
# 手机 Termux 推荐：检测到签到 → 通知确认 → 签到
python chaoxing_sign.py 手机号 密码 --monitor --confirm --interval 60
```

| 场景 | 命令 | 行为 |
|---|---|---|
| 电脑挂机 | `--monitor`（不加 confirm） | 检测到直接签，无需人工 |
| 手机挂机 | `--monitor --confirm` | 检测到 → 通知 + 确认 → 签 |

> 📱 手机端完整部署看 [TERMUX.md](TERMUX.md)。

- `Ctrl+C` 退出
- 日志写入 `sign_log.txt`，签到成功有提示音

### Windows 开机自启（可选）

用任务计划程序或把命令写进 `.bat` 放启动文件夹：

```bat
@echo off
cd /d E:\chaoxing-sign
python chaoxing_sign.py 手机号 密码 --monitor --signcode 0721
```

---

## ❓ 常见问题

**Q: 提示「登录失败」？**
检查手机号/密码是否正确；密码错误会返回「用户名或密码错误」。

**Q: 提示「未检测到有效签到活动」？**
当前没有进行中的签到（暑假/下课时间正常）。工具只认 `status=1` 且开始 2 小时内的活动。

**Q: 提示「签到失败[90002]」？**
手势/签到码签到时没传码或码错误。请用 `--signcode` 传入正确码。

**Q: 提示「码校验失败: 手势不正确」？**
`--signcode` 的码不对，checkSignCode 接口校验失败。确认码后重试。

**Q: 提示「您已签到过了」？**
说明该活动你已经签过了（可能是工具签的或你自己签的），正常。

**Q: 拍照签到提示「云盘未找到图片」？**
检查云盘根目录有没有 `0.jpg`/`0.png`，文件名必须完全一致。

**Q: 换了电脑/系统还能用吗？**
能，只要装了 Python + requests + pyDes。

**Q: 会封号吗？**
正常使用（一天几次签到请求）风险极低。监听模式建议间隔 30 秒以上，不要高频轮询。

---

## 🔬 协议原理（2026-08 实测）

```
登录     POST passport2.chaoxing.com/fanyalogin
         密码 DES-ECB 加密（密钥取硬编码串前 8 字节）
         set-cookie 拿到 _uid/_d/vc3/fid 等凭证

课程     GET mooc1-api.chaoxing.com/mycourse/backclazzdata?rss=1
         JSON: content.id=classId, course.data[].id=courseId

检测     GET mobilelearn.chaoxing.com/v2/apis/active/student/activelist
         activeList[0] 满足 status==1 && otherId∈[0,5] 即为有效签到
         otherId: 0=普通/拍照, 2=二维码, 3=手势, 4=位置, 5=签到码

预签到   newsign/preSign → pptSign/analysis(抠code) → pptSign/analysis2

提交     GET mobilelearn.chaoxing.com/pptSign/stuSignajax
         普通:   latitude=-1&longitude=-1&fid=<学校id>&name=<姓名>
         位置:   address&latitude&longitude&ifTiJiao=1
         拍照:   objectId=<云盘文件ID>
         二维码: enc&location={JSON位置}
         手势/签到码: signCode=<码>，必须先过 checkSignCode 校验
```

**踩坑记录**（本项目开发时实测）：

1. `courselistdata` 页面已改版（clazzId=0 无效），必须用 `backclazzdata` JSON 接口
2. `accountManage` 页面结构改版，姓名从 `<p class="fr colorBlue">` 提取
3. 手势/签到码服务端强校验：直接提交返回 `90002`，必须 `checkSignCode` 校验通过后带 `signCode` 提交
4. `fid` 参数必须用登录 cookie 里的真实学校 ID（不能写死 -1）
5. 老项目（2020-2023）的「跳过校验直接提交」方案已失效

---

## 📄 免责声明

本项目仅供学习网络通信、接口编程技术。使用本项目产生的任何后果（包括但不限于账号异常、考勤记录问题）与作者无关。请勿用于商业用途或侵害他人权益。
