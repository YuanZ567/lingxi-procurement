"""
统一日志模块
所有 Agent 节点/工具都通过 get_logger("名字") 拿自己的日志器
日志同时写到：控制台（开发时看）+ logs/lingxi.log 文件（事后追溯/面试演示留痕）
对应 PRD §4.2 "每一步执行留痕、可追溯、可复盘" 的硬性验收要求
"""

import os                                            # os.path/makedirs：处理日志目录
import logging
from logging.handlers import RotatingFileHandler       # 按大小轮转的文件日志处理器
from src.utils.config import get_settings               # 日志级别从配置中心读

# ---------- 日志输出格式 ----------
# 时间 | 级别(左对齐7位) | 模块名 | 消息内容
LOG_FORMAT="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"

#日志文件目录：项目根目录下的 logs/
LOG_DIR="logs"
os.makedirs(LOG_DIR,exist_ok=True)   # 目录不存在就创建；已存在不报错

def get_logger(name:str)->logging.Logger:
    """
    获取指定模块名的日志器
    用法：logger = get_logger("market_agent")，之后 logger.info("xxx")
    同一个名字重复调用会返回同一个日志器，不会重复添加输出通道
    """

    logger=logging.getLogger(name)      # 按名字从日志系统取（不存在会自动创建）
    if logger.handlers:                 # handlers 不为空说明这个日志器配置过了
        return logger                    # 直接返回，防止重复加输出通道导致日志打印两遍

    logger.setLevel(get_settings().LOG_LEVEL)   # 设置总开关级别（低于该级别的日志不输出）

    formatter=logging.Formatter(LOG_FORMAT)        # 创建格式化器，所有输出通道共用

    # ---------- 通道1：控制台输出（开发调试用） ----------
    console_handler=logging.StreamHandler()  #往终端打的处理器
    console_handler.setFormatter(formatter)     #套上格式
    logger.addHandler(console_handler)           #挂到日志器上

     # ---------- 通道2：文件输出（留痕追溯用） ----------
    file_handler=RotatingFileHandler(                   # 轮转文件处理器：文件太大自动换新文件
        filename=os.path.join(LOG_DIR,"lingxi.log"),     # 日志文件路径
        maxBytes=5 * 1024 *1024,                          # 单个文件最大 5MB
        backupCount=3,                                     # 最多保留 3 个旧文件，防止磁盘被撑爆
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger