# -*- coding: utf-8 -*-
"""
CDE临床试验爬虫 cde8【智能断点版 + 全程计时】
在 cde7 基础上新增：
  1. 全程计时：从程序启动（输入页数前）到下载结束，打印总耗时
  2. 抓取计时：从开始连数据库到抓取全部结束，打印本次抓取用时
断点逻辑（同 cde7）：
  1. 读MongoDB里最大页码max_p
  2. 统计max_p页已存了多少条
  3. 如果满20条（一页正好20条），说明这页抓完了，直接从max_p+1开始
  4. 如果不足20条，说明上次中断在这页，从max_p重抓（upsert自动去重）
其他跟cde6一样：无头模式、JS点击、随机桌面UA、断点续爬
"""
import time
import random
import pymongo
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdrivermanager_cn.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.keys import Keys
from fake_useragent import FakeUserAgent

# ====================== 全局配置区 ======================
LOG_FILE = "crawl_log.txt"
MONGO_HOST = "127.0.0.1"
MONGO_PORT = 27017
MONGO_DB = "cde"
MONGO_COLLECTION = "trials"
WAIT_SECONDS = 15                # 页面等待超时秒数
RANDOM_DELAY_MIN = 0.3           # 每条之间最小随机延时
RANDOM_DELAY_MAX = 1             # 每条之间最大随机延时
NEXT_PAGE_RETRY = 2              # 翻页失败重试次数
RESTART_INTERVAL = 20            # 每抓多少页重启一次浏览器释放内存
INPUT_JUMP_RETRY = 2             # 输入框跳转重试次数
AFTER_SEARCH_SLEEP = 2.5         # 点完查询后固定等待秒数，等页面渲染
PAGE_SIZE = 20                   # CDE列表每页固定20条数据
# =======================================================


def format_duration(seconds: float) -> str:
    """
    把秒数格式化成 时:分:秒 的字符串，方便日志阅读
    例如：3661秒 -> "1时1分1秒"
    """
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h}时{m}分{s}秒"


