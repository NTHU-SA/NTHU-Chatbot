/**
 * Auth0 post-login Action: NTHUSA ID for the chat apps.
 *
 * Paste into Auth0 → Actions → Library → Create Action (trigger "Login / Post Login",
 * runtime Node 22), add the secrets below, deploy, then drag it into the Post Login flow.
 * Setup and testing steps: infra/README.md, section "Auth0".
 *
 * - The first time a user signs in to a chat app, mint their NTHUSA ID (`usr_` + ULID) and
 *   keep it in app_metadata.usr. It never changes afterwards; Auth0's own user_id does change
 *   when accounts are linked, so the backend keys on this ID instead of `sub`.
 * - Put it in the access token as https://nthusa.tw/usr. The backend rejects tokens without it.
 * - When the user has a LINE identity from one of our LINE connections (they reuse the bot's
 *   LINE Login channels, same Provider), also put its userId in https://nthusa.tw/line_user_id
 *   so the bot's webhook reaches the same person.
 *
 * Only runs for the chat apps (CHAT_CLIENT_IDS): the tenant is shared with other NTHUSA
 * projects, and the account service (account.nthusa.tw) will take this over later.
 *
 * Secrets (not sensitive, but kept out of the code so staging and prod share one Action):
 *   CHAT_CLIENT_IDS   comma-separated client IDs of "Chat (staging)" and "Chat (prod)"
 *   LINE_CONNECTIONS  comma-separated names of the Auth0 LINE connections for those apps
 */

const crypto = require("crypto");

const NAMESPACE = "https://nthusa.tw/";
const NTHUSA_ID = /^usr_[0-9A-HJKMNP-TV-Z]{26}$/;
const CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";

function list(value) {
  return (value || "").split(",").map((item) => item.trim()).filter(Boolean);
}

// ULID: 48-bit millisecond time + 80 random bits, Crockford base32 (sortable by creation time).
function newNthusaId() {
  let time = Date.now();
  let head = "";
  for (let i = 0; i < 10; i += 1) {
    head = CROCKFORD[time % 32] + head;
    time = Math.floor(time / 32);
  }
  // 256 is a multiple of 32, so byte % 32 is uniform.
  const tail = Array.from(crypto.randomBytes(16), (byte) => CROCKFORD[byte % 32]).join("");
  return `usr_${head}${tail}`;
}

exports.onExecutePostLogin = async (event, api) => {
  if (!list(event.secrets.CHAT_CLIENT_IDS).includes(event.client.client_id)) return;

  let nthusaId = event.user.app_metadata && event.user.app_metadata.usr;
  if (typeof nthusaId !== "string" || !NTHUSA_ID.test(nthusaId)) {
    nthusaId = newNthusaId();
    api.user.setAppMetadata("usr", nthusaId);
  }
  api.accessToken.setCustomClaim(`${NAMESPACE}usr`, nthusaId);

  const lineConnections = list(event.secrets.LINE_CONNECTIONS);
  const line = (event.user.identities || []).find(
    (identity) => identity.provider === "line" && lineConnections.includes(identity.connection),
  );
  if (line && typeof line.user_id === "string") {
    api.accessToken.setCustomClaim(`${NAMESPACE}line_user_id`, line.user_id);
  }
};
