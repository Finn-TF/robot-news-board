#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepSeek 连通性自检（不写入任何文件，不打印 key）。

用法：
    $env:DEEPSEEK_API_KEY = "..."     # PowerShell，只在本机终端设置
    python check_llm.py

    set DEEPSEEK_API_KEY=...          # cmd
    export DEEPSEEK_API_KEY=...       # bash

它会读取 config.json 里的 base_url / model，发一次最小请求，告诉你：
- key 是否有效（401/403）
- 余额是否足够（402/429 等）
- 模型名与接口地址是否正确（404/400）
- 网络是否可通（超时）
成功时打印「连通成功」与模型回显；失败时打印可操作的中文原因。
退出码：0 成功，1 key 未设置，2 调用失败。
"""

import json
import os
import sys
import urllib.error
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")


def load_llm():
    cfg = {"model": "deepseek-chat", "base_url": "https://api.deepseek.com",
           "api_key_env": "DEEPSEEK_API_KEY", "timeout": 30, "max_tokens": 32}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        llm = data.get("llm") or {}
        for k in cfg:
            if llm.get(k):
                cfg[k] = llm[k]
    except Exception as e:  # noqa: BLE001
        print(f"[提示] 读取 config.json 失败，使用默认配置：{e}")
    return cfg


def human_error(code, body):
    """把接口返回翻译成可操作的中文说明，隐藏任何疑似 key 的内容。"""
    body = (body or "").strip()[:400]
    hints = {
        400: "请求格式被拒绝：多半是模型名不对，或 base_url 多写了 /chat/completions。",
        401: "鉴权失败：key 无效、复制不完整或已被删除。请到 platform.deepseek.com 的 API keys 重新创建并完整复制。",
        402: "账户余额不足：到 DeepSeek 控制台充值后再试。",
        403: "无权限：key 被禁用，或账户状态异常。",
        404: "接口或模型不存在：检查 config.json 的 base_url（应为 https://api.deepseek.com）和 model（如 deepseek-chat）。",
        422: "参数不被接受：检查 model 名称与 max_tokens 设置。",
        429: "请求过于频繁或额度受限：稍后再试，或检查账户限额。",
        500: "DeepSeek 服务端错误：稍后重试。",
        502: "网关错误：稍后重试。",
        503: "服务暂不可用：稍后重试。",
    }
    return f"HTTP {code}：{hints.get(code, '调用失败。')}\n接口返回：{body}"


def main():
    cfg = load_llm()
    env_name = cfg.get("api_key_env") or "DEEPSEEK_API_KEY"
    key = (os.environ.get(env_name) or "").strip()
    if not key:
        print(f"[未设置] 环境变量 {env_name} 为空。")
        print("  本机临时设置（PowerShell）：$env:" + env_name + ' = "你的 key"')
        print("  cmd：set " + env_name + "=你的 key")
        print("  注意：只在当前终端生效，不会写进项目文件，也不需要写进 config.json。")
        return 1

    masked = key[:3] + "***" + key[-2:] if len(key) > 8 else "***"
    base = (cfg.get("base_url") or "https://api.deepseek.com").rstrip("/")
    url = base + "/chat/completions"
    print(f"接口：{url}")
    print(f"模型：{cfg.get('model')}")
    print(f"key ：{masked}（长度 {len(key)}）")

    payload = {
        "model": cfg.get("model"),
        "messages": [
            {"role": "system", "content": "你是一个连通性测试助手，只回复四个字。"},
            {"role": "user", "content": "请回复：连通成功"},
        ],
        "temperature": 0,
        "max_tokens": int(cfg.get("max_tokens") or 32),
        "stream": False,
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=int(cfg.get("timeout") or 30)) as resp:
            raw = resp.read().decode("utf-8", "replace")
            data = json.loads(raw)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        body = body.replace(key, "***")
        print("[失败] " + human_error(e.code, body))
        return 2
    except urllib.error.URLError as e:
        print(f"[失败] 网络不通或超时：{e.reason}")
        print("  检查本机代理/网络；若使用代理，请在终端设置 HTTPS_PROXY 后再试。")
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"[失败] {type(e).__name__}: {e}")
        return 2

    try:
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    except Exception:  # noqa: BLE001
        text = ""
    usage = data.get("usage") or {}
    print("[成功] DeepSeek 连通，模型有正常返回。")
    print(f"  模型回显：{text.strip()[:60] or '(空)'}")
    if usage:
        print(f"  token 用量：prompt {usage.get('prompt_tokens', '-')} / "
              f"completion {usage.get('completion_tokens', '-')}")
    print("  下一步：把同一把 key 加到仓库 Settings → Secrets and variables → Actions → "
          "New repository secret，名称 DEEPSEEK_API_KEY；config.json 的 llm.enabled 已为 true。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
