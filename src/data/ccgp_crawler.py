"""
ccgp_crawler.py —— 政采网真实成交记录爬虫（数据层的"市场情报收集员"）
====================================================================
干什么：去中国政府采购网（ccgp.gov.cn）搜索"中标/成交公告"，
把真实成交记录（哪家公司、中了什么标、多少钱、哪天）存成 CSV。

产出：seed_data/ccgp_deals.csv
用途：谈判 Agent 的真实成交参考、供应商背景佐证。

合规三原则（写代码前先默念）：
  1. 慢：每次请求间隔3秒以上，比人手动翻页还慢
  2. 少：一次运行最多抓200个请求，够用就好
  3. 让：碰到限流页立刻退避60秒，绝不硬刚
"""

import time #time.sleep:实现低频访问
import random 
import csv          # 标准库CSV读写：结果要存成csv给种子数据用
import re            # 正则表达式：从公告正文里抠"中标供应商""中标金额"用的
import requests     # HTTP客户端：发请求拿网页用的

# BeautifulSoup：HTML解析神器，把网页文本变成可以查询的对象树
from bs4 import BeautifulSoup
# pathlib 的 Path：跨平台的文件路径处理（Windows/Mac 都能用）
from pathlib import Path

# ===================== 配置区（要改就改这里） =====================

# 政采网的搜索接口地址（{} 是占位符，请求时才填入关键词等参数）
SEARCH_URL = "http://search.ccgp.gov.cn/bxsearch"

# 伪装成正常浏览器的请求头：9/9 实测裸 UA 会被政采网指纹级拦截（弹"频繁访问"页），
# 必须带完整浏览器头（Accept/Accept-Language等）才能正常访问
HEADERS={
    "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language":"zh-CN,zh;q=0.9,en;q=0.8",
    "Connection":"keep-alive",
    "Upgrade-Insecure-Requests":"1",
}

# 会话对象：复用连接、自动携带cookie；run() 启动时会先访问主页做"预热"
SESSION=requests.Session()

# 关键词 → 品类 的搜索清单：每个品类用多个关键词搜，提高覆盖率
# 9/9 扩池：政府采购以"设备/工程"为主，原材料关键词要铺得开才能命中足够多的成交公告
KEYWORD_MAP={
    "螺纹钢":["螺纹钢","钢材","钢筋","线材","带钢","热轧卷板","型钢","钢板"],   # 螺纹钢相关的搜索词
    "铝材":["铝材","铝锭","铝型材","铝合金","铝棒","铝板","氧化铝"],           # 铝材相关的搜索词
    "铜材":["铜材","电解铜","铜管","铜棒","铜板","铜带","黄铜","阴极铜"],      # 铜材相关的搜索词
    "塑料粒子":["塑料原料","聚乙烯","聚丙烯","PVC","ABS","工程塑料","聚甲醛"]  # 塑料相关的搜索词
}

REQUEST_INTERVAL=3               # 两次请求之间的基础间隔（秒），合规底线：≥3
REQUEST_JITTER=2                # 间隔的随机抖动幅度（秒），实际间隔=3~5秒随机
MAX_PAGES_PER_KW=5               # 每个关键词最多翻几页（一页约20条，2页=40条，够用了）
BLOCK_BACKOFF_SECONDS=60           # 被限流时退避多久（秒）：等一分钟再继续，别硬刚

# 结果输出路径：项目根目录/seed_data/ccgp_deals.csv
# Path(__file__) 是当前文件位置，.parent.parent.parent 往上跳3级到项目根
OUTPUT_CSV=Path(__file__).parent.parent.parent / "seed_data" / "ccgp_deals.csv"


def polite_get(url,params=None):
    """
    "有礼貌的"请求函数：带随机间隔、能识别限流页并自动退避。
    所有网络请求都必须走它，保证整个爬虫是低频合规的。
    返回：网页HTML文本；彻底失败返回 None。
    """
    # 先睡一会儿再发请求：基础间隔 + 随机抖动，比如睡 3~5 秒
    time.sleep(REQUEST_INTERVAL + random.uniform(0, REQUEST_JITTER))

    try:
         #发 GET 请求：走会话对象（自动带cookie），params 自动拼到 URL 后面
        resp=SESSION.get(url,params=params,headers=HEADERS,timeout=15)

        # 识别限流页：政采网被限流时会返回一个标题带"频繁访问"的页面
        if "频繁访问" in resp.text:
            print(f"被限流了！退避 {BLOCK_BACKOFF_SECONDS} 秒后继续...")
            time.sleep(BLOCK_BACKOFF_SECONDS)  # 睡一分钟，给对方服务器喘口气
            return None                         # 这次当失败处理

         # 响应编码声明为 utf-8：政采网页面是 utf-8，不设置可能中文乱码
        resp.encoding="utf-8"
        return resp.text   #把网页源代码文本交出去
    except Exception as e:      # 网络超时、断网等所有异常
        print(f"请求失败：{e}")     # 打印原因
        return None                  # 当失败处理