def log(msg: str):
    """打印日志并写入文件"""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} | {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def get_start_page(coll) -> int:
    """
    智能断点判断：
    - 没数据 → 从第1页开始
    - 最后一页满20条 → 说明抓完了，从下一页开始
    - 最后一页不足20条 → 说明中断在这页，重抓这页
    """
    try:
        last_doc = coll.find_one({"页数": {"$exists": True}}, sort=[("页数", pymongo.DESCENDING)])
        if not last_doc:
            log("【DB断点】无历史数据，从第1页开始抓取")
            return 1

        max_p = int(last_doc["页数"])
        # 统计这一页已经存了多少条
        count_on_page = coll.count_documents({"页数": max_p})

        if count_on_page >= PAGE_SIZE:
            # 这页满了，说明上次抓完了，从下一页开始
            log(f"【DB断点】第{max_p}页已抓满({count_on_page}条)，从第{max_p + 1}页继续")
            return max_p + 1
        else:
            # 这页没抓完，重抓这页（upsert去重，不会重复）
            log(f"【DB断点】第{max_p}页只抓了{count_on_page}/{PAGE_SIZE}条，从第{max_p}页重抓")
            return max_p
    except Exception as e:
        log(f"【DB断点】读取失败({e})，默认从第1页开始")
        return 1


def click_search_button(driver, wait):
    """点击查询按钮，加载列表数据，分页控件才会渲染"""
    log("【页面初始化】点击查询按钮加载试验列表...")
    try:
        search_btn = wait.until(EC.presence_of_element_located((By.XPATH, '//button[normalize-space()="查询"]')))
        driver.execute_script("arguments[0].click();", search_btn)   # 无头模式用JS点击
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table tbody tr")))
        time.sleep(AFTER_SEARCH_SLEEP)
        log("✅ 查询成功，列表数据加载完成，分页控件已渲染")
        return True
    except Exception as e:
        log(f"❌ 点击查询失败：{str(e)}")
        return False


def jump_to_page_by_input(driver, wait, target_page: int) -> bool:
    """在页码输入框里输入页码跳转"""
    if target_page == 1:
        log("已经在第1页，无需跳转")
        return True
    log(f"【输入框跳转】准备跳转到第{target_page}页...")
    for retry in range(INPUT_JUMP_RETRY):
        try:
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table tbody tr")))
            time.sleep(0.8)
            page_input = wait.until(
                EC.element_to_be_clickable((By.XPATH, '//div[contains(@class,"pageInfo")]/input'))
            )
            page_input.click()
            page_input.send_keys(Keys.CONTROL + "a")
            time.sleep(0.2)
            page_input.send_keys(Keys.DELETE)
            time.sleep(0.2)
            page_input.send_keys(str(target_page))
            time.sleep(0.3)
            page_input.send_keys(Keys.ENTER)
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table tbody tr")))
            time.sleep(1.0)
            log(f"✅ 输入框跳转成功，到达第{target_page}页")
            return True
        except TimeoutException:
            log(f"⚠️ 输入框跳转第{retry+1}次失败，重试...")
            time.sleep(1.5)
        except Exception as e:
            log(f"⚠️ 输入框跳转异常：{e}，重试...")
            time.sleep(1.5)
    log(f"❌ 输入框跳转{INPUT_JUMP_RETRY}次全部失败")
    return False


def safe_click_next_page(driver, wait) -> bool:
    """点下一页按钮"""
    for retry in range(NEXT_PAGE_RETRY):
        try:
            next_btn = wait.until(EC.element_to_be_clickable((By.XPATH, '//a[@aria-label="Next"]')))
            driver.execute_script("arguments[0].click();", next_btn)
            log("翻页成功，等待列表加载...")
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table tbody tr")))
            time.sleep(1.0)
            return True
        except TimeoutException:
            try:
                next_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//a[normalize-space()='下一页']")))
                driver.execute_script("arguments[0].click();", next_btn)
                log("翻页成功(文本匹配)，等待列表加载...")
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table tbody tr")))
                time.sleep(1.0)
                return True
            except Exception:
                log(f"翻页重试 {retry+1}/{NEXT_PAGE_RETRY} 失败")
                time.sleep(1)
        except Exception:
            log(f"翻页重试 {retry+1}/{NEXT_PAGE_RETRY} 异常")
            time.sleep(1)
    log("翻页多次失败，无下一页或按钮不可点击")
    return False


def create_driver():
    """创建Chrome浏览器（无头模式 + 随机桌面UA）"""
    log("正在启动浏览器...")
    service = Service(ChromeDriverManager().install())
    chrome_options = Options()
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--headless=old")           # 旧版无头，反爬检测弱
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    # 随机桌面UA
    ua = FakeUserAgent(platforms="desktop").random
    chrome_options.add_argument(f"user-agent={ua}")
    log(f"本次使用UA: {ua}")
    driver = webdriver.Chrome(service=service, options=chrome_options)
    # 抹掉webdriver标识
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
    })
    log("浏览器启动成功")
    wait = WebDriverWait(driver, WAIT_SECONDS)
    return driver, wait


