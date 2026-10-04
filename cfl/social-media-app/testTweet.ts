import { postToTwitter } from "./lib/postToTwitter";

async function run() {
  await postToTwitter("🚨 Test tweet from my model");
}

run();