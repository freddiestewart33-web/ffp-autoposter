# Final Frame Prints Auto-Poster — Your Setup Steps

Everything code-side is built. These are the only things left that must be done
by you (they need your logins).

## 1. Meta (Instagram + Facebook) — DO THIS FIRST, unlocks everything

1. Go to **developers.facebook.com** → My Apps → **Create App** → type **Business** → name it "Final Frame Prints Automation" → link to your Business Portfolio.
2. In the app dashboard, **Add Product** → Instagram Graph API.
3. Go to **business.facebook.com → Settings → Users → System Users** → **Add** → name "Poster Bot", role **Admin** → **Assign Assets** → tick your Facebook Page AND your Instagram account (full control).
4. On the System User → **Generate New Token** → pick the app from step 1 → tick permissions:
   `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`, `instagram_basic`, `instagram_content_publish`, `business_management`
   → Expiration: **Never** → Generate → copy it.
5. Collect and send to Claude:
   - App ID + App Secret (app dashboard → Settings → Basic)
   - The token from step 4
   - Page ID (Business Settings → Accounts → Pages → click your page)
   - Instagram Business Account ID (Business Settings → Accounts → Instagram accounts)

## 2. Gemini API key (for Nano Banana image generation)

1. Go to **aistudio.google.com** → sign in with Google → **Get API key** → Create API key.
2. Send it to Claude. Free tier to start; paid images cost pennies.

## 3. Pinterest — file now, approval takes weeks

1. Go to **developers.pinterest.com** → Apps → **Create app** (log in with your Pinterest Business account).
2. Fill the Trial Access request. In the description say: *"Automating pin scheduling for my own single business account (Final Frame Prints, poster shop). No third-party users."*
3. Request scopes: `pins:write`, `boards:read`, `boards:write`.
4. Tell Claude when approval lands — then we do a one-time OAuth to get the token.

## 4. TikTok — file now, audit takes time

1. First: convert your TikTok account to a **Business account** (app → Settings → Account → Switch to Business account). Free.
2. Go to **developers.tiktok.com** → register → **Manage apps** → Create app.
3. Add the **Content Posting API** product → enable Direct Post → submit the app for review/audit.
4. Until the audit passes, API posts are private-only (we can still test).
5. Tell Claude when it's approved.

## 5. Image hosting (needed before first real post)

Instagram and Pinterest APIs only accept images via **public URL** — they can't
take direct file uploads. Cheapest zero-cost fix: a public GitHub repository
where Claude commits each day's creative and uses the raw URL. If you have a
GitHub account, send Claude the username; if not, creating one at github.com
takes 2 minutes. (Alternatives: Cloudflare R2 free tier.)

## Then what happens

Once item 1 + 5 arrive, Claude will:
1. Run a test post end-to-end on IG + FB with one poster.
2. You sanity-check it in the apps.
3. Claude wires the daily scheduled task: pick poster → generate creative
   (Nano Banana + Adobe tools) → caption + hashtags → post → log it.
4. Pinterest and TikTok get switched on automatically as their approvals land —
   the code for them is already written and waiting.
