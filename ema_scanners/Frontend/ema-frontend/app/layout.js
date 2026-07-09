import "./globals.css";

export const metadata = {
  title: "EMA Scanner",
  description: "Real-time Binance Spot + USDT Futures EMA crossover scanner.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
