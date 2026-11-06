"""             no.1                """
# import json
# import scrapy
# from scrapy import item
# from twisted.pair import raw
#
# # 两个接口地址：列表接口拿一页品牌名单，详情接口拿单个品牌的详细字段
# LIST_API = "https://www.winshangdata.com/wsapi/brand/getBigdataList3_4"
# DETAIL_API = "https://www.winshangdata.com/wsapi/brand/getBigdataBrandExpandDemand"
# HEAD = {"Content-Type": "application/json;charset=UTF-8"}
#
#
# class WinshangBrandSpider(scrapy.Spider):
#     # 爬虫的名字
#     name = "yingshang"
#     # 只允许抓这个域名下的链接
#     allowed_domains = ["www.winshangdata.com"]
#     page_max = 1        # 抓几页：每页60条，这里设1就只抓前60条，改成5就是全部286条
#     page_count = 0      # 已经抓了几页，用来控制翻页
#
#     def post(self, url, payload, cb, **kw):
#         # 统一发一个 POST JSON 请求
#         # url：请求地址；payload：发给服务器的数据；cb：拿到响应后回调哪个函数
#         return scrapy.Request(url, method="POST", body=json.dumps(payload),
#                               headers=HEAD, callback=cb, cb_kwargs=kw)
#
#     def start_requests(self):
#         # 请求第1页的品牌列表
#         yield self.post(LIST_API, {"pageNum": 1, "pageSize": 60, "orderBy": "1"}, self.parse_list)
#
#     def parse_list(self, response):
#         # 处理列表接口的返回结果：从这一页里取出每个品牌，再逐个去请求它的详情
#         data = json.loads(response.text)["data"]       # 把返回的 JSON 字符串转成字典
#         self.page_count += 1                           # 已抓页数 +1
#         for b in data["list"]:                         # 遍历这一页的每个品牌
#             # 拿到 brandId 后，再请求详情接口，结果交给 parse_item 处理
#             yield self.post(DETAIL_API, {"brandId": str(b["brandId"])},
#                             self.parse_item, brand=b)
#         if self.page_count < self.page_max:            # 还没到设定的最大页数，就继续抓下一页
#             yield self.post(LIST_API,
#                             {"pageNum": self.page_count + 1, "pageSize": 60, "orderBy": "1"},
#                             self.parse_list)
#
#     def parse_item(self, response, brand):
#         # 处理单个品牌的详情返回，提取我们要的5个字段，打包成字典返回给管道入库
#         d = json.loads(response.text).get("data") or {}   # 详情数据，取不到就当空字典
#
#         # 把最小~最大拼成 "最小值-最大值单位"，比如 200-500㎡；缺值就返回 None
#         rng = lambda a, b, u: f"{float(a):g}-{float(b):g}{u}" if a and b else None
#
#         # 左边字段名，右边是从接口数据里取值
#         data = response.json()["data"]
#         item["创建时间"] = data.get("createDate", "暂无") or "暂无"
#         item["开店方式"] = data.get("kaiDianMode", "暂无") or "暂无"
#         item["合作期限"] = f'{data["qiXian_Min"]}-{data["qiXian_Max"]}年' if data.get("qiXian_Min") else "暂无"
#         item["面积要求"] = f'{int(data["mianJi_Min"])}-{int(data["mianJi_Max"])}㎡' if data.get("mianJi_Min") else "暂无"
#
#         # 循环有值就转成字符串，没值就填"暂无"
#         return {k: str(v) if v not in (None, "", []) else "暂无" for k, v in raw.items()}


