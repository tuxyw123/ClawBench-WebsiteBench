from __future__ import annotations

from html import escape
from typing import Mapping, Sequence
from urllib.parse import unquote, urlencode, urlsplit


AUTH_STYLESHEET = "/static/auth.css?v=20260722-03"


def _first(query: Mapping[str, Sequence[str]], name: str) -> str:
    values = query.get(name, ())
    return values[0] if values else ""


def safe_return_target(query: Mapping[str, Sequence[str]]) -> str | None:
    """Return a same-origin path, never an absolute or scheme-relative URL."""
    candidate = _first(query, "openid.return_to").strip()
    if not candidate or any(ord(character) < 32 for character in candidate):
        return None
    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc:
        return None
    if not parsed.path.startswith("/") or parsed.path.startswith("//") or "\\" in parsed.path:
        return None
    decoded_path = parsed.path
    for _ in range(2):
        decoded_path = unquote(decoded_path)
    if (
        decoded_path.startswith("//")
        or "\\" in decoded_path
        or any(ord(character) < 32 for character in decoded_path)
    ):
        return None
    return candidate


def _logo() -> str:
    return '<a class="auth-logo" href="/" aria-label="Amazon"></a>'


def _hidden_return_target(query: Mapping[str, Sequence[str]]) -> str:
    target = safe_return_target(query)
    if target is None:
        return ""
    return (
        '<input type="hidden" name="openid.return_to" value="'
        f'{escape(target, quote=True)}">'
    )


def _return_target_suffix(query: Mapping[str, Sequence[str]]) -> str:
    target = safe_return_target(query)
    return "?" + urlencode({"openid.return_to": target}) if target else ""


def _error_notice(message: str | None) -> str:
    if not message:
        return ""
    return f'<div class="auth-error" role="alert">{escape(message)}</div>'


