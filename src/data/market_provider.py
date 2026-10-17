"""
market_provider.py —— 实时行情供应商模块（数据层的"行情进货员"）
====================================================================
这个文件只干一件事：让系统随时能拿到"大宗商品今天的最新价格"。

数据从哪来？akshare 库（背后是新浪财经的期货公开行情），免费、不用注册、不用密钥。

获取价格的顺序（三级接力，保证永远有价可用）：
  第1级【Redis缓存】5分钟内刚查过的价格直接复用，不浪费请求
  第2级【上网实时抓】缓存过期了，现场去抓最新价，抓到就存进数据库留底
  第3级【降级读库】万一断网/接口挂了，退回读数据库里最近一次存的价格

为什么"写库留痕"很关键？每抓到一次真实价格就存一行，
数据库里的行情时间序列会随着使用自动越攒越多、越来越真实。
"""

# ===================== 导入依赖 =====================

import logging  # 日志模块：出问题的时候往日志里写字，方便排查，不影响程序运行
from datetime import date  # 只用来拿"今天的日期"，判断今天有没有存过价格

# akshare 是行情数据源，整个程序只有这个文件用它
# 用 try 包住：万一哪台机器没装 akshare，程序也不崩，只是实时功能自动关闭
try:
    import akshare as ak  # akshare：免费开源的财经数据库接口
    HAS_AKSHARE = True    # 开关：True 表示 akshare 装好了，实时抓取功能可用
except ImportError:       # ImportError = "找不到这个库"的报错类型
    HAS_AKSHARE = False   # 标记成 False，后面所有上网抓取的代码都会自动跳过

import redis  # Redis 客户端：连缓存服务器用的（缓存就是"临时小本本"）

# 从本项目导入之前写好的东西：
from src.db.session import SessionLocal  # 数据库会话工厂：每次要操作数据库就找它领一个"会话"
from src.db.models import CommodityPrice  # 行情表的 ORM 模型：Python 里代表 commodity_price 表

# ===================== 常量配置 =====================

# 品类名 → 期货主力合约代码 的对照表（大白话：我们卖场里的菜名 ↔ 菜市场的摊位号）
# 主力合约 = 这个品种交易最活跃的那个合约，价格最真实、流动性最好
COMMODITY_FUTURES_MAP = {
    "螺纹钢": "RB0",     # RB0 = 上期所螺纹钢主力连续合约
    "铝材": "AL0",       # AL0 = 上期所铝主力连续合约
    "铜材": "CU0",       # CU0 = 上期所铜主力连续合约
    "塑料粒子": "L0",    # L0  = 大商所 LLDPE（线性低密度聚乙烯）主力连续合约
}

# 缓存有效期：300 秒 = 5 分钟。
# 意思是：5 分钟内再来查同一个品类，直接用缓存里存的价，不上网。
# 期货价格本身每几秒才跳一次，5 分钟的缓存对采购决策完全够用，还省请求。
CACHE_TTL_SECONDS = 300

# 期货市场有交易时段（白天 9:00-15:00、夜盘），非交易时间抓到的是最近收盘价，这是正常的
# 所以即使"实时抓"，拿到"旧几小时的价"也不算错——那是市场最新的公开成交价

# ===================== 日志 =====================

# 生成一个本模块专用的日志记录器，名字叫 market_provider
# 以后日志文件里看到 [market_provider] 开头的，就是这个模块干的活
logger = logging.getLogger("market_provider")

# ===================== Redis 缓存连接 =====================

_redis_client = None  # 模块级单例：连接池复用，避免每次请求都重建连接

# redis-py 的 connect 内置"重试+退避"：对拒绝连接的端口 ping 一次能拖 6~8 秒（实测），
# 这是行情面板慢的真正元凶。这里配"零退避零重试"，失败立刻返回。
try:
    from redis.backoff import NoBackoff                    # 不做指数退避
    from redis.retry import Retry                          # redis-py 的重试控制器
    _FAST_RETRY = Retry(NoBackoff(), 0)                    # 0 次重试 = 失败立即抛错
except Exception:                                          # 老版本 redis-py 没有 retry API 也不崩
    _FAST_RETRY = None

