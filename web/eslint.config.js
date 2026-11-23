// ESLint 9 flat config（2026-11-23 接入）
// 定位：AI 开发的机械护栏——复杂度硬上限 + 常见缺陷模式清零。
// CI 不安装 web/node_modules，本 lint 仅本地执行（npm run lint）。
import js from '@eslint/js';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import globals from 'globals';

export default [
  { ignores: ['dist', 'landing', 'node_modules', 'scripts'] },
  {
    // 与 Python 侧 RUF100 对齐：重构后不再需要的 disable 注释必须清掉
    linterOptions: { reportUnusedDisableDirectives: 'error' },
    files: ['**/*.{js,jsx}'],
    languageOptions: {
      ecmaVersion: 2023,
      globals: { ...globals.browser },
      parserOptions: { ecmaVersion: 2023, sourceType: 'module', ecmaFeatures: { jsx: true } },
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      // ---- 基础（@eslint/js recommended 全量）----
      ...js.configs.recommended.rules,
      'no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
      eqeqeq: ['error', 'smart'],
      'prefer-const': 'error',
      'no-var': 'error',

      // ---- 复杂度硬上限（棘轮基线：超标即拒绝，阈值只许收紧）----
      complexity: ['error', 12],
      'max-depth': ['error', 3],
      'max-statements': ['error', 60],
      'max-lines-per-function': ['error', { max: 200, skipBlankLines: true, skipComments: true }],

      // ---- React ----
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
      'react-refresh/only-export-components': 'warn',
    },
  },
];
