import path from "node:path";

import { createClient } from "@supabase/supabase-js";
import WebSocket from "ws";

import { createApp } from "./app.js";
import { appDirectory, loadConfig } from "./config.js";
import { createStockDataService } from "./data-service.js";

try {
  const config = loadConfig();
  const supabase = createClient(config.supabaseUrl, config.supabaseKey, {
    auth: {
      autoRefreshToken: false,
      detectSessionInUrl: false,
      persistSession: false,
    },
    realtime: { transport: WebSocket },
  });
  const app = createApp({
    dataService: createStockDataService(supabase),
    staticDirectory: path.join(appDirectory, "dist"),
  });
  const port = config.productionPort;
  app.listen(port, "0.0.0.0", () => {
    console.log(
      `TDCC mobile web running at http://localhost:${port}`,
    );
  });
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
