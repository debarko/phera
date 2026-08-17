# Channel setup (WhatsApp + email)

Use these adapters to test real inbound chats. Phera never talks to vendors from the dashboard — `rx-gateway` exposes public hooks, then proxies to Phera.

Local URLs after a tunnel (Cloudflare Tunnel or ngrok) pointing at rx-gateway `:3001`:

| Channel | Hook |
|---|---|
| WhatsApp (Gallabox) | `POST https://<public-host>/api/crm/hooks/gallabox/whatsapp` |
| Email (Google Group) | `POST https://<public-host>/api/crm/hooks/google_group/email` |

Copy `.env.example` into `.env` and fill credentials. Restart Phera after changing env.

## WhatsApp via Gallabox

Docs: [Webhooks](https://docs.gallabox.com/developer-resources/webhook), [API keys](https://docs.gallabox.com/developer-resources/api-key-and-secret), [Messages API](https://developers.gallabox.com/gallabox-api/messages).

**Preferred: add each number as a Channel in the Support Inbox settings UI** (`/crm/settings/channels`, WhatsApp section), or via `POST /v1/whatsapp-channels`. Each number's API key/secret/webhook secret are encrypted at rest in the database — nothing goes in `.env`. This supports any number of WhatsApp numbers side by side; each is polled/sent independently but flows into the same support queue.

1. In Gallabox: **Settings → API keys → Add new**. Copy the API Key and Secret.
2. Copy **Account ID** (account settings) and **Channel ID** (WhatsApp channel settings).
3. In the Channels settings UI, click **Add channel** under WhatsApp: enter a display name, the connected WhatsApp business number (E.164, e.g. `+9198…`), Account ID, Channel ID, API Key, API Secret, and a webhook secret you choose yourself (any random string — you'll paste the same value into Gallabox's webhook config in the next step). Test Connection checks the fields are present before you save.
4. **Settings → Webhook → Add new** in Gallabox:
   - Request URL: `https://<public-host>/api/crm/hooks/gallabox/whatsapp` — **same URL for every number**, no per-number reconfiguration needed on the Gallabox side.
   - Secret: the webhook secret you chose in step 3, for that number.
   - Events: `Message.received` (add `Message.WA.status.received` later if you want receipts)
5. Send a WhatsApp message to the business number. Phera creates/reuses a ticket on the messaging channel. Reply from Support Inbox; that calls Gallabox `POST /devapi/messages/whatsapp` as a session text (must be inside the 24-hour customer-care window). Templates stay inside Gallabox until we pass `template` on send.

HMAC: Gallabox signs the body with `x-gallabox-signature` (HMAC SHA-256 of the raw JSON). Phera checks the signature against every active WhatsApp channel's own webhook secret (and the legacy `GALLABOX_WEBHOOK_SECRET` env var as a fallback) — it accepts if any one matches. If literally none of them have a secret configured, the hook stays open (local-dev only — don't ship a deployment with unsigned webhooks).

**Legacy env fallback**: `GALLABOX_API_KEY` / `GALLABOX_API_SECRET` / `GALLABOX_ACCOUNT_ID` / `GALLABOX_CHANNEL_ID` / `GALLABOX_WEBHOOK_SECRET` / `GALLABOX_WHATSAPP_NUMBER` in `.env` still work for the single seeded `gallabox` ChannelAccount (the one with no `connector_id`) — kept for local dev and as a safety net, not the recommended path for new numbers.

## Email via a Google Group

The Group is a pipe, not the agent inbox. Humans should not work tickets in the Group UI.

Docs: [Gmail push notifications](https://developers.google.com/workspace/gmail/api/guides/push) if you later watch a mailbox; for a first test, **forward MIME into the hook**.

1. Create a Google Group (or reuse one). **Group settings → Email options**: turn **off** the Group footer so `In-Reply-To` / `References` survive.
2. Add a member that is **only** the CRM pipe, not a person. Options:
   - A dedicated Gmail that forwards to an inbound email provider (Cloudflare Email Routing, Mailgun route, SendGrid Inbound Parse) which HTTP POSTs JSON to the hook below.
   - A small [Gmail Apps Script](https://developers.google.com/apps-script/reference/gmail) time trigger that POSTs new threads to the hook.
3. Hook body (JSON):

```json
{
  "from": "patient@example.com",
  "to": "support@example.com",
  "subject": "Need records",
  "text": "Please send my reports.",
  "message_id": "<id@mail.gmail.com>",
  "in_reply_to": null,
  "references": []
}
```

Raw RFC822 is also accepted as `{"raw_rfc822": "From: ..."}`.

4. If `EMAIL_WEBHOOK_SECRET` is set, send it as `x-email-signature` or `x-webhook-secret` (HMAC SHA-256 hex of the raw body, or the secret itself for a simple shared header during first tests — HMAC is preferred).
5. Set SMTP so agent replies leave as the Group address:
   - Gmail/Google Workspace: app password, `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_FROM=<group@yourdomain>`
   - `SMTP_FROM` must be allowed to send as the Group (Group → Allow posting / send-as).

Threading order: `In-Reply-To` → subject `[#TCK-<ticket-uuid>]` → same contact + open ticket within 7 days.

## Tunnel for local

Gallabox and Google will not reach `localhost`. Example with Cloudflare:

```bash
cloudflared tunnel --url http://127.0.0.1:3001
```

Use the printed `https://*.trycloudflare.com` host in the webhook URL. rx-gateway already mounts `/api/crm/hooks` without staff auth.

## Seeded channel rows

Default seed creates ChannelAccounts with `adapter_type=gallabox` and `adapter_type=google_group`. Update `address` to the real WhatsApp number and Group email so inbound matching sticks to the right pipe.
