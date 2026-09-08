import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
    setupFiles: './src/setupTests.ts',
    fileParallelism: false,
    testTimeout: 15_000,
    coverage: {
      include: [
        'src/App.tsx',
        'src/ProjectName.tsx',
        'src/Markdown.tsx',
        'src/api.ts',
        'src/components.tsx',
        'src/useObservability.ts',
        'src/useProjectData.ts',
        'src/useTeam.ts',
        'src/WorkView.tsx',
        'src/WorkDropdown.tsx',
        'src/SprintWork.tsx',
        'src/WorkPicker.tsx',
      ],
      reporter: ['text'],
      thresholds: { lines: 85, functions: 85, statements: 85, branches: 80 },
    },
  },
});