def _document(title: str, content: str, *, body_class: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <link rel="stylesheet" href="{AUTH_STYLESHEET}">
</head>
<body class="auth-page {escape(body_class, quote=True)}">
  <header class="auth-header">{_logo()}</header>
  <main class="auth-main">
    {content}
  </main>
  <footer class="auth-footer">
    <nav aria-label="Amazon account policies">
      <a href="#">Conditions of Use</a>
      <a href="#">Privacy Notice</a>
      <a href="#">Help</a>
    </nav>
    <p>© 1996–2026, Amazon.com, Inc. or its affiliates</p>
  </footer>
</body>
</html>"""


def signin_page(query: Mapping[str, Sequence[str]], error: str | None = None) -> str:
    if _first(query, "stage").lower() == "password":
        content = f"""
        <section class="auth-card" aria-labelledby="auth-heading">
          <h1 id="auth-heading">Sign in</h1>
          <p class="auth-identity">Amazon account</p>
          {_error_notice(error)}
          <form method="post" action="/ap/signin">
            {_hidden_return_target(query)}
            <label for="ap-password">Password</label>
            <a class="auth-inline-link" href="/ap/forgotpassword">Forgot password?</a>
            <input id="ap-password" name="password" type="password" autocomplete="current-password" required autofocus>
            <button class="auth-primary" type="submit">Sign in</button>
          </form>
          <label class="auth-checkbox"><input name="rememberMe" type="checkbox"> Keep me signed in</label>
          <details class="auth-details"><summary>Details</summary><p>Use this option only on your personal device.</p></details>
        </section>
        """
        return _document("Amazon Sign-In", content, body_class="auth-signin auth-password-stage")

    content = f"""
    <section class="auth-card" aria-labelledby="auth-heading">
      <h1 id="auth-heading">Sign in or create account</h1>
      {_error_notice(error)}
      <form method="post" action="/ap/signin">
        {_hidden_return_target(query)}
        <label for="ap-email">Enter mobile number or email</label>
        <input id="ap-email" name="email" type="text" autocomplete="username" inputmode="email" required autofocus>
        <button class="auth-primary" type="submit">Continue</button>
      </form>
      <p class="auth-legal">By continuing, you agree to Amazon's <a href="#">Conditions of Use</a> and <a href="#">Privacy Notice</a>.</p>
      <details class="auth-details"><summary>Need help?</summary><a href="/ap/forgotpassword">Forgot your password?</a></details>
    </section>
    <section class="auth-divider" aria-label="New to Amazon"><span>New to Amazon?</span></section>
    <a class="auth-secondary" href="/ap/register{escape(_return_target_suffix(query), quote=True)}">Create your Amazon account</a>
    <aside class="auth-business"><strong>Buying for work?</strong> <a href="#">Create a free business account</a></aside>
    """
    return _document("Amazon Sign-In", content, body_class="auth-signin auth-identifier-stage")


def register_page(query: Mapping[str, Sequence[str]], error: str | None = None) -> str:
    suggested_email = _first(query, "email").strip()
    content = f"""
    <section class="auth-card" aria-labelledby="auth-heading">
      <h1 id="auth-heading">Create account</h1>
      {_error_notice(error)}
      <form method="post" action="/ap/register">
        {_hidden_return_target(query)}
        <label for="ap-customer-name">Your name</label>
        <input id="ap-customer-name" name="customerName" type="text" autocomplete="name" placeholder="First and last name" required autofocus>
        <label for="ap-register-email">Mobile number or email</label>
        <input id="ap-register-email" name="email" type="text" autocomplete="email" inputmode="email" value="{escape(suggested_email, quote=True)}" required>
        <label for="ap-register-password">Password</label>
        <input id="ap-register-password" name="password" type="password" autocomplete="new-password" minlength="6" placeholder="At least 6 characters" required aria-describedby="password-hint">
        <p id="password-hint" class="auth-hint"><span aria-hidden="true">i</span> Passwords must be at least 6 characters.</p>
        <label for="ap-password-check">Re-enter password</label>
        <input id="ap-password-check" name="passwordCheck" type="password" autocomplete="new-password" minlength="6" required>
        <button class="auth-primary" type="submit">Continue</button>
      </form>
      <p class="auth-legal">By creating an account, you agree to Amazon's <a href="#">Conditions of Use</a> and <a href="#">Privacy Notice</a>.</p>
      <hr>
      <p class="auth-existing">Already have an account? <a href="/ap/signin{escape(_return_target_suffix(query), quote=True)}">Sign in <span aria-hidden="true">›</span></a></p>
    </section>
    """
    return _document("Amazon Registration", content, body_class="auth-register")


def forgot_password_page(
    query: Mapping[str, Sequence[str]], error: str | None = None
) -> str:
    if _first(query, "stage").lower() == "reset-password":
        content = f"""
        <section class="auth-card" aria-labelledby="auth-heading">
          <h1 id="auth-heading">Create new password</h1>
          <p>We'll ask for this password whenever you sign in.</p>
          {_error_notice(error)}
          <form method="post" action="/ap/forgotpassword">
            <label for="ap-new-password">New password</label>
            <input id="ap-new-password" name="password" type="password" autocomplete="new-password" minlength="6" required autofocus>
            <label for="ap-new-password-check">Re-enter password</label>
            <input id="ap-new-password-check" name="passwordCheck" type="password" autocomplete="new-password" minlength="6" required>
            <button class="auth-primary" type="submit">Save changes and sign in</button>
          </form>
        </section>
        """
        return _document("Create New Password", content, body_class="auth-forgot auth-reset-stage")

    content = f"""
    <section class="auth-card" aria-labelledby="auth-heading">
      <h1 id="auth-heading">Password assistance</h1>
      <p>Enter the email address or mobile phone number associated with your Amazon account.</p>
      {_error_notice(error)}
      <form method="post" action="/ap/forgotpassword">
        {_hidden_return_target(query)}
        <label for="ap-forgot-email">Email or mobile phone number</label>
        <input id="ap-forgot-email" name="email" type="text" autocomplete="username" inputmode="email" required autofocus>
        <button class="auth-primary" type="submit">Continue</button>
      </form>
    </section>
    <section class="auth-support-copy">
      <h2>Has your email or mobile number changed?</h2>
      <p>If you no longer use the email address associated with your Amazon account, you may contact Customer Service for help restoring access to your account.</p>
    </section>
    """
    return _document("Amazon Password Assistance", content, body_class="auth-forgot auth-email-stage")


def _mail_delivery_status_markup(
    purpose: str,
    mail_delivery: Mapping[str, object] | None,
    mail_delivery_mode: str,
    local_inbox_url: str | None = None,
) -> str:
    status = str((mail_delivery or {}).get("status") or "")
    if status not in {
        "QUEUED",
        "LOCAL_ONLY",
        "SMTP_PENDING",
        "SMTP_SENT",
        "SMTP_FAILED",
    }:
        status = "SMTP_PENDING" if mail_delivery_mode == "SMTP" else "LOCAL_ONLY"

    descriptions = {
        "QUEUED": (
            "If an account matches that address, its verification message is "
            "being handled. Refreshing does not reveal whether an account exists."
        ),
        "LOCAL_ONLY": (
            "This message is stored only in the protected local outbox; "
            "no external email was sent."
        ),
        "SMTP_PENDING": (
            "The configured SMTP service is processing this message. "
            "Refresh to check the latest delivery result."
        ),
        "SMTP_SENT": "The configured SMTP service accepted this message.",
        "SMTP_FAILED": (
            "The SMTP attempt failed. No address or provider error is exposed here."
        ),
    }
    action_suffix = "?" + urlencode({"purpose": purpose})
    actions: list[str] = []
    if status in {"QUEUED", "SMTP_PENDING"}:
        actions.append(
            f'<a class="auth-status-link" href="/ap/cvf/verify{escape(action_suffix, quote=True)}">Refresh delivery status</a>'
        )
    if status == "SMTP_FAILED" and bool((mail_delivery or {}).get("can_retry")):
        actions.append(
            '<form class="auth-retry-form" method="post" '
            f'action="/ap/cvf/verify{escape(action_suffix, quote=True)}">'
            '<button class="auth-link-button" type="submit" name="action" '
            'value="retry-delivery">Retry email delivery</button></form>'
        )
    if local_inbox_url and status in {"SMTP_PENDING", "SMTP_SENT", "SMTP_FAILED"}:
        actions.append(
            f'<a class="auth-status-link" href="{escape(local_inbox_url, quote=True)}">'
            "Open local SMTP inbox</a>"
        )
    action_markup = (
        f'<div class="auth-delivery-actions">{"".join(actions)}</div>'
        if actions
        else ""
    )
    return (
        f'<section class="auth-delivery-status auth-delivery-{status.casefold()}" '
        'aria-live="polite"><h2>Email delivery</h2>'
        f'<p><strong>{status}</strong><span>{escape(descriptions[status])}</span></p>'
        f'{action_markup}</section>'
    )


def verification_page(
    query: Mapping[str, Sequence[str]],
    *,
    masked_destination: str | None = None,
    error: str | None = None,
    mail_delivery_mode: str = "LOCAL_ONLY",
    mail_delivery: Mapping[str, object] | None = None,
    local_inbox_url: str | None = None,
) -> str:
    purpose = _first(query, "purpose").lower()
    if purpose == "registration" and masked_destination:
        lead = (
            f"A verification code was queued for {masked_destination}. "
            "Enter it below when it arrives."
        )
    elif purpose == "registration":
        lead = "Start account creation again to request a new email verification code."
    elif purpose == "password-reset":
        lead = (
            "If an account matches that address, a verification code was queued. "
            "Enter it below if it arrives."
        )
    else:
        lead = "Enter the one-time password that was queued for you."
    action_suffix = (
        "?" + urlencode({"purpose": purpose})
        if purpose in {"registration", "password-reset"}
        else ""
    )
    delivery_status = _mail_delivery_status_markup(
        purpose,
        mail_delivery,
        mail_delivery_mode,
        local_inbox_url,
    )
    content = f"""
    <section class="auth-card" aria-labelledby="auth-heading">
      <h1 id="auth-heading">Verify email address</h1>
      <p>{escape(lead)}</p>
      {_error_notice(error)}
      <form method="post" action="/ap/cvf/verify{escape(action_suffix, quote=True)}">
        <label for="ap-code">Enter verification code</label>
        <input id="ap-code" name="code" type="text" autocomplete="one-time-code" inputmode="numeric" pattern="[0-9]{{6}}" maxlength="6" required autofocus>
        <button class="auth-primary" type="submit">Verify</button>
      </form>
      <form class="auth-resend-form" method="post" action="/ap/cvf/verify{escape(action_suffix, quote=True)}">
        <button class="auth-link-button" type="submit" name="action" value="resend">Resend code</button>
      </form>
      {delivery_status}
    </section>
    """
    return _document("Amazon Verification", content, body_class="auth-verification")


def page_for(path: str, query: Mapping[str, Sequence[str]]) -> str:
    if path == "/ap/signin":
        return signin_page(query)
    if path == "/ap/register":
        return register_page(query)
    if path == "/ap/forgotpassword":
        return forgot_password_page(query)
    if path == "/ap/cvf/verify":
        return verification_page(query)
    raise ValueError(f"unsupported auth path: {path}")
