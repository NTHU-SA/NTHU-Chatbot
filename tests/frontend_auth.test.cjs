const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const test = require("node:test");
const { runInNewContext } = require("node:vm");

// auth.js with the Auth0 SDK import swapped for a fake one.
const source = readFileSync(join(__dirname, "..", "frontend", "js", "auth.js"), "utf8")
  .replaceAll("export ", "")
  .replace("await import(AUTH0_SDK)", "await importSdk()");

const CONFIG = {
  liffId: "liff-id",
  auth0: { domain: "auth.example", clientId: "client", audience: "api", lineConnection: "line" },
};

function storage() {
  const items = new Map();
  return {
    getItem: (key) => (items.has(key) ? items.get(key) : null),
    setItem: (key, value) => items.set(key, String(value)),
    removeItem: (key) => items.delete(key),
  };
}

// silent: what getTokenSilently does ("ok" or an Auth0 error code).
// callback: what handleRedirectCallback does ("ok" or an Auth0 error code).
function setup({ url = "https://chat.example/", silent = "ok", callback = "ok", inClient = false } = {}) {
  const calls = { logins: [], liffInit: 0 };
  const location = new URL(url);
  class Auth0Client {
    async handleRedirectCallback() {
      if (callback !== "ok") throw Object.assign(new Error("Invalid state"), { error: callback });
      return { appState: { returnTo: "/?s=abc" } };
    }
    async getTokenSilently() {
      if (silent !== "ok") throw Object.assign(new Error(silent), { error: silent });
      return "token";
    }
    async loginWithRedirect(options) {
      calls.logins.push(options);
    }
  }
  const context = {
    URL,
    URLSearchParams,
    Date,
    location,
    history: { replaceState: (_state, _title, path) => { location.href = new URL(path, location.origin).href; } },
    sessionStorage: storage(),
    localStorage: storage(),
    liff: { isInClient: () => inClient, init: async () => { calls.liffInit += 1; } },
    importSdk: async () => ({ Auth0Client }),
  };
  runInNewContext(source, context);
  return { context, calls, location };
}

test("a first visit without a session goes to the login page", async () => {
  const { context, calls } = setup({ silent: "login_required" });
  assert.equal(await context.signIn(CONFIG), false);
  assert.equal(calls.logins.length, 1);
});

test("a signed-in visit needs no redirect", async () => {
  const { context, calls } = setup();
  assert.equal(await context.signIn(CONFIG), true);
  assert.equal(calls.logins.length, 0);
});

test("a finished callback cleans the address bar and restores the page", async () => {
  const { context, calls, location } = setup({ url: "https://chat.example/?code=c&state=s" });
  assert.equal(await context.signIn(CONFIG), true);
  assert.equal(location.pathname + location.search, "/?s=abc");
  assert.equal(calls.logins.length, 0);
});

test("a callback that matches no pending login is reported, not retried", async () => {
  const { context, calls, location } = setup({
    url: "https://chat.example/?code=c&state=s", callback: "missing_transaction", inClient: true,
  });
  await assert.rejects(context.signIn(CONFIG), /Invalid state/);
  assert.equal(location.search, "");
  assert.equal(calls.logins.length, 0);
});

test("other callback errors are not swallowed", async () => {
  const { context } = setup({ url: "https://chat.example/?error=access_denied&state=s", callback: "access_denied" });
  await assert.rejects(context.signIn(CONFIG), /Invalid state/);
});

test("inside LINE the login goes straight to the LINE connection", async () => {
  const { context, calls } = setup({ silent: "login_required", inClient: true });
  assert.equal(await context.signIn(CONFIG), false);
  assert.equal(calls.logins[0].authorizationParams.connection, "line");
});

test("LIFF is never initialised, so it never runs a second LINE login", async () => {
  for (const url of ["https://chat.example/", "https://chat.example/?code=c&state=s"]) {
    const { context, calls } = setup({ url, inClient: true });
    assert.equal(await context.signIn(CONFIG), true);
    assert.equal(calls.liffInit, 0);
  }
});

test("LIFF's tokens in the fragment are dropped inside LINE", async () => {
  const { context, location } = setup({ url: "https://chat.example/?s=abc#access_token=t", inClient: true });
  assert.equal(await context.signIn(CONFIG), true);
  assert.equal(location.href, "https://chat.example/?s=abc");
});

test("a liff.line.me link restores its path and query", async () => {
  const { context, location } = setup({
    url: "https://chat.example/?liff.state=%2F%3Fq%3Dbus", inClient: true,
  });
  assert.equal(await context.signIn(CONFIG), true);
  assert.equal(location.pathname + location.search, "/?q=bus");
});
