// Self-hosted variable fonts (bundled woff2 — no runtime/CDN fetch). These
// give the whole product a real typographic identity instead of the OS
// default stack: Inter for UI/body, Fraunces for display headings, JetBrains
// Mono for code surfaces. Imported before globals so the @font-face families
// are registered when the token layer points --rds-font-* at them.
import "@fontsource-variable/inter/index.css";
import "@fontsource-variable/fraunces/opsz.css";
import "@fontsource-variable/fraunces/opsz-italic.css";
import "@fontsource-variable/jetbrains-mono/wght.css";
import "./globals.css";
import type { Metadata } from "next";
import { SiteNav } from "./_components/SiteNav";

export const metadata: Metadata = {
  title: "Ronin AI OS — specialized intelligence, one world at a time",
  description:
    "A local-first, provider-agnostic AI operating system. Enter an industry world — Coding, Education, Healthcare Information — with its own tools, safety rules, memory boundaries, and evaluations.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="font-sans antialiased">
        {/* No-flash theme bootstrap: apply the persisted RDS theme before paint,
            so the whole site (landing + OS) shares the viewer's choice. */}
        <script
          dangerouslySetInnerHTML={{
            __html:
              "(function(){try{var t=localStorage.getItem('rds-theme');if(t){document.documentElement.setAttribute('data-theme',t);}}catch(e){}})();",
          }}
        />
        <SiteNav />
        {children}
      </body>
    </html>
  );
}
