import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Ronin AI OS — specialized intelligence, one world at a time",
  description:
    "A local-first, provider-agnostic AI operating system. Enter an industry world — Coding, Education, Healthcare Information — with its own tools, safety rules, memory boundaries, and evaluations.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="font-sans antialiased">{children}</body>
    </html>
  );
}
