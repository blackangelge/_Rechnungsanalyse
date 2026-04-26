/**
 * Startseite: Weiterleitung zum Dashboard.
 * Next.js App Router: redirect() muss aus next/navigation importiert werden.
 */

import { redirect } from "next/navigation";

export default function RootPage() {
  // Startseite leitet immer zum Dashboard weiter
  redirect("/dashboard");
}
