import nextCoreWebVitals from "eslint-config-next/core-web-vitals"
import nextTypeScript from "eslint-config-next/typescript"

const eslintConfig = [
  // Ignore patterns (replacing .eslintignore)
  {
    ignores: ["node_modules/**", "build/**", "delete/**", "tmp/**", ".next/**"],
  },
  ...nextCoreWebVitals,
  ...nextTypeScript,
  {
    rules: {
      // Treat these as warnings instead of errors to prevent build failures
      "@typescript-eslint/no-explicit-any": "warn",
      "@typescript-eslint/no-unused-vars": "warn",
    },
  },
]

export default eslintConfig
