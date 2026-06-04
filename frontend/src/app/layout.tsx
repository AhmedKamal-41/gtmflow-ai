import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";

import { AppHeader } from "@/components/AppHeader";

import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "GTMFlow: lead scoring & outreach",
  description:
    "Upload a lead list, score it, draft outreach, and send the best ones to Slack, with the numbers to show what it saved.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${inter.variable} ${jetbrainsMono.variable}`}>
      <body className="min-h-dvh font-sans antialiased">
        <AppHeader />
        <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
          {children}
        </main>
        <footer className="mx-auto max-w-7xl px-4 pb-10 pt-6 text-xs text-slate-400 sm:px-6 lg:px-8">
          GTMFlow · runs with mock AI and Slack out of the box, so you can try
          it without any keys.
        </footer>
      </body>
    </html>
  );
}
