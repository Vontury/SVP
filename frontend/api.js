const supabaseClient = supabase.createClient(
  SVP_CONFIG.supabaseUrl,
  SVP_CONFIG.supabasePublishableKey
);

async function apiRequest(method, path, body) {
  const { data } = await supabaseClient.auth.getSession();
  const headers = {};
  if (data.session) {
    headers["Authorization"] = `Bearer ${data.session.access_token}`;
  }
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let payload = null;
  if (response.status !== 204) {
    try {
      payload = await response.json();
    } catch (error) {
      payload = null;
    }
  }
  return { status: response.status, ok: response.ok, data: payload };
}