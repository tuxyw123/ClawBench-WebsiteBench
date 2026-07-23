import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "WebsiteBench · Agent Reconstruction Viewer",
  description: "Explore offline reference websites and inspect how future Agents reconstruct them.",
  icons: {
    icon: "/static/favicon.svg",
    shortcut: "/static/favicon.svg",
  },
  openGraph: {
    title: "WebsiteBench · Agent Reconstruction Viewer",
    description: "Explore offline reference websites and inspect how future Agents reconstruct them.",
    images: ["/static/og-v2.png"],
  },
  twitter: {
    card: "summary_large_image",
    title: "WebsiteBench · Agent Reconstruction Viewer",
    description: "Explore offline reference websites and inspect how future Agents reconstruct them.",
    images: ["/static/og-v2.png"],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        {children}
      </body>
    </html>
  );
}
