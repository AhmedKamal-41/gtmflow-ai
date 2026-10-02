import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";

import { AIStatusProvider } from "@/components/AIStatusProvider";
import { AppShell } from "@/components/AppShell";
import { AuthProvider } from "@/components/AuthProvider";

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
  title: "GTMFlow",
  description:
    "Prioritize your leads, review drafts written by GTMFlow's fine-tuned model, and send the best ones to Slack.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${inter.variable} ${jetbrainsMono.variable}`}>
      <body className="min-h-dvh font-sans antialiased">
        <AuthProvider>
          <AIStatusProvider>
            <AppShell>{children}</AppShell>
          </AIStatusProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
