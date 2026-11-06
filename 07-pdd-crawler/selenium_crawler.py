# -*- coding: utf-8 -*-
"""
拼多多批发 - 按分类采集版
=====================================
作业题七：拼多多批发数码分类数据采集
功能：从数码分类下的9个二级分类采集商品数据，存入MongoDB
技术：Selenium模拟浏览器 + BeautifulSoup解析HTML + MongoDB去重存储

采集流程：
1. 启动带登录态的Chrome浏览器
2. 按分类逐个采集：
   - 打开分类第1页 → 滚动加载
   - URL翻到第2页 → 等待10秒加载
   - 真人式滚动（下→上→下）自动加载更多
   - 每滚3次解析一次HTML，提取商品数据
3. 按商品ID去重，存入MongoDB
4. 遇到验证码自动暂停，等待人工处理
5. 浏览器崩溃自动重启，每个分类最多重试3次
"""
import os
import time
import random
import re
import pymongo
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import InvalidSessionIdException
from bs4 import BeautifulSoup

# ===================== 配置项 =====================
# Chrome用户数据目录（保存登录态，不用每次扫码登录）
PROFILE_DIR = "./profiles/pdd_profile"

# HTML文件保存目录（当前版本不保存，预留）
HTML_DIR = "./htmls"

# MongoDB连接地址
MONGO_URI = "mongodb://127.0.0.1:27017/"

# 每个分类目标采集条数
TARGET_PER_CAT = 600

# 本次采集总目标条数（MongoDB总数达到此数即停止）
TARGET = 2000

# 数码二级分类列表（从拼多多批发页面获取的cate_id）
# 每个分类包含：分类名称 + 分类ID
CATEGORIES = [
    {"name": "3C数码配件", "cate_id": "9689"},
    {"name": "游戏设备", "cate_id": "11000"},
    {"name": "电脑外设网络", "cate_id": "10995"},
    {"name": "智能设备", "cate_id": "10453"},
    {"name": "文化用品", "cate_id": "9697"},
    {"name": "办公用品", "cate_id": "9696"},
    {"name": "相机单反", "cate_id": "9695"},
    {"name": "娱乐影音", "cate_id": "9694"},
    {"name": "电脑整机组件", "cate_id": "9693"},
]

# 创建HTML保存目录（如果不存在）
os.makedirs(HTML_DIR, exist_ok=True)


def create_driver():
    """
    创建Chrome浏览器驱动
    功能：启动带反爬绕过的Chrome，复用登录态
    返回：webdriver对象
    """
    t0 = time.time()
    print("正在启动Chrome浏览器...")

    opts = Options()
    # 加载用户数据目录（复用登录态）
    opts.add_argument(f"--user-data-dir={PROFILE_DIR}")
    # 禁用自动化检测特征
    opts.add_argument("--disable-blink-features=AutomationControlled")
    # 排除自动化开关
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    # 窗口大小
    opts.add_argument("--window-size=1600,900")
    # 禁用扩展，加快启动
    opts.add_argument("--disable-extensions")

    # 启动Chrome
    driver = webdriver.Chrome(options=opts)

    # 注入JS，抹掉webdriver标识（反爬检测）
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
    })

    t1 = time.time()
    print(f"Chrome启动成功！用时：{t1-t0:.1f}秒")
    return driver


