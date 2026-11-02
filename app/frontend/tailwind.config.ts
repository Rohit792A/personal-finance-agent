import type { Config } from 'tailwindcss';
import { legacyThemeExtend, legacyThemePlugin } from './tailwind.legacy-theme';

/** Semantic design tokens. Values live in src/styles.css as RGB channels, per theme. */
const token = (name: string) => `rgb(var(--color-${name}) / <alpha-value>)`;

const systemSans = [
  'ui-sans-serif',
  'system-ui',
  '-apple-system',
  'BlinkMacSystemFont',
  '"Segoe UI"',
  'Roboto',
  '"Helvetica Neue"',
  'Arial',
  'sans-serif',
];

export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx,html}'],
  darkMode: ['selector', '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        canvas: token('canvas'),
        surface: { DEFAULT: token('surface'), 2: token('surface-2'), 3: token('surface-3') },
        line: { DEFAULT: token('line'), strong: token('line-strong') },
        fg: { DEFAULT: token('fg'), muted: token('fg-muted') },
        accent: {
          DEFAULT: token('accent'),
          hover: token('accent-hover'),
          fg: token('accent-fg'),
          soft: token('accent-soft'),
          text: token('accent-text'),
        },
        success: { DEFAULT: token('success'), soft: token('success-soft') },
        warning: { DEFAULT: token('warning'), soft: token('warning-soft') },
        danger: { DEFAULT: token('danger'), soft: token('danger-soft') },
        focus: token('focus'),
      },
      fontFamily: {
        sans: systemSans,
        // Legacy screens use font-display for headings; one family keeps the type consistent.
        display: systemSans,
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', '"Liberation Mono"', 'monospace'],
      },
      ...legacyThemeExtend,
      animation: {
        'fade-in': 'fadeIn 0.25s ease-out forwards',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0', transform: 'translateY(4px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      },
    },
  },
  plugins: [legacyThemePlugin],
} satisfies Config;
