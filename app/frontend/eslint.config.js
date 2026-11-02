import js from '@eslint/js';
import { defineConfig, globalIgnores } from 'eslint/config';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import globals from 'globals';
import tseslint from 'typescript-eslint';

export default defineConfig([
  globalIgnores([
    'dist',
    'playwright-report',
    'test-results',
    'public',
    // Legacy vanilla-JS screens: kept as-is until they are rebuilt in React (Phase 3).
    'src/components/**/*.js',
    'src/utils/**/*.js',
    'src/legacy/*.js',
  ]),

  // All TypeScript: strict, type-checked rules.
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, tseslint.configs.strictTypeChecked, tseslint.configs.stylisticTypeChecked],
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      '@typescript-eslint/restrict-template-expressions': ['error', { allowNumber: true }],
      // `text || fallback` intentionally treats '' as missing.
      '@typescript-eslint/prefer-nullish-coalescing': ['error', { ignorePrimitives: { string: true } }],
    },
  },

  // Browser app code (React).
  {
    files: ['src/**/*.{ts,tsx}'],
    extends: [reactHooks.configs.flat['recommended-latest'], reactRefresh.configs.vite],
    languageOptions: { globals: globals.browser },
  },

  // Node-side TypeScript: tool configs and Playwright tests.
  {
    files: ['*.config.ts', 'tailwind.legacy-theme.ts', 'tests/**/*.ts'],
    languageOptions: { globals: globals.node },
  },

  // Plain JS tool configs.
  {
    files: ['*.config.js'],
    extends: [js.configs.recommended],
    languageOptions: { globals: globals.node },
  },
]);
