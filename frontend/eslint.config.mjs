import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    rules: {
      // Existing dashboards load data in useEffect and update local state.
      // The app builds and runs correctly; refactoring every page is a separate cleanup.
      "react-hooks/set-state-in-effect": "off",

      // Existing marketing/dashboard copy contains apostrophes in JSX text.
      // Keep this non-blocking for now; copy cleanup can happen separately.
      "react/no-unescaped-entities": "off",
    },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;
