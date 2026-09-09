"use client";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import {
  AudioWaveform,
  BookOpenText,
  CircleUserRound,
  Info,
  FolderKanban,
  History,
  Mic2,
  Settings2,
  SlidersHorizontal,
} from "lucide-react";
import { Suspense, type ReactNode } from "react";
import { Brand } from "./brand";
import { ApiStatus } from "./api-status";
import { GlobalPlayer } from "@/features/player/global-player";

type NavigationItem = {
  href: string;
  label: string;
  mobileLabel?: string;
  icon: typeof SlidersHorizontal;
  kind?: "daw" | "projects";
};

const nav: NavigationItem[] = [
  { href: "/studio", label: "음악 만들기", mobileLabel: "만들기", icon: SlidersHorizontal },
  { href: "/projects?mode=daw", label: "DAW 편집", mobileLabel: "DAW", icon: AudioWaveform, kind: "daw" },
  { href: "/lyrics", label: "가사 만들기", mobileLabel: "가사", icon: BookOpenText },
  { href: "/voice", label: "내 목소리", mobileLabel: "목소리", icon: Mic2 },
  { href: "/history", label: "만든 음악", mobileLabel: "음악", icon: History },
  { href: "/projects", label: "프로젝트", mobileLabel: "프로젝트", icon: FolderKanban, kind: "projects" },
  { href: "/settings", label: "설정", icon: Settings2 },
  { href: "/about", label: "서비스 소개", icon: Info },
];
const mobileNav = [nav[0], nav[1], nav[4], nav[5], nav[3]];
export function AppShell({
  children,
  context,
  variant,
}: {
  children: ReactNode;
  context?: ReactNode;
  variant?: "daw";
}) {
  return (
    <div className={`app-shell${variant === "daw" ? " app-shell-daw" : ""}`}>
      <aside className="sidebar">
        <Brand />
        <nav aria-label="주요 메뉴">
          <Suspense fallback={<StaticNavigationLinks items={nav} />}>
            <NavigationLinks items={nav} />
          </Suspense>
        </nav>
        <div className="sidebar-foot">
          <CircleUserRound />
          <span>
            Local Creator<small>개인 작업 공간</small>
          </span>
        </div>
      </aside>
      <header className="mobile-header">
        <Brand compact />
        <ApiStatus />
        <CircleUserRound />
      </header>
      <main id="main-content" className="main-workspace">
        {children}
      </main>
      <aside className="context-panel">
        <div className="context-head">
          <span>도움말</span>
          <ApiStatus />
        </div>
        {context ?? <ContextDefault />}
      </aside>
      <GlobalPlayer />
      <nav className="mobile-nav" aria-label="모바일 메뉴">
        <Suspense fallback={<StaticNavigationLinks items={mobileNav} mobile />}>
          <NavigationLinks items={mobileNav} mobile />
        </Suspense>
      </nav>
    </div>
  );
}

function NavigationLinks({ items, mobile = false }: { items: NavigationItem[]; mobile?: boolean }) {
  const path = usePathname();
  const searchParams = useSearchParams();
  const dawIntent = path === "/projects" && searchParams.get("mode") === "daw";
  const projectDetail = path.startsWith("/projects/");
  return items.map(({ href, label, mobileLabel, icon: Icon, kind }) => {
    const current = kind === "daw"
      ? dawIntent || projectDetail
      : kind === "projects"
        ? path === "/projects" && !dawIntent
        : path.startsWith(href);
    return (
      <Link
        key={href}
        href={href}
        className={current ? "active" : ""}
        aria-label={label}
        aria-current={current ? "page" : undefined}
      >
        <Icon size={mobile ? undefined : 19} />
        <span>{mobile ? mobileLabel ?? label : label}</span>
      </Link>
    );
  });
}

function StaticNavigationLinks({ items, mobile = false }: { items: NavigationItem[]; mobile?: boolean }) {
  return items.map(({ href, label, mobileLabel, icon: Icon }) => (
    <Link key={href} href={href} aria-label={label}>
      <Icon size={mobile ? undefined : 19} />
      <span>{mobile ? mobileLabel ?? label : label}</span>
    </Link>
  ));
}
function ContextDefault() {
  return (
    <div className="context-copy">
      <p className="eyebrow">DOHA MUSIC</p>
      <h2>처음이어도 괜찮아요</h2>
      <p>음악 스타일과 가사, 내 목소리를 차례로 선택하면 완성까지 안내해 드립니다.</p>
    </div>
  );
}
