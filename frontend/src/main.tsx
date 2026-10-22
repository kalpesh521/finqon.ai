import { createRoot } from "react-dom/client";

import "@fontsource/poppins/400.css";
import "@fontsource/poppins/500.css";
import "@fontsource/poppins/600.css";
import "@fontsource/poppins/700.css";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";

import { App } from "@/App";
import "@/index.css";

const root = document.getElementById("root");
if (!root) throw new Error("Root element missing");

createRoot(root).render(<App />);
