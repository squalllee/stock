import path from "node:path";
import { fileURLToPath } from "node:url";

import dotenv from "dotenv";

const serverDirectory = path.dirname(fileURLToPath(import.meta.url));
export const appDirectory = path.resolve(serverDirectory, "..");
const repositoryDirectory = path.resolve(appDirectory, "..");

dotenv.config({ path: path.join(repositoryDirectory, ".env") });
dotenv.config({ path: path.join(appDirectory, ".env"), override: true });

const DEFAULT_SUPABASE_URL = "https://vngtmamxhvcldecesfwh.supabase.co";

export function loadConfig(environment = process.env) {
  const supabaseKey =
    environment.SUPABASE_SECRET_KEY || environment.SUPABASE_SERVICE_ROLE_KEY;

  if (!supabaseKey) {
    throw new Error(
      "缺少 SUPABASE_SECRET_KEY（或 SUPABASE_SERVICE_ROLE_KEY），請設定在專案根目錄 .env。",
    );
  }

  return {
    supabaseUrl: environment.SUPABASE_URL || DEFAULT_SUPABASE_URL,
    supabaseKey,
    productionPort: Number(environment.PORT || 3000),
    isProduction: environment.NODE_ENV === "production",
  };
}
