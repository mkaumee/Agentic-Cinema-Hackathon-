/**
 * One Firebase app, built once at module load.
 *
 * `VITE_USE_EMULATOR=1` points everything at the local emulators instead of
 * the real project, which is how this is developed: `make e2e` fills the
 * emulator with a project, four items and twelve negotiations, so the panel
 * has something on it from the first run — and the loop can be run again in
 * another terminal to watch rows move.
 *
 * Against the emulator the config values are ignored, so the committed
 * placeholders are fine there. Against the real project they are not, and the
 * failure that produces deep inside the SDK is unreadable — hence the check.
 */

import { initializeApp, type FirebaseApp } from "firebase/app";
import {
  connectAuthEmulator,
  getAuth,
  GoogleAuthProvider,
  signInWithPopup,
  signOut,
  type Auth,
  type User,
} from "firebase/auth";
import {
  connectFirestoreEmulator,
  getFirestore,
  type Firestore,
} from "firebase/firestore";

import {
  EMULATOR_PROJECT,
  firebaseConfig,
  isPlaceholder,
} from "./firebase-config";

export const USE_EMULATOR = import.meta.env["VITE_USE_EMULATOR"] === "1";

if (!USE_EMULATOR && isPlaceholder()) {
  throw new Error(
    "src/firebase-config.ts still holds placeholders. Run " +
      "`firebase apps:sdkconfig web --project <project-id>` and paste the " +
      "result in, or develop against the emulator with VITE_USE_EMULATOR=1.",
  );
}

const app: FirebaseApp = initializeApp(
  USE_EMULATOR ? { ...firebaseConfig, projectId: EMULATOR_PROJECT } : firebaseConfig,
);

export const auth: Auth = getAuth(app);
export const db: Firestore = getFirestore(app);

if (USE_EMULATOR) {
  connectAuthEmulator(auth, "http://127.0.0.1:9099", { disableWarnings: true });
  connectFirestoreEmulator(db, "127.0.0.1", 8080);
}

/**
 * Google sign-in, not anonymous.
 *
 * Reads are gated behind `isSignedIn()`, so some identity is required either
 * way. It is Google because Phase 6's Approve screen needs the `producer`
 * custom claim that `scripts/grant_producer.py` sets on a real account, and an
 * anonymous identity cannot carry one. Doing it now is twenty lines that are
 * not redone later.
 */
export const signIn = async (): Promise<void> => {
  await signInWithPopup(auth, new GoogleAuthProvider());
};

export const signOutOfEverything = async (): Promise<void> => {
  await signOut(auth);
};

/**
 * Throw away whatever is stored locally and start again.
 *
 * The escape hatch for a session the SDK cannot resolve. `onAuthStateChanged`
 * normally fires within a moment of load — with the restored user, or with
 * null — and the app waits for it before rendering anything, because
 * rendering "signed out" to somebody who is signed in is worse than a brief
 * spinner. When that callback never fires, though, the wait has no end and no
 * button on it: the person is stuck on a spinner with no way back to a sign-in
 * screen, which is the one thing this must never do on somebody else's laptop.
 *
 * Seen for real: signs in perfectly on a second device, hangs forever on the
 * first. That is a local artefact, and this is how you clear it without
 * knowing what DevTools is.
 *
 * `signOut` is raced rather than awaited, because it goes through the same
 * wedged machinery and can hang exactly as hard. Getting the person back to a
 * usable screen is the point; a tidy sign-out that never resolves defeats it.
 */
export const resetSession = async (): Promise<void> => {
  await Promise.race([
    signOut(auth).catch(() => undefined),
    new Promise((resolve) => setTimeout(resolve, 2000)),
  ]);

  // Where the SDK persists a session. A corrupt record here is the usual
  // reason the callback never comes.
  try {
    indexedDB.deleteDatabase("firebaseLocalStorageDb");
  } catch {
    // Private windows and blocked site data both throw on access rather than
    // returning empty. There is nothing to clear in that case anyway.
  }
  try {
    // Only Firebase's own keys: this is a repair, not a factory reset, and
    // wiping somebody's unrelated storage to fix a login is overreach.
    for (const key of Object.keys(localStorage)) {
      if (key.startsWith("firebase:")) localStorage.removeItem(key);
    }
  } catch {
    // As above.
  }
};

export type { User };
