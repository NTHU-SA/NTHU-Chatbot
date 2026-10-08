// Sign-in. In an ordinary browser the page uses Auth0 Universal Login (Authorization
// Code + PKCE) and sends an Auth0 access token for this environment's API (provider
// "auth0"); the token carries the NTHUSA ID. Inside the LINE app it does the same through
// Auth0's LINE connection (config auth0.lineConnection), which reuses the bot's LINE Login
// channel, so LINE itself signs the user in. Without that connection the LINE app keeps
// using the LIFF id_token (provider "line"), and without an auth0 block every browser
// falls back to LIFF's LINE web login. The backend verifies every token.

const AUTH0_SDK = "../vendor/auth0-spa-js.production.esm.js";
const AUTH0_WORKER = "./vendor/auth0-spa-js.worker.production.js";
// Errors that just mean "no usable session": send the user to the login page.
const LOGIN_NEEDED = new Set([
  "login_required", "consent_required", "interaction_required", "missing_refresh_token",
]);
// A 401 right after logging in means something is misconfigured; stop instead of looping.
const RELOGIN_KEY = "auth.relogin";
const RELOGIN_COOLDOWN_MS = 60_000;

let provider = null; // "line" | "auth0"
let auth0 = null;
let connection = null; // Auth0 connection to go straight to (LINE inside the LINE app)

export const authProvider = () => provider;

function callbackUrl() {
  return `${location.origin}/`;
}

// Only same-origin paths may be restored after login (no open redirect).
function samePath(value) {
  try {
    const url = new URL(value || "/", location.origin);
    return url.origin === location.origin ? url.pathname + url.search : "/";
  } catch (_) {
    return "/";
  }
}

// A liff.line.me link opened outside LINE lands here as ?liff.state=<path?query>;
// LIFF would restore it in liff.init, but the Auth0 path skips LIFF entirely.
function restoreLiffState() {
  const state = new URLSearchParams(location.search).get("liff.state");
  if (state) history.replaceState(null, "", samePath(state));
}

function loginWithLine() {
  liff.login({ redirectUri: location.href });
}

async function loginWithAuth0() {
  await auth0.loginWithRedirect({
    appState: { returnTo: location.pathname + location.search },
    ...(connection && { authorizationParams: { connection } }),
  });
}

async function startLine(cfg) {
  provider = "line";
  await liff.init({ liffId: cfg.liffId });
  if (!liff.isLoggedIn()) {
    loginWithLine();
    return false;
  }
  if (!liff.getIDToken()) {
    // Scope openid missing or stale session — force a fresh login.
    liff.logout();
    loginWithLine();
    return false;
  }
  return true;
}

async function startAuth0(cfg) {
  provider = "auth0";
  const { Auth0Client } = await import(AUTH0_SDK);
  auth0 = new Auth0Client({
    domain: cfg.auth0.domain,
    clientId: cfg.auth0.clientId,
    authorizationParams: {
      audience: cfg.auth0.audience,
      redirect_uri: callbackUrl(),
      scope: "openid profile offline_access",
    },
    // Refresh tokens stay inside a same-origin worker (memory only, never localStorage);
    // after a reload the SDK falls back to a hidden iframe on the Auth0 domain.
    useRefreshTokens: true,
    useRefreshTokensFallback: true,
    cacheLocation: "memory",
    workerUrl: AUTH0_WORKER,
  });

  const params = new URLSearchParams(location.search);
  if (params.has("state") && (params.has("code") || params.has("error"))) {
    let returnTo = "/";
    try {
      const result = await auth0.handleRedirectCallback();
      returnTo = result.appState && result.appState.returnTo;
    } finally {
      // Never leave the code/state (or an error) in the address bar or history.
      history.replaceState(null, "", samePath(returnTo));
    }
  } else {
    restoreLiffState();
  }

  try {
    await auth0.getTokenSilently();
    return true;
  } catch (err) {
    if (!LOGIN_NEEDED.has(err.error)) throw err;
    await loginWithAuth0();
    return false;
  }
}

// Returns false when the page is navigating away to a login page.
export async function signIn(cfg) {
  // isInClient works before liff.init.
  if (!liff.isInClient()) return cfg.auth0 ? startAuth0(cfg) : startLine(cfg);
  if (!(cfg.auth0 && cfg.auth0.lineConnection)) return startLine(cfg);
  connection = cfg.auth0.lineConnection;
  // Finish the Auth0 callback first: liff.init must not see Auth0's code/state in the URL.
  if (!(await startAuth0(cfg))) return false;
  // Still needed for the LIFF environment info and LINE features; no LIFF login here.
  await liff.init({ liffId: cfg.liffId });
  return true;
}

export async function accessToken() {
  if (provider !== "auth0") return liff.getIDToken();
  try {
    return await auth0.getTokenSilently();
  } catch (err) {
    // A long-lived tab whose session can no longer be renewed fails here, before any
    // request is sent: recover the same way as a 401 (guarded against redirect loops).
    if (LOGIN_NEEDED.has(err.error)) return relogin();
    throw err;
  }
}

// Called on 401: the token expired or was revoked. Throws "re-login" when redirecting.
export async function relogin() {
  if (provider !== "auth0") {
    liff.logout();
    loginWithLine();
    throw new Error("re-login");
  }
  let last = 0;
  try { last = Number(sessionStorage.getItem(RELOGIN_KEY)) || 0; } catch (_) { /* storage off */ }
  if (Date.now() - last < RELOGIN_COOLDOWN_MS) throw new Error("登入驗證失敗，請稍後再試。");
  try { sessionStorage.setItem(RELOGIN_KEY, String(Date.now())); } catch (_) { /* storage off */ }
  await loginWithAuth0();
  throw new Error("re-login");
}

export async function logout() {
  if (provider !== "auth0") return;
  await auth0.logout({ logoutParams: { returnTo: callbackUrl() } });
}
