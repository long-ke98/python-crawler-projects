# -*- coding: utf-8 -*-
"""
题六：天眼查 - 半导体公司信息采集（整合版）
====================================================
技术路线：在线保存详情页HTML（Selenium串行降频防风控）→ 本地多线程解析提取字段 → MongoDB入库 → CSV导出

设计思路：
  1. 在线采集和离线解析分离：即使被风控封号，已保存的HTML不丢失
  2. 在线阶段串行降频：8~15秒随机间隔，每10家休息30~60秒，避免触发406账号封禁
  3. 离线阶段多线程并发：8线程同时解析HTML，充分利用CPU，符合作业"多任务"要求
  4. 按统一社会信用代码去重upsert，断点续爬已存在的HTML自动跳过

用法：
  # 在线采集单个城市
  python tianyancha_spider.py --mode crawl --keyword "上海半导体"

  # 在线采集6个城市（默认顺序）
  python tianyancha_spider.py --mode crawl --cities 北京,上海,深圳,广州,杭州,成都

  # 只解析本地已保存的HTML（不打开浏览器）
  python tianyancha_spider.py --mode parse

  # 一键模式：先在线采集6个城市，再解析入库导出CSV
  python tianyancha_spider.py --mode all
"""
import os
import re
import sys
import time
import random
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

# Selenium相关：控制Chrome浏览器，模拟真人操作
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from fake_useragent import FakeUserAgent  # 随机UA，伪装不同浏览器

# 数据解析相关
from lxml import etree  # HTML解析，XPath定位字段
from pymongo import MongoClient  # MongoDB数据库操作


# ====================== 一、全局配置 ======================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # 脚本所在目录
MAIN_PROFILE = BASE_DIR + r"\tyc_profile"  # Chrome登录态目录（已扫码登录）
SAVE_DIR = os.path.join(os.environ["USERPROFILE"], "Downloads")  # HTML保存目录

# ---- 防风控参数（在线采集阶段用）----
DETAIL_SLEEP_MIN = 8       # 详情页之间最小间隔（秒）
DETAIL_SLEEP_MAX = 15     # 详情页之间最大间隔（秒）
REST_EVERY = 10            # 每采集多少家休息一次
REST_MIN = 30               # 休息最小时长（秒）
REST_MAX = 60               # 休息最大时长（秒）
RISK_TEXT = "系统检测到您当前帐号的操作存在异常"  # 风控页特征文本

# ---- 本地解析参数 ----
MAX_WORKERS = 8             # 多线程数（作业要求多任务）
CSV_PATH = os.path.join(SAVE_DIR, "半导体企业_汇总.csv")  # CSV导出路径

# ---- MongoDB配置 ----
MONGO_URI = "mongodb://127.0.0.1:27017/"  # 本地MongoDB地址
MONGO_DB = "tianyancha"                    # 数据库名
MONGO_COL = "semiconductor_all"            # 集合名（存所有城市的半导体公司）

# ---- 22个字段XPath定位规则 ----
# 采用"标签td精确等值 + following-sibling取相邻td"的模板，跨公司不会漂移
XPATH_FIELDS = {
    "企业名称": "//h1[contains(@class,'name')]",  # 页面顶部大标题
    "法定代表人": "//td[normalize-space(.)='法定代表人']/following-sibling::td[1]//a[contains(@class,'link-click')]",
    "统一社会信用代码": "//td[normalize-space(.)='统一社会信用代码']/following-sibling::td[1]",
    "登记状态": "//td[normalize-space(.)='登记状态']/following-sibling::td[1]",
    "成立日期": "//td[normalize-space(.)='成立日期']/following-sibling::td[1]",
    "注册资本": "//td[normalize-space(.)='注册资本']/following-sibling::td[1]",
    "实缴资本": "//td[normalize-space(.)='实缴资本']/following-sibling::td[1]",
    "工商注册号": "//td[normalize-space(.)='工商注册号']/following-sibling::td[1]",
    "企业类型": "//td[normalize-space(.)='企业类型']/following-sibling::td[1]",
    "营业期限": "//td[normalize-space(.)='营业期限']/following-sibling::td[1]",
    "行业": "//td[(normalize-space(.)='国标行业') or (normalize-space(.)='行业')]/following-sibling::td[1]",
    "核准日期": "//td[normalize-space(.)='核准日期']/following-sibling::td[1]",  # 天眼查加密，预期为空
    "人员规模": "//td[normalize-space(.)='人员规模']/following-sibling::td[1]",
    "组织机构代码": "//td[normalize-space(.)='组织机构代码']/following-sibling::td[1]",
    "纳税人识别号": "//td[normalize-space(.)='纳税人识别号']/following-sibling::td[1]",
    "纳税人资质": "//td[normalize-space(.)='纳税人资质']/following-sibling::td[1]",
    "登记机关": "//td[normalize-space(.)='登记机关']/following-sibling::td[1]",
    "注册地址": "//td[normalize-space(.)='注册地址']/following-sibling::td[1]",
    "英文名称": "//td[normalize-space(.)='英文名称']/following-sibling::td[1]",
    "经营范围": "//td[normalize-space(.)='经营范围']/following-sibling::td[1]",
    "参保人数": "//td[normalize-space(.)='参保人数']/following-sibling::td[1]",  # 纯数字
    "天眼评分": "//span[contains(@class,'sort-score-value')]",  # 天眼查评分，纯数字
}

