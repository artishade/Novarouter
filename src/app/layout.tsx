import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { Toaster } from "@/components/ui/sonner";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "NovaRouter — AI Gateway Control Plane",
  description:
    "One API key, every provider, every modality. NovaRouter fans out to OpenAI-compatible, Anthropic-native and Gemini providers with key rotation, fallback routing, identity spoofing, a built-in autonomous agent, free storage backends and a sandbox terminal.",
  keywords: ["NovaRouter", "AI gateway", "API router", "LLM", "OpenRouter", "free models"],
  authors: [{ name: "NovaRouter" }],
  icons: {
    icon: "/logo.svg",
  },
};

export const viewport: Viewport = {
  themeColor: "#080c14",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased bg-[#080c14] text-slate-200`}
      >
        {children}
        <Toaster position="bottom-right" richColors closeButton />
      </body>
    </html>
  );
}
