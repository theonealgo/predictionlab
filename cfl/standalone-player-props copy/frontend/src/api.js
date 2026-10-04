function resolveApiBase() {
  const fromEnv = import.meta.env.VITE_PROPS_API_BASE;
  if (fromEnv != null && String(fromEnv).trim() !== "") {
    return String(fromEnv).replace(/\/$/, "");
  }
  if (import.meta.env.DEV) {
    return "/api";
  }
  return "http://127.0.0.1:8101";
}

const API_BASE = resolveApiBase();
const API_TIMEOUT_MS = 20000;

async function fetchWithTimeout(url, options = {}, timeoutMs = API_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } catch (err) {
    if (err?.name === "AbortError") {
      throw new Error("Request timed out. Please click Run again.");
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

async function readJsonResponse(response) {
  const text = await response.text();
  let body = {};
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = { detail: text.slice(0, 240) };
    }
  }
  if (!response.ok) {
    const detail = body.detail ?? body.message;
    const msg =
      typeof detail === "string"
        ? detail
        : detail != null
          ? JSON.stringify(detail)
          : `${response.status} ${response.statusText}`;
    throw new Error(msg);
  }
  return body;
}

export function getPropsApiBase() {
  return API_BASE;
}

export async function fetchLeagues() {
  const r = await fetchWithTimeout(`${API_BASE}/leagues`);
  return readJsonResponse(r);
}

export async function fetchPlayers(league) {
  const r = await fetchWithTimeout(`${API_BASE}/players?league=${encodeURIComponent(league)}`);
  return readJsonResponse(r);
}

export async function fetchProps({ league, propType, side }) {
  const q = new URLSearchParams({ league });
  if (propType) q.set("prop_type", propType);
  if (side) q.set("side", side);
  const r = await fetchWithTimeout(`${API_BASE}/props?${q.toString()}`);
  return readJsonResponse(r);
}

export async function fetchResults(league) {
  const r = await fetchWithTimeout(`${API_BASE}/results?league=${encodeURIComponent(league)}`);
  return readJsonResponse(r);
}