# 文件名非法字符正则（Windows不允许）
INVALID_CHARS = re.compile(r'[\\/:*?"<>|]')


# ====================== 二、工具函数 ======================
def _find_local_chromedriver():
    """优先使用本地缓存的chromedriver，避免每次联网下载镜像超时"""
    import glob
    home = os.path.expanduser("~")
    patterns = [
        # Selenium默认缓存路径
        os.path.join(home, ".cache", "selenium", "chromedriver", "win64", "*", "chromedriver.exe"),
    ]
    for pat in patterns:
        candidates = glob.glob(pat)
        for c in candidates:
            if os.path.exists(c):
                return c
    return ""  # 找不到就用默认路径


def safe_filename(name: str) -> str:
    """清理文件名中的非法字符，防止Windows保存报错"""
    name = INVALID_CHARS.sub("_", name)  # 非法字符替换为下划线
    return name[:80]  # 限制文件名长度，防止过长


def create_driver():
    """创建带登录态的Chrome浏览器实例"""
    opts = Options()
    # 关键：复用本地扫码登录态，不用每次重新扫码
    opts.add_argument(f"--user-data-dir={MAIN_PROFILE}")
    # 伪装成真人浏览器，抹掉自动化标识
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1600,900")
    # 随机桌面UA，每次启动模拟不同浏览器
    opts.add_argument(f"user-agent={FakeUserAgent(platforms='desktop').random}")

    # 用本地缓存的chromedriver，避免联网超时
    local = _find_local_chromedriver()
    service = Service(local) if local else Service()
    driver = webdriver.Chrome(service=service, options=opts)

    # CDP命令：抹掉navigator.webdriver标识，高级风控检测
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"})
    return driver


def is_risk_page(driver) -> bool:
    """检测当前页面是否是风控封禁页（406异常）"""
    try:
        # 只看body前400个字符，避免长文本匹配性能问题
        return RISK_TEXT in driver.find_element(By.TAG_NAME, "body").text[:400]
    except Exception:
        return False  # 页面加载失败时不判定为风控


# ====================== 三、在线采集模块 ======================
def crawl_one_city(driver, keyword, max_pages=2):
    """
    采集单个城市的所有公司详情页HTML
    参数：
        driver: Chrome浏览器实例
        keyword: 搜索关键词，如"上海半导体"
        max_pages: 搜索结果翻页数
    返回：新保存的HTML数量
    """
    print(f"\n>>> 开始采集：{keyword}")
    # 打开搜索结果页
    driver.get("https://www.tianyancha.com/search?key=" + keyword)
    time.sleep(8)  # 等页面加载

    # 风控检测，触发就停止
    if is_risk_page(driver):
        print("⛔ 检测到风控，停止")
        return 0

    # 收集搜索结果里的公司列表：(公司名, 公司ID)
    companies = []
    seen = set()  # 去重，防止同一公司重复出现
    for page in range(1, max_pages + 1):
        # 提取当前页所有公司链接
        links = driver.find_elements(By.XPATH, "//a[contains(@href,'/company/')]")
        for a in links:
            href = a.get_attribute("href") or ""
            # 从URL里提取公司ID，格式：/company/123456789
            m = re.search(r"/company/(\d+)", href)
            if not m:
                continue
            name = a.text.strip()
            # 过滤掉不是公司名的链接（比如字段标签、法定代表人链接等）
            if not name or name in seen:
                continue
            if name.startswith(("执行事务合伙人", "法定代表人", "注册资本", "经营范围")):
                continue
            seen.add(name)
            companies.append((name, m.group(1)))

        # 翻到下一页
        if page < max_pages:
            try:
                # 页码按钮XPath
                xp = f"//div[contains(@class,'pageWrap')]//div[contains(@class,'num')][normalize-space(text())='{page+1}']"
                btn = driver.find_element(By.XPATH, xp)
                # JS点击，防止元素遮挡
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(5)  # 等新页面加载
            except Exception:
                break  # 找不到翻页按钮就停止

    print(f">>> 共找到 {len(companies)} 家公司")

    # 逐个打开详情页，保存HTML
    done = 0
    for name, cid in companies:
        filename = safe_filename(name) + ".html"
        filepath = os.path.join(SAVE_DIR, filename)

        # 断点续爬：已存在且大于50KB的HTML自动跳过
        if os.path.exists(filepath) and os.path.getsize(filepath) > 50000:
            continue

        # 打开详情页
        driver.get(f"https://www.tianyancha.com/company/{cid}")
        time.sleep(random.uniform(6, 10))  # 等页面渲染

        # 风控检测
        if is_risk_page(driver):
            print("⛔ 检测到风控，停止保存")
            break

        # 保存完整HTML到本地
        html = driver.page_source
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html)

        done += 1
        print(f"  ✅ 《{name}》已保存")

        # 防风控：随机间隔，模拟真人浏览速度
        time.sleep(random.uniform(DETAIL_SLEEP_MIN, DETAIL_SLEEP_MAX))
        # 每采集N家，强制休息一段时间
        if done % REST_EVERY == 0 and done < len(companies):
            rest = random.randint(REST_MIN, REST_MAX)
            print(f"  😴 已完成 {done} 家，休息 {rest} 秒...")
            time.sleep(rest)

    print(f">>> {keyword} 完成，新保存 {done} 家")
    return done


