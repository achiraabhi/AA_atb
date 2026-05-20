/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Industrial dark theme palette
        surface:  '#0d1117',
        panel:    '#161b22',
        card:     '#1c2230',
        border:   '#2a3448',
        muted:    '#3a4560',
        // accent / state colors
        primary:  '#1a6fd4',
        accent:   '#00e5ff',
        glow:     '#00ff88',
        warning:  '#f59e0b',
        danger:   '#ef4444',
        success:  '#22c55e',
        // relay states
        relayOn:  '#00ff88',
        relayOff: '#1c2230',
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'Consolas', 'monospace'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      animation: {
        'pulse-fast':  'pulse 0.8s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'glow-pulse':  'glowPulse 1.5s ease-in-out infinite',
        'flow-right':  'flowRight 1.2s linear infinite',
      },
      keyframes: {
        glowPulse: {
          '0%, 100%': { filter: 'drop-shadow(0 0 4px #00ff88)' },
          '50%':       { filter: 'drop-shadow(0 0 12px #00ff88)' },
        },
        flowRight: {
          '0%':   { strokeDashoffset: '24' },
          '100%': { strokeDashoffset: '0' },
        },
      },
      boxShadow: {
        glow:    '0 0 12px rgba(0,255,136,0.4)',
        accent:  '0 0 12px rgba(0,229,255,0.3)',
        danger:  '0 0 12px rgba(239,68,68,0.4)',
      },
    },
  },
  plugins: [],
}
