import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "ronin — the Monday-morning founder briefing",
  description:
    "One command: revenue, churn, payment failures, top engineering issues — pulled live from your stack in 10 seconds.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="font-sans antialiased">{children}</body>
    </html>
  );
}
