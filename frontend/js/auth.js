// Sign-in. Every sign-in goes through Auth0 (Authorization Code + PKCE) and sends an
// Auth0 access token for this environment's API (provider "auth0"); the token carries the
// NTHUSA ID, which is the user's id in the backend. In an ordinary browser the user picks
// a login method on Universal Login; inside the LINE app the page goes straight to Auth0's
// LINE connection (config auth0.lineConnection), whose channel is in the bot's LINE
// Provider, so LINE itself signs the user in. The backend verifies every token.
//
// The LIFF SDK is never initialised: inside LINE, liff.init() runs its own LINE login once
// the page has been through Auth0, and that login comes back as ?code=…&state=…, which the
// Auth0 callback handling took for its own ("Invalid state"). One login is enough.

const AUTH0_SDK = "../vendor/auth0-spa-js.production.esm.js";
const AUTH0_WORKER = "./vendor/auth0-spa-js.worker.production.js";
// Errors that just mean "no usable session": send the user to the login page.
const LOGIN_NEEDED = new Set([
  "login_required", "consent_required", "interaction_required", "missing_refresh_token",
]);
// A 401 right after logging in means something is misconfigured; stop instead of looping.
const RELOGIN_KEY = "auth.relogin";
const RELOGIN_COOLDOWN_MS = 60_000;

let auth0 = null;
let connection = null; // Auth0 connection to go straight to (LINE inside the LINE app)

export const authProvider = () => "auth0";

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

// A liff.line.me link lands here as ?liff.state=<path?query>; liff.init would restore it,
// but the page never initialises LIFF.
function restoreLiffState() {
  const state = new URLSearchParams(location.search).get("liff.state");
  if (state) history.replaceState(null, "", samePath(state));
}

async function loginWithAuth0() {
  await auth0.loginWithRedirect({
    appState: { returnTo: location.pathname + location.search },
    ...(connection && { authorizationParams: { connection } }),
  });
}

function isCallback() {
  const params = new URLSearchParams(location.search);
  return params.has("state") && (params.has("code") || params.has("error"));
}

// Finishes the login this page came back from.
async function finishRedirect() {
  let returnTo = "/";
  try {
    const result = await auth0.handleRedirectCallback();
    returnTo = result.appState && result.appState.returnTo;
  } finally {
    // Never leave the code/state (or an error) in the address bar or history.
    history.replaceState(null, "", samePath(returnTo));
  }
}

async function startAuth0(cfg) {
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

  if (isCallback()) await finishRedirect();
  else restoreLiffState();

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
  if (!cfg.auth0) throw new Error("登入服務尚未設定，請稍後再試。");
  // liff.isInClient() works without liff.init().
  if (liff.isInClient()) {
    connection = cfg.auth0.lineConnection || null;
    // LINE opens the page with LIFF's tokens in the fragment; liff.init would consume them.
    // Unused here, so keep them out of the address bar and history.
    if (location.hash) history.replaceState(null, "", location.pathname + location.search);
  }
  return startAuth0(cfg);
}

export async function accessToken() {
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
  let last = 0;
  try { last = Number(sessionStorage.getItem(RELOGIN_KEY)) || 0; } catch (_) { /* storage off */ }
  if (Date.now() - last < RELOGIN_COOLDOWN_MS) throw new Error("登入驗證失敗，請稍後再試。");
  try { sessionStorage.setItem(RELOGIN_KEY, String(Date.now())); } catch (_) { /* storage off */ }
  await loginWithAuth0();
  throw new Error("re-login");
}

// Ends the Auth0 session too, so the next visit starts from the login page.
export async function logout() {
  await auth0.logout({ logoutParams: { returnTo: callbackUrl() } });
}

// After DELETE /api/me: sign out and, once back, tell the user the data is gone
// (the flag survives the logout redirect, which stays in the same tab).
const DELETED_KEY = "auth.deleted";

export async function logoutAfterDeletion() {
  try { sessionStorage.setItem(DELETED_KEY, "1"); } catch (_) { /* storage off */ }
  await logout();
}

export function takeDeletedFlag() {
  try {
    if (!sessionStorage.getItem(DELETED_KEY)) return false;
    sessionStorage.removeItem(DELETED_KEY);
    return true;
  } catch (_) {
    // Storage off: no flag could have been saved either, so just sign in as usual.
    return false;
  }
}
