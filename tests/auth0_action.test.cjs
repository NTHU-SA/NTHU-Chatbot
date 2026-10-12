const assert = require("node:assert/strict");
const { join } = require("node:path");
const test = require("node:test");

const action = require(join(__dirname, "..", "infra", "auth0", "post-login-nthusa-id.js"));

const NAMESPACE = "https://nthusa.tw/";
const NTHUSA_ID = /^usr_[0-9A-HJKMNP-TV-Z]{26}$/;
const LINE_USER_ID = "U0123456789abcdef0123456789abcdef";
const secrets = { CHAT_CLIENT_IDS: "chatStaging, chatProd", LINE_CONNECTIONS: "line-chat" };

async function login({ clientId = "chatStaging", appMetadata = {}, identities = [] } = {}) {
  const claims = {};
  const metadata = {};
  const api = {
    user: { setAppMetadata: (key, value) => { metadata[key] = value; } },
    accessToken: { setCustomClaim: (key, value) => { claims[key] = value; } },
  };
  const event = {
    secrets,
    client: { client_id: clientId },
    user: { app_metadata: appMetadata, identities },
  };
  await action.onExecutePostLogin(event, api);
  return { claims, metadata };
}

test("first chat login mints an NTHUSA ID and puts it in the token", async () => {
  const { claims, metadata } = await login();
  assert.match(metadata.usr, NTHUSA_ID);
  assert.equal(claims[`${NAMESPACE}usr`], metadata.usr);
});

test("an existing NTHUSA ID is kept", async () => {
  const usr = "usr_01K7AJ3Z8Q4N5V6W7X8Y9ZABCD";
  const { claims, metadata } = await login({ clientId: "chatProd", appMetadata: { usr } });
  assert.deepEqual(metadata, {});
  assert.equal(claims[`${NAMESPACE}usr`], usr);
});

test("a malformed stored ID is replaced", async () => {
  const { metadata } = await login({ appMetadata: { usr: "usr_" + "0".repeat(32) } });
  assert.match(metadata.usr, NTHUSA_ID);
});

test("IDs are unique and sort by creation time", async () => {
  const ids = [];
  for (let i = 0; i < 50; i += 1) ids.push((await login()).metadata.usr);
  assert.equal(new Set(ids).size, ids.length);
  assert.ok(ids.every((id) => id.slice(4, 14) <= ids.at(-1).slice(4, 14)));
});

test("only our LINE connections add the LINE claim", async () => {
  const ours = await login({
    identities: [
      { provider: "google-oauth2", connection: "google-oauth2", user_id: "1" },
      { provider: "line", connection: "line-chat", user_id: LINE_USER_ID },
    ],
  });
  assert.equal(ours.claims[`${NAMESPACE}line_user_id`], LINE_USER_ID);
  const theirs = await login({
    identities: [{ provider: "line", connection: "another-line", user_id: LINE_USER_ID }],
  });
  assert.equal(theirs.claims[`${NAMESPACE}line_user_id`], undefined);
});

test("other applications in the shared tenant are left alone", async () => {
  const { claims, metadata } = await login({ clientId: "someOtherApp" });
  assert.deepEqual(claims, {});
  assert.deepEqual(metadata, {});
});
