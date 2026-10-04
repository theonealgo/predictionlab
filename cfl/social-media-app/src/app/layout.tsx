import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'streamly.blog — AI Video + Social Autopost Platform',
  description: 'Create faceless AI videos and connect social accounts through built-in OAuth.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
