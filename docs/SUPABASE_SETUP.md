# Supabase email/password setup for this Streamlit project

This update replaces Google/OIDC sign-in in the customer app. It is Python, not Next.js: do not install npm packages or use NEXT_PUBLIC_ variables. The existing customer workspace package must already be installed. Supabase handles identity only. Documents, vectors, reviews, permissions and background jobs remain in customer_data/ on your server.

## 1. Install these files

Finish active work, stop the customer UI and back up app/pilot/auth.py and requirements-customer.txt. Copy the archive's contents into the project root, matching paths. The only replacements are app/pilot/auth.py and requirements-customer.txt; config_examples/supabase.env.example and this guide are additions.

From your activated virtual environment:

```bash
pip install -r requirements-customer.txt
```

The previous app/customer_main.py, pilot/store.py, pilot_admin.py and pilot_worker.py stay in place. Do not copy files into app/services. This change does not require regenerating existing embeddings. Pending jobs fingerprint application code, so finish them before changing files; previously paused jobs may require a new review after code changes.

## 2. Create the Supabase project

Open https://supabase.com/dashboard and create a project in your organization. Choose a project name, database password and appropriate region. Wait for provisioning. The database password is not an app-user password or the publishable API key. This implementation does not use a direct database connection, so do not paste that password into the app.

## 3. Configure Email authentication

In Authentication > Sign In / Providers > Email, enable Email and keep Confirm email on. Allow new user signup if you want the supplied Create account form to work. Review the provider's password policy; the app additionally requires at least 12 characters when creating/resetting a password.

In Authentication > URL Configuration, set Site URL to:

```text
http://localhost:8501
```

Allow that same URL for local confirmation/reset redirects. In production replace it with your HTTPS customer-app URL. This flow uses codes entered in the app, so it does not need Google's /oauth2callback or a JavaScript callback page.

## 4. Configure email delivery

For your first development account, the Supabase default sender is limited to addresses on your Supabase organization team and has low sending limits. External customer emails need custom SMTP. Configure the SMTP host, port, sender, username and password under Authentication email/SMTP settings, using your email provider's values. Keep those SMTP credentials in Supabase, not the app's publishable-key field. Enable sender-domain verification as required by your mail provider.

Do not disable email confirmation to avoid delivery problems. Test with a permitted development address or configure delivery correctly. The app still requires email_confirmed_at before allowing workspace access.

Official delivery details: https://supabase.com/docs/guides/auth/auth-smtp

## 5. Use email-code templates

In Authentication > Email Templates, update these two templates. Preserve any branding you want; the necessary variable is {{ .Token }}.

Confirm signup:

```html
<h2>Confirm your account</h2>
<p>Enter this confirmation code in the Financial claim review app:</p>
<p><strong>{{ .Token }}</strong></p>
<p>If you did not request this account, ignore this email.</p>
```

Reset password:

```html
<h2>Reset your password</h2>
<p>Enter this reset code in the Financial claim review app:</p>
<p><strong>{{ .Token }}</strong></p>
<p>If you did not request this, ignore this email.</p>
```

The app has Confirm email and Reset password forms. Use the whole code delivered by your project; its configured length/expiry can vary. Do not paste access tokens into a URL or use default fragment-based password-reset links with this implementation.

Official template/OTP references:
- https://supabase.com/docs/guides/auth/auth-email-templates
- https://supabase.com/docs/reference/python/auth-verifyotp

## 6. Copy the project configuration

Open the project's Connect dialog or Settings > API Keys. Copy the Project URL and publishable key (starts sb_publishable_). This code deliberately rejects secret/service-role keys.

Add these lines to the existing project-root .env, preserving your model API settings:

```dotenv
SUPABASE_URL=https://YOUR_PROJECT_REF.supabase.co
SUPABASE_PUBLISHABLE_KEY=sb_publishable_REPLACE_WITH_YOUR_KEY
SUPABASE_SITE_URL=http://localhost:8501
SUPABASE_ENABLE_SIGNUP=true
```

Use your actual values, not the placeholders. The URL must be the base HTTPS Project URL without /auth/v1. The code derives its issuer by appending /auth/v1.

Alternatively place the same names as top-level string keys in .streamlit/secrets.toml. Existing environment variables take precedence. Avoid maintaining conflicting values in both places. Remove the obsolete OIDC [auth] and old [pilot] configuration after backing it up, preserving unrelated settings. Google client ID, client secret, discovery URL and cookie secret are no longer used by this authentication module.

Keep .env and secrets.toml out of Git. There is no need for a Supabase secret key, service-role key, database password or NEXT_PUBLIC_ prefix in this integration.