def parse_html(html_content, category_name):
    """
    解析HTML内容，提取商品数据
    参数：
        html_content: HTML字符串（来自driver.page_source）
        category_name: 分类名称（存入数据库的"分类"字段）
    返回：商品列表，每个商品是字典
    """
    soup = BeautifulSoup(html_content, "lxml")
    goods_list = []

    # 找到所有商品卡片
    items = soup.select(".goods-item")

    for item in items:
        # ===== 提取商品标题 =====
        # 标题在 .goods-name 里，可能包在span里
        title_el = item.select_one(".goods-name span")
        if title_el is None:
            title_el = item.select_one(".goods-name")
        title = title_el.get_text(strip=True) if title_el else ""

        # ===== 提取商品价格 =====
        # 价格由"￥"和数字两个span组成，需要拼接
        price_el = item.select_one(".goods-price")
        price = ""
        if price_el:
            unit_el = price_el.select_one(".price-unit")   # ￥符号
            text_el = price_el.select_one(".price-text")   # 数字
            unit = unit_el.get_text(strip=True) if unit_el else ""
            text = text_el.get_text(strip=True) if text_el else ""
            price = unit + text

        # ===== 提取商品销量 =====
        sold_el = item.select_one(".sold-quantity")
        sold = sold_el.get_text(strip=True) if sold_el else ""

        # ===== 提取店铺名称 =====
        store_el = item.select_one(".store-name-text")
        store = store_el.get_text(strip=True) if store_el else ""

        # ===== 提取商品ID（gid）=====
        # 从详情页链接里提取gid参数，用于去重
        gid = ""
        link = item.select_one("a[href*='goods/detail']")
        if link:
            href = link.get("href", "")
            m = re.search(r"gid=(\d+)", href)
            if m:
                gid = m.group(1)

        # 组装商品字典（中文键名）
        goods_list.append({
            "商品ID": gid,
            "商品标题": title,
            "商品价格": price,
            "商品销量": sold,
            "商品店铺": store,
            "分类": category_name
        })

    return goods_list


def check_captcha(driver):
    """
    检测页面是否出现滑块/图形验证码
    返回：True=需要人工处理验证码，False=正常
    """
    try:
        page = driver.page_source
        # 验证码常见提示关键词
        for kw in ("安全验证", "拖动滑块", "向右滑动", "请完成验证", "captcha", "verify"):
            if kw in page:
                return True
        # 常见验证码iframe容器
        if driver.find_elements("css selector", "iframe[id*='captcha' i], iframe[src*='captcha' i], #captcha"):
            return True
    except Exception:
        pass
    return False


def wait_captcha_solved(driver, timeout=300):
    """
    检测到验证码后暂停采集，等待人工滑动完成
    参数：
        timeout: 最长等待时间（秒），默认5分钟
    返回：True=验证码已通过，False=超时
    """
    print("  检测到滑块验证码，请手动在浏览器中完成滑动...")
    waited = 0
    while waited < timeout:
        time.sleep(5)
        waited += 5
        if not check_captcha(driver):
            print("  验证码已通过，继续采集")
            return True
    print(f"  等待{timeout}秒超时，验证码仍未通过")
    return False