def _get_redis():
    """
    拿一个 Redis 连接（缓存服务器的小本本）。
    重点1：Redis 是"软依赖"——连不上也绝不报错崩溃，返回 None（空），
           程序自动跳过缓存直接走"上网抓 / 读数据库"，功能照常。
    重点2：连接是模块级复用的——健康时直接返回旧连接（亚毫秒级）。
    重点3：先用裸 socket 探活（被拒秒回），再让 redis-py 建连；
           绝不让 redis-py 自己去撞死端口（它内部重试会把 0 秒的 refused 拖成 6 秒+）。
    返回：Redis 连接对象；连不上返回 None。
    """
    global _redis_client                   # 要改模块级变量，先声明
    import os                              # 读取环境变量用
    host = os.getenv("REDIS_HOST", "127.0.0.1")   # Redis 地址，默认本机
    port = int(os.getenv("REDIS_PORT", "6379"))   # 端口，默认 6379

    if _redis_client is not None:          # 有旧连接：先试旧连接（连接池复用，几乎零开销）
        try:
            _redis_client.ping()           # 探活：旧连接还活着吗（零重试配置下，失败秒回）
            return _redis_client           # 活着 → 直接复用，这就是快路径
        except Exception:                  # 旧连接死了（Redis 重启/网络抖动）
            _redis_client = None           # 丢掉死连接，走下面的重建流程

    try:
        # ---- 第一步：裸 socket 探活（create_connection 对 refused 立刻报错，0 开销）----
        import socket as _s                # 只在这里需要
        probe = _s.create_connection((host, port), 0.5)   # 0.5 秒探活超时
        probe.close()                      # 探通就关，真正的连接交给 redis-py
    except Exception as e:                 # 探活失败 = Redis 不在（拒绝/超时）
        logger.warning(f"Redis 缓存不可用（裸探失败），跳过缓存：{e}")   # 记警告不报错
        return None                        # 调用方自动走下一级方案

    try:
        # ---- 第二步：裸探已通过，redis-py 建连（带零重试配置，绝不再拖时间）----
        kwargs = dict(                     # 连接参数打包
            host=host,                     # Redis 服务器地址
            port=port,                     # 端口
            db=0,                          # 用 0 号库（缓存数据放这个库）
            socket_connect_timeout=1,      # 建连最多等 1 秒
            socket_timeout=1,              # 读写最多等 1 秒
            decode_responses=True,         # 返回值自动转成 Python 字符串
        )
        if _FAST_RETRY is not None:        # redis-py 版本支持 retry API 才传
            kwargs["retry"] = _FAST_RETRY  # 零退避零重试：失败立刻返回
        r = redis.Redis(**kwargs)          # 创建客户端
        r.ping()                           # 再 ping 一次验证真正可用（此时必然秒回）
        _redis_client = r                  # 存进模块级单例，下次请求直接复用
        return r                           # 交出连接
    except Exception as e:                 # 连不上、超时、没装 Redis……所有错误都归到这一类
        logger.warning(f"Redis 缓存不可用，跳过缓存直接抓实时：{e}")  # 记条警告日志，但不报错
        return None  # 返回空，调用方会自动走下一级方案

# ===================== 第2级：上网抓实时价 =====================

def fetch_realtime_price(commodity_name):
    """
    上网抓某个品类的实时价格（去新浪期货接口抓）。
    参数：commodity_name —— 品类中文名，比如"螺纹钢"
    返回：最新价格（数字）；抓不到返回 None。
    """
    # 没装 akshare 就别试了，直接放弃（后面会自动降级读数据库）
    if not HAS_AKSHARE:
        logger.warning("未安装 akshare，实时抓取功能不可用")  # 提醒一句：功能没开
        return None  # 返回空

    # 查对照表：这个品类对应哪个期货合约？
    symbol = COMMODITY_FUTURES_MAP.get(commodity_name)
    if symbol is None:  # 对照表里没有这个品类（比如用户输入了个没收录的材料名）
        logger.warning(f"品类 [{commodity_name}] 不在期货合约对照表里，无法实时抓取")  # 记日志
        return None  # 返回空，调用方会降级读库

    try:
        # akshare 的实时接口：futures_zh_spot = 期货实时盘口
        # symbol=合约代码，market="CF" 表示国内商品期货
        # 返回一个表格(DataFrame)，每行是一个时点的报价
        df = ak.futures_zh_spot(symbol=symbol, market="CF", adjust="0")

        # 表格可能是空的（接口没返回数据），空的话当失败处理
        if df is None or len(df) == 0:
            logger.warning(f"[{commodity_name}] 实时接口返回空数据")  # 记日志
            return None  # 返回空

        # 取最后一行 = 最新一个时点的报价
        row = df.iloc[-1]

        # 按优先级依次找价格列（接口不同版本列名不同，逐个兼容）：
        # current_price=当前最新价（新版akshare的列名）→ 最新价（中文列名旧版）→ last_close=最近收盘价
        for col in ("current_price", "最新价", "last_close", "收盘价"):
            if col in df.columns and row[col] is not None:  # 这个列存在且当前行有值
                return float(row[col])  # 转成数字返回，这就是最新价格
        # 所有候选列都没有 → 数据格式变了，当失败处理
        logger.warning(f"[{commodity_name}] 实时接口返回的数据里没有价格列")  # 记日志
        return None  # 返回空
    except Exception as e:  # 网络超时、接口改版、被限流……全都算"抓不到"
        logger.warning(f"[{commodity_name}] 实时抓取失败：{e}")  # 记日志，方便排查
        return None  # 返回空，调用方降级

