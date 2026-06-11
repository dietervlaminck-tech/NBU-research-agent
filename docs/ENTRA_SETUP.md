# Microsoft Entra ID (Azure AD) SSO — Setup

Researcher login uses Entra ID exclusively. Respondent links (interview chat,
survey runner) stay public — respondents never need an institutional account.

When the `AZURE_*` variables are unset the app runs in **dev mode**: auth is
disabled and every request acts as a synthetic local user. Never deploy to
production without these variables set.

## 1. App registration (Azure portal → Entra ID → App registrations)

1. **New registration**
   - Name: `NBU Research Agent`
   - Supported account types: **Accounts in this organizational directory only**
     (single tenant — the callback additionally validates the `tid` claim).
2. **Redirect URI** (type *Web*):
   - Production: `https://<your-app>.azurewebsites.net/auth/callback`
   - Local testing of auth: `http://localhost:5050/auth/callback`
3. **Certificates & secrets** → *New client secret*. Copy the **value**
   immediately (shown once). Set a reminder before its expiry date.
4. Note the **Application (client) ID** and **Directory (tenant) ID** from the
   Overview page.

## 2. App roles (optional but recommended)

App registration → **App roles** → *Create app role*, e.g.:

| Display name | Value | Allowed member types |
|---|---|---|
| Researcher | `researcher` | Users/Groups |
| Admin | `admin` | Users/Groups |

Then assign users/groups under **Enterprise applications → NBU Research Agent
→ Users and groups**. Role values arrive in the `roles` claim and are stored in
the session; users without an assigned role default to `researcher`.

## 3. Token claims

The default ID token already carries everything the app uses: `oid` (user id),
`name`, `preferred_username` (email), `tid` (tenant — validated server-side
against `AZURE_TENANT_ID`), and `roles`. No *Expose an API* step and no Graph
permissions are required; the app never calls Graph.

## 4. Environment variables

```
AZURE_CLIENT_ID=<Application (client) ID>
AZURE_CLIENT_SECRET=<client secret value>
AZURE_TENANT_ID=<Directory (tenant) ID>
AZURE_REDIRECT_URI=https://<your-app>.azurewebsites.net/auth/callback
```

Set them in Azure Web App **app settings** (or `.env` locally). `deploy.sh`
users: add them to `.deploy.env` and extend the `appsettings set` call, or set
them once in the portal.

## 5. What the app enforces

- A global `before_request` guard: every endpoint requires a session user
  except the public allowlist (`auth.PUBLIC_ENDPOINTS`) — respondent routes and
  static files. API-style requests get `401` instead of a redirect.
- The callback validates the `tid` claim — tokens from other tenants are
  rejected even if Azure lets them authenticate.
- On first login the user is upserted into the `users` table and any pending
  project invites for their email are converted to memberships.
- Logout clears the Flask session and ends the Microsoft session
  (`/oauth2/v2.0/logout`).
