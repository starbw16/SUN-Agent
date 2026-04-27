/**
 * CF Pages Function: POST /api/update-store
 * Updates config/<store_id>.json in the GitHub repo via the GitHub API.
 *
 * Required CF Pages environment variables:
 *   GITHUB_TOKEN  — Personal Access Token with Contents: Read & Write on this repo
 *   GITHUB_REPO   — e.g. "starbw16/SUN-Agent"
 *   ADMIN_SECRET  — a secret string the admin page must include in each request
 *
 * Protect this endpoint with Cloudflare Access (same policy as /admin/)
 * so only you can call it.
 */

const EDITABLE_FIELDS = [
  "store_name",
  "booking_url",
  "pages_url",
  "timezone",
  "slot_duration_minutes",
  "utilization_tier",
  "brief_frequency",
];

export async function onRequestPost(context) {
  const { request, env } = context;

  // CORS headers for same-origin requests from CF Pages
  const headers = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
  };

  // Verify secret token
  const secret = env.ADMIN_SECRET;
  if (secret) {
    const auth = request.headers.get("X-Admin-Secret") || "";
    if (auth !== secret) {
      return new Response(JSON.stringify({ error: "Unauthorized" }), { status: 401, headers });
    }
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return new Response(JSON.stringify({ error: "Invalid JSON" }), { status: 400, headers });
  }

  const { store_id, ...fields } = body;
  if (!store_id) {
    return new Response(JSON.stringify({ error: "store_id required" }), { status: 400, headers });
  }

  // Only allow known editable fields
  const sanitized = {};
  for (const key of EDITABLE_FIELDS) {
    if (key in fields) sanitized[key] = fields[key];
  }

  const token = env.GITHUB_TOKEN;
  const repo  = env.GITHUB_REPO || "starbw16/SUN-Agent";
  const path  = `config/${store_id}.json`;
  const apiBase = `https://api.github.com/repos/${repo}/contents/${path}`;
  const ghHeaders = {
    Authorization: `Bearer ${token}`,
    "User-Agent": "SUN-Agent-Admin",
    Accept: "application/vnd.github+json",
  };

  // Fetch existing file (need sha for update)
  let sha, currentConfig = { store_id };
  const getRes = await fetch(apiBase, { headers: ghHeaders });
  if (getRes.ok) {
    const data = await getRes.json();
    sha = data.sha;
    try {
      currentConfig = JSON.parse(atob(data.content.replace(/\n/g, "")));
    } catch {}
  } else if (getRes.status !== 404) {
    return new Response(JSON.stringify({ error: "GitHub read failed" }), { status: 502, headers });
  }

  // Merge and write back
  const updated = { ...currentConfig, ...sanitized, store_id };
  const content = btoa(unescape(encodeURIComponent(JSON.stringify(updated, null, 2))));

  const putBody = { message: `Admin: update config for ${store_id}`, content };
  if (sha) putBody.sha = sha;

  const putRes = await fetch(apiBase, {
    method: "PUT",
    headers: { ...ghHeaders, "Content-Type": "application/json" },
    body: JSON.stringify(putBody),
  });

  if (!putRes.ok) {
    const errText = await putRes.text();
    return new Response(JSON.stringify({ error: "GitHub write failed", detail: errText }), { status: 502, headers });
  }

  return new Response(JSON.stringify({ ok: true, updated: sanitized }), { status: 200, headers });
}

export async function onRequestOptions() {
  return new Response(null, {
    status: 204,
    headers: { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type, X-Admin-Secret" },
  });
}
