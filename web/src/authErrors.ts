/**
 * Turn a Firebase auth error into something that names the fix.
 *
 * `Firebase: Error (auth/configuration-not-found)` is accurate and tells the
 * reader nothing. Every one of these was hit for real getting this project
 * onto Firebase Hosting, and each time the code was the whole diagnosis and
 * the message was noise. This screen is the debugging surface for the rest of
 * the system; it should not need a second screen to debug it.
 *
 * The code is kept alongside the explanation rather than replaced — it is the
 * part that is searchable, and hiding it would trade one unhelpfulness for
 * another.
 */
export const AUTH_HELP: Record<string, string> = {
  "auth/configuration-not-found":
    "Authentication has never been switched on for this project. " +
    "Firebase console → Authentication → Get started. The provider list " +
    "does not exist until that button is pressed.",
  "auth/operation-not-allowed":
    "This sign-in method is off. Firebase console → Authentication → " +
    "Sign-in method → Enable Google or Email/Password as appropriate.",
  "auth/invalid-credential": "The email address or password is incorrect.",
  "auth/wrong-password": "The email address or password is incorrect.",
  "auth/user-not-found": "The email address or password is incorrect.",
  "auth/invalid-email": "Enter a valid email address.",
  "auth/user-disabled": "This account is disabled. Contact your administrator.",
  "auth/too-many-requests": "Too many attempts. Wait a while and try again.",
  "auth/network-request-failed": "Check your connection and try again.",
  "auth/unauthorized-domain":
    "This domain is not on the authorised list. Firebase console → " +
    "Authentication → Settings → Authorised domains.",
  "auth/popup-blocked":
    "The browser blocked the sign-in popup. Allow popups for this site " +
    "and try again — nothing is misconfigured.",
  "auth/popup-closed-by-user":
    "The sign-in window was closed before it finished. Nothing is " +
    "misconfigured; try again.",
};

export const explain = (cause: unknown): string => {
  const code = (cause as { code?: unknown } | null)?.code;
  const help = typeof code === "string" ? AUTH_HELP[code] : undefined;
  const raw = cause instanceof Error ? cause.message : String(cause);
  return help === undefined ? raw : `${help}  (${String(code)})`;
};
