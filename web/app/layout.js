import "./globals.css";
import Nav from "./nav";

export const metadata = {
  title: "SetuHaul Dock Desk",
  description: "Driver exception handling with a deterministic capacity core",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <head>
        <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" />
      </head>
      <body><Nav />{children}</body>
    </html>
  );
}
