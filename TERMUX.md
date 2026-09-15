# 📱 手机端部署教程（Termux / 安卓）

把自动签到跑在**安卓手机**上：后台持续监听，检测到签到任务后**通知你确认**，确认后自动签到。

> 需要一台安卓手机。iPhone 后台限制严格，不推荐（见文末）。

---

## 1. 安装 Termux

- **推荐渠道**：F-Droid 下载 [Termux](https://f-droid.org/packages/com.termux/)（Google Play 版已停止维护，功能残缺）
- 安装后打开，首次会初始化环境，等待出现命令行提示符 `$`

## 2. 安装 Python 和依赖

在 Termux 里依次执行：

```bash
# 更新软件源（首次必做）
pkg update && pkg upgrade -y

# 安装 Python
pkg install python -y

# 安装依赖
pip install requests pyDes
```

## 3. 下载脚本

GitHub 直连在部分网络下不可用，用镜像接口下载（任选其一）：

**方式 A（推荐，走 GitHub API）：**
```bash
curl -sL -H "Accept: application/vnd.github.v3+json" \
  https://api.github.com/repos/HEDBS/chaoxing-sign/contents/chaoxing_sign.py \
  | python -c "import sys,json,base64;open('chaoxing_sign.py','w').write(base64.b64decode(json.load(sys.stdin)['content']).decode())"
```

**方式 B（git clone，需要网络能通 GitHub）：**
```bash
pkg install git -y
git clone https://github.com/HEDBS/chaoxing-sign.git
cd chaoxing-sign
```

**方式 C（最快）：电脑上下载后传手机**
在电脑 `E:\Hermes\chaoxing-py\chaoxing_sign.py` 拷到手机（微信文件传输/数据线），放到 Termux 能访问的位置（先执行 `termux-setup-storage` 授权，文件放 `~/storage/downloads/`，然后 `cp ~/storage/downloads/chaoxing_sign.py ~/`）。

## 4. 运行

```bash
# 进入脚本目录（方式A/B 下载的路径）
cd ~

# ① 确认模式（推荐）：检测到签到 → 通知 + 等你确认 → 签到
python chaoxing_sign.py 手机号 密码 --monitor --confirm --interval 60

# ② 全自动模式：检测到签到直接签（无需码的类型）
python chaoxing_sign.py 手机号 密码 --monitor --interval 60

# ③ 预置签到码/位置（手势/签到码/位置也能自动）
python chaoxing_sign.py 手机号 密码 --monitor --confirm \
  --signcode 0721 --location "113.516,34.817,河南科技大学"
```

**确认模式的交互**：检测到签到后 Termux 会响铃 + 发系统通知（需装 termux-api，见下），回到 Termux 看到提示：

```
[08-07 09:00:01] 检测到活动: [普通/拍照] 普通签到 —— 回车确认签到, 输入 n 跳过
```

- 按 **回车** → 立即签到
- 输入 **n** + 回车 → 跳过本次（比如老师课上发的、人不在场不方便签的）

## 5. 保持后台运行（关键）

安卓会杀后台进程，按下面三步设置：

**① 唤醒锁**（防止息屏后 CPU 休眠）：
```bash
pkg install termux-api -y
termux-wake-lock
```

**② 电池优化白名单**：设置 → 应用 → Termux → 电池 → **不限制/无限制**

**③ 息屏保持**：设置 → 应用 → Termux → 耗电管理 → 允许后台运行/自启动

设置完锁屏也能持续监听。

## 6. 签到通知（可选）

装了 termux-api 后，检测到签到会自动发**系统通知**（通知栏可见）：

```bash
pkg install termux-api -y
```

无需额外配置，脚本检测到 `termux-notification` 命令会自动调用。

## 7. 常见问题

**Q: 跑一会就被系统杀了？**
确认做了第 5 步（wake-lock + 电池白名单）。部分国产 ROM（MIUI/EMUI）还要在"自启动管理"里允许 Termux。

**Q: 提示 `ModuleNotFoundError: No module named 'requests'`？**
`pip install requests pyDes` 没成功，重跑一次；或先 `pkg install python-pip`。

**Q: 提示「登录失败」？**
检查手机号密码，或网络问题重试。

**Q: 锁屏后怎么看到通知？**
确认模式下检测到签到会发系统通知（通知栏有「学习通签到」标题），点开 Termux 回车确认即可。

**Q: 电量消耗大吗？**
每 60 秒一次轻量 HTTP 请求，一晚上耗电约 1-3%，可忽略。

---

## iPhone 用户替代方案

iOS 无 Termux、后台限制严格，长期监听不可行。替代：

1. **家里电脑/旧安卓机**跑 `--monitor --confirm`，效果相同
2. 或接 **Server酱/PushPlus** 把签到结果推到微信（改造成本高，不建议）

---

## 免责声明

仅供学习交流。请只签自己的课，代签/伪造位置有风控风险。
