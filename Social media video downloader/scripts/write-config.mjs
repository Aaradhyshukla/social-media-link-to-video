import { mkdirSync, writeFileSync } from "node:fs";

const apiBaseUrl = (process.env.API_BASE_URL || "").trim().replace(/\/+$/, "");

if (apiBaseUrl && !/^https?:\/\//i.test(apiBaseUrl)) {
  throw new Error("API_BASE_URL must start with http:// or https://");
}

mkdirSync("public", { recursive: true });
writeFileSync(
  "public/config.js",
  `window.APP_CONFIG = ${JSON.stringify({ API_BASE_URL: apiBaseUrl }, null, 2)};\n`,
);

if (apiBaseUrl) {
  console.log(`Configured frontend API base URL: ${apiBaseUrl}`);
} else {
  console.log("API_BASE_URL is empty; frontend will call same-origin FastAPI endpoints.");
}