def crawl_category(driver, col, category):
    """
    采集一个分类的所有商品
    参数：
        driver: 浏览器驱动
        col: MongoDB集合对象
        category: 分类字典（包含name和cate_id）
    返回：本分类新增的商品数量
    """
    name = category["name"]
    cate_id = category["cate_id"]
    print(f"\n{'='*40}")
    print(f">>> 采集分类：{name} (cate={cate_id})")
    print(f"{'='*40}")

    total = 0  # 本分类已采集数量

    # ===== 第1页：URL打开 =====
    t_page1 = time.time()
    print("正在打开第1页...")
    driver.get(f"https://pifa.pinduoduo.com/search?cate={cate_id}&level=1")
    time.sleep(5)
    print(f"第1页加载完成！用时：{time.time()-t_page1:.1f}秒")

    # ===== URL翻到第2页 =====
    # 拼多多批发的特点：第1页用URL打开，翻到第2页后开始用滚动加载
    t_page2 = time.time()
    print("正在翻到第2页...")
    driver.get(f"https://pifa.pinduoduo.com/search?cate={cate_id}&level=1&page=2")
    print("等待10秒加载...")
    time.sleep(10)
    print(f"第2页加载完成！用时：{time.time()-t_page2:.1f}秒")

    # ===== 滚动加载阶段 =====
    scroll_count = 0   # 滚动次数计数
    no_new = 0         # 连续没有新商品的次数
    while total < TARGET_PER_CAT:
        # 真人式滚动模拟（下→上→下）
        # 拼多多反爬：一直往下滚会被检测，需要模拟真人回头看
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        driver.execute_script("window.scrollBy(0, -500);")  # 往上滚一点
        time.sleep(1)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(3)

        scroll_count += 1

        # 检测是否弹出验证码
        if check_captcha(driver):
            if not wait_captcha_solved(driver):
                print("  验证码长时间未通过，暂停本分类（稍后重试）")
                break

        # 每滚3次解析一次HTML（不用每次都解析，节省时间）
        if scroll_count % 3 == 0:
            goods = parse_html(driver.page_source, name)
            new_cnt = 0    # 本次新增数量
            fixed_cnt = 0  # 本次修复数量（旧数据价格缺失）

            for g in goods:
                # 按商品ID去重
                exists = col.find_one({"商品ID": g["商品ID"]})
                if not exists:
                    # 新商品，直接插入
                    col.update_one({"商品ID": g["商品ID"]}, {"$set": g}, upsert=True)
                    new_cnt += 1
                    total += 1
                elif not re.search(r"\d", str(exists.get("商品价格", ""))) and g["商品价格"]:
                    # 旧记录价格缺失，用新数据修复
                    col.update_one({"商品ID": g["商品ID"]}, {"$set": g})
                    fixed_cnt += 1

            print(f"  滚动{scroll_count}次 | 商品{len(goods)} | 新增{new_cnt} | 修复{fixed_cnt} | 本分类累计{total}/{TARGET_PER_CAT}")

            # 判断是否到底了：连续2次滚动都没有新商品
            if new_cnt == 0:
                no_new += 1
                if no_new >= 2:
                    print("  到底了，没有更多商品了")
                    break
            else:
                no_new = 0

        # 随机等待，防风控
        time.sleep(random.uniform(2, 4))

    print(f">>> 分类《{name}》完成，本分类新增{total}条")
    return total


def main():
    """主函数：程序入口"""
    t_start = time.time()  # 脚本开始时间
    first_data_printed = False  # 是否已打印过第一条数据用时

    # 连接MongoDB
    client = pymongo.MongoClient(MONGO_URI)
    col = client["pdd"]["goods"]
    print(f"MongoDB连接成功，当前已有 {col.count_documents({})} 条")

    # 启动浏览器
    driver = create_driver()

    total_all = 0
    # 逐个采集分类
    for cat in CATEGORIES:
        # 每个分类最多重试3次（浏览器崩溃自动重启）
        attempt = 0
        while attempt < 3:
            attempt += 1
            try:
                cnt = crawl_category(driver, col, cat)
                total_all += cnt

                # 第一次采集到数据，打印总用时
                if not first_data_printed and total_all > 0:
                    t_first = time.time()
                    print(f"\n{'='*50}")
                    print(f"⏱️  从脚本启动到第一条数据入库，总用时：{t_first-t_start:.1f}秒")
                    print(f"{'='*50}\n")
                    first_data_printed = True
                break

            except InvalidSessionIdException:
                # 浏览器会话丢失（窗口被关/崩溃），重启浏览器
                print(f"  浏览器会话丢失(可能窗口被手动关闭)，正在重启浏览器... 第{attempt}次")
                try:
                    driver.quit()
                except Exception:
                    pass
                time.sleep(5)
                driver = create_driver()
                if attempt >= 3:
                    print(f"  分类《{cat['name']}》连续3次会话异常，跳过该分类")

        # 每个分类之间休息10秒，防风控
        print(f"\n休息10秒...")
        time.sleep(10)

        # 总目标达到就停止
        if col.count_documents({}) >= TARGET:
            break

    # 采集完成
    print(f"\n{'='*50}")
    print(f"🎉 全部完成！")
    print(f"MongoDB总数：{col.count_documents({})} 条")
    print(f"目标：{TARGET}条")
    print(f"{'='*50}")

    driver.quit()


if __name__ == "__main__":
    main()
