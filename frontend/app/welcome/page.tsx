import type { Metadata } from "next";

import { Landing } from "@/components/landing";

export const metadata: Metadata = {
  title: "PayFlow AI · Payments that heal themselves",
  description: "Autonomous payment operations: detect, investigate, decide, fix and prove, with a human in control.",
};

export default function WelcomePage() {
  return <Landing />;
}
