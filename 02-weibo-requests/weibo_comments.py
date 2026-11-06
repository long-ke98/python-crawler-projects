import re
import requests
import pymongo
from datetime import datetime

# ====== 只要改这里 ======
TARGET_NICK = "人民日报"                        # 要搜的用户名
KEYWORD = "深耕一寸，日进一尺"                    # 要搜的关键词
NICK_TO_UID = {"人民日报": "2803301701"}        # 用户名uid对照表（ID在网页网址栏末端）
# ========================

# 请求头和cookie（请替换成你自己的Cookie）
COOKIE = "你的微博Cookie，从浏览器F12里复制"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0",
    "X-XSRF-TOKEN": "你的XSRF-TOKEN，从Cookie里取",
    "Cookie": COOKIE,
    "Referer": "https://weibo.com/",
}

# 连接本地MongoDB，指定库weibo、集合comments
collection = pymongo.MongoClient("127.0.0.1", 27017)["weibo"]["comments"]

#用户名 uid
author_uid = NICK_TO_UID[TARGET_NICK]

# 在该用户微博里按关键词搜帖子，挑出评论数>100的第一条
search_result = requests.get(
    "https://weibo.com/ajax/statuses/searchProfile",
    headers=headers, params={"uid": author_uid, "q": KEYWORD, "page": 1}
).json()
post_list = search_result["data"]["list"]
target_post = next(post for post in post_list if post["comments_count"] > 100)
print("选中帖子评论数:", target_post["comments_count"])

# 翻页抓这条帖子的评论并入库
max_id = 0          # 翻页游标：下一页的位置
scraped_count = 0   # 已采集条数

while True:
    response = requests.get(
        "https://weibo.com/ajax/statuses/buildComments",
        headers=headers, params={
            "is_reload": 1, "id": target_post["idstr"],
            "is_show_bulletin": 2, "is_mix": 0, "count": 20,
            "uid": author_uid, "fetch_level": 0, "locale": "zh-CN",
            "max_id": max_id,
        }
    ).json()

    for comment in response.get("data", []):
        user = comment.get("user", {})

        # 英文时间转成"年月日时分秒"格式
        comment_time = datetime.strptime(
            comment["created_at"], "%a %b %d %H:%M:%S %z %Y"
        ).strftime("%Y-%m-%d %H:%M:%S")

        # 去掉评论里的HTML标签和表情图片
        content = re.sub(r"<.*?>", "", comment.get("text", ""))

        # 整理成要保存的一条数据
        item = {
            "用户ID": user.get("idstr"),            # 评论用户id
            "评论时间": comment_time,                # 评论时间
            "评论内容": content,                     # 评论内容
            "用户地点": user.get("location"),        # 用户地点
        }

        # 按"用户ID+评论时间"去重写入：有就更新，没有就插入
        collection.update_one(
            {"用户ID": item["用户ID"], "评论时间": item["评论时间"]},
            {"$set": item}, upsert=True)
        scraped_count += 1

    # 取下一页游标，没有就结束
    max_id = response.get("max_id", 0)
    if not max_id:
        break

print("共采集", scraped_count, "条评论，已存入 MongoDB（weibo.comments）")