# ===================== 历史回填：补齐断档的交易日 =====================

def backfill_history(commodity_name):
    """
    补齐行情断档：系统几天没开机 / 上次采集停在 9/8，中间的交易日就是空的，
    图表上会出现一段"跳崖"。本函数用新浪【日线历史】接口把缺的交易日一次拉齐。
    规则：拉全量日线 → 和库内已有日期做差集 → 只插入缺失的（幂等，重复调不产生脏数据）。
    只补最近 400 天，避免全历史重扫。返回：本次补了几条（0 = 无缺口或补失败）。

    【性能闸门（2026-10-17 加）】拉全量日线本身要下载几千行，放在请求路径里就是面板慢的元凶：
      闸门1【无缺口早退】：先花 5ms 查库内最新日期，最近两天都有数据就说明没断档，
                          连新浪都不用碰（覆盖周末/节假日：库里是周五的价，周六周日 -2 天内必然命中）；
      闸门2【半小时节流】：真有断档时，30 分钟内只允许真正拉一次新浪（Redis NX 锁），
                          防止断档期间每次轮询都白拉一遍全量。
    """
    if not HAS_AKSHARE:                                   # 没装 akshare 直接放弃，降级逻辑照旧
        return 0
    symbol = COMMODITY_FUTURES_MAP.get(commodity_name)    # 品类名 → 主力合约代码
    if symbol is None:
        return 0
    try:
        from datetime import timedelta                    # 只在这里需要"400 天前"的换算
        from sqlalchemy import func                       # max() 聚合要用

        # ---- 闸门1：无缺口早退（一次 5ms 的 max 查询，省一次几千行的全量下载） ----
        with SessionLocal() as db:
            latest = db.query(func.max(CommodityPrice.record_date))\
                       .filter(CommodityPrice.commodity_name == commodity_name).scalar()  # 库内最新行情日期
        if latest is not None:                            # 库里有数据才谈得上"新不新"
            if str(latest) >= (date.today() - timedelta(days=2)).isoformat():   # 最近两天已覆盖 → 无断档
                return 0                                  # 直接走人，不碰网络

        # ---- 闸门2：断档场景 30 分钟节流（Redis NX 锁，Redis 挂了就退化为每次都试） ----
        r = _get_redis()                                  # 拿缓存连接（复用单例，快）
        if r is not None:                                 # Redis 可用才有节流能力
            lock_key = f"market:backfill:{commodity_name}"    # 每个品类一把锁
            if not r.set(lock_key, "1", ex=1800, nx=True):    # nx=True：锁已存在返回 None → 30 分钟内已试过
                return 0                                  # 刚试过没补上，别再白拉全量

        df = ak.futures_zh_daily_sina(symbol=symbol)      # 新浪日线历史：全部交易日的开高低收
        if df is None or len(df) == 0:
            return 0
        earliest = (date.today() - timedelta(days=400)).isoformat()   # 回填窗口下界
        added = 0
        with SessionLocal() as db:
            existing = {str(r[0]) for r in db.query(CommodityPrice.record_date)
                        .filter(CommodityPrice.commodity_name == commodity_name).all()}  # 库内已有日期集合
            for _, row in df.iterrows():
                d = str(row["date"])[:10]                 # 兼容 date 列是字符串或 datetime
                if d in existing or d < earliest:         # 已有 / 太老 → 跳过（幂等关键）
                    continue
                price = row.get("close")                  # 用收盘价，和现有序列口径一致
                if price is None or price != price:       # NaN 检查（price != price 即 NaN）
                    continue
                db.add(CommodityPrice(commodity_name=commodity_name,
                                      price=float(price), record_date=d))
                added += 1
            db.commit()                                   # 一次性提交整段补齐
        if added:
            logger.info(f"[{commodity_name}] 历史回填 {added} 个交易日")
        return added
    except Exception as e:                                # 断网/接口改版都不影响主流程
        logger.warning(f"[{commodity_name}] 历史回填失败（不影响主流程）：{e}")
        return 0