"""             no.2                """
#
# import json
# import scrapy
#
# # 两个接口地址：列表接口拿一页品牌名单，详情接口拿单个品牌的详细字段
# LIST_API = "https://www.winshangdata.com/wsapi/brand/getBigdataList3_4"
# DETAIL_API = "https://www.winshangdata.com/wsapi/brand/getBigdataBrandExpandDemand"
# HEAD = {
#     "Content-Type": "application/json;charset=UTF-8",
#     "apptype": "bigdata",
#     "platform": "pc",
#     "uid": "ws124537705",
#     "pwd": "a5f98e908a160cfc",
#     "uuid": "123456",
#     "origin": "https://www.winshangdata.com",
#     "referer": "https://www.winshangdata.com/brandDetail",
# }
#
# class WinshangBrandSpider(scrapy.Spider):
#     # 爬虫的名字
#     name = "yingshang"
#     # 只允许抓这个域名下的链接
#     allowed_domains = ["www.winshangdata.com"]
#     page_max = 1        # 抓几页：每页60条，这里设1就只抓前60条，改成5就是全部286条
#     page_count = 0      # 已经抓了几页，用来控制翻页
#
#     def post(self, url, payload, cb, **kw):
#         # 统一发一个 POST JSON 请求
#         return scrapy.Request(url, method="POST", body=json.dumps(payload),
#                               headers=HEAD, callback=cb, cb_kwargs=kw)
#
#     async def start(self):
#         # 请求第1页的品牌列表
#         yield self.post(LIST_API, {"pageNum": 1, "pageSize": 60, "orderBy": "1"}, self.parse_list)
#
#     def parse_list(self, response):
#         # 处理列表接口的返回结果：从这一页里取出每个品牌，再逐个去请求它的详情
#         data = json.loads(response.text)["data"]
#         self.page_count += 1
#         for b in data["list"]:
#             yield self.post(DETAIL_API, {"brandId": str(b["brandId"])},
#                             self.parse_item, brand=b)
#         if self.page_count < self.page_max:
#             yield self.post(LIST_API,
#                             {"pageNum": self.page_count + 1, "pageSize": 60, "orderBy": "1"},
#                             self.parse_list)
#
#     def parse_item(self, response, brand):
#         d = json.loads(response.text).get("data") or {}
#
#         # 如果数据为空，重新请求一次（dont_filter=True 防止去重）
#         if not d or not d.get("createDate"):
#             yield self.post(DETAIL_API, {"brandId": str(brand["brandId"])},
#                             self.parse_item_retry, brand=brand).replace(dont_filter=True)
#             return
#
#         raw = {
#             "brand_id": str(brand["brandId"]),
#             "品牌名称": d.get("brandName") or brand["brandName"],
#             "创建时间": d.get("createDate"),
#             "开店方式": d.get("kaiDianMode"),
#             "合作期限": f'{d["qiXian_Min"]}-{d["qiXian_Max"]}年' if d.get("qiXian_Min") else None,
#             "面积要求": f'{int(d["mianJi_Min"])}-{int(d["mianJi_Max"])}㎡' if d.get("mianJi_Min") else None,
#         }
#         # 注意：这里必须用 yield，不能用 return
#         # 因为上面有空数据时 yield 了重试请求，整个函数已经是 generator
#         # generator 里 return 的 dict 会被 Python 吞成 StopIteration.value，Scrapy 读不到
#         yield {k: str(v) if v not in (None, "", []) else "暂无" for k, v in raw.items()}
#
#     def parse_item_retry(self, response, brand):
#         # 重试一次后正常解析
#         d = json.loads(response.text).get("data") or {}
#         raw = {
#             "brand_id": str(brand["brandId"]),
#             "品牌名称": d.get("brandName") or brand["brandName"],
#             "创建时间": d.get("createDate"),
#             "开店方式": d.get("kaiDianMode"),
#             "合作期限": f'{d["qiXian_Min"]}-{d["qiXian_Max"]}年' if d.get("qiXian_Min") else None,
#             "面积要求": f'{int(d["mianJi_Min"])}-{int(d["mianJi_Max"])}㎡' if d.get("mianJi_Min") else None,
#         }
#         return {k: str(v) if v not in (None, "", []) else "暂无" for k, v in raw.items()}


"""         no.3            """
import json
import scrapy