def get_trial_data(total_pages: int):
    # ===== cde8新增：抓取阶段计时（连接数据库即视为抓取开始）=====
    crawl_start = time.time()
    log("【计时】开始抓取")

    mongo_client = pymongo.MongoClient(MONGO_HOST, MONGO_PORT, serverSelectionTimeoutMS=5000)
    mongo_client.admin.command("ping")
    coll = mongo_client[MONGO_DB][MONGO_COLLECTION]
    log("MongoDB连接成功")

    try:
        coll.create_index("登记号", unique=True)
    except Exception:
        pass

    # 智能判断从第几页开始
    start_page = get_start_page(coll)
    if start_page > total_pages:
        log("任务完成，已抓取全部目标页数，程序退出")
        return

    driver, wait = create_driver()
    log("正在打开CDE列表页...")
    driver.get("https://www.chinadrugtrials.org.cn/clinicaltrials.searchlist.dhtml")
    log(f"页面已打开，标题: {driver.title}")
    time.sleep(2)

    # 先点查询加载列表
    search_ok = click_search_button(driver, wait)
    if not search_ok:
        log("❌ 查询加载列表失败，任务终止")
        driver.quit()
        return

    # 跳转到起始页
    jump_ok = jump_to_page_by_input(driver, wait, start_page)
    if not jump_ok:
        log(f"❌ 跳转到起始页{start_page}失败，任务终止")
        driver.quit()
        return

    for page in range(start_page, total_pages + 1):
        log(f"\n===== 抓取第{page}/{total_pages}页 =====")
        rows = wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "table tbody tr")))
        log(f"本页共{len(rows)}行")

        fail_count = 0
        for i in range(len(rows)):
            try:
                rows = wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "table tbody tr")))
                tr = rows[i]
                cols = tr.find_elements(By.TAG_NAME, "td")
                if len(cols) < 6:
                    continue
                log(f"  第{i+1}/{len(rows)}条: {cols[1].text.strip()}")

                record = {
                    "页数": page,
                    "登记号": cols[1].text.strip(),
                    "试验状态": cols[2].text.strip(),
                    "药物名称": cols[3].text.strip(),
                    "适应症": cols[4].text.strip(),
                    "试验通俗题目": cols[5].text.strip(),
                    "申请联系人": "",
                    "首次公示信息日期": "",
                    "申请人": ""
                }

                # 点详情链接打开新标签页
                link = tr.find_element(By.TAG_NAME, "a")
                driver.execute_script("arguments[0].click();", link)
                wait.until(lambda d: len(d.window_handles) > 1)
                driver.switch_to.window(driver.window_handles[-1])

                # 从详情页补充字段
                try:
                    record["试验状态"] = wait.until(EC.presence_of_element_located((By.XPATH, '//th[normalize-space()="试验状态"]/following::td[1]'))).text.strip()
                except Exception: pass
                try:
                    record["登记号"] = wait.until(EC.presence_of_element_located((By.XPATH, '//th[normalize-space()="登记号"]/following::td[1]'))).text.strip()
                except Exception: pass
                try:
                    record["申请联系人"] = wait.until(EC.presence_of_element_located((By.XPATH, '//th[normalize-space()="申请人联系人"]/following::td[1]'))).text.strip()
                except Exception: pass
                try:
                    record["首次公示信息日期"] = wait.until(EC.presence_of_element_located((By.XPATH, '//th[normalize-space()="首次公示信息日期"]/following::td[1]'))).text.strip()
                except Exception: pass
                try:
                    record["申请人"] = wait.until(EC.presence_of_element_located((By.XPATH, '//th[normalize-space()="申请人名称"]/following::td[1]'))).text.strip()
                except Exception: pass

                # upsert去重
                coll.update_one({"登记号": record["登记号"]}, {"$set": record}, upsert=True)
                driver.close()
                driver.switch_to.window(driver.window_handles[0])
                time.sleep(random.uniform(RANDOM_DELAY_MIN, RANDOM_DELAY_MAX))
            except Exception:
                fail_count += 1
                log(f"警告：第{page}页第{i+1}条抓取失败，跳过")
                if len(driver.window_handles) > 1:
                    driver.close()
                    driver.switch_to.window(driver.window_handles[0])
                continue
        log(f"✅ 第{page}页完成，失败{fail_count}条")

        # 每RESTART_INTERVAL页重启浏览器
        if page % RESTART_INTERVAL == 0 and page != total_pages:
            log(f"已抓{page}页，重启浏览器释放内存...")
            driver.quit()
            time.sleep(2)
            driver, wait = create_driver()
            driver.get("https://www.chinadrugtrials.org.cn/clinicaltrials.searchlist.dhtml")
            time.sleep(2)
            search_ok = click_search_button(driver, wait)
            if not search_ok:
                log("❌ 重启后查询失败，任务终止")
                return
            jump_ok = jump_to_page_by_input(driver, wait, page + 1)
            if not jump_ok:
                log(f"❌ 重启后跳转第{page+1}页失败")
                return

        if page < total_pages:
            log("准备翻页")
            if not safe_click_next_page(driver, wait):
                log("翻页失败，任务提前结束")
                break

    log("\n🎉 本次抓取任务全部结束")
    if driver:
        driver.quit()
        log("浏览器关闭")

    # ===== cde8新增：抓取阶段结束计时 =====
    crawl_cost = time.time() - crawl_start
    log(f"【计时】本次抓取用时: {format_duration(crawl_cost)}（{crawl_cost:.1f}秒）")


if __name__ == '__main__':
    # ===== cde8新增：程序全程计时（从输入页数前开始）=====
    program_start = time.time()
    log("【计时】程序开始")
    try:
        target = int(input("输入计划抓取总页数："))
        get_trial_data(target)
    except KeyboardInterrupt:
        log("\n用户手动停止，下次自动从断点继续")
    except ValueError:
        log("输入错误，请输入数字")
    finally:
        # ===== cde8新增：无论正常结束还是中途退出，都打印程序总耗时 =====
        program_cost = time.time() - program_start
        log(f"【计时】程序总耗时: {format_duration(program_cost)}（{program_cost:.1f}秒）")
