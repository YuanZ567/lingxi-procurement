# ============ 灵犀采购 · 前端镜像配方（多阶段构建） ============

# ── 阶段 1：构建（build）────────────────────────────
# 用 node 镜像来跑 npm 构建；alpine 是超小版 Linux
FROM node:20-alpine AS build

# 容器工作目录
WORKDIR /app

# 先复制依赖清单（利用缓存：package.json 没变就不重装）
COPY package.json package-lock.json ./

# 安装依赖：--registry 用淘宝镜像源，国内下载快
RUN npm install --registry=https://registry.npmmirror.com

# 复制全部源码，然后执行构建 → 产物输出到 dist/
COPY . .
RUN npm run build

# ── 阶段 2：运行（nginx）────────────────────────────
# 构建完的 dist/ 只需要一个静态服务器来"托管"，nginx 最合适
# 小镜像，只有 ~50MB
FROM nginx:alpine

# 把阶段 1 构建出来的 dist 目录，复制到 nginx 的网页根目录
COPY --from=build /app/dist /usr/share/nginx/html

# 用我们自己的 nginx 配置（第 4 步会建）覆盖默认配置
COPY nginx.conf /etc/nginx/conf.d/default.conf

# nginx 默认监听 80 端口
EXPOSE 80
