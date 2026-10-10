"""
src 包初始化

【import src 路径问题：已全局解决，代码里不需要任何特殊处理】

背景：早期在 IDE 里直接运行 src/data/xxx.py 这类子目录脚本时，
会报 `ModuleNotFoundError: No module named 'src'`。
原因是 Python 只把「脚本所在目录」插入 sys.path[0]，项目根不在搜索路径上。

现在的解决方式（一次性环境配置，永久生效）：
    已在 Python 环境的 site-packages 下放置 .pth 文件：
        D:\\Pycharm\\python\\Lib\\site-packages\\lingxi_procurement.pth
    文件内容只有一行（项目根绝对路径）：
        D:\\Pycharm\\python project\\灵犀采购LinXi-Procure

Python 解释器启动时会自动读取该文件，把项目根加入 sys.path。
因此：
    - src/ 下任何脚本（含以后新建的）都可直接 `from src.xxx import yyy`
    - 不管从哪个目录运行、用哪种 IDE 配置，都生效
    - 脚本里【不需要】再加任何 sys.path 修复代码

注意：
    若将来移动或重命名项目目录，需同步修改该 .pth 文件中的路径，否则失效。
"""