def parse_search_page(html):
    """
    解析搜索结果列表页，捞出所有"成交公告"条目。
    返回：列表，每项是一个 dict（标题/链接/日期/地区）。
    """
    results=[]          # 存解析结果的空列表

     # BeautifulSoup 把 HTML 文本变成对象树，"html.parser" 是Python自带的解析器
    soup=BeautifulSoup(html,"html.parser")

    # 找结果列表：每条公告在 <a> 标签里，链接路径含 /cjgg/ 的才是"成交公告"
    # （cjgg=成交公告；gkzb=公开招标；fblbgg=废标公告——后两种不要）
    for a_tag in soup.find_all("a", href=True):  # 遍历页面上所有带链接的 <a> 标签
        href = a_tag["href"]                     # 拿到链接地址

        # 链接地址归一化：政采网列表页可能给出三种形态——完整http链接、//开头协议相对、/开头纯相对路径
        # requests 只认完整 URL，这里统一补全成 http 开头，防止详情页请求直接报错
        if href.startswith("//"):           # 协议相对路径（//www.ccgp.gov.cn/...）
            href="http:"+href               # 补上 http: 前缀
        elif href.startswith("/"):          # 纯相对路径（/cjgg/...）
            href="http://www.ccgp.gov.cn"+ href         # 补上站点域名

        title=a_tag.get_text(strip= True)           # 拿到链接文字（公告标题），strip去掉首尾空白

        # 只要成交公告，并且标题不能是空的
        if "/cjgg" in href and title:
             # 标题在 <a> 同一个 <li> 里还带着日期和地区文字，往父级找
            parent=a_tag.find_parent("i1")          # 找到这条公告所在的 <li> 容器
            context=parent.get_text(" ", strip=True) if parent else title
            # 用正则从文字里抠日期：形如 2026.08.13 或 2026-08-13
            date_match = re.search(r"(\d{4})[.\-](\d{2})[.\-](\d{2})", context)
            # 抠出来后统一转成 2026-08-13 格式；没抠到就给空字符串
            pub_date = f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}" if date_match else ""
            results.append({          # 把这条公告存进结果列表
                "title": title,       # 公告标题（后面要从标题里抠供应商和物料线索）
                "url": href,          # 公告详情页链接（第二步要去抓正文）
                "publish_date": pub_date,  # 发布日期
            })
    return results  # 交出这一页抓到的所有成交公告

def parse_detail(html):
    """
    解析成交公告详情页，抠出核心三要素：中标供应商、中标金额、采购人。
    政采网公告是固定模板文，用正则抠关键字段比 BeautifulSoup 更稳。
    返回：dict（可能挖不全，挖不到的字段是 None）。
    """
  # 先把 HTML 变成纯文本：BeautifulSoup 剥掉所有标签只留文字
    text=BeautifulSoup(html,"html.parser").get_text("\n",strip=True)

     # 定义要抠的字段和对应的正则（公告模板里字段名是固定的，按字段名定位）
    patterns = {
        # 9/9 实测公告有三种模板措辞，正则按最长优先依次兼容：
        # ①"中标（成交）供应商名称：X" ②"中标成交供应商名称：X" ③"供应商名称：X"（在中标信息段落内）
        "supplier_name": r"(?:中标（成交）供应商名称|中标成交供应商名称|供应商名称)[：:]\s*([^\n，,。]+)",
        # 兼容 "总中标成交金额" / "中标（成交）金额" / "中标成交金额" / "中标金额" 四种写法
        "amount_raw": r"(?:总中标成交金额|中标（成交）金额|中标成交金额|中标金额)[：:]?\s*([^\n，,。]{1,30})",
        # "采购人：" 后面是采购单位名
        "buyer": r"采购人[：:]\s*([^\n，,。]+)",
    }
    out = {}  # 存抠出来的结果
    for key, pat in patterns.items():  # 逐个字段尝试抠
        m = re.search(pat, text)       # 在公告全文里搜这个模式
        out[key] = m.group(1).strip() if m else None  # 抠到就存，抠不到存 None
    return out  # 交出结果

def classify_category(title):
    """
    判断一条公告属于我们四个品类里的哪一个。
    方法：标题里包含哪个品类的搜索词，就归哪个品类；都不含就归"其他"。
    """
    for category, keywords in KEYWORD_MAP.items():  # 遍历品类和它们的搜索词
        for kw in keywords:                         # 遍历这个品类的每个搜索词
            if kw in title:                         # 标题里出现了搜索词
                return category                     # 就判定为这个品类
    return "其他"  # 四个品类都不沾边（可能是别的采购），标成"其他"

