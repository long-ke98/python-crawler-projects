# # Define your item pipelines here
# from itemadapter import ItemAdapter
# import pymongo
#
#
# class WinshangdataPipeline:
#     def __init__(self):
#         # 实例化一个 mongodb 数据库的客户端连接对象
#         self.client = pymongo.MongoClient('127.0.0.1', 27017)
#
#         # 指定数据库，如果数据库名称不存在，就会创建一个数据库
#         self.db = self.client['winshang_brand']
#
#         # 指定集合，如果集合名称不存在，就会创建该集合
#         self.collection = self.db['brands']
#
#     def process_item(self, item, spider):
#         # 将 item 字典数据写入到 mongodb 数据库中
#         doc = dict(ItemAdapter(item))
#
#         # brand_id 只作为唯一主键 _id，不写入业务字段
#         bid = doc.pop('brand_id', None)
#
#         # 按 _id 去重写入（重复运行不会产生重复文档）
#         self.collection.update_one({'_id': bid}, {'$set': doc}, upsert=True)
#
#         return item
#
#     # 关闭爬虫后调用的方法
#     def close_spider(self, spider):
#         # 关闭客户端
#         self.client.close()

# 导入MongoDB客户端
import pymongo

class WinshangPipeline:
    def open_spider(self, spider):
        # 连接MongoDB
        self.client = pymongo.MongoClient("mongodb://127.0.0.1:27017/")
        self.db = self.client["winshang"]  # 数据库名
        self.col = self.db["brand"]         # 集合名

    def process_item(self, item, spider):
        # 品牌名当key去重
        self.col.update_one(
            {"品牌名称": item["品牌名称"]},
            {"$set": dict(item)},
            upsert=True
        )
        # 打印进度
        print(f'《{item["品牌名称"]}》已完成')
        return item

    def close_spider(self, spider):
        self.client.close()