# ===================== 写库留痕 =====================

def save_price_to_db(commodity_name, price):
    """
    把抓到的价格存进 commodity_price 表（留底，攒时间序列）。
    规则：同一天同一品类只存一条（当天已有就更新它），防止重复。
    参数：commodity_name 品类名；price 最新价格
    返回：True 存成功了 / False 存失败（失败不影响主流程）。
    """
    # 自己开一个数据库会话，用完立刻关（with 语句自动关，防止连接泄漏）
    with SessionLocal() as db:
        try:
            # 先查今天这个品类有没有已经存过的记录
            existing = (
                db.query(CommodityPrice)  # 查行情表
                .filter(CommodityPrice.commodity_name == commodity_name)  # 条件1：品类对上
                .filter(CommodityPrice.record_date == date.today())       # 条件2：日期是今天
                .first()  # 拿第一条（没有就返回 None）
            )
            if existing:  # 今天已经有记录了
                existing.price = price  # 直接把价格更新成最新的
            else:  # 今天还没有记录
                db.add(CommodityPrice(  # 新建一行行情记录
                    commodity_name=commodity_name,  # 品类名
                    price=price,                    # 最新价格
                    record_date=date.today(),       # 今天的日期
                ))
            db.commit()  # 提交！不 commit 的话改动不会真正写进 MySQL
            return True  # 告诉调用方：存成功了
        except Exception as e:  # 数据库没启动、表不存在、连接断……
            db.rollback()  # 回滚：把这次没成功的改动撤销，保持数据库干净
            logger.warning(f"[{commodity_name}] 行情写库失败：{e}")  # 记日志
            return False  # 告诉调用方：存失败了（但程序继续跑）

# ===================== 主入口：三级接力取价 =====================

def refresh_market_price(commodity_name):
    """
    给业务方调用的总入口：刷新某个品类的行情，返回价格来源（大白话说明用哪级拿到的）。
    业务节点（市场情报Agent）在查数据库之前先调它一下，
    这样数据库里永远是最新的价，后面原有查库逻辑一行都不用改。

    三级接力逻辑：
      1. 缓存有 → 直接用（source="缓存"）
      2. 上网抓到 → 写库留痕 + 更新缓存（source="实时"）
      3. 抓不到 → 读库里最近一条顶着用（source="数据库降级"）
      4. 库里也没有 → 真没辙了（source="无数据"）

    参数：commodity_name 品类名
    返回：来源说明文字（"缓存"/"实时"/"数据库降级"/"无数据"），业务方可拼进备注展示。
    """
    # ---------- 第1级：查缓存 ----------
    r = _get_redis()  # 先尝试拿 Redis 连接（拿不到也不报错）
    cache_key = f"market:price:{commodity_name}"  # 缓存的钥匙名，比如 market:price:螺纹钢
    if r is not None:  # Redis 连上了才有资格查缓存
        cached = r.get(cache_key)  # 用钥匙去找缓存里存的价格
        if cached:  # 找到了（5 分钟内刚查过的）
            logger.info(f"[{commodity_name}] 命中缓存，价格 {cached}")  # 记日志：用了缓存
            return "缓存"  # 告诉调用方：这个价是缓存给的

    # ---------- 第2级：上网实时抓 ----------
    # 注意：抓取函数内部虽有自己的容错，但异常类型不可穷尽（akshare 升级/SSL/超时……），
    # 这里必须再兜一层：实时级"任何异常"都视同抓不到，交给第3级降级，绝不让异常炸到调用方。
    try:
        price = fetch_realtime_price(commodity_name)  # 去新浪接口抓最新价
    except Exception as e:  # 实时级兜底：网络抖动、接口改版、SSL 错误等一律降级
        logger.warning(f"[{commodity_name}] 实时抓取异常，转入降级：{type(e).__name__}: {e}")
        price = None  # 视同"没抓到"，继续走第3级读库
    if price is not None:  # 抓到了！
        save_price_to_db(commodity_name, price)  # 先写库留底（攒真实时间序列）
        if r is not None:  # Redis 能用的话，顺手更新缓存
            try:
                # set 的带过期时间写法：存值 + 300 秒后自动作废，下次重新抓
                # （不用旧版 setex：新版 redis 库已把它标记为弃用）
                r.set(cache_key, str(price), ex=CACHE_TTL_SECONDS)
            except Exception as e:  # 缓存写失败无所谓，不影响价格本身
                logger.warning(f"缓存写入失败（不影响主流程）：{e}")  # 记一句日志就好
        logger.info(f"[{commodity_name}] 实时抓到价格 {price}，已写库留痕")  # 记日志
        return "实时"  # 告诉调用方：这个价是刚从网上抓的

    # ---------- 第3级：降级读数据库 ----------
    try:
        # 自己开个数据库会话，查这个品类最近一条行情（按日期倒序取第一条）
        with SessionLocal() as db:
            last = (
                db.query(CommodityPrice)  # 查行情表
                .filter(CommodityPrice.commodity_name == commodity_name)  # 品类对上
                .order_by(CommodityPrice.record_date.desc())  # 按日期从新到旧排
                .first()  # 取最新那条
            )
            if last:  # 库里有历史行情
                logger.warning(  # 记警告：这是降级来的价，不是实时的
                    f"[{commodity_name}] 实时抓取失败，降级用库中 {last.record_date} 的价格 {last.price}"
                )
                return "数据库降级"  # 告诉调用方：实时没抓到，用的库里存的
    except Exception as e:  # 连数据库都挂了（MySQL 没启动之类）
        logger.error(f"[{commodity_name}] 降级读库也失败：{e}")  # 记错误日志

    # ---------- 第4级：真没辙了 ----------
    logger.error(f"[{commodity_name}] 缓存/实时/数据库全都没拿到价格")  # 记严重错误
    return "无数据"  # 告诉调用方：完全没拿到价（业务方自己决定怎么处理）