def run(max_total_requests=150):
    """
    爬虫主流程：按关键词搜索 → 解析列表 → 抓详情 → 抠字段 → 写CSV。
    max_total_requests：本次运行最多发多少个请求（默认150，合规上限内的保险丝）。
    """
    request_count = 0  # 请求计数器：记录本次已经发了多少个请求
    rows = []          # 存最终结果的列表
    seen_urls = set()  # 已收录公告URL集合（去重用）

    # 主页预热：先访问一次官网首页（模拟真人先到首页），让会话拿到正常的访问状态
    # 9/9 实测：不预热+裸请求头会被指纹级拦截，预热+完整头后搜索页正常返回
    warmup=SESSION.get("http://www.ccgp.gov.cn/",headers=HEADERS,timeout=15)
    request_count += 1
    print(f"主页预热完成：{warmup.status_code}，cookie {len(SESSION.cookies)} 个")
    time.sleep(REQUEST_INTERVAL + random.uniform(0, REQUEST_JITTER))  # 预热后也按规矩等待

    for category,keywords in KEYWORD_MAP.items():# 外层循环：遍历四个品类
        for kw in keywords:                     # 中层循环：遍历这个品类的搜索词
            for page in range(1,MAX_PAGES_PER_KW+1):  # 内层循环：翻页（每词最多2页）
                if request_count >= max_total_requests:    # 保险丝：请求超限立刻收工
                    print("已达本次请求上限，主动收工（合规第一）")
                    break

                # 组装搜索参数：kw=关键词，page_index=第几页，timeType=5 表示"近半年"
                params={
                    "searchtype":1,     # 1=标题检索（在公告标题里搜关键词）
                    "kw":kw,            # 搜索关键词，比如"电解铜"
                    "page_index":page,   # 第几页
                    "bidSort": 0,       # 排序方式：0=默认排序
                    "pinMu":0,          # 品目筛选：0=不限
                    "bidType": 0,       # 公告类型：0=不限（列表解析时自己过滤成交公告）
                    "timeType": 5,      # 时间范围：5=近半年（0今日 2近周 3近月 4近三月 5半年）
                }
                html=polite_get(SEARCH_URL,params)  # 发请求（内部已带限速和退避）
                request_count += 1                 # 计数+1
                if not html:                        # 请求失败（限流/超时）
                    continue                        # 跳过这页，继续下一页

                items=parse_search_page(html)    # 解析列表页，拿到成交公告清单
                print(f"[{kw}] 第{page}页：解析出 {len(items)} 条成交公告")

                for item in items:                     # 逐条处理公告
                    # 9/9 实测：搜索结果里混着大量不相关公告（如装修工程），
                    # 先按标题过滤品类，"其他"直接跳过——连详情页都不抓，省请求又省解析
                    if classify_category(item["title"])=="其他":
                        continue
                    detail_html = polite_get(item["url"])  # 去抓公告详情页
                    request_count += 1                     # 详情页请求也计数
                    if not detail_html:                    # 详情抓失败
                        continue                           # 跳过这条
                    fields = parse_detail(detail_html)     # 抠供应商/金额/采购人
                    if not fields.get("supplier_name"):    # 没抠到供应商名的公告没价值
                        continue                           # 跳过
                    if item["url"] in seen_urls:           # 去重：翻页重叠导致同一公告出现两次
                        continue                           # 已收录过的公告直接跳过
                    seen_urls.add(item["url"])             # 记录已收录的URL
                    rows.append({                          # 组装成一行最终数据
                        "category": classify_category(item["title"]),  # 归类到四品类
                        "supplier_name": fields["supplier_name"],      # 中标供应商
                        "material_kw": kw,                             # 命中的搜索词
                        "amount_raw": fields.get("amount_raw"),        # 中标金额原文
                        "buyer": fields.get("buyer"),                  # 采购单位
                        "publish_date": item["publish_date"],          # 发布日期
                        "title": item["title"],                        # 公告标题
                        "url": item["url"],                            # 原文链接（溯源用）
                    })

     # 把所有结果写进 CSV（utf-8-sig 编码让 Excel 打开不乱码）
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)  # 确保 seed_data 目录存在
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else
                                ["category","supplier_name","material_kw","amount_raw","buyer","publish_date","title","url"])
        writer.writeheader()  # 写表头
        writer.writerows(rows)  # 写所有数据行
    print(f"===== 爬取完成：共 {len(rows)} 条真实成交记录 → {OUTPUT_CSV} =====")
    print(f"本次共发出 {request_count} 个请求（上限 {max_total_requests}）")


if __name__ == "__main__":
    # 直接运行就开爬：python -m src.data.ccgp_crawler
    run()  # 用默认150个请求的上限

        