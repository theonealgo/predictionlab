import { twitterClient } from "./twitter";

export async function postToTwitter(text: string) {
  try {
    await twitterClient.v2.tweet(text);
    console.log("Tweet sent ✅");
  } catch (err) {
    console.error("Twitter error:", err);
  }
}