# ===================== 命令行直接运行时的自测入口 =====================

def quick_source(commodity_name):
    """
    【只花几毫秒】快速判断行情"新鲜度徽标"该显示什么，不上网、不拉全量：
      缓存命中               → "缓存"（5 分钟内刚抓过实时价）
      库里有今天的价         → "实时"（今天已经抓成功过一次并落库了）
      库里最新是昨天及以前   → "数据库降级"（今天的实时价还没抓到）
    给 /api/market 的立即返回用——真正的联网刷新挪到后台任务里做，请求路径零等待。
    """
    r = _get_redis()                                       # 拿缓存连接（单例复用，亚毫秒）
    if r is not None:                                      # Redis 可用才查得到缓存
        try:
            if r.get(f"market:price:{commodity_name}"):    # 5 分钟内的缓存还在
                return "缓存"                              # 徽标显示"缓存"
        except Exception:                                  # 缓存读失败无所谓，继续往下判
            pass
    try:
        with SessionLocal() as db:                         # 查库：今天这个品类有没有价
            has_today = (db.query(CommodityPrice.id)
                           .filter(CommodityPrice.commodity_name == commodity_name,
                                   CommodityPrice.record_date == date.today())
                           .first() is not None)           # 有今天记录 → 今天的实时价已落库
        return "实时" if has_today else "数据库降级"       # 给前端徽标用
    except Exception as e:                                 # 连库都挂了（极端情况）
        logger.warning(f"[{commodity_name}] quick_source 查库失败：{e}")
        return "无数据"                                    # 与原接口的第4级口径一致

def refresh_market_background(commodity_name):
    """
    【后台任务专用】把"补历史 + 抓实时"这两件慢事打包，丢给 FastAPI 的 BackgroundTasks 执行。
    请求路径里绝不调它——这就是 stale-while-revalidate（先用旧数据秒回，后台悄悄刷新）。
    任何异常都吞掉：后台刷新失败只影响数据新鲜度，绝不能反过来砸了已经返回的响应。
    """
    try:
        backfill_history(commodity_name)                   # 先补历史断档（有闸门，多数情况秒退）
    except Exception as e:                                 # 补历史失败不拦着抓实时
        logger.warning(f"[{commodity_name}] 后台回填失败：{e}")
    try:
        refresh_market_price(commodity_name)               # 再抓实时价（三级接力内部自带容错）
    except Exception as e:                                 # 抓实时失败下次轮询再试
        logger.warning(f"[{commodity_name}] 后台刷新失败：{e}")

# 直接 `python -m src.data.market_provider 螺纹钢` 就能单独测这个模块
if __name__ == "__main__":
    import sys  # 用来读命令行参数
    name = sys.argv[1] if len(sys.argv) > 1 else "螺纹钢"  # 命令行第一个参数当品类名，没传就默认测螺纹钢
    source = refresh_market_price(name)  # 跑一遍三级接力
    print(f"品类[{name}] 行情来源：{source}")  # 打印价格来源（缓存/实时/降级/无数据）