# ====================== 四、本地解析模块（多任务） ======================
def parse_one_html(filepath):
    """
    解析单个HTML文件，提取22个字段
    参数：HTML文件路径
    返回：字段字典，解析失败返回None
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            html = f.read()
        tree = etree.HTML(html)  # 构建DOM树

        # 按XPath规则提取所有22个字段
        data = {}
        for field, xpath in XPATH_FIELDS.items():
            # 用string()函数直接取文本，不依赖Element对象方法
            val = tree.xpath(f"string({xpath})").strip()
            data[field] = val

        # 兜底：如果h1没取到企业名称，从文件名提取
        if not data.get("企业名称"):
            fname = os.path.basename(filepath).replace(".html", "")
            data["企业名称"] = fname.split("_")[0]

        return data
    except Exception as e:
        print(f"  ❌ 解析失败 {os.path.basename(filepath)}: {e}")
        return None


def parse_all_html():
    """
    多线程解析所有HTML，写入MongoDB，导出CSV
    符合作业"多任务"要求：ThreadPoolExecutor 8线程并发
    """
    print(f"\n>>> 开始解析本地HTML，目录：{SAVE_DIR}")
    print(f">>> 多线程数：{MAX_WORKERS}")

    # 收集所有大于50KB的HTML文件
    html_files = []
    for f in os.listdir(SAVE_DIR):
        if f.endswith(".html"):
            fp = os.path.join(SAVE_DIR, f)
            if os.path.getsize(fp) > 50000:  # 过滤太小的无效文件
                html_files.append(fp)

    print(f">>> 找到 {len(html_files)} 个HTML文件")

    # 多线程并发解析：作业要求的多任务
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # 提交所有解析任务
        futures = {executor.submit(parse_one_html, fp): fp for fp in html_files}
        # 按完成顺序收集结果
        for future in as_completed(futures):
            data = future.result()
            if data and data.get("企业名称"):
                results.append(data)

    print(f">>> 解析完成，共 {len(results)} 条有效数据")

    # 写入MongoDB：按统一社会信用代码upsert去重
    client = MongoClient(MONGO_URI)
    col = client[MONGO_DB][MONGO_COL]

    upserted = 0
    for data in results:
        credit = data.get("统一社会信用代码", "")
        if credit:  # 有信用代码才去重入库
            col.update_one(
                {"统一社会信用代码": credit},  # 去重键
                {"$set": data},
                upsert=True  # 存在就更新，不存在就插入
            )
            upserted += 1

    total = col.count_documents({})
    print(f">>> MongoDB 共 {total} 条记录")

    # 导出CSV文件（utf-8-sig编码，Excel直接打开中文不乱码）
    import csv
    # 列顺序：先搜索关键词，再按22个字段顺序
    fields_order = ["搜索关键词"] + list(XPATH_FIELDS.keys())
    with open(CSV_PATH, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields_order)
        writer.writeheader()  # 写表头
        # 从MongoDB读所有数据写CSV
        for doc in col.find():
            row = {k: doc.get(k, "") for k in fields_order}
            writer.writerow(row)

    print(f">>> CSV 已导出：{CSV_PATH}")
    print(f">>> 全部完成！")


# ====================== 五、主函数 ======================
def main():
    """命令行入口，解析参数"""
    parser = argparse.ArgumentParser(description="天眼查半导体企业信息采集")
    parser.add_argument("--mode", choices=["crawl", "parse", "all"], default="all",
                        help="运行模式：crawl=在线采集, parse=本地解析, all=一键")
    parser.add_argument("--keyword", type=str, default="北京半导体",
                        help="搜索关键词（单个城市）")
    parser.add_argument("--cities", type=str, default="北京,上海,深圳,广州,杭州,成都",
                        help="城市列表，逗号分隔")
    parser.add_argument("--max-pages", type=int, default=2,
                        help="每个城市翻页数")
    args = parser.parse_args()

    # 在线采集阶段
    if args.mode in ["crawl", "all"]:
        # 把城市名拼成"XX半导体"关键词
        cities = [c.strip() + "半导体" for c in args.cities.split(",")]
        driver = create_driver()
        try:
            # 依次跑每个城市，串行防风控
            for city in cities:
                crawl_one_city(driver, city, args.max_pages)
                time.sleep(10)  # 城市之间休息10秒
        finally:
            driver.quit()  # 确保浏览器关闭

    # 本地解析阶段
    if args.mode in ["parse", "all"]:
        parse_all_html()


if __name__ == "__main__":
    main()
