export default {                            // PostCSS 依次跑 autoprefixer 和 tailwindcss
  plugins: {
    tailwindcss: {},                        // 把 @tailwind 指令展开成真实 CSS
    autoprefixer: {}                        // 自动补 -webkit- 等厂商前缀
  }
}