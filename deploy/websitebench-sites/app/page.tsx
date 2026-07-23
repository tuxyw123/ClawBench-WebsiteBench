import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "WebsiteBench · Agent Reconstruction Viewer",
  description: "Inspect offline reference websites, interaction coverage, and future Agent reconstruction scores.",
};

export default function Home() {
  return (
    <main>
      <h1>WebsiteBench Reconstruction Viewer</h1>
      <p>Explore offline reference websites and inspect how future Agents reconstruct them.</p>
    </main>
  );
}