## 7. Start the customer app

```bash
streamlit run app/customer_main.py
```

You should see Sign in, Create account, Confirm email and Reset password. Seeing the old Google Sign in button means the old auth.py is still installed or Streamlit was not restarted. Seeing Workspace / Evaluation / History / How it works means you opened the old internal app/main.py instead of customer_main.py.

## 8. Create and confirm an app-user account

Choose Create account, enter your email and a strong password, then submit. Find the code in your email. Choose Confirm email, enter the same address and code, and submit. You can also sign in normally with the password after confirmation.

Creating a Supabase dashboard organization login is separate from creating this app-user account. App users appear under Authentication > Users.

If the form reports an authentication error, inspect your Supabase Authentication logs, email settings and rate limits. Error messages in the app are intentionally generic to avoid revealing whether an email has an account.

## 9. Grant workspace access

After a successful sign-in, an uninvited user sees a workspace-access message with two values:

- Account subject: their Supabase User UID.
- Issuer: https://YOUR_PROJECT_REF.supabase.co/auth/v1.

Open another terminal in the project folder, activate the same virtual environment and run this on one line:

```bash
python app/pilot_admin.py grant --issuer "https://YOUR_PROJECT_REF.supabase.co/auth/v1" --subject "YOUR_SUPABASE_USER_UID" --name "My test workspace"
```

Use the exact values shown in the app. This creates a private workspace and membership in your local control database. Return to the browser and click Refresh workspace access. You should see Reviews, Documents and History.

For a second customer, create a separate workspace by running grant without --workspace. To add someone to an existing workspace, explicitly pass --workspace WORKSPACE_ID. Members of one workspace intentionally share documents and reviews.

If you already have a workspace from Google/OIDC, run:

```bash
python app/pilot_admin.py list-workspaces
```

Then grant the verified Supabase UID access to the intended existing workspace using --workspace. The administrator must establish that this is the correct person; do not automatically link identities by matching email strings. Revoke the old membership when appropriate. Existing data is not migrated or deleted by signing in through Supabase.

## 10. Start the worker

In a second activated terminal:

```bash
python app/pilot_worker.py
```

The model provider keys/configuration must still be in the server environment or existing .env for the worker. Upload a small public PDF, refresh until ready, submit a known claim and inspect its result. Existing internal research data is not automatically exposed to a customer workspace.

## 11. Check the authentication flow

- Wrong password: access denied.
- Unconfirmed email: workspace access denied.
- Confirmed but uninvited account: only the access-request screen.
- Granted account: only its authorized workspaces.
- Second account in a different workspace: no access to the first customer's sources, reviews or exports.
- Sign out: current app session cleared; opening the app requires sign-in.
- Normal widget interaction: session retained and verified server-side on each rerun.
- Hard browser refresh, a new tab, or server restart: you may need to sign in again. Tokens are kept in server-side Streamlit session state, not persistent browser cookies. Saved reviews remain in the database.
- Reset password: request code, enter email/code/new password, then sign in with the new password.
- Membership revocation: refresh the existing tab; workspace operations should be denied.

The code calls Supabase get_user(access_token) before rendering protected customer actions. Existing store functions independently check workspace membership and Access expiry. It does not authorize from get_session() or an unverified JWT payload. Clients are created per rerun; they are never shared through st.cache_resource. Refresh tokens are rotated through set_session when needed.

Logout revokes refresh sessions when the provider is available and always clears local state. Already-issued JWTs can remain valid until expiration; this is normal Supabase behavior. Use membership revocation for immediate application-level access removal. The existing native Streamlit media-link limitations still apply; this auth change does not turn exports into a separately authenticated file API.

## 12. Supabase database/RLS scope

This update uses Supabase Auth only; it does not create Supabase document/review tables or storage buckets. Existing customer records remain in the server's SQLite database and filesystem. There are no new application Supabase tables to write RLS policies for in this patch. If you later migrate data into Supabase tables/storage, design and test its row/bucket policies before exposing that data.

## Validation status

The assistant has not installed packages, run the app, executed tests, called Supabase authentication or changed your project settings. Follow the tests above on your machine. Configure HTTPS, external email delivery and the prior customer acceptance checks before inviting real customers.

Official Python references:
- https://supabase.com/docs/reference/python/initializing
- https://supabase.com/docs/reference/python/auth-signinwithpassword
- https://supabase.com/docs/reference/python/auth-getuser
- https://supabase.com/docs/reference/python/auth-setsession
- https://supabase.com/docs/reference/python/auth-signout
