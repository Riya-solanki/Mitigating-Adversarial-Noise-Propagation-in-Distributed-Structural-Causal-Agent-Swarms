/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
      colors: {
        'city-bg': '#070b14',
        'city-surface': '#0f1729',
        'city-accent': '#22d3ee',
        'city-accent-alt': '#a78bfa',
      }
    },
  },
  plugins: [],
}
