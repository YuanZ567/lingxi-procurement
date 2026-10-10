/** @type {import('tailwindcss').Config} */
export default {
  content: [                                // Tailwind 只扫描这些文件里的 class，避免打包无用样式
    "./index.html",
    "./src/**/*.{vue,js}"
  ],
  theme: {
    extend: {
      colors: {                             // 把项目配色登记成 Tailwind 颜色，后面用 bg-ink / text-accent 等
        ink:    '#2c2a27',                  // 主文字色（暖墨）
        muted:  '#938b80',                  // 次要文字
        accent: '#46b3c4',                  // 冷青强调色（灵犀仔的眼睛色，去橙）
        surface:'#fffdfa',                  // 卡片表面色
        bg:     '#f6f3ee'                   // 页面暖白底
      }
    }
  },
  plugins: []
}