# 两个接口地址：列表接口拿一页品牌名单，详情接口拿单个品牌的详细字段
LIST_API = "https://www.winshangdata.com/wsapi/brand/getBigdataList3_4"
DETAIL_API = "https://www.winshangdata.com/wsapi/brand/getBigdataBrandExpandDemand"
HEAD = {
    "Content-Type": "application/json;charset=UTF-8",
    "apptype": "bigdata",
    "platform": "pc",
    "uid": "ws124537705",
    "pwd": "a5f98e908a160cfc",
    "uuid": "123456",
    "origin": "https://www.winshangdata.com",
    "referer": "https://www.winshangdata.com/brandDetail",
}

class WinshangBrandSpider(scrapy.Spider):
    # 爬虫的名字
    name = "yingshang"
    # 只允许抓这个域名下的链接
    allowed_domains = ["www.winshangdata.com"]
    page_max = 1        # 抓几页：每页60条，这里设1就只抓前60条，改成5就是全部286条
    page_count = 0      # 已经抓了几页，用来控制翻页

    def post(self, url, payload, cb, **kw):
        # 统一发一个 POST JSON 请求
        return scrapy.Request(url, method="POST", body=json.dumps(payload),
                              headers=HEAD, callback=cb, cb_kwargs=kw)

    async def start(self):
        # 请求第1页的品牌列表
        yield self.post(LIST_API, {"pageNum": 1, "pageSize": 60, "orderBy": "1"}, self.parse_list)

    def parse_list(self, response):
        # 处理列表接口的返回结果：从这一页里取出每个品牌，再逐个去请求它的详情
        data = json.loads(response.text)["data"]
        self.page_count += 1
        for b in data["list"]:
            yield self.post(DETAIL_API, {"brandId": str(b["brandId"])},
                            self.parse_item, brand=b)
        if self.page_count < self.page_max:
            yield self.post(LIST_API,
                            {"pageNum": self.page_count + 1, "pageSize": 60, "orderBy": "1"},
                            self.parse_list)

    def parse_item(self, response, brand):
        d = json.loads(response.text).get("data") or {}

        # 如果数据为空（被限流），重新请求一次
        # dont_filter=True：和原请求 URL+body 一模一样，不加这个会被 dupefilter 过滤掉
        # 改用单独的回调 parse_item_retry，避免再次走"空就重试"的分支
        if not d or not d.get("createDate"):
            yield self.post(DETAIL_API, {"brandId": str(brand["brandId"])},
                            self.parse_item_retry, brand=brand).replace(dont_filter=True)
            return

        raw = {
            "品牌ID": str(brand["brandId"]),
            "品牌名称": d.get("brandName") or brand["brandName"],
            "创建时间": d.get("createDate"),
            "开店方式": d.get("kaiDianMode"),
            "面积要求": f'{int(d["mianJi_Min"])}-{int(d["mianJi_Max"])}㎡' if d.get("mianJi_Min") else None,
            "合作期限": f'{d["qiXian_Min"]}-{d["qiXian_Max"]}年' if d.get("qiXian_Min") else None,
        }
        # 注意：这里必须用 yield，不能用 return
        # 因为上面有空数据时 yield 了重试请求，整个函数已经是 generator
        # generator 里 return 的 dict 会被 Python 吞成 StopIteration.value，Scrapy 读不到
        yield {k: str(v) if v not in (None, "", []) else "暂无" for k, v in raw.items()}

    def parse_item_retry(self, response, brand):
        # 重试一次后正常解析。这个函数里没有 yield，是普通函数，return dict 没问题
        d = json.loads(response.text).get("data") or {}
        raw = {
            "品牌ID": str(brand["brandId"]),
            "品牌名称": d.get("brandName") or brand["brandName"],
            "创建时间": d.get("createDate"),
            "开店方式": d.get("kaiDianMode"),
            "面积要求": f'{int(d["mianJi_Min"])}-{int(d["mianJi_Max"])}㎡' if d.get("mianJi_Min") else None,
            "合作期限": f'{d["qiXian_Min"]}-{d["qiXian_Max"]}年' if d.get("qiXian_Min") else None,
        }
        return {k: str(v) if v not in (None, "", []) else "暂无" for k, v in raw.items()}
