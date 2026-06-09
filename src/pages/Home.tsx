import { Hero } from "@/components/ui/animated-hero";
import { MacbookScroll } from "@/components/ui/macbook-scroll";
import type { Page } from "../App";

interface HomeProps {
  navigate: (page: Page) => void;
}

export default function Home({ navigate }: HomeProps): JSX.Element {
  return (
    <main className="min-h-screen overflow-hidden bg-white text-foreground dark:bg-[#0B0B0F]">
      <Hero onTryDemo={() => navigate("dashboard")} />
      <div className="w-full overflow-hidden bg-white dark:bg-[#0B0B0F]">
        <MacbookScroll
          title={
            <span>
              Built with a multi-database architecture. Powered by Neo4j.
            </span>
          }
          src="/dashboard-preview.webp"
          showGradient={false}
        />
      </div>
    </main>
  );
}
