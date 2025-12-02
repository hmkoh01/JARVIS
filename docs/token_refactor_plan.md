# Authentication & Token Lifecycle Refactor Plan

## 1. Current State Diagnosis

### JWT issuance & expiration
- Issued inside `backend/api/auth_routes.py::create_jwt_token` with payload `{user_id, exp=now+24h, iat}`.
- No refresh endpoint; once expired every request/WebSocket simply receives 401/403.

### Storage strategy
- `frontend/login_view.py` stores the freshly-issued `jarvis_token` in keyring, falls back to `~/.jarvis_token.json`.
- `start.py` still launches the login window on every run; stored token is only re-used by helper calls such as `perform_initial_data_collection`.
- Token read helpers are duplicated (`login_view` vs `start.py`), and failures don't cascade cleanly.

### Usage flow
- HTTP requests use whichever token `start.py` passes via `Authorization: Bearer ...`.
- WebSocket connection embeds the same token in `ws://localhost:8000/ws/<token>`.
- When the token expires, WebSocket receives 403 repeatedly; frontend keeps retrying forever with the same stale token.

### Recent hotfix impact
- `user_info["jarvis_token"]` is now passed directly to `start.py`, bypassing the storage layer. Stored tokens can become out of sync with the active one, and refresh strategies cannot be implemented cleanly.

## 2. Target Architecture (After)

1. **TokenStore abstraction (frontend/token_store.py)**
   - `save_token`, `load_token`, `delete_token` unify keyring/file handling.
   - `is_expiring(token, slack=5m)` inspects exp claim without requiring the secret.
   - All CLI/frontend components only touch tokens through this module.

2. **Effective token resolver**
   - `start.py` (and future shared util) exposes `get_effective_token()`:
     1. Try loading stored token.
     2. If missing or expiring – call `/auth/token/refresh` (new endpoint). On success, save & return.
     3. If refresh fails, delete stored token and run the login UI once; successful login stores token via TokenStore.
   - `start.py` only launches login UI when needed.

3. **Refresh endpoint**
   - `POST /auth/token/refresh` uses the user’s persisted Google `refresh_token` to mint a new Google access token and then a new JARVIS JWT.
   - On failure (no refresh_token / Google error) respond 401 so the client can re-login.

4. **WebSocket hardening**
   - Decode JWT with detailed exception handling (ExpiredSignatureError → code 4401, JWTClaimsError → 4402, other JWTError → 4400).
   - Log reason-specific warnings.
   - Client side: stop infinite reconnect loops; if we see repeated 4401/403, wipe token & prompt re-login.

5. **Remove hotfix**
   - `login_view` no longer relies on returning `jarvis_token` for downstream consumers; it simply stores the token through TokenStore.
   - `start.py` (and other modules) always fetch tokens via `get_effective_token()`.

## 3. Implementation Checklist

1. Create `frontend/token_store.py` with save/load/delete/is_expiring helpers.
2. Refactor `frontend/login_view.py` to depend on TokenStore.
3. Refactor `start.py`:
   - Implement `get_effective_token()` with refresh/login fallback.
   - Ensure all API calls/WebSocket setups call this function.
4. Backend changes:
   - Add `/auth/token/refresh` route that leverages stored Google refresh tokens.
   - Augment `database/sqlite.py` helpers if needed for refresh token updates.
   - Improve WebSocket JWT handling in `backend/main.py`.
5. Frontend WebSocket client (tk app) update:
   - Use TokenStore + get_effective_token() for socket auth.
   - Handle repeated auth failures gracefully and prompt re-login.
6. Documentation / migration notes:
   - Mention that existing keyring/json token files remain compatible; expired or malformed tokens will trigger auto refresh/login.
   - Describe the new refresh flow and failure UX.
