import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Media Myth Buster",
  description:
    "Claim-by-claim credibility analysis of Instagram reels, with cited evidence.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="wrap">
          <header
            style={{
              display: "flex",
              alignItems: "baseline",
              justifyContent: "space-between",
              gap: 16,
              marginBottom: 28,
            }}
          >
            <Link href="/" style={{ textDecoration: "none", fontWeight: 600 }}>
              Media Myth Buster
            </Link>
            <nav style={{ display: "flex", gap: 16, fontSize: 13 }}>
              <Link href="/methodology" className="sub">
                Methodology
              </Link>
            </nav>
          </header>
          {children}
        </div>
      </body>
    </html>
  );
}
