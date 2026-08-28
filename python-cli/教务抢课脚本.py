# -*- coding: utf-8 -*-
"""
教务抢课脚本（Python 发包版）
==============================
绕过浏览器，直接向教务系统选课接口发送请求。
适合：网页崩溃/油猴脚本失效时使用，也可与油猴脚本同时运行。

⚠️ 使用前必须自行抓包替换以下内容（见文末说明）：
  1. JSESSIONID（登录教务后的会话 Cookie）
  2. jxb_ids / kch_id / xkkz_id / zyh_id 等课程与选课批次参数

⚠️ 本脚本默认以固定间隔无限循环请求，请勿滥用，以免给教务系统造成压力。
"""
import time

import requests

# 选课接口
url = 'https://jw.qlu.edu.cn/jwglxt/xsxk/zzxkyzbjk_xkBcZyZzxkYzb.html?gnmkdm=N253512'
# 课程状态查询接口
url2 = 'https://jw.qlu.edu.cn/jwglxt/xsxk/zzxkyzb_cxXkTitleMsg.html?gnmkdm=N253512'

headers = {
    'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
    # cookie 请自行抓取：F12 → 网络 → 任意教务请求 → 请求头 → Cookie
    'Cookie': 'JSESSIONID=**********（替换成你的）'
}

# 选课请求参数（从 F12 网络面板抓包复制）
data = {
    'jxb_ids': '（替换：教学班ID，从选课请求中抓取）',
    'kch_id': '（替换：课程ID）',
    'kcmc': '（替换：课程名称，如 (B08t022)传统文化与现代企业管理 - 1.0 学分）',
    'rwlx': '2',
    'rlkz': '0',
    'rlzlkz': '1',
    'sxbj': '1',
    'xxkbj': '0',
    'qz': '0',
    'cxbj': '0',
    'xkkz_id': '（替换：选课控制ID，选课前从页面/抓包获取）',
    'njdm_id': '（替换：年级代码）',
    'zyh_id': '（替换：专业号）',
    'kklxdm': '10',
    'xklc': '1',
    'xkxnm': '（替换：学年，如 2025）',
    'xkxqm': '（替换：学期，如 3）',
}

# 课程状态查询参数（注意 jxb_ids 需与选课的一致）
payload = {
    "jxb_ids": data['jxb_ids'],
    "xkxnm": data['xkxnm'],
    "xkxqm": data['xkxqm'],
    "bj": "7",
    "kch_id": data['kch_id'],
    "njdm_id": data['njdm_id'],
    "zyh_id": data['zyh_id'],
    "kklxdm": data['kklxdm'],
}

INTERVAL = 0.2  # 每次请求间隔（秒），请勿设置过小

if '（替换' in ' '.join(str(v) for v in data.values()):
    print('⚠️  请先抓包替换脚本中的 JSESSIONID 和课程参数，再运行！')
    print('    方法：登录教务选课页 → F12 → 网络(Network) → 选中课请求 →')
    print('    复制请求头里的 Cookie 与请求体里的表单参数。')
    exit(1)

while True:
    response = requests.post(url, data=data, headers=headers)
    reeee = requests.post(url2, data=payload, headers=headers)
    response_json = response.json()
    jsone = reeee.json()
    print(response_json)
    # 若选课成功则停止（可自行按需调整）
    if response_json.get('success') or str(response_json.get('flag')) == '1':
        print('✅ 选课成功！')
        break
    time.sleep(INTERVAL)
