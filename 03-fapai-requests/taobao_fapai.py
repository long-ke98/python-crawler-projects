"""
法拍房爬虫 fapaino.5
接口: 淘宝阿里拍卖-资产搜索页 (zichansearch)
功能: 按城市批量抓取法拍房源，自动去重后存入 MongoDB
"""
import requests
import time
import hashlib
import json
import pymongo

# ===================== 抓取配置 =====================
# 省会/直辖市城市代码（按大区分组，需要抓哪个直接复制到下方 TARGET_CITIES）
# 华北: ("北京","110000"), ("天津","120000"), ("石家庄","130000"), ("太原","140000"), ("呼和浩特","150100")
# 东北: ("沈阳","210100"), ("长春","220100"), ("哈尔滨","230100")
# 华东: ("上海","310000"), ("南京","320100"), ("杭州","330100"), ("合肥","340100"), ("福州","350100"), ("南昌","360100"), ("济南","370100")
# 华中: ("郑州","410100"), ("武汉","420100"), ("长沙","430100")
# 华南: ("广州","440100"), ("南宁","450100"), ("海口","460100")
# 西南: ("成都","510100"), ("贵阳","520100"), ("昆明","530100"), ("拉萨","540100")
# 西北: ("西安","610100"), ("兰州","620100"), ("西宁","630100"), ("银川","640100"), ("乌鲁木齐","650100")

# 要抓的城市列表: (城市名, 行政区划代码)
TARGET_CITIES = [
    ("北京", "110000"),
    ("上海", "310000"),
    ("广州", "440100"),
]
PAGE_RANGE = range(1, 16)   # 每个城市抓第 1 ~ 15 页
# ===================================================

# ---- 登录凭证 (从浏览器 F12 复制) ----
LOGIN_COOKIE = "你的淘宝Cookie，从浏览器F12里复制"
APP_TOKEN = "你的_m_h5_tk下划线前的部分"
APP_KEY = "12574478"

# ---- MongoDB 连接 ----
mongo_client = pymongo.MongoClient("127.0.0.1", 27017)
collection = mongo_client["fapai"]["全部房源"]


def fetch_list_page(city_code, page_num, session_sid=None):
    """
    请求一页法拍房列表数据。
    参数:
        city_code:   城市行政区划代码, 如 "110000"
        page_num:    页码, 从 1 开始
        session_sid: 翻页会话 ID, 第1页不传, 之后从响应里取并带回
    返回:
        (房源列表, 新的 session_sid)
    """
    # 构造内层模块上下文 (房源列表模块)
    module_context = json.dumps({
        "fcatV4Ids": ["206060601"],          # 206060601 = 住宅类法拍
        "page": str(page_num),
        "locationCodes": [city_code],        # 城市筛选
        "userInfo": {},
        "appendMap": {"sid": session_sid} if session_sid else {}
    }, ensure_ascii=False)

    # 组装完整请求体
    request_body = {
        "dfApp": "auctionwalle",
        "dfApiName": "auctionwalle.datou.getPageModulesData",
        "dfVariables": json.dumps({
            "pageId": 1910955,
            "moduleIds": "2004318340:items",   # 只取房源列表模块
            "context": {
                "_b_2004318340:items": module_context,
                "userInfo": "{}",
                "device": "pc",
                "sceneCode": "20210823QCG72BUD",
                "appendMap": '{"isAutoSelect": true}'
            }
        }, ensure_ascii=False),
        "dfUniqueId": "1910955.2004318340:items",
        "dfVariablesRecover": "{}"
    }
    body_str = json.dumps(request_body, ensure_ascii=False, separators=(",", ":"))

    # mtop 接口签名: md5(token & 时间戳 & appKey & 请求体)
    timestamp = str(int(time.time() * 1000))
    sign = hashlib.md5(f"{APP_TOKEN}&{timestamp}&{APP_KEY}&{body_str}".encode()).hexdigest()

    api_url = (
        "https://h5api.m.taobao.com/h5/mtop.taobao.datafront.invoke.auctionwalle/1.0/"
        f"?jsv=2.6.1&appKey={APP_KEY}&t={timestamp}&sign={sign}&bxPageId=1910955"
        f"&api=mtop.taobao.datafront.invoke.auctionwalle&v=1.0&type=originaljson&dataType=json"
    )

    response = requests.post(api_url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/154.0",
        "referer": "https://zc-paimai.taobao.com/wow/pm/default/pc/zichansearch",
        "Cookie": LOGIN_COOKIE
    }, data={"data": body_str}, timeout=15).json()

    # 从嵌套结构里取出房源列表和下一页要用的 sid
    items = response["data"]["data"]["GQL_getPageModulesData"]["2004318340"]["items"]
    return items.get("schemeList", []), items.get("dsExtra", {}).get("sid")


# ===================== 主流程: 逐城市逐页抓取 =====================
for city_name, city_code in TARGET_CITIES:
    print(f"\n== 开始抓取【{city_name}】 ==")
    session_sid = None          # 翻页会话ID, 第1页为空, 之后自动带回
    seen_titles = set()         # 当前城市已见过的标题, 用于去重
    total_inserted = 0          # 当前城市实际入库条数

    for page_num in PAGE_RANGE:
        house_list, session_sid = fetch_list_page(city_code, page_num, session_sid)
        skipped_dup = 0

        for house in house_list:
            title = house["auctionTitle"]
            # 标题重复则跳过, 不入库
            if title in seen_titles:
                skipped_dup += 1
                continue
            seen_titles.add(title)

            # auctionBenefits 是小区名/面积等标签的数组, 从中拆分
            benefits = house.get("auctionBenefits") or []
            community = next((b for b in benefits if "m²" not in b and "室" not in b), "暂无")
            area = next((b for b in benefits if "m²" in b), "暂无")
            price = f'{house["displayInitialPrice"]}{house["displayInitialPriceUnit"]}'

            collection.insert_one({
                "房源ID": house["itemId"],
                "城市": city_name,
                "标题": title,
                "小区名称": community,
                "面积": area,
                "起拍价": price,
                "地址": title,
                "详情链接": "https:" + house["auctionLink"],
                "抓取页码": page_num,
            })
            total_inserted += 1

        print(f"  第{page_num}页: 返回{len(house_list)}条, 跳过重复{skipped_dup}条")
        time.sleep(1)   # 控制请求频率, 防风控

    print(f">> {city_name} 共获取 {total_inserted} 条真实房源")

print(f"\n全部完成, 数据库累计 {collection.count_documents({})} 条")
