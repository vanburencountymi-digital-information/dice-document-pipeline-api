// Forwards every request to the Cloud Run app. Cloud Run picks the service by the `Host`
// header, so the request has to go out with the `run.app` host. The public hostname travels in
// `X-Forwarded-Host` and the secret in `X-Origin-Secret`, which the app checks (ADR 0036, 0041).
// The header name must match `HEADER_META_KEY` in `common/middleware.py`.
export default {
  /**
   * @param {Request} request The incoming request on the public hostname.
   * @param {{ORIGIN_HOST: string, ORIGIN_LOCK_SECRET: string}} env `ORIGIN_HOST` is set by
   *   OpenTofu (worker.tf). `ORIGIN_LOCK_SECRET` is set by hand; if it is missing the app
   *   answers every request with a 403.
   * @returns {Promise<Response>} The app's response, passed back unchanged.
   */
  async fetch(request, env) {
    const incoming = new URL(request.url);
    const target = new URL(request.url);
    target.protocol = "https:";
    target.hostname = env.ORIGIN_HOST;
    target.port = "";

    const headers = new Headers(request.headers);
    headers.delete("Host");
    headers.set("X-Origin-Secret", env.ORIGIN_LOCK_SECRET);
    headers.set("X-Forwarded-Host", incoming.hostname);

    return fetch(target, {
      method: request.method,
      headers,
      body: request.body,
      redirect: "manual",
    });
  },
};
