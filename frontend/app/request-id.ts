export const REQUEST_ID_HEADER = "x-request-id";

const SAFE_REQUEST_ID = /^[A-Za-z0-9._-]{1,64}$/;

// 浏览器传进来的 id 会被后端写进日志，必须限制字符集，否则换行符可以伪造日志行。
export function resolveRequestId(request: Request): string {
  const incoming = request.headers.get(REQUEST_ID_HEADER)?.trim();
  if (incoming && SAFE_REQUEST_ID.test(incoming)) return incoming;

  return crypto.randomUUID().replaceAll("-", "").slice(0, 12);
}
