import { NavLink, Outlet } from "react-router-dom";
import {
  FileText,
  ListChecks,
  Play,
  SettingsIcon,
  Tags,
} from "./icons";

const NAV_ITEMS = [
  { to: "/", label: "Resume", icon: FileText, end: true },
  { to: "/keywords", label: "Keywords & roles", icon: Tags, end: false },
  { to: "/run", label: "Run", icon: Play, end: false },
  { to: "/queue", label: "Review queue", icon: ListChecks, end: false },
  { to: "/settings", label: "Settings", icon: SettingsIcon, end: false },
];

export default function Layout() {
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="sidebar-brand">Outreach Agent</div>
        <nav className="sidebar-nav">
          {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                `nav-item${isActive ? " active" : ""}`
              }
            >
              <Icon size={16} strokeWidth={1.5} />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">local-only · manual runs</div>
      </aside>
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
