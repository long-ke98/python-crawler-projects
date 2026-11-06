# -*- coding: utf-8 -*-
"""
Bank_stock.py：中国银河证券 - 融资融券标的证券名单采集
只负责把"标的证券"调整名单存进 MongoDB 的 margin_list 集合（约625条）

数据来源有两种：
  1. 公告正文里直接嵌的HTML表格（大部分公告是这种）
  2. 正文写"详见附件"的，自动下载标的证券PDF附件解析（少量公告是这种）

注意：折算率相关的PDF不在这个脚本处理，归 Bank_y.py 管
"""
import requests   # 用来发HTTP请求抓网页
import time       # 用来加延时，避免请求太快被封
import re         # 正则表达式，用来从HTML里抠表格
import io         # 把PDF二进制数据包装成文件对象给pdfplumber读
import pymongo    # 连MongoDB
import pdfplumber # 解析PDF里的表格

# ===================== 配置区 =====================
BASE_URL = "http://www.chinastock.com.cn/website2020"   # 网站接口前缀
CATEGORY = "yhgg_调整融资融券标的证券名单的公告"          # 左侧菜单选的公告分类
MAX_PAGES = 3                                            # 只翻前3页（最新的公告都在前面）

# 请求头：模拟浏览器访问
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/154.0",
    "X-Requested-With": "XMLHttpRequest",   # 告诉服务器这是AJAX请求
    "Referer": "http://www.chinastock.com.cn/newsite/cgs-services/stockFinance/businessAnnc.html?type=marginList"
}

# 连本地MongoDB：库叫 chinastock，集合叫 margin_list（存标的证券名单）
collection = pymongo.MongoClient("127.0.0.1", 27017)["chinastock"]["margin_list"]


def fetch_announcement_list(page_no):
    """抓第 page_no 页的公告列表，返回公告数组（每条含docId、标题、时间等）"""
    params = {"pageSize": "50", "pageNo": str(page_no),
              "dayLimit": "9000", "catName": CATEGORY}
    # 最多重试3次，网络抖动能自动恢复
    for retry in range(3):
        try:
            resp = requests.get(BASE_URL + "/doc/queryDocList",
                                headers=REQUEST_HEADERS, params=params, timeout=30).json()
            return resp["data"]["list"] or []
        except Exception:
            print(f"  列表第{page_no}页请求超时，第{retry+1}次重试...")
            time.sleep(2)
    return []


def fetch_announcement_detail(doc_id):
    """根据公告id抓详情页，返回整个data字典（里面有正文content和附件列表attachedList）"""
    # 下载PDF时必须带Referer到详情页，否则服务器返回错误
    referer = f"{BASE_URL.replace('/website2020','')}/newsite/cgs-services/product/productAnncDetail.html?type=stockFinance&docId={doc_id}"
    for retry in range(3):
        try:
            resp = requests.get(BASE_URL + "/doc/detailInfo",
                                headers=dict(REQUEST_HEADERS, Referer=referer),
                                params={"docId": doc_id}, timeout=30).json()
            return resp.get("data") or {}
        except Exception:
            time.sleep(2)
    return {}


def parse_stock_rows(html):
    """从HTML正文里正则抠出三列表格：证券代码 / 证券简称 / 调整方式"""
    rows = []
    # 找所有<tr>...</tr>表格行
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        # 从每行里抠出所有<td>单元格，并去掉HTML标签
        cells = [re.sub(r"<[^>]+>", "", td).strip()
                 for td in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        # 只留数据行：至少3列，且第一列长得像证券代码（带.或者纯数字）
        if len(cells) >= 3 and cells[0] and ("." in cells[0] or cells[0].isdigit()):
            rows.append(cells)
    return rows


def fetch_pdf_rows(attached_doc_id, doc_id):
    """下载标的证券PDF附件（三列），返回[(代码,简称,调整方式), ...]"""
    referer = f"{BASE_URL.replace('/website2020','')}/newsite/cgs-services/product/productAnncDetail.html?type=stockFinance&docId={doc_id}"
    rows = []
    try:
        # attachedInfo接口返回的是PDF二进制文件，不是JSON
        resp = requests.get(BASE_URL + "/doc/attachedInfo",
                            headers=dict(REQUEST_HEADERS, Referer=referer),
                            params={"docId": attached_doc_id}, timeout=60, verify=False)
        if not resp.content.startswith(b"%PDF"):   # 不是PDF开头说明下错了
            return rows
        # 把二进制数据包成文件对象，pdfplumber直接读内存里的PDF
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            for page in pdf.pages:                   # 逐页解析
                for table in page.extract_tables(): # 每页可能有多个表格
                    for cells in table:
                        cells = [c.strip() if c else "" for c in cells]
                        # 跳过表头行和空行
                        if not cells[0] or "证券代码" in cells[0]:
                            continue
                        if "." not in cells[0]:      # 第一列必须像代码（带.）
                            continue
                        if len(cells) >= 3:
                            rows.append((cells[0], cells[1], cells[2]))
    except Exception as e:
        print(f"  PDF {attached_doc_id} 解析失败：{e}")
    return rows


# ====== 主流程：一页一页翻，逐条入库 ======
serial_no = 0   # 全局自增序号，从1开始
for page_no in range(1, MAX_PAGES + 1):
    print(f"正在获取第{page_no}页数据，请稍后.....")
    announcements = fetch_announcement_list(page_no)
    if not announcements:          # 这页没公告了，提前结束
        break

    page_count = 0   # 记录本页抓了多少条，最后打印用
    for ann in announcements:
        detail = fetch_announcement_detail(ann["docId"])
        # 先试试正文里有没有HTML表格
        rows = parse_stock_rows(detail.get("content") or "")

        # 正文没表格，说明是"详见附件"类型的公告，去下PDF
        if not rows:
            for att in (detail.get("attachedList") or []):
                att_name = att.get("attachedName") or ""
                if not att_name.lower().endswith(".pdf"):
                    continue
                # 折算率PDF不归这个脚本管，跳过
                if "可充抵保证金" in att_name or "折算率" in att_name:
                    continue
                print(f"  正文无表格，下载标的证券PDF：{att_name}")
                rows.extend(fetch_pdf_rows(att["attachedDocId"], ann["docId"]))

        # 把抓到的每一行写入MongoDB
        for stock in rows:
            serial_no += 1
            page_count += 1
            record = {
                "序号": serial_no,
                "标题": ann["title"],
                "发布时间": ann["uploadDate"],
                "发布人": ann["source"],
                "证券代码": stock[0],
                "证券简称": stock[1],
                "调整方式": stock[2]
            }
            # 按"标题+证券代码+调整方式"去重
            collection.update_one(
                {"标题": ann["title"], "证券代码": stock[0], "调整方式": stock[2]},
                {"$set": record}, upsert=True)
        time.sleep(0.3)   # 每条间隔0.3秒，别把服务器打崩

    print(f"第{page_no}页获取{page_count}条信息。")

print(f"\n全部结束，margin_list 共 {collection.count_documents({})} 条")
