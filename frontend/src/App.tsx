import { BrowserRouter, Route, Routes } from "react-router-dom";
import Layout from "./Layout";
import ResumePage from "./pages/ResumePage";
import KeywordsPage from "./pages/KeywordsPage";
import RunPage from "./pages/RunPage";
import QueuePage from "./pages/QueuePage";
import SettingsPage from "./pages/SettingsPage";
import { ToastProvider } from "./toast";

export default function App() {
  return (
    <ToastProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<ResumePage />} />
            <Route path="/keywords" element={<KeywordsPage />} />
            <Route path="/run" element={<RunPage />} />
            <Route path="/queue" element={<QueuePage />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ToastProvider>
  );
}
